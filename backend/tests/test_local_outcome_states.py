"""``HSS_LOCAL_OUTCOME`` walks a stubbed local run to each state the app renders.

Every capture status the app renders — ``manual_review``, ``complete``,
``needs_views``, ``failed`` — has an outcome-table row behind it
(``server/orchestrator/outcomes.py``), and until now no local run could reach
any of them but ``manual_review``: the stage stubs always decided
``manual_review``, so "walk the app locally" stopped at one screen. This packet
adds a dev-only script: with stage stubs and the dev environment, setting
``HSS_LOCAL_OUTCOME`` (or ``HSS_LOCAL_STAGE_DELAY_S`` for pacing) drives one
run onto each row.

These tests are that proof, over the same documented dev settings as the
journey tests — memory state, volume storage, static test auth, stage stubs,
no Modal, no GPU, no network. The committed fixtures under
``tests/fixtures/app_states/<state>/`` are the served device payloads and
event walks, one per state, regenerated with
``PYTHONPATH=. uv run python tests/test_local_outcome_states.py --generate`` and re-checked
here so a later contract change cannot silently invalidate the evidence.

The scripted outcomes are test stand-ins: every message and review page says so
("local test outcome, not a check of a wall"), and the default stub run keeps
today's bytes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.api.app import create_app
from server.api.deps import drain_finalize
from server.common.config import Settings
from server.contracts.result import ResultResponse
from server.orchestrator.config import load_orch
from server.orchestrator.outcomes import Facts, decide
from server.result.compose import FOOTPRINT

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKET_FIXTURE = REPO_ROOT / "backend" / "data" / "fixtures" / "packets" / "mini-arkit"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "app_states"

# (name, HSS_LOCAL_OUTCOME, SHOW_REJECT_TO_HOMEOWNER, capture status, outcome kind).
STATES = [
    ("manual_review", "manual_review", False, "manual_review", "manual_review"),
    ("eligible", "eligible", False, "complete", "eligible"),
    ("needs_more_photos", "needs_more_photos", False, "needs_views", "needs_more_photos"),
    ("not_eligible", "not_eligible", True, "complete", "not_eligible"),
    ("failed", "failed", False, "failed", "manual_review"),
]

TEMPLATE_ENV = {
    "HSS_ORCH_STAGE_STUBS": "1",
    "HSS_ORCH_PREWARM": "0",
    "VISION_EXTERNAL_CALLS": "off",
    "STATE_BACKEND": "memory",
    "STORAGE_BACKEND": "volume",
    "SMP_ENVIRONMENT": "dev",
}


def _settings(tmp: Path, show_reject: bool) -> Settings:
    return Settings(
        SMP_ENVIRONMENT="dev",
        HSS_AUTH_MODE="static",
        # Synthetic, explicitly static test auth — no real principal.
        CAPTURE_API_TOKENS=f"device:{'d' * 40}",
        UPLOAD_URL_HMAC_SECRET="h" * 32,
        STORAGE_BACKEND="volume",
        STATE_BACKEND="memory",
        VOLUME_ROOT=str(tmp / "vol"),
        CAPTURE_API_PUBLIC_URL="http://testserver",
        HSS_INGEST_HOOKS="auto",
        VISION_EXTERNAL_CALLS="off",
        GIT_SHA="0123456789abcdef",
        SMP_SERVICE_NAME="capture-api",
        SHOW_REJECT_TO_HOMEOWNER=show_reject,
    )


@pytest.fixture
def local_app(tmp_path, monkeypatch):
    """Build the app behind the capture front door with a scripted outcome.

    Returns a builder ``(outcome, show_reject, delay=None) -> (root, packet,
    client, app, deps)``; monkeypatch restores the environment afterwards.
    """
    for key, value in TEMPLATE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("HSS_LOCAL_OUTCOME", raising=False)
    monkeypatch.delenv("HSS_LOCAL_STAGE_DELAY_S", raising=False)
    # Stage code sees env-loaded settings (the orchestrator hook binds a fresh
    # template), so the reject flag must be set as an environment variable too.
    monkeypatch.delenv("SHOW_REJECT_TO_HOMEOWNER", raising=False)

    def build(outcome: str | None = None, show_reject: bool = False, delay: float | None = None):
        root = tmp_path / f"packet-{outcome or 'default'}"
        shutil.copytree(PACKET_FIXTURE, root)
        packet = json.loads((root / "packet.json").read_text())
        monkeypatch.setenv("VOLUME_ROOT", str(tmp_path / f"vol-{outcome or 'default'}"))
        if outcome is not None:
            monkeypatch.setenv("HSS_LOCAL_OUTCOME", outcome)
        if delay is not None:
            monkeypatch.setenv("HSS_LOCAL_STAGE_DELAY_S", str(delay))
        if show_reject:
            monkeypatch.setenv("SHOW_REJECT_TO_HOMEOWNER", "1")
        settings = _settings(tmp_path, show_reject)
        app = create_app(settings=settings)
        client = TestClient(app)
        client.headers["Authorization"] = f"Bearer {'d' * 40}"
        return root, packet, client, app, app.state.deps

    return build


# --- the capture front door, as the journey tests walk it -------------------


def _content_type(role: str) -> str:
    if role == "stream":
        return "application/gzip"
    if role in {"features", "depth", "confidence", "mesh"}:
        return "application/octet-stream"
    return "image/jpeg"


def _register_and_commit(root: Path, packet: dict, client: TestClient, app, capture_id: str) -> None:
    registered_paths = []
    for entry in packet["files"]:
        data = (root / entry["path"]).read_bytes()
        item = {
            "path": entry["path"],
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "md5": base64.b64encode(hashlib.md5(data).digest()).decode(),
            "role": entry.get("role", "still"),
            "contentType": _content_type(entry.get("role", "still")),
        }
        registered = client.post(f"/v1/captures/{capture_id}/files", json={"files": [item]})
        assert registered.status_code == 200, registered.text
        upload = registered.json()["files"][0]["upload"]
        # The signed URL is its own capability: no bearer header on the PUT.
        put = TestClient(app).put(upload["url"], content=data, headers=upload.get("headers") or {})
        assert put.status_code == 200, put.text
        registered_paths.append({"path": item["path"], "sha256": item["sha256"]})
    commit = client.post(f"/v1/captures/{capture_id}/files:commit", json={"files": registered_paths})
    assert commit.status_code == 200, commit.text


def _open_front_door(root: Path, packet: dict, client: TestClient, app, packet_id: str) -> tuple[str, bytes]:
    document = dict(packet)
    document["packetId"] = packet_id
    raw = (json.dumps(document, indent=2) + "\n").encode()
    (root / "packet.json").write_bytes(raw)
    session = packet["session"]
    created = client.post(
        "/v1/captures",
        json={
            "packetId": packet_id,
            "formatVersion": packet["formatVersion"],
            "tier": session["tier"],
            "flow": "guided",
            "device": {
                "model": session["deviceModel"],
                "systemVersion": session["systemVersion"],
                "appVersion": session["appVersion"],
            },
        },
        headers={"Idempotency-Key": packet_id},
    )
    assert created.status_code == 201, created.text
    capture_id = created.json()["captureId"]
    _register_and_commit(root, packet, client, app, capture_id)
    return capture_id, raw


def _run_capture(root: Path, packet: dict, client: TestClient, app, packet_id: str) -> tuple[str, str, object]:
    """Front door, finalize, drain; returns (capture_id, run_id, capture record)."""
    capture_id, raw = _open_front_door(root, packet, client, app, packet_id)
    response = client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=raw,
        headers={
            "Idempotency-Key": packet_id,
            "Content-Type": "application/json",
            "X-Packet-Sha256": hashlib.sha256(raw).hexdigest(),
        },
    )
    assert response.status_code == 202, response.text
    drain_finalize(app.state.deps)
    run_id = response.json()["runId"]
    return capture_id, run_id, app.state.deps.state.get_capture(capture_id)


def _flat(client: TestClient, capture_id: str) -> list[dict]:
    """Walk the events cursor to quiescence, flattened in delivery order."""
    events: list[dict] = []
    after = 0
    for _ in range(60):
        page = client.get(f"/v1/captures/{capture_id}/events", params={"after": after}).json()
        assert page["next"] >= after, "the events cursor moved backwards"
        events.extend(page["events"])
        if page["next"] == after:
            return events
        after = page["next"]
    pytest.fail("event feed never stopped moving")


TERMINAL_EVENT_TYPES = {"verdict_ready", "retake_request", "failed"}


def _last_terminal(events: list[dict]) -> dict:
    """The outcome event, walking back past the later stage events."""
    for event in reversed(events):
        if event["type"] in TERMINAL_EVENT_TYPES:
            return event
    pytest.fail(f"no terminal outcome event among {[event['type'] for event in events[-6:]]}")


# --- the states --------------------------------------------------------------


@pytest.mark.parametrize(
    "name,outcome,show_reject,status,kind",
    STATES,
    ids=[state[0] for state in STATES],
)
def test_hss_local_outcome_walks_the_app_to_each_state(
    name: str, outcome: str, show_reject: bool, status: str, kind: str | None, local_app
) -> None:
    root, packet, client, app, deps = local_app(outcome, show_reject)
    capture_id, run_id, capture = _run_capture(root, packet, client, app, f"local-{name}")

    assert capture.status == status, f"HSS_LOCAL_OUTCOME={outcome} must land {status}"
    served = client.get(f"/v1/captures/{capture_id}/result")
    assert served.status_code == 200, served.text
    payload = served.json()
    # The device envelope stays contract-valid in every state.
    ResultResponse.model_validate(payload)

    run = deps.state.get_run(run_id)
    if outcome == "failed":
        # Row 1: the validate stub fails the run. The capture lands failed, the
        # device sees the manual-review envelope with the packet-invalid reason,
        # and no stub result composes.
        assert capture.status == "failed"
        assert run.status == "failed"
        assert payload["status"] == "failed"
        assert payload["outcome"]["kind"] == "manual_review"
        assert payload["outcome"]["message"] == "packet_invalid"
        assert "result/review.html" not in run.artifacts
        return

    review = deps.storage.get_bytes(run.artifacts["result/review.html"])
    assert b"stand-in" in review
    assert b"placement still ran the real CPU solver" in review
    assert payload["status"] == status
    assert payload["outcome"]["kind"] == kind
    final = _last_terminal(_flat(client, capture_id))
    if outcome == "needs_more_photos":
        assert final["type"] == "retake_request"
        assert final["data"]["viewsNeeded"] == [payload["viewsNeeded"][0]["id"]]
    else:
        assert final["type"] == "verdict_ready"
        assert final["data"]["kind"] == kind
    if outcome == "manual_review":
        # The scripted default keeps today's plain stub wording.
        assert payload["outcome"]["message"] == "Review required before a placement is shown."
    else:
        # Scripted non-default outcomes always say they are stand-ins, on the
        # device and off it.
        assert "local test outcome" in payload["outcome"]["message"]
        assert "This is a local test" in payload["outcome"]["message"]
        assert b"HSS_LOCAL_OUTCOME" in review
        internal = json.loads(deps.storage.get_bytes(run.artifacts["result/result.internal.json"]))
        assert internal["scripted_outcome"] == outcome
    assert "scripted_outcome" not in json.dumps(payload), "internal detail leaked to the device"

    if outcome == "eligible":
        placement = payload["outcome"]["recommendedPlacement"]
        assert placement is not None
        assert placement["footprintM"] == {"w": 0.7874, "h": 1.0033, "d": 0.5588}
        assert placement["startSM"] == 0.3
        # The fixture packet carries exactly one ARKit epoch ("e1", an object
        # with an id), so the proposal is anchored in ARKit world.
        assert payload["outcome"]["arkitEpoch"] == "e1"
        assert placement["boxArkitWorld"] is not None
        assert placement["boxArkitWorld"]["size"] == [0.7874, 1.0033, 0.5588]
        assert placement["boxSceneM"]["centerM"][0] == 0.3 + placement["footprintM"]["w"] / 2
    if outcome == "needs_more_photos":
        view = payload["viewsNeeded"][0]
        assert view["kind"] == "band"
        assert view["promptId"]
        assert view["prompt"]["title"] and view["prompt"]["body"]
        assert payload["outcome"]["viewsNeeded"] == payload["viewsNeeded"]


def test_default_stub_run_keeps_todays_manual_review_bytes(local_app) -> None:
    """Without the variable the stub run is exactly today's run."""
    root, packet, client, app, deps = local_app(None, False)
    capture_id, run_id, capture = _run_capture(root, packet, client, app, "local-default")
    assert capture.status == "manual_review"
    payload = client.get(f"/v1/captures/{capture_id}/result").json()
    assert payload["status"] == "manual_review"
    assert payload["outcome"]["kind"] == "manual_review"
    assert payload["outcome"]["message"] == "Review required before a placement is shown."
    assert payload["viewsNeeded"] == []
    final = _last_terminal(_flat(client, capture_id))
    assert final["type"] == "verdict_ready"
    assert final["data"]["kind"] == "manual_review"


