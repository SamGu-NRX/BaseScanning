"""Storage-to-device proof: the real composer publishes the bytes the route serves.

The device result boundary was only ever proven with seeded ``result/result.json``
(#256) or a stub composer (#274). Nothing showed a real composed result traveling
the full path this packet owns: upstream stage artifacts in storage → production
hydration into a stage work dir → ``server.result.compose.run`` through
``runner.run_stage`` with stubs disabled → normal coordinator completion →
``GET /v1/captures/{id}/result`` as a device and as a reviewer.

The upstream scene/verdicts/reads/scale artifacts are **fixture-authored** at
this packet's boundary: they are committed synthetic fixtures published at the
exact storage keys the upstream stages write, registered the way a stage
completion registers them. The final criteria-integration packet replaces this
boundary with real criteria output. ``placement/placement.json`` is published as
opaque bytes and is never read as a criteria verdict — it does not govern
eligibility here.

Nothing seeds ``result/result.json``, ``result/result.internal.json`` or
``result/review.html``; the route only ever serves bytes the real composer
published. A private canary is written into the seeded verdict blob so the
device-output checks prove internal verdict data never leaks.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.api.app import create_app
from server.api.replay import run_replay
from server.common.config import Settings
from server.contracts.result import ResultResponse
from server.orchestrator.coordinator import Coordinator
from server.orchestrator.runner import run_stage
from server.orchestrator.services import bind, current
from server.orchestrator.stateio import get_json, put_json
from server.result.compose import ResultError
from server.result.tests.support import DATA, eligible_verdict, reject_verdict, views_verdict

ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "data" / "fixtures" / "packets" / "mini-arkit" / "packet.json"

TOKEN = "d" * 40
REVIEWER_TOKEN = "f" * 40
CANARY = "private-canary-burst9-9f3a"

# Fixture-authored upstream inputs, pinned because the composed result must
# carry them. reads.auto_fill.json is the committed reads fixture; the scale
# document mirrors its committed shape. Without the #255 hydration fallback
# both are dropped (reads_missing warning, scale "none"/"unverified").
SCALE_FIXTURE = {"source": "lidar", "status": "verified", "rel_bound_95": 0.1}
REPORT_FIXTURE = {"judge_flags": [], "c2": {"vetoes": []}}
PLACEHOLDER_BYTES = json.dumps({"fixture-authored": "burst9-bs-result-published-inputs"}).encode()

TERMINAL_EVENTS = frozenset({"verdict_ready", "retake_request", "failed"})

# Independently declared expectations per authored verdict fixture.
EXPECTED = {
    "eligible": {"capture_status": "complete", "event": "verdict_ready", "views": []},
    "views": {
        "capture_status": "needs_views",
        "event": "retake_request",
        "views": ["vn-wall", "vn-ground"],
    },
    "reject": {"capture_status": "manual_review", "event": "verdict_ready", "views": []},
}


def _with_canary(builder) -> dict:
    doc = builder()
    doc["evaluator"]["path"] = f"server/result/tests#{CANARY}"
    return doc


def _verdict_bytes(doc: dict) -> bytes:
    return (json.dumps(doc, indent=1) + "\n").encode()


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        STORAGE_BACKEND="volume",
        STATE_BACKEND="memory",
        VOLUME_ROOT=str(tmp_path / "vol"),
        CAPTURE_API_PUBLIC_URL="http://testserver",
        CAPTURE_API_TOKENS=f"device:{TOKEN},reviewer:{REVIEWER_TOKEN}",
        UPLOAD_URL_HMAC_SECRET="h" * 32,
        HSS_INGEST_HOOKS="auto",
        HSS_AUTH_MODE="static",
        GIT_SHA="0123456789abcdef",
        SMP_SERVICE_NAME="capture-api",
        SHOW_REJECT_TO_HOMEOWNER=False,
    )


class Journey:
    """One capture through the real front door, composed by the real result stage."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scene_dir: Path, verdict: dict) -> None:
        # Prewarm off, webhooks off, result knobs from the committed catalogue.
        monkeypatch.setenv("HSS_ORCH_PREWARM", "0")
        monkeypatch.delenv("WEBHOOK_HMAC_SECRET", raising=False)
        monkeypatch.setenv("BASE_WEBHOOK_URL", "")
        monkeypatch.setenv("STORAGE_BACKEND", "volume")
        monkeypatch.setenv("STATE_BACKEND", "memory")
        monkeypatch.setenv("VOLUME_ROOT", str(tmp_path / "vol"))
        monkeypatch.setenv("HSS_RESULT_PROMPTS_PATH", str(DATA / "prompts.en-US.json"))
        monkeypatch.setenv("HSS_RESULT_METER_AUTOFILL", "barcode")

        self.app = create_app(settings=_settings(tmp_path))
        self.client = TestClient(self.app)
        self.deps = self.app.state.deps
        self.services = replace(current(), state=self.deps.state, storage=self.deps.storage)
        self.coordinator = Coordinator(self.services)

        summary = run_replay(
            packet_path=PACKET,
            api="http://testserver/v1",
            token=TOKEN,
            client=self.client,
            until="processing",
        )
        assert summary["exit"] == 0, summary
        self.capture_id = summary["captureId"]
        self.run_id = self.deps.state.get_capture(self.capture_id).run_id
        run_row = self.deps.state.get_run(self.run_id)
        assert run_row is not None and run_row.status == "pending", run_row
        self.run = run_row

        self.seed_upstream(scene_dir, verdict)
        self.result_key: str | None = None

    # ------------------------------------------------------------------ seeds

    def seed_stage(self, stage: str, artifacts: dict[str, tuple[bytes, str]]) -> None:
        """Publish fixture bytes at the run's artifact keys and register completion.

        This mirrors what a real upstream stage completion writes: bytes in
        storage, ``stage_artifacts:<run>:<stage>`` in the state store, a done
        stage row, and the keys merged into the run record's artifact map.
        """

        prefix = f"captures/{self.capture_id}/runs/{self.run_id}"
        storage_keys = {}
        for key, (data, ctype) in artifacts.items():
            storage_key = f"{prefix}/{key}"
            self.deps.storage.put_bytes(storage_key, data, ctype)
            storage_keys[key] = storage_key
        put_json(self.deps.state, f"stage_artifacts:{self.run_id}:{stage}", storage_keys)
        rec = self.deps.state.get_stage(self.run_id, stage)
        assert rec is not None
        now = self.services.clock.iso()
        self.deps.state.upsert_stage(
            rec.model_copy(
                update={
                    "status": "done",
                    "heartbeat": now,
                    "finished_at": now,
                    "artifact_uri": next(iter(storage_keys.values()), None),
                    "metrics": {"duration_s": 0.5, "failures": 0.0, "respawns": 0.0},
                }
            )
        )
        merged = dict(self.run.artifacts)
        merged.update(storage_keys)
        self.run = self.run.model_copy(update={"artifacts": merged})
        self.deps.state.put_run(self.run)

    def seed_upstream(self, scene_dir: Path, verdict: dict) -> None:
        octet = "application/octet-stream"
        blob = (PLACEHOLDER_BYTES, "application/json")
        self.seed_stage("validate", {"packet/validation.json": blob, "packet/normalized.json": blob})
        self.seed_stage("poses", {"poses/cameras.json": blob, "poses/registration_report.json": blob})
        self.seed_stage("objects", {"objects/objects.json": blob})
        self.seed_stage(
            "reads",
            {"reads/reads.json": ((DATA / "reads.auto_fill.json").read_bytes(), "application/json")},
        )
        self.seed_stage("scale", {"scale/scale.json": (json.dumps(SCALE_FIXTURE).encode(), "application/json")})
        self.seed_stage("dense", {"dense/dense_report.json": blob})
        self.seed_stage("coverage", {"coverage/coverage_report.json": blob})
        self.seed_stage(
            "scene",
            {
                "scene/scene.json": ((scene_dir / "scene.json").read_bytes(), "application/json"),
                "scene/points.ply": ((scene_dir / "points.ply").read_bytes(), octet),
            },
        )
        self.seed_stage("objects_3d", {"objects/objects_3d.json": blob})
        # Opaque placement bytes: never a criteria verdict, never eligibility input.
        self.seed_stage("placement", {"placement/placement.json": blob})
        self.seed_stage(
            "criteria",
            {
                "verdicts/verdict.C.json": (_verdict_bytes(verdict), "application/json"),
                "verdicts/criteria_report.json": (json.dumps(REPORT_FIXTURE).encode(), "application/json"),
            },
        )

    def overwrite_verdict(self, verdict: dict) -> None:
        """Replace the published verdict bytes in place; registration is unchanged."""

        key = self.run.artifacts["verdicts/verdict.C.json"]
        self.deps.storage.put_bytes(key, _verdict_bytes(verdict), "application/json")

    # ------------------------------------------------- real result-stage runs

    def _running_rec(self, attempt: int):
        state = self.deps.state
        rec = state.get_stage(self.run_id, "result")
        assert rec is not None
        rec = rec.model_copy(
            update={
                "status": "running",
                "attempt": attempt,
                "worker_call_id": f"test-call-{attempt}",
                "error": None,
                "error_code": None,
                "heartbeat": self.services.clock.iso(),
            }
        )
        state.upsert_stage(rec)
        return rec

    def run_result_stage(self, attempt: int = 1) -> None:
        """Hydrate through services.stage_context, run the real composer, complete."""

        state = self.deps.state
        rec = self._running_rec(attempt)
        with bind(self.services):
            context = self.services.stage_context(state.get_run(self.run_id), rec, f"test-call-{attempt}")
            # No short input keys exist in orchestrated inputs; the hydrated
            # work_dir copies are the only reads/scale the composer can see.
            assert "reads" not in context.inputs and "scale" not in context.inputs
            result = run_stage(context, stubs=False)
            self.coordinator._mark_done(state.get_run(self.run_id), rec, result)
        self.coordinator._maybe_finish(state.get_run(self.run_id))
        self.result_key = self.deps.state.get_run(self.run_id).artifacts["result/result.json"]

    def run_result_stage_failing(self) -> ResultError:
        """The real composer on broken upstream bytes, closed through the coordinator."""

        state = self.deps.state
        rec = self._running_rec(1)
        with bind(self.services):
            context = self.services.stage_context(state.get_run(self.run_id), rec, "test-call")
            try:
                run_stage(context, stubs=False)
            except ResultError as exc:
                self.coordinator._mark_error(state.get_run(self.run_id), rec, exc)
                return exc
        raise AssertionError("the result stage unexpectedly succeeded on broken inputs")

    # ----------------------------------------------------------------- views

    def get_result(self, token: str = TOKEN):
        return self.client.get(
            f"/v1/captures/{self.capture_id}/result",
            headers={"Authorization": f"Bearer {token}"},
        )

    def final_events(self) -> list:
        events = self.deps.state.events_after(self.capture_id, 0, limit=500)
        return [event for event in events if event.type in TERMINAL_EVENTS and event.run_id == self.run_id]

    def internal_document(self) -> dict:
        assert self.result_key is not None
        internal_key = self.result_key.replace("result/result.json", "result/result.internal.json")
        return json.loads(self.deps.storage.get_bytes(internal_key))

    def assert_served_from_published_bytes(self, response) -> dict:
        """The served body equals the composer's stored bytes plus the composed keys."""

        assert self.result_key is not None
        stored = json.loads(self.deps.storage.get_bytes(self.result_key))
        body = response.json()
        composed_keys = ("status", "runId", "verdictUrl", "previewUrl", "reviewUrl")
        rest = {key: value for key, value in body.items() if key not in composed_keys}
        assert rest == stored, "the route served bytes the composer did not publish"
        return body

    def assert_common_shape(self, body: dict, expectation: dict) -> None:
        # Identity: the served body names this run, and the record agrees.
        assert body["runId"] == self.run_id
        assert self.deps.state.get_capture(self.capture_id).status == expectation["capture_status"]
        assert body["status"] == expectation["capture_status"]
        # Hydrated reads and scale survive into the served body (the values the
        # #255 fallback rescues; dropped without it).
        assert body["scale"] == {"source": "lidar", "status": "verified", "relBound95": 0.1}
        assert body["meter"]["status"] == "auto_filled"
        assert body["memberActions"] == []
        # Views match the authored verdict fixture, not a stub's empty list.
        assert [view["id"] for view in body["outcome"]["viewsNeeded"]] == expectation["views"]
        assert [view["id"] for view in body["viewsNeeded"]] == expectation["views"]
        # The response validates against the wire contract.
        ResultResponse.model_validate(body)
        # Exactly one terminal event for this run, of the declared type.
        finals = self.final_events()
        assert len(finals) == 1, [event.type for event in finals]
        assert finals[0].type == expectation["event"]
        # The internal document recorded no missing reads.
        internal = self.internal_document()
        assert "reads_missing" not in internal["warnings"]


