#!/usr/bin/env python3
"""Replay a saved capture-flow receipt against the real capture API, no cloud.

One repeatable command for reviewers:

    cd backend && uv run --extra api python scripts/replay_capture.py \
        --receipt <workdir>/receipt.json

The input is the JSON receipt ``scripts/capture_flow_e2e.py`` writes: it names
the packet, the idempotency keys, the sealed per-file bodies (path, bytes,
sha256) and the packet digest. The replay re-sends that journey — create,
register, PUT, commit, finalize — to a fresh in-process instance of the real
app entry point ``create_app()`` (memory state, local volume storage, the
documented dev handoff ``HSS_INGEST_HOOKS=auto`` with orchestrator stage
stubs, no GCP, no Modal, no network, no credentials), then drains the finalize
work and reads the terminal capture status and result back through the device
routes. It asserts four things the raw HTTP log of a run cannot prove:

- the saved idempotency keys are reused verbatim on create and finalize;
- a request that "times out" after the server processed it replays to the
  same capture id (a 200 on the stored body, never a second 201 capture);
- the committed file hashes are the receipt's hashes, on the first attempt
  and on a resumed commit after the files were already committed;
- the run lands on one terminal status, served once by ``GET /result``, with
  exactly one terminal verdict event and one run id.

A scripted, deterministic ``FailureInjector`` simulates lost responses: the
request really executes against the app, then the injector raises
``SimulatedTimeout`` so the caller never sees the response — the situation a
device hits when a cell handoff eats an HTTP reply. Each fault fires at most
once per scripted occurrence and every step retries at most ``MAX_RETRIES``
times; a fault scripted beyond that budget fails the replay loudly instead of
retrying forever.

It writes a JSON report (default ``<workdir>/replay-receipt.json``) and exits
0 when every assertion held, 1 otherwise. It never changes route contracts and
never touches iOS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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

# A lost response is retried once with the same idempotency key. A second
# failure is a finding, not something to hide behind an unbounded retry loop.
MAX_RETRIES = 1

# Bounded, deterministic readiness polling: drain_finalize joins the finalize
# thread, so a drained app is already terminal and one read normally settles
# it. The loop exists so a slow app fails loudly after N reads, never hangs.
MAX_POLLS = 5

TERMINAL_STATUSES = {"complete", "failed", "needs_views", "manual_review"}
TERMINAL_EVENTS = {"verdict_ready", "retake_request", "failed"}


class SimulatedTimeout(Exception):
    """A response the server produced but the client never received."""


class FailureInjector:
    """Scripted lost-response faults: ``{"create": 1}`` loses one create reply.

    Deterministic by construction — no clocks, no randomness. ``fire`` is
    called after a request has actually executed against the app, so the
    server-side effect exists before the caller is told the response was lost,
    which is exactly what makes the idempotent replay meaningful.
    """

    def __init__(self, script: dict[str, int] | None = None) -> None:
        self.script = dict(script or {})
        self.fired: list[str] = []

    def fire(self, step: str) -> None:
        remaining = self.script.get(step, 0)
        if remaining <= 0:
            return
        self.script[step] = remaining - 1
        self.fired.append(step)
        raise SimulatedTimeout(step)


def receipt_problem(receipt: object) -> str | None:
    """A named reason the saved receipt cannot be replayed, or None.

    The replay block the runner writes (files, idempotencyKeys, packetSha256)
    is the replay's ground truth; a receipt without it was written by a
    pre-replay runner and must be rejected, never replayed with guessed
    hashes.
    """
    if not isinstance(receipt, dict) or receipt.get("runner") != "capture_flow_e2e":
        return "not_a_capture_flow_e2e_receipt"
    missing = [key for key in ("files", "idempotencyKeys", "packetSha256") if not receipt.get(key)]
    if missing:
        return f"missing_replay_block:{','.join(missing)}"
    return None


def check_fixture(packet_dir: Path, receipt: dict, errors: list[str]) -> dict | None:
    """The replayed bytes must still hash to the saved receipt's hashes."""
    packet_json = packet_dir / "packet.json"
    if not packet_json.is_file():
        errors.append(f"packet_json_missing:{packet_dir}")
        return None
    packet = json.loads(packet_json.read_text())
    saved_files = receipt.get("files") or []
    saved_hashes = {item["path"]: item["sha256"] for item in saved_files}
    drift: list[str] = []
    for entry in packet["files"]:
        path = entry["path"]
        data = (packet_dir / path).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if path in saved_hashes and saved_hashes[path] != digest:
            drift.append(path)
        if entry.get("sha256") and entry["sha256"] != digest:
            drift.append(f"{path}:packet-manifest")
    manifest_paths = {entry["path"] for entry in packet["files"]}
    for path in saved_hashes:
        if path not in manifest_paths:
            drift.append(f"{path}:not-in-manifest")
    saved_sha = receipt.get("packetSha256")
    actual_sha = hashlib.sha256(packet_json.read_bytes()).hexdigest()
    if saved_sha and saved_sha != actual_sha:
        drift.append("packet.json")
    if drift:
        errors.append(f"fixture_drift:{','.join(drift)}")
    return {"packet": packet, "packetSha256": actual_sha, "drift": drift}