def test_not_eligible_without_show_reject_lands_manual_review_loudly(local_app) -> None:
    """With the reject hidden, a scripted reject cannot land; the run fails loudly
    with the variable to set, and the capture stays on the safe manual-review row."""
    root, packet, client, app, deps = local_app("not_eligible", show_reject=False)
    capture_id, run_id, capture = _run_capture(root, packet, client, app, "local-reject-hidden")
    assert capture.status == "manual_review"
    run = deps.state.get_run(run_id)
    assert run.status == "failed"
    stage_errors = " ".join(
        str((stage.model_dump(mode="json") if hasattr(stage, "model_dump") else dict(stage)).get("error") or "")
        for stage in deps.state.stages(run_id)
    )
    assert "SHOW_REJECT_TO_HOMEOWNER" in stage_errors


def test_stage_delay_paces_the_stub_run(local_app) -> None:
    root, packet, client, app, deps = local_app("manual_review", False, delay=0.03)
    started = time.monotonic()
    capture_id, run_id, capture = _run_capture(root, packet, client, app, "local-delay")
    elapsed = time.monotonic() - started
    assert capture.status == "manual_review"
    # Every stub slept once; scheduling jitter stays inside the margin.
    assert elapsed >= 12 * 0.03 - 0.05, f"12 stub stages slept nowhere near 0.36s ({elapsed:.2f}s)"