@pytest.fixture
def make_journey(tmp_path, monkeypatch):
    def _make(scene_dir: Path, verdict: dict) -> Journey:
        return Journey(tmp_path, monkeypatch, scene_dir, verdict)

    return _make


def test_eligible_result_served_from_published_bytes(make_journey) -> None:
    journey = make_journey(DATA / "synthetic", _with_canary(eligible_verdict))
    journey.run_result_stage()

    response = journey.get_result()
    assert response.status_code == 200
    body = journey.assert_served_from_published_bytes(response)
    expectation = EXPECTED["eligible"]
    journey.assert_common_shape(body, expectation)
    # Outcome and placement come from the authored verdict, not a stub.
    assert body["outcome"]["kind"] == "eligible"
    assert body["site"]["decision"] == "pass"
    assert body["outcome"]["recommendedPlacement"] is not None
    # The private canary lives in the stored verdict blob and never in the body.
    verdict_key = journey.run.artifacts["verdicts/verdict.C.json"]
    assert CANARY in journey.deps.storage.get_bytes(verdict_key).decode()
    assert CANARY not in response.text


def test_reviewer_gets_signed_verdict_and_device_does_not(make_journey) -> None:
    journey = make_journey(DATA / "synthetic", _with_canary(eligible_verdict))
    journey.run_result_stage()

    device = journey.get_result()
    assert device.status_code == 200
    device_body = device.json()
    assert device_body["verdictUrl"] is None
    assert CANARY not in device.text

    reviewer = journey.get_result(REVIEWER_TOKEN)
    assert reviewer.status_code == 200
    reviewer_body = reviewer.json()
    assert reviewer_body["verdictUrl"] is not None
    assert reviewer_body["verdictUrl"].startswith("http://testserver/v1/uploads/")
    assert CANARY not in reviewer.text

    # Redaction is the only device/reviewer difference in composed fields.
    for key in ("status", "runId", "outcome", "site", "scale", "meter", "viewsNeeded"):
        assert device_body[key] == reviewer_body[key], key


