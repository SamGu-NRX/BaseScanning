#!/usr/bin/env python3
"""Drive the whole capture journey over the committed mini-arkit packet, no cloud.

One repeatable command for reviewers:

    cd backend && uv run --extra api python scripts/capture_flow_e2e.py

It copies the committed ``data/fixtures/packets/mini-arkit`` packet into a clean
temporary directory, starts the real app entry point ``create_app()`` in-process
(memory state, local volume storage, static auth, a test HMAC secret — no GCP,
no Modal, no secrets), and sends the app's own sequence: create the capture,
register files, PUT bytes to the signed upload URLs, commit, finalize
``packet.json``, and read events. It executes the HTTP API and the finalize
handoff only; it does not run the reconstruction/orchestrator pipeline, does not
touch the network beyond the in-process ASGI transport, and never deploys.

It writes a small JSON receipt (default ``<workdir>/receipt.json``) with request
IDs, per-stage HTTP statuses, file counts, the final capture status and error
codes, then exits 0 when the run matched the expected outcome and 1 otherwise.
The receipt is the replay anchor: rerunning the command reproduces the same
journey, and a failed run leaves the exact captureId, runId and failing stage
behind so a later session can replay or diff against it.

``--skip-upload PATH`` stops one file short on purpose, which drives the
capture into ``awaiting_files`` — combine with ``--expect-status awaiting_files``
to check that path instead.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from server.api.app import create_app  # noqa: E402
from server.api.deps import drain_finalize  # noqa: E402
from server.common.config import Settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKET = ROOT / "data/fixtures/packets/mini-arkit"

# Same static test token the API test suite uses (server/api/tests/conftest.py).
TOKEN = "d" * 40


def _content_type(role: str) -> str:
    if role == "stream":
        return "application/gzip"
    if role in {"features", "depth", "confidence", "mesh"}:
        return "application/octet-stream"
    return "image/jpeg"


def _sealed_files(root: Path, packet: dict) -> list[dict]:
    """Register bodies with the hashes the app computes from the sealed files."""
    files = []
    for entry in packet["files"]:
        data = (root / entry["path"]).read_bytes()
        role = entry.get("role", "still")
        files.append(
            {
                "path": entry["path"],
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "md5": base64.b64encode(hashlib.md5(data).digest()).decode(),
                "role": role,
                "contentType": _content_type(role),
            }
        )
    return files


class _Journey:
    """The app under test plus a private copy of the committed packet fixture."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.root = workdir / "packet"
        self.packet = json.loads((self.root / "packet.json").read_text())
        settings = Settings(
            SMP_ENVIRONMENT="dev",
            HSS_AUTH_MODE="static",
            CAPTURE_API_TOKENS=f"device:{TOKEN}",
            UPLOAD_URL_HMAC_SECRET="h" * 32,
            STORAGE_BACKEND="volume",
            STATE_BACKEND="memory",
            VOLUME_ROOT=str(workdir / "vol"),
            CAPTURE_API_PUBLIC_URL="http://testserver",
            HSS_INGEST_HOOKS="null",
            VISION_EXTERNAL_CALLS="on",
            GIT_SHA="0123456789abcdef",
            SMP_SERVICE_NAME="capture-api",
        )
        self.app = create_app(settings=settings)
        self.client = TestClient(self.app)
        self.client.headers["Authorization"] = f"Bearer {TOKEN}"

    def drain(self) -> None:
        """Join finalize background work so the receipt is deterministic."""
        drain_finalize(self.app.state.deps)