def test_failed_validation_artifact_is_scoped_to_its_run(local_app) -> None:
    """Two failed captures in a row: each ends ``failed`` with its own
    validation.json, at the run-prefixed key the stage publisher uses — a later
    capture can never overwrite an earlier run's evidence."""
    root, packet, client, app, deps = local_app("failed")
    first_capture, first_run, first = _run_capture(root, packet, client, app, "failed-1")
    second_capture, second_run, second = _run_capture(root, packet, client, app, "failed-2")
    assert first.status == "failed"
    assert second.status == "failed"
    for capture_id, run_id in ((first_capture, first_run), (second_capture, second_run)):
        run = deps.state.get_run(run_id)
        key = run.artifacts["packet/validation.json"]
        assert key == f"captures/{capture_id}/runs/{run_id}/packet/validation.json"
        doc = json.loads(deps.storage.get_bytes(key))
        assert doc["imagesUsable"] is False


@pytest.mark.parametrize("profile", ["A", "B", "C"])
def test_eligible_footprint_follows_the_configured_profile(local_app, monkeypatch, profile) -> None:
    """The scripted placement advertises the configured profile, so its footprint
    must come from that profile's row in the real composer's FOOTPRINT table."""
    monkeypatch.setenv("RULES_PROFILE_DEFAULT", profile)
    root, packet, client, app, deps = local_app("eligible")
    capture_id, run_id, capture = _run_capture(root, packet, client, app, f"local-footprint-{profile}")
    assert capture.status == "complete"
    payload = client.get(f"/v1/captures/{capture_id}/result").json()
    outcome = payload["outcome"]
    assert outcome["profile"] == profile
    width, height, depth = FOOTPRINT[profile]
    assert outcome["recommendedPlacement"]["footprintM"] == {"w": width, "h": height, "d": depth}