def test_needs_views_result_lists_fixture_views(make_journey) -> None:
    journey = make_journey(DATA / "photo", _with_canary(views_verdict))
    journey.run_result_stage()

    response = journey.get_result()
    assert response.status_code == 200
    body = journey.assert_served_from_published_bytes(response)
    journey.assert_common_shape(body, EXPECTED["views"])
    assert body["outcome"]["kind"] == "needs_more_photos"
    assert body["site"]["decision"] == "manual_review"


def test_hidden_reject_masks_device_and_keeps_internal_decision(make_journey) -> None:
    journey = make_journey(DATA / "photo", _with_canary(reject_verdict))
    journey.run_result_stage()

    response = journey.get_result()
    assert response.status_code == 200
    body = journey.assert_served_from_published_bytes(response)
    expectation = EXPECTED["reject"]
    journey.assert_common_shape(body, expectation)
    # Hidden-rejection policy: the device sees manual_review, no reasons, no
    # failed-criteria detail, and no verdict link.
    assert body["outcome"]["kind"] == "manual_review"
    assert body["site"]["decision"] == "manual_review"
    assert body["outcome"]["reasons"] == []
    assert "closer to the driveway" not in response.text
    assert body["verdictUrl"] is None
    # The internal document keeps the true decision.
    internal = journey.internal_document()
    assert internal["decision"] == "reject"
    assert internal["outcome_kind_internal"] == "not_eligible"
    assert CANARY not in response.text