def run_capture_flow(
    workdir: Path,
    *,
    packet_dir: Path | None = None,
    skip_upload: str | None = None,
    expect_status: str = "processing",
) -> dict:
    """Run the journey and return the receipt dict. Never raises: failures land
    in ``receipt["errorCodes"]`` with ``receipt["ok"] is False`` — including an
    exception before any stage, which becomes the ``runner_exception`` code."""
    started_at = datetime.now(UTC).isoformat()
    packet_dir = packet_dir or DEFAULT_PACKET
    receipt: dict = {
        "runner": "capture_flow_e2e",
        "startedAt": started_at,
        "packetDir": str(packet_dir),
        "workdir": str(workdir),
        "expectStatus": expect_status,
        "skipUpload": skip_upload,
        "captureId": None,
        "runId": None,
        "stages": [],
        "fileCounts": {"registered": 0, "uploaded": 0, "committed": 0, "expected": 0},
        "finalCaptureStatus": None,
        "errorCodes": [],
        "ok": False,
    }
    errors = receipt["errorCodes"]
    stages = receipt["stages"]

    def record(stage: str, **fields) -> None:
        stages.append({"stage": stage, **fields})

    try:
        workdir.mkdir(parents=True, exist_ok=True)
        fixture_root = workdir / "packet"
        shutil.copytree(packet_dir, fixture_root)
        journey = _Journey(workdir)
        packet = journey.packet
        receipt["packet"] = {
            "packetId": packet["packetId"],
            "formatVersion": packet["formatVersion"],
            "tier": packet["session"]["tier"],
            "files": len(packet["files"]),
        }

        # Create the capture.
        session = packet["session"]
        created = journey.client.post(
            "/v1/captures",
            json={
                "packetId": packet["packetId"],
                "formatVersion": packet["formatVersion"],
                "tier": session["tier"],
                "flow": "guided",
                "device": {
                    "model": session["deviceModel"],
                    "systemVersion": session["systemVersion"],
                    "appVersion": session["appVersion"],
                },
            },
            headers={"Idempotency-Key": packet["packetId"]},
        )
        body = created.json() if created.status_code == 201 else {}
        capture_id = body.get("captureId")
        receipt["captureId"] = capture_id
        record(
            "create",
            httpStatus=created.status_code,
            captureId=capture_id,
            idempotencyKey=packet["packetId"],
            status=body.get("status"),
        )
        if created.status_code != 201:
            errors.append(f"create_http_{created.status_code}")
        if body.get("status") != "uploading":
            errors.append("create_status_not_uploading")
        if not capture_id:
            return _finish(receipt, errors, f"no captureId; body={created.text[:300]}")

        # Register, PUT to signed URLs, commit.
        files = _sealed_files(fixture_root, packet)
        # The replay anchor: a saved receipt carries the exact bodies the device
        # sent, so a later replay can prove the same hashes still commit.
        receipt["idempotencyKeys"] = {"create": packet["packetId"], "finalize": packet["packetId"]}
        receipt["files"] = [dict(item) for item in files]
        receipt["fileCounts"]["expected"] = len(files)
        registered = journey.client.post(f"/v1/captures/{capture_id}/files", json={"files": files})
        record("register", httpStatus=registered.status_code, files=len(files))
        if registered.status_code != 200:
            errors.append(f"register_http_{registered.status_code}")
            return _finish(receipt, errors, f"register failed: {registered.text[:300]}")
        receipt["fileCounts"]["registered"] = len(registered.json().get("files", []))

        uploaded: list[str] = []
        for item, target in zip(files, registered.json()["files"], strict=True):
            if target["path"] == skip_upload:
                continue
            put = TestClient(journey.app).put(
                target["upload"]["url"],
                content=(fixture_root / item["path"]).read_bytes(),
                headers=target["upload"].get("headers") or {},
            )
            if put.status_code == 200:
                uploaded.append(target["path"])
            else:
                errors.append(f"upload_http_{put.status_code}:{target['path']}")
        journey.drain()
        receipt["fileCounts"]["uploaded"] = len(uploaded)
        record(
            "upload",
            uploaded=len(uploaded),
            skipped=[skip_upload] if skip_upload else [],
        )

        commit = journey.client.post(
            f"/v1/captures/{capture_id}/files:commit",
            json={"files": [{"path": item["path"], "sha256": item["sha256"]} for item in files]},
        )
        commit_body = commit.json() if commit.status_code == 200 else {}
        receipt["fileCounts"]["committed"] = len(commit_body.get("committed", []))
        record(
            "commit",
            httpStatus=commit.status_code,
            committed=commit_body.get("committed", []),
            notFound=commit_body.get("notFound", []),
            mismatch=commit_body.get("mismatch", []),
        )
        if commit.status_code != 200:
            errors.append(f"commit_http_{commit.status_code}")
        if commit_body.get("notFound"):
            errors.append("commit_not_found")
        if commit_body.get("mismatch"):
            errors.append("commit_sha_mismatch")
        journey.drain()

        # Finalize packet.json.
        raw = (fixture_root / "packet.json").read_bytes()
        receipt["packetSha256"] = hashlib.sha256(raw).hexdigest()
        accepted = journey.client.post(
            f"/v1/captures/{capture_id}/finalize",
            content=raw,
            headers={
                "Idempotency-Key": packet["packetId"],
                "Content-Type": "application/json",
                "X-Packet-Sha256": hashlib.sha256(raw).hexdigest(),
            },
        )
        payload = accepted.json() if accepted.status_code == 202 else {}
        run_id = payload.get("runId")
        receipt["runId"] = run_id
        record(
            "finalize",
            httpStatus=accepted.status_code,
            status=payload.get("status"),
            runId=run_id,
            missing=payload.get("missing", []),
        )
        if accepted.status_code != 202:
            errors.append(f"finalize_http_{accepted.status_code}")
        if payload.get("missing"):
            errors.append("finalize_missing_files")
        journey.drain()

        # Final status and the event log the device observes.
        status = journey.client.get(f"/v1/captures/{capture_id}").json()
        receipt["finalCaptureStatus"] = status.get("status")
        record(
            "status",
            finalCaptureStatus=status.get("status"),
            filesCommitted=status.get("filesCommitted"),
        )
        if status.get("status") != expect_status:
            errors.append("unexpected_final_status")

        events = journey.client.get(f"/v1/captures/{capture_id}/events").json()
        types = [event["type"] for event in events.get("events", [])]
        validate_ok = any(
            (event.get("data") or {}).get("stage") == "validate" and (event.get("data") or {}).get("runId") == run_id
            for event in events.get("events", [])
        )
        record("events", count=len(events.get("events", [])), types=types, validateStageForRun=validate_ok)
        if events.get("status") != status.get("status"):
            errors.append("events_status_mismatch")
        if expect_status == "processing" and not validate_ok:
            errors.append("events_missing_validate_stage")
    except Exception as exc:  # noqa: BLE001 - the receipt must survive any failure
        errors.append("runner_exception")
        return _finish(receipt, errors, f"exception: {type(exc).__name__}: {exc}")
    return _finish(receipt, errors, None)


