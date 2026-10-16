"""The result boundary the capture client consumes: GET /v1/captures/{id}/result.

The route and the stored-artifact boundary are tested together through the FastAPI
app, because the client consumes both: the envelope the route composes and the
bytes WS-10 left in storage. Schema-only validation (ResultResponse against a
payload) would prove neither, so each test seeds synthetic stored artifacts and
reads the exact wire body back.

The stored artifacts derive from the committed fixtures in
`server/result/tests/data`: the verdict blobs are copied verbatim, and the device
`result.json` maps the committed verdict's site decision onto the wire envelope
the way `server.result.compose` does (a reject is masked as manual_review for the
device). The schema-negative fixture in `server/contracts/tests/negatives` feeds
the drift case.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from server.api.tests.conftest import OTHER, REVIEWER, TOKEN, create_body
from server.contracts.jobs import RunRecord, StageRecord

RESULT_DATA = Path(__file__).resolve().parents[3] / "server" / "result" / "tests" / "data"
NEGATIVES = Path(__file__).resolve().parents[3] / "server" / "contracts" / "tests" / "negatives"

ENVELOPE_KEYS = {
    "runId",
    "status",
    "viewsNeeded",
    "memberActions",
    "outcome",
    "site",
    "scale",
    "criteria",
    "meter",
    "verdict",
    "verdictUrl",
    "previewUrl",
    "reviewUrl",
}


def _committed_verdict(decision: str) -> dict:
    # The committed fixture for a pass decision lives under synthetic/.
    directory = {"pass": "synthetic", "reject": "reject"}[decision]
    return json.loads((RESULT_DATA / directory / "verdicts" / "verdict.C.json").read_text())


def _outcome_for(decision: str) -> dict:
    """The device outcome for a committed verdict's site decision.

    `pass` composes an eligible placement; `reject` is masked as manual_review
    for the device and arrives with no reasons.
    """

    if decision == "pass":
        return {
            "kind": "eligible",
            "profile": "C",
            "message": "A battery fits beside the meter.",
            "viewsNeeded": [],
            "reasons": [],
            "recommendedPlacement": {
                "wallId": "w1",
                "startSM": 2.5,
                "footprintM": {"w": 2.0, "h": 1.0, "d": 0.6},
                "boxSceneM": {"centerM": [0.0, 0.5, 0.0], "sizeM": [2.0, 1.0, 0.6], "yawDeg": 0.0},
                "confidence": 0.9,
                "profile": "C",
            },
        }
    return {
        "kind": "manual_review",
        "profile": "C",
        "message": "We need a person to look at this one.",
        "viewsNeeded": [],
        "reasons": [],
    }


def _create(client: TestClient, packet_id: str) -> str:
    made = client.post("/v1/captures", json=create_body(packet_id), headers={"Idempotency-Key": packet_id})
    assert made.status_code == 201, made.text
    return made.json()["captureId"]


def _seed(
    client: TestClient,
    capture_id: str,
    artifacts: dict,
    *,
    run_id: str = "run_result_boundary",
    stage_status: str = "done",
    capture_status: str | None = None,
) -> None:
    deps = client.app_state
    deps.state.put_run(
        RunRecord(
            run_id=run_id,
            capture_id=capture_id,
            packet_sha256="a" * 64,
            status="done",
            branch="image",
            flow="photo",
            tier="photo_only",
            profile_default="C",
            created_at="2026-09-26T00:00:00.000Z",
            deadline_at="2026-09-26T01:00:00.000Z",
            artifacts=artifacts,
        )
    )
    deps.state.upsert_stage(
        StageRecord(run_id=run_id, stage="result", status=stage_status, attempt=1, heartbeat="2026-09-26T00:00:00.000Z")
    )
    if capture_status is not None:
        deps.state.set_status(capture_id, capture_status)  # type: ignore[attr-defined]


def _seed_ready(
    client: TestClient,
    packet_id: str,
    decision: str,
    *,
    extra_result_fields: dict | None = None,
    with_verdict: bool = True,
    with_preview: bool = True,
) -> str:
    """A ready capture whose stored artifacts come from the committed fixtures.

    The stored payload deliberately omits the envelope's optional fields, so the
    ready body shows the route's real key set: stored keys, plus verdictUrl,
    previewUrl and reviewUrl — no null padding (that is the not-ready path's
    shape, not this one's).
    """

    capture_id = _create(client, packet_id)
    deps = client.app_state
    result_key = f"captures/{capture_id}/result/result.json"
    stored: dict = {
        "runId": "run_stale_marker",
        "status": "processing",
        "viewsNeeded": [],
        "memberActions": [],
        "outcome": _outcome_for(decision),
    }
    stored.update(extra_result_fields or {})
    deps.storage.put_bytes(result_key, json.dumps(stored).encode(), "application/json")
    artifacts: dict = {"result/result.json": result_key}
    if with_verdict:
        verdict_key = f"captures/{capture_id}/verdicts/verdict.C.json"
        deps.storage.put_bytes(verdict_key, json.dumps(_committed_verdict(decision)).encode(), "application/json")
        artifacts["verdicts/verdict.C.json"] = verdict_key
    if with_preview:
        preview_key = f"captures/{capture_id}/result/preview.ply"
        deps.storage.put_bytes(
            preview_key, (RESULT_DATA / "synthetic" / "points.ply").read_bytes(), "application/octet-stream"
        )
        artifacts["result/preview.ply"] = preview_key
    _seed(client, capture_id, artifacts, capture_status="complete" if decision == "pass" else "manual_review")
    return capture_id


# The envelope the not-ready body composes, field for field.
NOT_READY_ENVELOPE = {
    "runId": None,
    "status": "uploading",
    "viewsNeeded": [],
    "memberActions": [],
    "outcome": None,
    "site": None,
    "scale": None,
    "criteria": None,
    "meter": None,
    "verdict": None,
    "verdictUrl": None,
    "previewUrl": None,
    "reviewUrl": None,
}


def test_not_ready_poll_before_any_run(client: TestClient) -> None:
    capture_id = _create(client, "packet-result-poll-fresh")
    body = client.get(f"/v1/captures/{capture_id}/result")
    assert body.status_code == 200, body.text
    assert body.json() == NOT_READY_ENVELOPE


def test_not_ready_poll_with_pending_run_is_stable(client: TestClient) -> None:
    capture_id = _create(client, "packet-result-poll-run")
    client.app_state.state.set_status(capture_id, "processing")
    _seed(
        client,
        capture_id,
        {},
        run_id="run_pending",
        stage_status="running",
    )
    first = client.get(f"/v1/captures/{capture_id}/result")
    assert first.status_code == 200, first.text
    # The run id arrives as soon as a run exists; the client stops only on outcome.
    assert first.json() == {**NOT_READY_ENVELOPE, "runId": "run_pending", "status": "processing"}
    second = client.get(f"/v1/captures/{capture_id}/result")
    assert second.status_code == 200, second.text
    assert second.json() == first.json()


def test_ready_envelope_pass_decision(client: TestClient) -> None:
    capture_id = _seed_ready(client, "packet-result-ready", "pass")
    body = client.get(f"/v1/captures/{capture_id}/result")
    assert body.status_code == 200, body.text
    payload = body.json()
    # The ready route serves the stored keys plus the three it composes; it does
    # not pad omitted optional fields with nulls.
    assert set(payload) == {
        "runId",
        "status",
        "viewsNeeded",
        "memberActions",
        "outcome",
        "verdictUrl",
        "previewUrl",
        "reviewUrl",
    }
    # The route overwrites the stored marker values with the live record's.
    assert payload["runId"] == "run_result_boundary"
    assert payload["status"] == "complete"
    assert payload["viewsNeeded"] == []
    assert payload["memberActions"] == []
    assert payload["outcome"]["kind"] == "eligible"
    assert payload["outcome"]["recommendedPlacement"]["wallId"] == "w1"
    # Device masking: the verdict URL stays closed and the verdict blob never leaks.
    assert payload["verdictUrl"] is None
    assert "verdict" not in payload
    assert "C1-rules-3d" not in body.text
    # The signed preview and the review path ride along.
    assert isinstance(payload["previewUrl"], str)
    assert "/v1/uploads/" in payload["previewUrl"]
    assert payload["reviewUrl"] == f"/v1/captures/{capture_id}/review"


def test_ready_envelope_reject_decision_is_masked(client: TestClient) -> None:
    capture_id = _seed_ready(client, "packet-result-reject", "reject")
    body = client.get(f"/v1/captures/{capture_id}/result")
    assert body.status_code == 200, body.text
    payload = body.json()
    assert payload["status"] == "manual_review"
    assert payload["outcome"]["kind"] == "manual_review"
    assert payload["outcome"]["reasons"] == []
    assert "drive_clearance" not in body.text
    assert payload["verdictUrl"] is None


def test_verdict_url_is_reviewer_only(client: TestClient) -> None:
    capture_id = _seed_ready(client, "packet-result-reviewer", "pass")
    owner = client.get(f"/v1/captures/{capture_id}/result")
    assert owner.json()["verdictUrl"] is None
    reviewer = client.get(f"/v1/captures/{capture_id}/result", headers={"Authorization": f"Bearer {REVIEWER}"})
    assert reviewer.status_code == 200, reviewer.text
    verdict_url = reviewer.json()["verdictUrl"]
    assert isinstance(verdict_url, str)
    assert "/v1/uploads/" in verdict_url
    # The verdict blob itself still never enters the body, even for a reviewer.
    assert "verdict" not in reviewer.json()
    # Another device's token reads nothing at all.
    stranger = client.get(f"/v1/captures/{capture_id}/result", headers={"Authorization": f"Bearer {OTHER}"})
    assert stranger.status_code == 403, stranger.text
    assert stranger.json()["errors"][0]["code"] == "forbidden"
    _ = TOKEN


def test_reviewer_verdict_url_closed_for_internal_result(client: TestClient) -> None:
    """An artifact named result.internal.json is masked even from a reviewer."""

    capture_id = _create(client, "packet-result-internal")
    deps = client.app_state
    result_key = f"captures/{capture_id}/result/result.json"
    internal_key = f"captures/{capture_id}/result/result.internal.json"
    deps.storage.put_bytes(
        result_key,
        json.dumps(
            {"runId": "r", "status": "complete", "viewsNeeded": [], "memberActions": [], "outcome": None}
        ).encode(),
        "application/json",
    )
    deps.storage.put_bytes(internal_key, json.dumps(_committed_verdict("pass")).encode(), "application/json")
    _seed(
        client,
        capture_id,
        {"result/result.json": result_key, "result/result.internal.json": internal_key},
        capture_status="complete",
    )
    body = client.get(f"/v1/captures/{capture_id}/result", headers={"Authorization": f"Bearer {REVIEWER}"})
    assert body.status_code == 200, body.text
    assert body.json()["verdictUrl"] is None
    assert "C1-rules-3d" not in body.text


def test_hidden_artifact_references_stripped_recursively(client: TestClient) -> None:
    """Every string naming verdicts/ or result.internal.json becomes null, at any depth."""

    capture_id = _seed_ready(
        client,
        "packet-result-strip",
        "pass",
        extra_result_fields={
            "site": {
                "decision": "pass",
                "profile": "C",
                "reasons": ["verdicts/verdict.C.json", "kept"],
            },
            "criteria": [
                {
                    "id": "drive_clearance",
                    "outcome": "pass",
                    "unsureCause": "result/result.internal.json",
                }
            ],
            "meter": {
                "status": "not_captured",
                "value": None,
                "candidates": [],
                "barcodeConfirmed": False,
                "readersAgree": False,
                "clean": False,
                "evidence": {"image": "verdicts/verdict.C.json", "boxPx": None},
            },
        },
    )
    body = client.get(f"/v1/captures/{capture_id}/result")
    assert body.status_code == 200, body.text
    payload = body.json()
    assert payload["site"] == {"decision": "pass", "profile": "C", "reasons": [None, "kept"]}
    assert payload["criteria"] == [{"id": "drive_clearance", "outcome": "pass", "unsureCause": None}]
    assert payload["meter"]["evidence"] == {"image": None, "boxPx": None}


def test_preview_url_absent_without_preview_artifact(client: TestClient) -> None:
    capture_id = _seed_ready(client, "packet-result-nopreview", "pass", with_preview=False)
    body = client.get(f"/v1/captures/{capture_id}/result")
    assert body.status_code == 200, body.text
    # Without the artifact the key is absent, not null: the client's optional
    # decode reads nil either way, and the composer never invents a URL.
    assert "previewUrl" not in body.json()


def test_malformed_stored_result_is_a_storage_503(client: TestClient) -> None:
    capture_id = _create(client, "packet-result-malformed")
    deps = client.app_state
    result_key = f"captures/{capture_id}/result/result.json"
    deps.storage.put_bytes(result_key, b"{not json at all", "application/json")
    _seed(client, capture_id, {"result/result.json": result_key}, capture_status="complete")
    body = client.get(f"/v1/captures/{capture_id}/result")
    assert body.status_code == 503, body.text
    assert body.json()["errors"][0]["code"] == "storage_unavailable"
    assert body.json()["errors"][0]["pointer"] == "/"
    assert body.headers["Retry-After"] == "5"


def test_missing_result_blob_is_a_storage_503(client: TestClient) -> None:
    """The artifact map names a key the storage never received."""

    capture_id = _create(client, "packet-result-missing-blob")
    _seed(
        client,
        capture_id,
        {"result/result.json": f"captures/{capture_id}/result/result.json"},
        capture_status="complete",
    )
    body = client.get(f"/v1/captures/{capture_id}/result")
    assert body.status_code == 503, body.text
    assert body.json()["errors"][0]["code"] == "storage_unavailable"


def test_drifted_eligible_without_placement_still_served(client: TestClient) -> None:
    """The committed schema-negative fixture is served as stored: the route
    validates and logs, it does not rewrite or reject, so the client's
    unknown-field tolerance stays the drift guard."""

    capture_id = _create(client, "packet-result-drift")
    deps = client.app_state
    result_key = f"captures/{capture_id}/result/result.json"
    drifted = json.loads((NEGATIVES / "result-eligible-without-placement.json").read_text())
    deps.storage.put_bytes(result_key, json.dumps(drifted).encode(), "application/json")
    _seed(client, capture_id, {"result/result.json": result_key}, capture_status="complete")
    body = client.get(f"/v1/captures/{capture_id}/result")
    assert body.status_code == 200, body.text
    payload = body.json()
    assert payload["outcome"]["kind"] == "eligible"
    assert "recommendedPlacement" not in payload["outcome"]
    assert payload["runId"] == "run_result_boundary"
    assert payload["status"] == "complete"


def test_failed_capture_is_terminal_with_null_outcome(client: TestClient) -> None:
    capture_id = _create(client, "packet-result-failed")
    deps = client.app_state
    result_key = f"captures/{capture_id}/result/result.json"
    deps.storage.put_bytes(
        result_key,
        json.dumps(
            {"runId": "r", "status": "processing", "viewsNeeded": [], "memberActions": [], "outcome": None}
        ).encode(),
        "application/json",
    )
    _seed(client, capture_id, {"result/result.json": result_key}, capture_status="failed")
    body = client.get(f"/v1/captures/{capture_id}/result")
    assert body.status_code == 200, body.text
    payload = body.json()
    # No preview artifact, so previewUrl is absent rather than null.
    assert set(payload) == {
        "runId",
        "status",
        "viewsNeeded",
        "memberActions",
        "outcome",
        "verdictUrl",
        "reviewUrl",
    }
    assert payload["status"] == "failed"
    assert payload["outcome"] is None
    assert payload["viewsNeeded"] == []
    assert payload["verdictUrl"] is None
    assert payload["reviewUrl"] == f"/v1/captures/{capture_id}/review"


def test_expired_capture_is_terminal_with_null_outcome(client: TestClient) -> None:
    capture_id = _create(client, "packet-result-expired")
    deps = client.app_state
    result_key = f"captures/{capture_id}/result/result.json"
    deps.storage.put_bytes(
        result_key,
        json.dumps(
            {"runId": "r", "status": "processing", "viewsNeeded": [], "memberActions": [], "outcome": None}
        ).encode(),
        "application/json",
    )
    _seed(client, capture_id, {"result/result.json": result_key}, capture_status="expired")
    body = client.get(f"/v1/captures/{capture_id}/result")
    assert body.status_code == 200, body.text
    payload = body.json()
    assert payload["status"] == "expired"
    assert payload["outcome"] is None
    assert payload["viewsNeeded"] == []
    assert payload["reviewUrl"] == f"/v1/captures/{capture_id}/review"