# --- loud refusals -----------------------------------------------------------


def test_local_outside_dev_stage_stubs_is_refused() -> None:
    with pytest.raises(ValueError, match="HSS_LOCAL_OUTCOME"):
        load_orch({"HSS_LOCAL_OUTCOME": "eligible"})
    with pytest.raises(ValueError, match="HSS_LOCAL_STAGE_DELAY_S"):
        load_orch({"SMP_ENVIRONMENT": "dev", "HSS_LOCAL_STAGE_DELAY_S": "1.5"})
    with pytest.raises(ValueError, match="HSS_LOCAL_STAGE_DELAY_S"):
        load_orch({"HSS_ORCH_STAGE_STUBS": "1", "SMP_ENVIRONMENT": "prod", "HSS_LOCAL_STAGE_DELAY_S": "1.5"})


def test_local_unknown_outcome_value_is_refused() -> None:
    with pytest.raises(ValueError, match="needs_more_photos"):
        load_orch(
            {
                "HSS_ORCH_STAGE_STUBS": "1",
                "SMP_ENVIRONMENT": "dev",
                "HSS_LOCAL_OUTCOME": "bogus",
            }
        )


def test_local_delay_out_of_range_is_refused() -> None:
    with pytest.raises(ValueError, match="0 to 30"):
        load_orch(
            {
                "HSS_ORCH_STAGE_STUBS": "1",
                "SMP_ENVIRONMENT": "dev",
                "HSS_LOCAL_STAGE_DELAY_S": "45",
            }
        )