def _finish(receipt: dict, errors: list[str], detail: str | None) -> dict:
    if detail:
        receipt["errorDetail"] = detail
    receipt["finishedAt"] = datetime.now(UTC).isoformat()
    # A recorded detail without an error code is still a failure: a run that
    # crashed before any stage could record a code must never report ok.
    receipt["ok"] = not errors and not detail
    return receipt


def write_receipt(receipt: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workdir", type=Path, help="run directory (default: a fresh temp dir)")
    parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET, help="committed packet fixture")
    parser.add_argument("--receipt", type=Path, help="receipt path (default: <workdir>/receipt.json)")
    parser.add_argument("--skip-upload", help="register but never upload this file (fault injection)")
    parser.add_argument("--expect-status", default="processing", help="final capture status the run must end in")
    args = parser.parse_args(argv)
    owned = args.workdir is None
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="capture-flow-e2e-"))
    receipt = run_capture_flow(
        workdir,
        packet_dir=args.packet,
        skip_upload=args.skip_upload,
        expect_status=args.expect_status,
    )
    receipt_path = args.receipt or workdir / "receipt.json"
    write_receipt(receipt, receipt_path)
    print(
        json.dumps(
            {
                "ok": receipt["ok"],
                "receipt": str(receipt_path),
                **({"errorDetail": receipt["errorDetail"]} if "errorDetail" in receipt else {}),
            }
        )
    )
    if owned:
        # Keep the default-run evidence for inspection; the caller can delete it.
        print(f"workdir: {workdir}")
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