class _ReplayApp:
    """The real app under replay: dev handoff on, everything else local."""

    def __init__(self, workdir: Path) -> None:
        # The orchestrator hook binds services from the environment; it needs
        # the same volume root the API storage uses or the bind no-ops.
        self._env = {key: os.environ.get(key) for key in ("VOLUME_ROOT", "HSS_ORCH_STAGE_STUBS", "STATE_BACKEND")}
        os.environ["VOLUME_ROOT"] = str(workdir / "vol")
        os.environ["HSS_ORCH_STAGE_STUBS"] = "1"
        os.environ.setdefault("STATE_BACKEND", "memory")
        settings = Settings(
            SMP_ENVIRONMENT="dev",
            HSS_AUTH_MODE="static",
            CAPTURE_API_TOKENS=f"device:{TOKEN}",
            UPLOAD_URL_HMAC_SECRET="h" * 32,
            STORAGE_BACKEND="volume",
            STATE_BACKEND="memory",
            VOLUME_ROOT=str(workdir / "vol"),
            CAPTURE_API_PUBLIC_URL="http://testserver",
            HSS_INGEST_HOOKS="auto",
            VISION_EXTERNAL_CALLS="off",
            GIT_SHA="0123456789abcdef",
            SMP_SERVICE_NAME="capture-api",
        )
        self.app = create_app(settings=settings)
        self.client = TestClient(self.app)
        self.client.headers["Authorization"] = f"Bearer {TOKEN}"

    def close(self) -> None:
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def drain(self) -> None:
        drain_finalize(self.app.state.deps)


def _send(step: str, request, injector: FailureInjector, report: dict, errors: list[str]) -> object:
    """Run ``request``; replay the identical request after a lost response.

    The fault fires only after the request executed, so a retried create is a
    genuine duplicate-delivery: the server must answer from its idempotency
    store, not open a second capture.
    """
    attempts: list[dict] = []
    report.setdefault("attempts", {})[step] = attempts
    attempt = 0
    while True:
        response = request()  # the request really went out and was processed
        try:
            injector.fire(step)
        except SimulatedTimeout:
            attempts.append({"attempt": attempt, "outcome": "simulated_timeout", "httpStatus": response.status_code})
            attempt += 1
            if attempt > MAX_RETRIES:
                errors.append(f"{step}_retry_budget_exhausted")
                return response
            continue  # same bytes, same idempotency key
        attempts.append({"attempt": attempt, "outcome": "delivered", "httpStatus": response.status_code})
        return response