def test_local_delay_non_finite_is_refused() -> None:
    """``float()`` accepts ``nan`` and ``inf``; refuse them by name with a
    message that says the value must be finite, before the range comparison."""
    for raw in ("nan", "inf", "-inf"):
        with pytest.raises(ValueError, match="not a finite number of seconds"):
            load_orch(
                {
                    "HSS_ORCH_STAGE_STUBS": "1",
                    "SMP_ENVIRONMENT": "dev",
                    "HSS_LOCAL_STAGE_DELAY_S": raw,
                }
            )


def test_local_settings_parse_when_dev_stage_stubs_are_on() -> None:
    cfg = load_orch(
        {
            "HSS_ORCH_STAGE_STUBS": "1",
            "SMP_ENVIRONMENT": "dev",
            "HSS_LOCAL_OUTCOME": "needs_more_photos",
            "HSS_LOCAL_STAGE_DELAY_S": "1.5",
        }
    )
    assert cfg.local_outcome == "needs_more_photos"
    assert cfg.local_stage_delay_s == 1.5
    plain = load_orch({})
    assert plain.local_outcome == "manual_review"
    assert plain.local_stage_delay_s == 0.0


def test_dispatch_refuses_a_stray_local_outcome_loudly(monkeypatch, tmp_path, capfd) -> None:
    """With stage stubs off, a stray variable runs nothing: no run record is
    created, the capture stays processing, and the finalize hook logs the refused
    dispatch. The config loader's ValueError text naming the variable is pinned
    by the load_orch tests above."""
    for key, value in TEMPLATE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("HSS_ORCH_STAGE_STUBS", "0")
    monkeypatch.setenv("HSS_LOCAL_OUTCOME", "eligible")
    monkeypatch.setenv("VOLUME_ROOT", str(tmp_path / "vol"))
    app = create_app(settings=_settings(tmp_path, show_reject=False))
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {'d' * 40}"
    packet = json.loads((PACKET_FIXTURE / "packet.json").read_text())
    shutil.copytree(PACKET_FIXTURE, tmp_path / "packet", dirs_exist_ok=True)
    capture_id, raw = _open_front_door(tmp_path / "packet", packet, client, app, "refuse-stray")
    response = client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=raw,
        headers={
            "Idempotency-Key": "refuse-stray",
            "Content-Type": "application/json",
            "X-Packet-Sha256": hashlib.sha256(raw).hexdigest(),
        },
    )
    assert response.status_code == 202, response.text
    drain_finalize(app.state.deps)
    deps = app.state.deps
    record = deps.state.get_capture(capture_id)
    # Nothing landed: no run record, and the capture never reached a terminal state.
    assert record.status not in {"complete", "needs_views", "manual_review", "failed"}
    assert deps.state.runs_for_capture(capture_id) == []
    captured = capfd.readouterr()
    assert "hook.error" in captured.out + captured.err


# --- committed state fixtures ------------------------------------------------


def test_committed_state_fixtures_match_the_outcome_table() -> None:
    """The committed evidence still says what the outcome table says."""
    directories = sorted(path for path in FIXTURES.iterdir() if path.is_dir())
    assert {path.name for path in directories} == {state[0] for state in STATES}
    for directory in directories:
        doc = json.loads((directory / "events.json").read_text())
        served = json.loads((directory / "result.json").read_text())
        ResultResponse.model_validate(served)
        verdict = doc["verdict"]
        if verdict["decision"] == "validate_failed":
            assert doc["captureStatus"] == "failed"
        else:
            row = decide(
                Facts(
                    site_decision=verdict["decision"],
                    views_needed=verdict["views"] > 0,
                    # The committed not_eligible fixture was captured with the
                    # reject shown; only the reject row reads this flag.
                    show_reject_to_homeowner=True,
                )
            )
            assert row.capture_status == doc["captureStatus"]
        assert served["status"] == doc["captureStatus"]
        if doc["outcomeKind"] is None:
            assert served["outcome"] is None
        else:
            assert served["outcome"]["kind"] == doc["outcomeKind"]
            if directory.name == "failed":
                # The degraded envelope's reason, not scripted copy.
                assert served["outcome"]["message"] == "packet_invalid"
            elif directory.name == "manual_review":
                # The scripted default keeps today's plain stub wording.
                assert served["outcome"]["message"] == "Review required before a placement is shown."
            else:
                assert "local test outcome" in served["outcome"]["message"]
        assert doc["terminalEventKind"] in TERMINAL_EVENT_TYPES