def test_malformed_verdict_yields_no_successful_result(make_journey) -> None:
    journey = make_journey(DATA / "synthetic", _with_canary(eligible_verdict))
    key = journey.run.artifacts["verdicts/verdict.C.json"]
    journey.deps.storage.put_bytes(key, b'{"site": not json', "application/json")

    exc = journey.run_result_stage_failing()
    assert exc.code == "verdict_invalid"
    rec = journey.deps.state.get_stage(journey.run_id, "result")
    assert rec is not None and rec.status == "failed"
    assert rec.error_code == "verdict_invalid"

    response = journey.get_result()
    assert response.status_code == 200
    body = response.json()
    # No valid successful result exists to serve: the not-ready envelope only.
    assert body["outcome"] is None
    assert body["status"] == "failed"
    assert body["viewsNeeded"] == []
    assert body["runId"] == journey.run_id
    assert journey.deps.state.get_capture(journey.capture_id).status == "failed"
    assert journey.final_events() == []
    assert journey.result_key is None


def test_missing_verdicts_yields_no_successful_result(make_journey) -> None:
    journey = make_journey(DATA / "synthetic", _with_canary(eligible_verdict))
    # Re-register the criteria stage with no verdict artifact at all.
    put_json(journey.deps.state, f"stage_artifacts:{journey.run_id}:criteria", {})
    merged = {key: value for key, value in journey.run.artifacts.items() if not key.startswith("verdicts/")}
    journey.run = journey.run.model_copy(update={"artifacts": merged})
    journey.deps.state.put_run(journey.run)

    exc = journey.run_result_stage_failing()
    assert exc.code == "verdict_missing"
    rec = journey.deps.state.get_stage(journey.run_id, "result")
    assert rec is not None and rec.status == "failed" and rec.error_code == "verdict_missing"

    response = journey.get_result()
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] is None
    assert body["status"] == "failed"
    assert journey.final_events() == []
    assert journey.result_key is None


def test_controlled_verdict_change_changes_the_served_response(make_journey) -> None:
    journey = make_journey(DATA / "synthetic", _with_canary(eligible_verdict))
    journey.run_result_stage()

    first = journey.get_result()
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["outcome"]["kind"] == "eligible"
    assert first_body["viewsNeeded"] == []

    # Controlled upstream change: swap the published verdict for the views
    # fixture and re-run the real composer (a second attempt on the same run).
    journey.overwrite_verdict(_with_canary(views_verdict))
    journey.run_result_stage(attempt=2)

    second = journey.get_result()
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["outcome"]["kind"] == "needs_more_photos"
    assert second_body["status"] == "needs_views"
    assert [view["id"] for view in second_body["viewsNeeded"]] == EXPECTED["views"]["views"]
    assert second_body["runId"] == journey.run_id
    # Still exactly one terminal event for the run despite the re-compose.
    finals = journey.final_events()
    assert len(finals) == 1 and finals[0].type == "retake_request"
    assert CANARY not in second.text