def run_replay(
    receipt_path: Path,
    workdir: Path,
    *,
    packet_dir: Path | None = None,
    faults: dict[str, int] | None = None,
) -> dict:
    """Replay the saved receipt and return the report. Never raises: failures
    land in ``report["errorCodes"]`` with ``report["ok"] is False`` — including
    receipt problems and any unexpected exception."""
    started_at = datetime.now(UTC).isoformat()
    report: dict = {
        "replay": "replay_capture",
        "startedAt": started_at,
        "receiptPath": str(receipt_path),
        "packetDir": None,
        "workdir": str(workdir),
        "faults": dict(faults or {}),
        "idempotencyKeys": {},
        "captureId": None,
        "runId": None,
        "steps": [],
        "fileHashesVerified": 0,
        "finalCaptureStatus": None,
        "resultOutcomeKind": None,
        "terminalEvents": [],
        "errorCodes": [],
        "ok": False,
    }
    errors = report["errorCodes"]
    injector = FailureInjector(faults)

    def record(step: str, **fields) -> None:
        report["steps"].append({"step": step, **fields})

    app: _ReplayApp | None = None
    try:
        receipt = json.loads(Path(receipt_path).read_text())
        problem = receipt_problem(receipt)
        if problem:
            errors.append(problem)
            return _finish(report, errors, f"{receipt_path}: {problem} — written by a runner without the replay block")
        saved_dir = receipt.get("packetDir")
        packet_dir = packet_dir or (Path(saved_dir) if saved_dir and Path(saved_dir).is_dir() else DEFAULT_PACKET)
        report["packetDir"] = str(packet_dir)
        report["idempotencyKeys"] = receipt.get("idempotencyKeys") or {}
        fixture = check_fixture(packet_dir, receipt, errors)
        record(
            "fixture", files=len((fixture or {}).get("packet", {}).get("files", [])), drift=(fixture or {}).get("drift")
        )
        if fixture is None or errors:
            return _finish(report, errors, "the saved receipt no longer matches the packet fixture")
        packet = fixture["packet"]
        packet_sha = fixture["packetSha256"]
        saved_files = receipt["files"]
        report["fileHashesVerified"] = len(saved_files)
        keys = receipt.get("idempotencyKeys") or {}
        create_key = keys.get("create") or packet["packetId"]
        finalize_key = keys.get("finalize") or packet["packetId"]
        report["idempotencyKeys"] = {"create": create_key, "finalize": finalize_key}

        workdir.mkdir(parents=True, exist_ok=True)
        app = _ReplayApp(workdir)

        # Create the capture. A lost first response must replay to the same
        # capture id from the idempotency store: a 200, never a second 201.
        body = {
            "packetId": packet["packetId"],
            "formatVersion": packet["formatVersion"],
            "tier": packet["session"]["tier"],
            "flow": "guided",
            "device": {
                "model": packet["session"]["deviceModel"],
                "systemVersion": packet["session"]["systemVersion"],
                "appVersion": packet["session"]["appVersion"],
            },
        }

        def _create():
            return app.client.post("/v1/captures", json=body, headers={"Idempotency-Key": create_key})

        created = _send("create", _create, injector, report, errors)
        created_body = created.json() if created.status_code in (200, 201) else {}
        capture_id = created_body.get("captureId")
        report["captureId"] = capture_id
        first_status = report["attempts"]["create"][0]["httpStatus"]
        record(
            "create",
            idempotencyKey=create_key,
            firstHttpStatus=first_status,
            httpStatus=created.status_code,
            captureId=capture_id,
        )
        if created.status_code not in (200, 201):
            errors.append(f"create_http_{created.status_code}")
            return _finish(report, errors, f"create failed: {created.text[:300]}")
        if not capture_id:
            return _finish(report, errors, "no captureId in create response")

        def _create_again():
            return app.client.post("/v1/captures", json=body, headers={"Idempotency-Key": create_key})

        again = _create_again()
        stable = again.status_code == 200 and again.json().get("captureId") == capture_id
        record("create-stable", httpStatus=again.status_code, captureId=again.json().get("captureId"), stable=stable)
        if not stable:
            errors.append("capture_id_not_stable")

        # Register, PUT the receipt's bytes, commit. The PUT bodies are hashed
        # again here: the replay uploads exactly what the receipt says it did.
        registered = app.client.post(f"/v1/captures/{capture_id}/files", json={"files": saved_files})
        record("register", httpStatus=registered.status_code, files=len(saved_files))
        if registered.status_code != 200:
            errors.append(f"register_http_{registered.status_code}")
            return _finish(report, errors, f"register failed: {registered.text[:300]}")
        uploaded: list[str] = []
        for saved, target in zip(saved_files, registered.json()["files"], strict=True):
            data = (packet_dir / saved["path"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != saved["sha256"]:
                errors.append(f"upload_hash_drift:{saved['path']}")
            if target.get("state") == "committed":
                continue  # already on the server: resume needs no second PUT
            put = TestClient(app.app).put(
                target["upload"]["url"],
                content=data,
                headers=target["upload"].get("headers") or {},
            )
            if put.status_code == 200:
                uploaded.append(target["path"])
            else:
                errors.append(f"upload_http_{put.status_code}:{target['path']}")
        record("upload", uploaded=len(uploaded), skippedCommitted=len(saved_files) - len(uploaded))
        app.drain()

        def _commit():
            return app.client.post(
                f"/v1/captures/{capture_id}/files:commit",
                json={"files": [{"path": item["path"], "sha256": item["sha256"]} for item in saved_files]},
            )

        commit = _send("commit", _commit, injector, report, errors)
        commit_body = commit.json() if commit.status_code == 200 else {}
        record(
            "commit",
            httpStatus=commit.status_code,
            committed=commit_body.get("committed", []),
            notFound=commit_body.get("notFound", []),
            mismatch=commit_body.get("mismatch", []),
        )
        if commit.status_code != 200 or commit_body.get("notFound") or commit_body.get("mismatch"):
            errors.append(f"commit_failed_http_{commit.status_code}")
        if sorted(commit_body.get("committed", [])) != sorted(item["path"] for item in saved_files):
            errors.append("commit_incomplete")
        app.drain()

        # A lost commit reply must resume without re-uploading: register
        # reports the files committed from server state, and the same commit
        # body commits cleanly again with unchanged hashes.
        if injector.fired.count("commit") or report["attempts"]["commit"][-1]["outcome"] == "simulated_timeout":
            resumed = app.client.post(f"/v1/captures/{capture_id}/files", json={"files": saved_files})
            states = (
                {item["path"]: item["state"] for item in resumed.json().get("files", [])}
                if resumed.status_code == 200
                else {}
            )
            recommit = _commit()
            recommit_body = recommit.json() if recommit.status_code == 200 else {}
            record(
                "resume-commit",
                registerHttpStatus=resumed.status_code,
                serverStates=states,
                recommitHttpStatus=recommit.status_code,
                recomputed=recommit_body.get("committed", []),
            )
            if not states or not all(state == "committed" for state in states.values()):
                errors.append("resume_register_not_committed")
            if recommit.status_code != 200 or sorted(recommit_body.get("committed", [])) != sorted(states):
                errors.append("resume_commit_failed")

        # Finalize with the saved key and packet digest. A lost finalize reply
        # replays to the same run id: the run is derived from the capture and
        # the packet bytes, so a retry can never start a second run.
        raw = (packet_dir / "packet.json").read_bytes()

        def _finalize():
            return app.client.post(
                f"/v1/captures/{capture_id}/finalize",
                content=raw,
                headers={
                    "Idempotency-Key": finalize_key,
                    "Content-Type": "application/json",
                    "X-Packet-Sha256": packet_sha,
                },
            )

        accepted = _send("finalize", _finalize, injector, report, errors)
        accepted_body = accepted.json() if accepted.status_code == 202 else {}
        run_id = accepted_body.get("runId")
        report["runId"] = run_id
        record("finalize", idempotencyKey=finalize_key, httpStatus=accepted.status_code, runId=run_id)
        if accepted.status_code != 202:
            errors.append(f"finalize_http_{accepted.status_code}")
            return _finish(report, errors, f"finalize failed: {accepted.text[:300]}")
        expected_run_id = "run_" + hashlib.sha256(capture_id.encode() + b"\0" + raw).hexdigest()[:16]
        if run_id != expected_run_id:
            errors.append("run_id_not_deterministic")
        if accepted_body.get("missing"):
            errors.append("finalize_missing_files")

        def _finalize_again():
            return _finalize()

        replayed = _finalize_again()
        replayed_body = replayed.json() if replayed.status_code == 202 else {}
        record("finalize-replay", httpStatus=replayed.status_code, runId=replayed_body.get("runId"))
        if replayed.status_code != 202 or replayed_body.get("runId") != run_id:
            errors.append("finalize_replay_run_id_changed")
        app.drain()

        # Terminal status and result, read the way a device reads them, with a
        # bounded poll: a drained app is already terminal, so this settles in
        # one read and any missed terminal state fails the replay loudly.
        status_payload = None
        for _ in range(MAX_POLLS):
            status_payload = app.client.get(f"/v1/captures/{capture_id}").json()
            if status_payload.get("status") in TERMINAL_STATUSES:
                break
        terminal = (status_payload or {}).get("status")
        report["finalCaptureStatus"] = terminal
        record("status", finalCaptureStatus=terminal, filesCommitted=(status_payload or {}).get("filesCommitted"))
        if terminal not in TERMINAL_STATUSES:
            errors.append(f"not_terminal:{terminal}")

        result_payload = None
        for _ in range(MAX_POLLS):
            got = app.client.get(f"/v1/captures/{capture_id}/result")
            if got.status_code == 200 and got.json().get("outcome"):
                result_payload = got.json()
                break
            result_payload = got.json() if got.status_code == 200 else None
        report["resultOutcomeKind"] = (result_payload or {}).get("outcome", {}).get("kind") if result_payload else None
        record(
            "result",
            httpStatus=200 if result_payload else None,
            outcomeKind=report["resultOutcomeKind"],
            runId=(result_payload or {}).get("runId"),
        )
        if result_payload is None:
            errors.append("result_not_served")
        else:
            if result_payload.get("runId") != run_id:
                errors.append("result_run_id_mismatch")
            if report["resultOutcomeKind"] != terminal:
                errors.append("result_outcome_status_mismatch")

        # The terminal state must not duplicate anything: a final create with
        # the saved key still maps to the one capture, and the event feed ends
        # with exactly one terminal verdict for the one run.
        final_create = app.client.post("/v1/captures", json=body, headers={"Idempotency-Key": create_key})
        record("create-terminal", httpStatus=final_create.status_code, captureId=final_create.json().get("captureId"))
        if final_create.status_code != 200 or final_create.json().get("captureId") != capture_id:
            errors.append("terminal_create_duplicated_capture")

        events: list[dict] = []
        after = 0
        while True:
            page = app.client.get(f"/v1/captures/{capture_id}/events", params={"after": after, "wait": 0}).json()
            events.extend(page.get("events", []))
            if not page.get("events") or page.get("next") in (None, after):
                break
            after = page["next"]
        terminals = [event["type"] for event in events if event["type"] in TERMINAL_EVENTS]
        report["terminalEvents"] = terminals
        record("events", count=len(events), terminalEvents=terminals)
        if terminals != ["verdict_ready"]:
            errors.append(f"terminal_events:{terminals}")
    except Exception as exc:  # noqa: BLE001 - the report must survive any failure
        errors.append(f"exception:{type(exc).__name__}")
        return _finish(report, errors, f"exception: {type(exc).__name__}: {exc}")
    finally:
        if app is not None:
            app.close()
    return _finish(report, errors, None)


def _finish(report: dict, errors: list[str], detail: str | None) -> dict:
    if detail:
        report["errorDetail"] = detail
    report["finishedAt"] = datetime.now(UTC).isoformat()
    report["ok"] = not errors
    return report


def write_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--receipt", type=Path, required=True, help="saved capture_flow_e2e receipt to replay")
    parser.add_argument("--workdir", type=Path, help="run directory (default: a fresh temp dir)")
    parser.add_argument(
        "--packet", type=Path, help="packet fixture (default: the receipt's packet dir, else the committed mini-arkit)"
    )
    parser.add_argument("--report", type=Path, help="report path (default: <workdir>/replay-receipt.json)")
    parser.add_argument(
        "--fail-at",
        default="",
        help="comma list of steps that lose one response: create,commit,finalize",
    )
    args = parser.parse_args(argv)
    faults = {step.strip(): 1 for step in args.fail_at.split(",") if step.strip()}
    owned = args.workdir is None
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="capture-replay-"))
    report = run_replay(args.receipt, workdir, packet_dir=args.packet, faults=faults)
    report_path = args.report or workdir / "replay-receipt.json"
    write_report(report, report_path)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "report": str(report_path),
                "captureId": report["captureId"],
                "finalCaptureStatus": report["finalCaptureStatus"],
                **({"errorDetail": report["errorDetail"]} if "errorDetail" in report else {}),
            }
        )
    )
    if owned:
        print(f"workdir: {workdir}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