@pytest.mark.parametrize("name,outcome,show_reject,_status,_kind", STATES, ids=[state[0] for state in STATES])
def test_live_terminal_event_matches_committed_fixture(name, outcome, show_reject, _status, _kind, local_app) -> None:
    root, packet, client, app, _deps = local_app(outcome, show_reject)
    capture_id, run_id, capture = _run_capture(root, packet, client, app, f"fixture-check-{name}")
    live = _last_terminal(_flat(client, capture_id))
    fixture = json.loads((FIXTURES / name / "events.json").read_text())
    expected = _last_terminal(fixture["events"])
    assert capture.status == fixture["captureStatus"]
    assert live["type"] == expected["type"] == fixture["terminalEventKind"]
    # Run IDs change on regeneration; retain every other terminal payload field.
    live_data = dict(live["data"])
    expected_data = dict(expected["data"])
    if "runId" in live_data:
        assert live_data.pop("runId") == run_id
        result = json.loads((FIXTURES / name / "result.json").read_text())
        assert expected_data.pop("runId") == result["runId"]
    assert live_data == expected_data


def _generate() -> None:
    """Regenerate the committed state fixtures. Run: PYTHONPATH=. uv run python tests/test_local_outcome_states.py --generate"""
    work = Path(tempfile.mkdtemp(prefix="hss-state-fixtures-"))
    keys = list(TEMPLATE_ENV) + [
        "VOLUME_ROOT",
        "HSS_LOCAL_OUTCOME",
        "HSS_LOCAL_STAGE_DELAY_S",
        "SHOW_REJECT_TO_HOMEOWNER",
    ]
    saved = {key: os.environ.get(key) for key in keys}
    try:
        for name, outcome, show_reject, _status, _kind in STATES:
            root = work / f"packet-{name}"
            shutil.copytree(PACKET_FIXTURE, root)
            packet = json.loads((root / "packet.json").read_text())
            for key, value in TEMPLATE_ENV.items():
                os.environ[key] = value
            os.environ["VOLUME_ROOT"] = str(work / f"vol-{name}")
            os.environ.pop("HSS_LOCAL_STAGE_DELAY_S", None)
            if show_reject:
                os.environ["SHOW_REJECT_TO_HOMEOWNER"] = "1"
            else:
                os.environ.pop("SHOW_REJECT_TO_HOMEOWNER", None)
            if outcome is not None:
                os.environ["HSS_LOCAL_OUTCOME"] = outcome
            else:
                os.environ.pop("HSS_LOCAL_OUTCOME", None)
            app = create_app(settings=_settings(work, show_reject))
            client = TestClient(app)
            client.headers["Authorization"] = f"Bearer {'d' * 40}"
            capture_id, run_id, capture = _run_capture(root, packet, client, app, f"fixture-{name}")
            served = client.get(f"/v1/captures/{capture_id}/result").json()
            deps = app.state.deps
            run = deps.state.get_run(run_id)
            if capture.status == "failed":
                verdict = {"decision": "validate_failed", "views": 0}
            else:
                doc = json.loads(deps.storage.get_bytes(run.artifacts["verdicts/verdict.C.json"]))
                verdict = {
                    "decision": doc["site"]["decision"],
                    "views": len(doc.get("views_needed") or []),
                }
            events = _flat(client, capture_id)
            final = _last_terminal(events)
            out = FIXTURES / name
            out.mkdir(parents=True, exist_ok=True)
            (out / "result.json").write_text(json.dumps(served, indent=2, sort_keys=True) + "\n")
            (out / "events.json").write_text(
                json.dumps(
                    {
                        "captureStatus": capture.status,
                        "outcomeKind": (served.get("outcome") or {}).get("kind"),
                        "terminalEventKind": final["type"],
                        "verdict": verdict,
                        "events": events,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            print(f"wrote {out} (capture {capture_id}, status {capture.status})")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    import sys

    if "--generate" in sys.argv:
        _generate()
    else:
        print("usage: python tests/test_local_outcome_states.py --generate")
