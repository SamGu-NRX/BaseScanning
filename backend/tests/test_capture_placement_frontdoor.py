"""The placement stage behind Hunter's capture front door, end to end.

The placement-stage PR (#268) proves the stage in isolation: its tests drive the
coordinator over a raw packet tar and pin the direct ``POST /v1/placements``
endpoint. The integration gate (#264) walks the device's own sequence through
``/v1/captures``, but with stage stubs on it only asserts that a ``placement``
stage event exists — nothing ties the placement artifact or its decision to the
run and result a device actually reads. This file is that missing proof: the
capture front door reaches the real placement stage.

The journey is the ``CaptureAPI`` wire contract — create, register files, PUT
bytes to the signed upload URLs, commit, finalize ``packet.json``, poll events,
read the result — over the committed ``mini-arkit`` packet fixture and the
documented dev settings (SD-73): ``HSS_INGEST_HOOKS=auto`` (the configured local
hook) with memory state, local volume storage and stage stubs on. No Modal, no
GPU, no network, no credentials. ``placement`` is ``stubs_exempt`` —
deterministic CPU work — so the real top-level solver runs behind the door.

The test asserts the stage is in the run evidence (its artifacts in the run's
artifact map, its stage record done with positive solver time, its event between
``scene`` and ``criteria``), so the journey fails if the capture path stops
invoking it. It asserts the decision is deterministic and visible in the run and
result evidence: the artifact validates against the top-level result schema, the
served outcome's kind and the capture's terminal status equal the solver's
``decision`` on every journey, and two journeys plus one direct
``POST /v1/placements`` call produce identical decision bytes — everything but
the solver's own ``stats.elapsed_ms``.

The direct check also stays here as its own test, unchanged from the contract the
placement-stage PR pinned: the front-door journeys prove the stage is wired into
the capture path, the direct check proves Hunter's public contract survives. Both
run on the same committed scene bytes, which is what makes the two comparable.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from server.api.app import create_app
from server.api.deps import drain_finalize
from server.common.config import Settings
from server.contracts.jobs import run_id_for_capture
from server.orchestrator.stateio import get_json
from server.placement import adapter

REPO_ROOT = Path(__file__).resolve().parents[2]
TOP_SERVER = REPO_ROOT / "server"
PACKET_FIXTURE = REPO_ROOT / "backend" / "data" / "fixtures" / "packets" / "mini-arkit"
SCENE_FIXTURE = TOP_SERVER / "tests" / "fixtures" / "example-scene.json"
RESULT_SCHEMA = json.loads((TOP_SERVER / "schemas" / "result.schema.json").read_text())

# The solver's own verdict for the committed scene, observed through the boundary:
# the placement artifact, the run outcome, the served result and the direct
# endpoint must all carry it.
EXPECTED_DECISION = "manual_review"

TERMINAL_EVENTS = {"verdict_ready", "retake_request", "failed"}

# The documented dev setting (SD-73), as in the integration gate: the real hook
# chain with stubbed stages. The placement stage is stubs_exempt, so it runs the
# real solver on this path. The orchestrator service template is built from the
# process environment (`server.orchestrator.services.current`), so these must be
# env, not only Settings fields — the finalize handoff runs on a background thread.
TEMPLATE_ENV = {
    "HSS_ORCH_STAGE_STUBS": "1",
    # Prewarm only asks Modal for warm GPU pools; with the Modal SDK installed
    # but no token its background spawn task throws an unretrieved AuthError.
    "HSS_ORCH_PREWARM": "0",
    "VISION_EXTERNAL_CALLS": "off",
    "STATE_BACKEND": "memory",
    "STORAGE_BACKEND": "volume",
    "SMP_ENVIRONMENT": "dev",
}


@pytest.fixture
def frontdoor(tmp_path, monkeypatch):
    """The app with the configured local hook, plus a private copy of the fixture."""
    for key, value in TEMPLATE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("VOLUME_ROOT", str(tmp_path / "vol"))
    root = tmp_path / "packet"
    shutil.copytree(PACKET_FIXTURE, root)
    packet = json.loads((root / "packet.json").read_text())
    settings = Settings(
        SMP_ENVIRONMENT="dev",
        HSS_AUTH_MODE="static",
        CAPTURE_API_TOKENS=f"device:{'d' * 40}",
        UPLOAD_URL_HMAC_SECRET="h" * 32,
        STORAGE_BACKEND="volume",
        STATE_BACKEND="memory",
        VOLUME_ROOT=str(tmp_path / "vol"),
        CAPTURE_API_PUBLIC_URL="http://testserver",
        HSS_INGEST_HOOKS="auto",
        VISION_EXTERNAL_CALLS="off",
        GIT_SHA="0123456789abcdef",
        SMP_SERVICE_NAME="capture-api",
    )
    app = create_app(settings=settings)
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {'d' * 40}"
    return root, packet, client, app


def _content_type(role: str) -> str:
    if role == "stream":
        return "application/gzip"
    if role in {"features", "depth", "confidence", "mesh"}:
        return "application/octet-stream"
    return "image/jpeg"


def _walk_front_door(root: Path, packet: dict, client: TestClient, app, packet_id: str):
    """One capture through the device's own sequence; returns (capture_id, run_id, events)."""
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

    finalize = client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=raw,
        headers={
            "Idempotency-Key": packet_id,
            "Content-Type": "application/json",
            "X-Packet-Sha256": hashlib.sha256(raw).hexdigest(),
        },
    )
    assert finalize.status_code == 202, finalize.text
    accepted = finalize.json()
    # The run id is not a random allocation: this capture plus these packet bytes.
    assert accepted["runId"] == run_id_for_capture(capture_id, raw)
    drain_finalize(app.state.deps)

    events: list[dict] = []
    after = 0
    for _ in range(50):
        page = client.get(f"/v1/captures/{capture_id}/events", params={"after": after}).json()
        events.extend(page["events"])
        if page["next"] == after:
            break
        after = page["next"]
    else:
        pytest.fail("event feed never stopped moving")
    return capture_id, accepted["runId"], events


def _run_artifacts(deps, run_id: str) -> dict[str, str]:
    """The run's artifact map, overlaid with the placement stage's own keys — the same
    recovery `routes/result.py` uses, because a parallel stub stage's put_run can drop
    a key that lives only on the per-stage map."""
    run = deps.state.get_run(run_id)
    artifacts = dict(run.artifacts or {})
    extra = get_json(deps.state, f"stage_artifacts:{run_id}:placement") or {}
    if isinstance(extra, dict):
        artifacts.update({str(key): str(value) for key, value in extra.items() if value is not None})
    return artifacts


def _decision_bytes(payload: dict) -> bytes:
    """The decision projection: everything but the solver's wall-clock timing."""
    decision = json.loads(json.dumps(payload))
    decision["stats"].pop("elapsed_ms")
    return json.dumps(decision, sort_keys=True).encode()


def test_capture_front_door_reaches_the_placement_stage(frontdoor) -> None:
    root, packet, client, app = frontdoor
    deps = app.state.deps
    fixture_bytes = SCENE_FIXTURE.read_bytes()

    projections = []
    for tag in ("a", "b"):
        capture_id, run_id, events = _walk_front_door(root, packet, client, app, f"{packet['packetId']}-{tag}")

        # --- the placement stage is in the run evidence -------------------------
        run = deps.state.get_run(run_id)
        assert run is not None and run.status == "done"
        assert run.outcome_kind == EXPECTED_DECISION
        assert run.failure_code is None and "placement" not in run.degraded

        artifacts = _run_artifacts(deps, run_id)
        # Published by the stage itself, never by a stub: absent only if the capture
        # path stopped invoking the stage.
        placement = json.loads(deps.storage.get_bytes(artifacts["placement/placement.json"]))
        Draft202012Validator(RESULT_SCHEMA).validate(placement)
        # The stage solved exactly the committed scene bytes.
        assert deps.storage.get_bytes(artifacts["placement/scene.json"]) == fixture_bytes

        record = deps.state.get_stage(run_id, "placement")
        assert record.status == "done" and record.error_code is None
        assert record.metrics["elapsed_ms"] > 0

        marks = [
            (event["data"].get("stage"), event["data"].get("status")) for event in events if event["type"] == "stage"
        ]
        assert ("placement", "done") in marks
        assert marks.index(("scene", "done")) < marks.index(("placement", "done")) < marks.index(("criteria", "done"))

        terminals = [event for event in events if event["type"] in TERMINAL_EVENTS]
        assert [event["type"] for event in terminals] == ["verdict_ready"]
        assert terminals[0]["data"]["kind"] == EXPECTED_DECISION

        # --- the decision is visible in the run and result evidence -------------
        assert placement["decision"] == EXPECTED_DECISION
        status = client.get(f"/v1/captures/{capture_id}").json()
        served = client.get(f"/v1/captures/{capture_id}/result")
        assert served.status_code == 200, served.text
        result = served.json()
        assert result["runId"] == run_id
        assert result["outcome"]["kind"] == placement["decision"]
        assert result["status"] == status["status"] == "manual_review"
        projections.append(_decision_bytes(placement))

    # Deterministic: two journeys over the same committed scene bytes, one decision.
    assert projections[0] == projections[1]

    # The direct endpoint and the capture front door agree on the same scene bytes.
    with TestClient(adapter.load_api()["api"].app) as direct:
        response = direct.post("/v1/placements", content=fixture_bytes, headers={"Content-Type": "application/json"})
    assert response.status_code == 200, response.text
    assert _decision_bytes(response.json()) == projections[0]


def test_direct_placements_contract_stays_live() -> None:
    """Hunter's own endpoint, unchanged: same loaded code, same committed fixture."""
    fixture_bytes = SCENE_FIXTURE.read_bytes()
    with TestClient(adapter.load_api()["api"].app) as client:
        response = client.post("/v1/placements", content=fixture_bytes, headers={"Content-Type": "application/json"})
    assert response.status_code == 200, response.text
    body = response.json()
    Draft202012Validator(RESULT_SCHEMA).validate(body)
    assert body["decision"] == EXPECTED_DECISION
    assert body["stats"]["input_sha256"] == hashlib.sha256(fixture_bytes).hexdigest()
