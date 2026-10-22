#!/usr/bin/env python3
"""Close the cold-restart gap PR #262 admitted: a client that dies and comes back.

One repeatable command for reviewers:

    cd backend && uv run --extra api python scripts/capture_cold_restart.py \
        --receipt <workdir>/receipt.json --stop commit-lost

``scripts/replay_capture.py`` resumes a lost response inside one Python
process: the client that retries is the same interpreter that lost the reply,
so it still holds whatever it cached. A phone does not get that luxury — the
app dies, relaunches, and must recover from what it persisted and what the
server remembers. This command drives exactly that against the real app entry
point ``create_app()``, served by a local uvicorn process (memory state, local
volume storage, the documented dev handoff ``HSS_INGEST_HOOKS=auto`` with
orchestrator stage stubs — no GCP, no Modal, no network, no credentials). It
splits the client across two fresh Python processes:

- the **primary client** is one Python process. It replays the saved journey
  from a ``capture_flow_e2e`` receipt — create, register, PUT, commit,
  finalize — and dies at the scripted interruption: right after a deliberately
  lost response (``create-lost``, ``commit-lost``, ``finalize-lost``) or before
  the next request (``pre-commit``, ``pre-finalize``). It persists only what a
  device could: a journal of what it sent and which replies it actually saw.
- the **resume client** is a fresh Python process with fresh in-memory client
  state. It reads the receipt and the journal and recovers through the real
  capture routes: create again with the saved idempotency key, re-register to
  learn the server's file states instead of trusting its own, re-send the
  identical commit body, and replay the finalize for its stored run id. When
  the dead process already finalized, the capture is closed to files and
  commits, so recovery goes straight through the finalize replay; otherwise
  the resume re-registers, uploads only what the server still lacks, commits,
  and finalizes. It then reads status, result and events the way a device
does.

Across the restart it asserts the four things a raw HTTP log cannot prove:

- **one capture id** — the resume create answers 200 from the idempotency
  store (never a second 201) with the capture id the dead process recorded,
  and a final create after the verdict still maps to the same capture;
- **one deterministic run id** — the run id equals ``run_`` + sha256 of
  captureId, NUL and the packet bytes, and a second finalize returns it
  unchanged even though the first request came from a process that is gone;
- **the committed hashes** — the resumed commit reports the receipt's paths
  and never a mismatch, on a first commit or a replayed one;
- **one terminal state** — the capture lands on one terminal status, served
  once by ``GET /result`` with that run id and outcome, and the event feed
  ends with exactly one terminal ``verdict_ready``.

Nothing retries forever: the resume sends each step once more at most (the
second finalize is the determinism proof, not a retry), terminal and event
polling is capped at ``MAX_POLLS`` reads, each client leg is a subprocess with
a hard timeout, and the server readiness wait is bounded too. A receipt
written before the replay fields existed fails before anything is sent,
naming the missing field.

It writes ``<workdir>/cold-restart-report.json`` and exits 0 when every
assertion held, 1 otherwise. It never changes route contracts, never touches
the iOS tree, ``.github/`` or deployment, and leaves ``replay_capture.py``'s
owned files alone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.replay_capture import (  # noqa: E402
    DEFAULT_PACKET,
    TERMINAL_EVENTS,
    TERMINAL_STATUSES,
    check_fixture,
    receipt_problem,
)
from server.api.app import create_app  # noqa: E402
from server.common.config import Settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# Same static test token the API test suite uses (server/api/tests/conftest.py).
TOKEN = "d" * 40

# Every wait in this command is bounded so a stuck server or leg fails loudly
# instead of hanging: readiness waits at most SERVER_TIMEOUT_S, each leg runs
# at most LEG_TIMEOUT_S, one HTTP request at most REQUEST_TIMEOUT_S, and the
# terminal status/events polls settle within MAX_POLLS * POLL_INTERVAL_S.
SERVER_TIMEOUT_S = 60.0
LEG_TIMEOUT_S = 180.0
REQUEST_TIMEOUT_S = 30.0
MAX_POLLS = 60
POLL_INTERVAL_S = 0.5

TERMINAL_EVENT = "verdict_ready"

# Where the primary client dies. The lost-response points stop after a request
# the server processed but never answered to this client; the pre-* points stop
# before the next request, the way a crash between steps does.
STOP_POINTS = {
    "create-lost": "the create reply was lost after the server processed it",
    "pre-commit": "the client died before sending commit (all bytes were PUT)",
    "commit-lost": "the commit reply was lost after the server processed it",
    "pre-finalize": "the client died after commit was delivered, before finalize",
    "finalize-lost": "the finalize reply was lost after the server processed it",
}


def _interruption(stop: str) -> dict:
    lost_step = stop.removesuffix("-lost")
    if lost_step != stop:
        return {"kind": "lost_response", "step": lost_step}
    return {"kind": "before_request", "step": stop.removesuffix("pre-")}


def missing_replay_fields(receipt: dict) -> list[str]:
    """Name every replay field the saved receipt is missing, one by one.

    Receipts written before the replay fields existed fail here — loudly and
    by name, never by replaying guessed hashes."""
    missing: list[str] = []
    keys = receipt.get("idempotencyKeys")
    if not isinstance(keys, dict):
        missing.append("idempotencyKeys")
    else:
        for name in ("create", "finalize"):
            if not keys.get(name):
                missing.append(f"idempotencyKeys.{name}")
    files = receipt.get("files")
    if not isinstance(files, list) or not files:
        missing.append("files")
    else:
        for index, item in enumerate(files):
            if not isinstance(item, dict) or not item.get("path") or not item.get("sha256"):
                missing.append(f"files[{index}]")
                break
    if not receipt.get("packetSha256"):
        missing.append("packetSha256")
    return missing


def _resolve_packet_dir(receipt: dict, override: Path | None) -> Path:
    if override is not None:
        return override
    saved = receipt.get("packetDir")
    if saved and Path(saved).is_dir():
        return Path(saved)
    return DEFAULT_PACKET


def _expected_run_id(capture_id: str, raw_packet: bytes) -> str:
    return "run_" + hashlib.sha256(capture_id.encode() + b"\0" + raw_packet).hexdigest()[:16]


def _create_body(packet: dict) -> dict:
    session = packet["session"]
    return {
        "packetId": packet["packetId"],
        "formatVersion": packet["formatVersion"],
        "tier": session["tier"],
        "flow": "guided",
        "device": {
            "model": session["deviceModel"],
            "systemVersion": session["systemVersion"],
            "appVersion": session["appVersion"],
        },
    }


class DeviceClient:
    """A minimal capture-API client: one base URL, one bearer token, no memory.

    It keeps no state between requests on purpose — the cold restart must
    prove recovery comes from the server and the persisted receipt, never from
    a cached register response or a reused upload URL."""

    def __init__(self, base_url: str, token: str = TOKEN) -> None:
        self.base_url = base_url.rstrip("/")

    def request(
        self, method: str, path: str, *, body: bytes | None = None, headers: dict | None = None
    ) -> tuple[int, bytes]:
        req = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={"Authorization": f"Bearer {TOKEN}", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:  # a non-2xx is still a server answer
            return error.code, error.read()

    def post_json(self, path: str, payload: dict, headers: dict | None = None) -> tuple[int, dict]:
        status, raw = self.request(
            "POST",
            path,
            body=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        return status, (json.loads(raw) if raw else {})

    def post_raw(self, path: str, payload: bytes, headers: dict | None = None) -> tuple[int, dict]:
        status, raw = self.request("POST", path, body=payload, headers=headers)
        return status, (json.loads(raw) if raw else {})

    def get_json(self, path: str) -> tuple[int, dict]:
        status, raw = self.request("GET", path)
        return status, (json.loads(raw) if raw else {})

    def put_signed(self, url: str, payload: bytes, headers: dict | None = None) -> tuple[int, bytes]:
        # The signed URL names this server's host; the token it guards is in
        # the path, so keep the path and put to the same local server.
        return self.request("PUT", urlsplit(url).path, body=payload, headers=headers)


def _journal_path(workdir: Path) -> Path:
    return workdir / "client-journal.json"


def _fresh_journal(stop: str) -> dict:
    return {
        "leg": "primary",
        "stop": stop,
        "pid": os.getpid(),
        "captureId": None,
        "createExecuted": False,
        "createResponseSeen": False,
        "registered": False,
        "uploadedPaths": [],
        "commitExecuted": False,
        "commitResponseSeen": False,
        "finalizeExecuted": False,
        "finalizeResponseSeen": False,
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _finish(report: dict, errors: list[str], detail: str | None) -> dict:
    if detail:
        report["errorDetail"] = detail
    report["finishedAt"] = datetime.now(UTC).isoformat()
    report["ok"] = not errors
    return report


def run_primary_leg(
    receipt_path: Path, workdir: Path, server_url: str, stop: str, *, packet_dir: Path | None = None
) -> dict:
    """Walk the saved journey and die at the scripted interruption.

    Dying on time is success: the leg report is ``ok`` when the walk was clean
    up to the stop point. The journal is the only state the next process gets.
    """
    started_at = datetime.now(UTC).isoformat()
    report: dict = {
        "leg": "primary",
        "stop": stop,
        "interruption": _interruption(stop),
        "pid": os.getpid(),
        "freshProcess": True,
        "serverUrl": server_url,
        "startedAt": started_at,
        "steps": [],
        "captureId": None,
        "errorCodes": [],
        "ok": False,
    }
    errors = report["errorCodes"]
    journal = _fresh_journal(stop)

    def record(step: str, **fields) -> None:
        report["steps"].append({"step": step, **fields})

    try:
        client = DeviceClient(server_url)
        receipt = json.loads(receipt_path.read_text())
        problem = receipt_problem(receipt)
        if problem:
            raise ValueError(f"{receipt_path}: {problem}")
        packet_dir = _resolve_packet_dir(receipt, packet_dir)
        fixture = check_fixture(packet_dir, receipt, errors)
        record("fixture", packetDir=str(packet_dir), files=len((fixture or {}).get("packet", {}).get("files", [])))
        if fixture is None or errors:
            raise RuntimeError("the saved receipt no longer matches the packet fixture")
        packet = fixture["packet"]
        packet_sha = fixture["packetSha256"]
        keys = receipt["idempotencyKeys"]
        saved_files = receipt["files"]

        # Create. With the response lost the client exits without reading the
        # body: the capture id exists only on the server now.
        status, body = client.post_json(
            "/v1/captures", _create_body(packet), headers={"Idempotency-Key": keys["create"]}
        )
        journal["createExecuted"] = True
        if stop == "create-lost":
            record("create", responseLost=True)
            return _finish(report, errors, None)
        journal["createResponseSeen"] = True
        capture_id = body.get("captureId") if status in (200, 201) else None
        report["captureId"] = capture_id
        journal["captureId"] = capture_id
        record("create", httpStatus=status, captureId=capture_id)
        if status != 201:
            errors.append(f"create_http_{status}")
        if not capture_id:
            raise RuntimeError(f"no captureId in create response: {body}")

        # Register, PUT the receipt's bytes, hash-checking each against it.
        registered = client.post_json(f"/v1/captures/{capture_id}/files", {"files": saved_files})[1]
        targets = registered.get("files", [])
        record("register", files=len(targets))
        if registered.get("files") is None:
            raise RuntimeError(f"register failed: {registered}")
        journal["registered"] = True
        for saved, target in zip(saved_files, targets, strict=True):
            data = (packet_dir / saved["path"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != saved["sha256"]:
                errors.append(f"upload_hash_drift:{saved['path']}")
            put_status, _ = client.put_signed(target["upload"]["url"], data, target["upload"].get("headers") or {})
            if put_status == 200:
                journal["uploadedPaths"].append(target["path"])
            else:
                errors.append(f"upload_http_{put_status}:{target['path']}")
        record("upload", uploaded=len(journal["uploadedPaths"]), expected=len(saved_files))

        # Commit, unless the client dies before sending it.
        if stop == "pre-commit":
            record("stop", before="commit")
            return _finish(report, errors, None)
        commit_status, commit_body = client.post_json(
            f"/v1/captures/{capture_id}/files:commit",
            {"files": [{"path": item["path"], "sha256": item["sha256"]} for item in saved_files]},
        )
        journal["commitExecuted"] = True
        if stop == "commit-lost":
            record("commit", responseLost=True)
            return _finish(report, errors, None)
        journal["commitResponseSeen"] = True
        record(
            "commit",
            httpStatus=commit_status,
            committed=commit_body.get("committed", []),
            notFound=commit_body.get("notFound", []),
            mismatch=commit_body.get("mismatch", []),
        )
        if commit_status != 200 or commit_body.get("notFound") or commit_body.get("mismatch"):
            errors.append(f"commit_failed_http_{commit_status}")

        # Finalize, unless the client dies before sending it.
        if stop == "pre-finalize":
            record("stop", before="finalize")
            return _finish(report, errors, None)
        raw = (packet_dir / "packet.json").read_bytes()
        finalize_status, finalize_body = client.post_raw(
            f"/v1/captures/{capture_id}/finalize",
            raw,
            headers={
                "Idempotency-Key": keys["finalize"],
                "Content-Type": "application/json",
                "X-Packet-Sha256": packet_sha,
            },
        )
        journal["finalizeExecuted"] = True
        if stop == "finalize-lost":
            record("finalize", responseLost=True)
            return _finish(report, errors, None)
        journal["finalizeResponseSeen"] = True
        record("finalize", httpStatus=finalize_status, runId=finalize_body.get("runId"))
        if finalize_status != 202:
            errors.append(f"finalize_http_{finalize_status}")
    except Exception as exc:  # noqa: BLE001 - the report and journal must survive any failure
        report["errorDetail"] = f"exception: {type(exc).__name__}: {exc}"
        errors.append(f"exception:{type(exc).__name__}")
    finally:
        # The dying client's last act: persist what it knows, nothing more.
        report["journal"] = journal
        _write_json(_journal_path(workdir), journal)
    return _finish(report, errors, report.get("errorDetail"))


def run_resume_leg(receipt_path: Path, workdir: Path, server_url: str, *, packet_dir: Path | None = None) -> dict:
    """Recover the journey from a fresh process and assert the invariants.

    The client shares nothing with the dead one: no cached responses, no
    upload URLs, no committed set. Everything it needs it re-reads from the
    receipt and the journal, and re-asks the server through the real routes.
    """
    started_at = datetime.now(UTC).isoformat()
    report: dict = {
        "leg": "resume",
        "pid": os.getpid(),
        "freshProcess": True,
        "serverUrl": server_url,
        "startedAt": started_at,
        "steps": [],
        "captureId": None,
        "createReplayHttpStatus": None,
        "recreatedSameCaptureId": None,
        "serverFileStates": {},
        "recoveryPath": None,
        "recommit": None,
        "commitCommitted": [],
        "commitMismatch": [],
        "filesCommitted": None,
        "runId": None,
        "expectedRunId": None,
        "finalizeReplayRunId": None,
        "finalCaptureStatus": None,
        "statusPolls": None,
        "resultOutcomeKind": None,
        "resultRunId": None,
        "terminalCreateCaptureId": None,
        "terminalEvents": [],
        "errorCodes": [],
        "ok": False,
    }
    errors = report["errorCodes"]

    def record(step: str, **fields) -> None:
        report["steps"].append({"step": step, **fields})

    try:
        journal_path = _journal_path(workdir)
        if not journal_path.is_file():
            raise RuntimeError(f"no client journal at {journal_path}: the primary leg never ran")
        journal = json.loads(journal_path.read_text())
        client = DeviceClient(server_url)
        receipt = json.loads(receipt_path.read_text())
        problem = receipt_problem(receipt)
        if problem:
            raise ValueError(f"{receipt_path}: {problem}")
        packet_dir = _resolve_packet_dir(receipt, packet_dir)
        fixture = check_fixture(packet_dir, receipt, errors)
        record("fixture", packetDir=str(packet_dir), files=len((fixture or {}).get("packet", {}).get("files", [])))
        if fixture is None or errors:
            raise RuntimeError("the saved receipt no longer matches the packet fixture")
        packet = fixture["packet"]
        packet_sha = fixture["packetSha256"]
        keys = receipt["idempotencyKeys"]
        saved_files = receipt["files"]

        # Create again with the saved key. The dead process's create really
        # executed, so this must answer 200 from the idempotency store with
        # the same capture id — a 201 here minted a second capture.
        status, body = client.post_json(
            "/v1/captures", _create_body(packet), headers={"Idempotency-Key": keys["create"]}
        )
        capture_id = body.get("captureId") if status == 200 else None
        report["captureId"] = capture_id
        report["createReplayHttpStatus"] = status
        report["recreatedSameCaptureId"] = journal.get("captureId") in (None, capture_id)
        record(
            "create",
            httpStatus=status,
            captureId=capture_id,
            journalCaptureId=journal.get("captureId"),
        )
        if status != 200:
            errors.append(f"resume_create_not_stored:{status}")
        if not report["recreatedSameCaptureId"]:
            errors.append("resume_capture_id_changed")
        if not capture_id:
            raise RuntimeError(f"create replay did not yield a capture id: {body}")

        # Register to learn the server's file states — the resume never
        # trusts the journal's idea of what was uploaded; the server says.
        # But a finalize the dead process already sent changes the recovery:
        # the capture is closed to files and commits (the server answers 409
        # to both), so the fresh client recovers through the finalize's
        # idempotent replay — the stored run id — and never re-registers.
        if journal.get("finalizeExecuted"):
            report["recoveryPath"] = "finalize_replay"
            report["recommit"] = False  # the resume sends no commit on this path
            record("register", skipped="the capture is closed to files after finalize")
        else:
            report["recoveryPath"] = "register_commit"
            register_status, registered = client.post_json(f"/v1/captures/{capture_id}/files", {"files": saved_files})
            record("register", httpStatus=register_status, files=len(saved_files))
            if register_status != 200:
                raise RuntimeError(f"register failed: {registered}")
            states = {item["path"]: item.get("state") for item in registered.get("files", [])}
            report["serverFileStates"] = states

            # PUT only what the server still lacks; the bytes hash against
            # the receipt before anything is sent.
            uploaded: list[str] = []
            for saved, target in zip(saved_files, registered.get("files", []), strict=True):
                if target.get("state") == "committed":
                    continue  # already on the server: a resume needs no second PUT
                data = (packet_dir / saved["path"]).read_bytes()
                if hashlib.sha256(data).hexdigest() != saved["sha256"]:
                    errors.append(f"upload_hash_drift:{saved['path']}")
                put_status, _ = client.put_signed(target["upload"]["url"], data, target["upload"].get("headers") or {})
                if put_status == 200:
                    uploaded.append(target["path"])
                else:
                    errors.append(f"upload_http_{put_status}:{target['path']}")
            record(
                "upload",
                uploaded=len(uploaded),
                alreadyCommitted=sorted(path for path, state in states.items() if state == "committed"),
            )

            # The identical commit body — first send or replay, the server
            # answers the receipt's hashes with the receipt's paths and no
            # mismatch.
            commit_status, commit_body = client.post_json(
                f"/v1/captures/{capture_id}/files:commit",
                {"files": [{"path": item["path"], "sha256": item["sha256"]} for item in saved_files]},
            )
            report["recommit"] = bool(journal.get("commitExecuted"))
            report["commitCommitted"] = commit_body.get("committed", []) if commit_status == 200 else []
            report["commitMismatch"] = commit_body.get("mismatch", [])
            record(
                "commit",
                httpStatus=commit_status,
                replayed=report["recommit"],
                committed=report["commitCommitted"],
                notFound=commit_body.get("notFound", []),
                mismatch=report["commitMismatch"],
            )
            if commit_status != 200 or commit_body.get("notFound") or report["commitMismatch"]:
                errors.append(f"commit_failed_http_{commit_status}")
            if sorted(report["commitCommitted"]) != sorted(item["path"] for item in saved_files):
                errors.append("commit_incomplete")

        # Finalize twice. The first response may be the stored replay of the
        # dead process's request; the second must return the same deterministic
        # run id — the run is derived from the capture and the packet bytes.
        raw = (packet_dir / "packet.json").read_bytes()
        expected_run_id = _expected_run_id(capture_id, raw)
        report["expectedRunId"] = expected_run_id
        finalize_status, finalize_body = client.post_raw(
            f"/v1/captures/{capture_id}/finalize",
            raw,
            headers={
                "Idempotency-Key": keys["finalize"],
                "Content-Type": "application/json",
                "X-Packet-Sha256": packet_sha,
            },
        )
        run_id = finalize_body.get("runId") if finalize_status == 202 else None
        report["runId"] = run_id
        record("finalize", httpStatus=finalize_status, replayed=bool(journal.get("finalizeExecuted")), runId=run_id)
        if finalize_status != 202:
            errors.append(f"finalize_http_{finalize_status}")
            raise RuntimeError(f"finalize failed: {finalize_body}")
        if finalize_body.get("missing"):
            errors.append("finalize_missing_files")
        replay_status, replay_body = client.post_raw(
            f"/v1/captures/{capture_id}/finalize",
            raw,
            headers={
                "Idempotency-Key": keys["finalize"],
                "Content-Type": "application/json",
                "X-Packet-Sha256": packet_sha,
            },
        )
        report["finalizeReplayRunId"] = replay_body.get("runId") if replay_status == 202 else None
        record("finalize-replay", httpStatus=replay_status, runId=report["finalizeReplayRunId"])
        if replay_status != 202 or report["finalizeReplayRunId"] != run_id:
            errors.append("finalize_replay_run_id_changed")
        if run_id != expected_run_id:
            errors.append("run_id_not_deterministic")

        # Terminal status, bounded: the stub pipeline runs in the server, so
        # the client waits with a capped poll, never forever.
        status_payload: dict = {}
        polls = 0
        while polls < MAX_POLLS:
            polls += 1
            poll_status, status_payload = client.get_json(f"/v1/captures/{capture_id}")
            if poll_status == 200 and status_payload.get("status") in TERMINAL_STATUSES:
                break
            time.sleep(POLL_INTERVAL_S)
        terminal = status_payload.get("status")
        report["finalCaptureStatus"] = terminal
        report["statusPolls"] = polls
        record("status", finalCaptureStatus=terminal, polls=polls, filesCommitted=status_payload.get("filesCommitted"))
        report["filesCommitted"] = status_payload.get("filesCommitted")
        if terminal not in TERMINAL_STATUSES:
            errors.append(f"not_terminal:{terminal}")

        # The result is served once, for the one run, agreeing with the status.
        result_payload: dict = {}
        for _ in range(MAX_POLLS):
            got_status, result_payload = client.get_json(f"/v1/captures/{capture_id}/result")
            if got_status == 200 and result_payload.get("outcome"):
                break
            time.sleep(POLL_INTERVAL_S)
        report["resultOutcomeKind"] = (result_payload or {}).get("outcome", {}).get("kind")
        report["resultRunId"] = (result_payload or {}).get("runId")
        record("result", outcomeKind=report["resultOutcomeKind"], runId=report["resultRunId"])
        if not result_payload.get("outcome"):
            errors.append("result_not_served")
        else:
            if report["resultRunId"] != run_id:
                errors.append("result_run_id_mismatch")
            if report["resultOutcomeKind"] != terminal:
                errors.append("result_outcome_status_mismatch")

        # A final create with the saved key still maps to the one capture.
        final_status, final_body = client.post_json(
            "/v1/captures", _create_body(packet), headers={"Idempotency-Key": keys["create"]}
        )
        report["terminalCreateCaptureId"] = final_body.get("captureId")
        record("create-terminal", httpStatus=final_status, captureId=report["terminalCreateCaptureId"])
        if final_status != 200 or report["terminalCreateCaptureId"] != capture_id:
            errors.append("terminal_create_duplicated_capture")

        # The event feed ends with exactly one terminal verdict for the one
        # run, read with the same bounded poll as the status.
        terminals: list[str] = []
        polls = 0
        while polls < MAX_POLLS:
            polls += 1
            events: list[dict] = []
            after = 0
            while True:
                _, page = client.get_json(f"/v1/captures/{capture_id}/events?after={after}&wait=0")
                events.extend(page.get("events", []))
                nxt = page.get("next")
                if not page.get("events") or nxt in (None, after):
                    break
                after = nxt
            terminals = [event["type"] for event in events if event["type"] in TERMINAL_EVENTS]
            if terminals == [TERMINAL_EVENT]:
                break
            time.sleep(POLL_INTERVAL_S)
        report["terminalEvents"] = terminals
        record("events", polls=polls, terminalEvents=terminals)
        if terminals != [TERMINAL_EVENT]:
            errors.append(f"terminal_events:{terminals}")
    except Exception as exc:  # noqa: BLE001 - the report must survive any failure
        report["errorDetail"] = f"exception: {type(exc).__name__}: {exc}"
        errors.append(f"exception:{type(exc).__name__}")
    return _finish(report, errors, report.get("errorDetail"))


def _invariants(receipt: dict, primary: dict, resume: dict) -> dict:
    """The four cross-restart claims, recomputed from both legs' evidence."""
    paths = sorted(item["path"] for item in receipt["files"])
    committed_ok = (
        sorted(resume.get("commitCommitted") or []) == paths and not (resume.get("commitMismatch") or [])
    ) or (
        # On the finalize-replay path the commit was the dead process's; the
        # server's status count is the evidence every file is committed.
        resume.get("recoveryPath") == "finalize_replay" and resume.get("filesCommitted") == len(paths)
    )
    return {
        "oneCaptureId": bool(resume.get("captureId"))
        and resume.get("createReplayHttpStatus") == 200
        and resume.get("recreatedSameCaptureId") is True
        and resume.get("terminalCreateCaptureId") == resume.get("captureId"),
        "oneDeterministicRunId": bool(resume.get("runId"))
        and resume.get("runId") == resume.get("expectedRunId") == resume.get("finalizeReplayRunId"),
        "committedHashesMatchReceipt": committed_ok,
        "oneTerminalEvent": resume.get("terminalEvents") == [TERMINAL_EVENT],
        "oneTerminalResult": bool(resume.get("runId"))
        and resume.get("resultRunId") == resume.get("runId")
        and resume.get("resultOutcomeKind") == resume.get("finalCaptureStatus")
        and resume.get("finalCaptureStatus") in TERMINAL_STATUSES,
    }


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _child_env() -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # The orchestrator reads this from the environment when the API binds it.
    env["HSS_ORCH_STAGE_STUBS"] = "1"
    return env


def _wait_for_server(server_url: str) -> bool:
    deadline = time.monotonic() + SERVER_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(server_url + "/v1/healthz", timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - not ready yet; bounded by the deadline
            time.sleep(0.2)
    return False


def run_cold_restart(
    receipt_path: Path, workdir: Path, *, stop: str = "commit-lost", packet_dir: Path | None = None
) -> dict:
    """Orchestrate the cold restart and return the report. Never raises."""
    started_at = datetime.now(UTC).isoformat()
    report: dict = {
        "coldRestart": "capture_cold_restart",
        "startedAt": started_at,
        "receiptPath": str(receipt_path),
        "stop": stop,
        "workdir": str(workdir),
        "processModel": {
            "legs": ["primary", "resume"],
            "freshPythonProcessPerLeg": True,
            "sharedState": "saved receipt + client journal on disk only",
        },
        "server": {
            "entry": "create_app()",
            "transport": "local uvicorn",
            "stateBackend": "memory",
            "storageBackend": "volume",
            "hooks": "auto",
            "stageStubs": True,
        },
        "missingReceiptFields": [],
        "invariants": {},
        "captureId": None,
        "runId": None,
        "finalCaptureStatus": None,
        "terminalEvents": [],
        "errorCodes": [],
        "ok": False,
    }
    errors = report["errorCodes"]
    receipt = json.loads(receipt_path.read_text())
    problem = receipt_problem(receipt)
    if problem == "not_a_capture_flow_e2e_receipt":
        errors.append(problem)
        return _finish(report, errors, f"{receipt_path}: {problem}")

    # Keep the cold-restart report's per-field diagnostics when the shared
    # validator rejects a receipt without its replay block.
    missing = missing_replay_fields(receipt)
    if missing:
        report["missingReceiptFields"] = missing
        errors.extend(f"receipt_missing_field:{name}" for name in missing)
        return _finish(
            report, errors, "the saved receipt predates the replay fields; rerun capture_flow_e2e.py to rewrite it"
        )
    packet_dir = _resolve_packet_dir(receipt, packet_dir)
    fixture_errors: list[str] = []
    fixture = check_fixture(packet_dir, receipt, fixture_errors)
    if fixture is None or fixture_errors:
        errors.extend(fixture_errors)
        return _finish(report, errors, "the saved receipt no longer matches the packet fixture")

    workdir.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    server_url = f"http://127.0.0.1:{port}"
    script = str(Path(__file__).resolve())
    server_log = (workdir / "server.log").open("wb")
    server = subprocess.Popen(
        [sys.executable, script, "--serve", "--workdir", str(workdir), "--port", str(port)],
        env=_child_env(),
        stdout=server_log,
        stderr=server_log,
    )
    try:
        if not _wait_for_server(server_url):
            errors.append("server_not_ready")
            return _finish(report, errors, f"the local app never answered /v1/healthz within {SERVER_TIMEOUT_S:g}s")
        report["server"]["url"] = server_url

        for leg, extra in (("primary", ["--stop", stop]), ("resume", [])):
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        script,
                        "--leg",
                        leg,
                        "--receipt",
                        str(receipt_path),
                        "--workdir",
                        str(workdir),
                        "--server-url",
                        server_url,
                        *extra,
                    ],
                    cwd=str(ROOT),
                    env=_child_env(),
                    capture_output=True,
                    text=True,
                    timeout=LEG_TIMEOUT_S,
                    check=False,
                )
                if completed.returncode != 0:
                    errors.append(f"{leg}_leg_failed")
                    report[f"{leg}Stderr"] = completed.stderr[-500:]
            except subprocess.TimeoutExpired:
                errors.append(f"{leg}_leg_timeout")

        leg_reports: dict[str, dict] = {}
        for leg in ("primary", "resume"):
            path = workdir / f"leg-{leg}.json"
            try:
                leg_reports[leg] = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                errors.append(f"{leg}_leg_report_missing")
        if leg_reports:
            primary = leg_reports.get("primary", {})
            resume = leg_reports.get("resume", {})
            report["primary"] = primary
            report["resume"] = resume
            if not primary.get("ok"):
                errors.append("primary_leg_not_ok")
            if not resume.get("ok"):
                errors.append("resume_leg_not_ok")
            report["invariants"] = _invariants(receipt, primary, resume)
            if not all(report["invariants"].values()):
                errors.append("invariants_broken")
            report["captureId"] = resume.get("captureId")
            report["runId"] = resume.get("runId")
            report["finalCaptureStatus"] = resume.get("finalCaptureStatus")
            report["terminalEvents"] = resume.get("terminalEvents", [])
        return _finish(report, errors, report.get("errorDetail"))
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
        server_log.close()


def serve(workdir: Path, port: int) -> int:
    """The local app: one uvicorn process holding the memory state and the
    volume the legs resume against. Internal mode of this script."""
    import uvicorn

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
        CAPTURE_API_PUBLIC_URL=f"http://127.0.0.1:{port}",
        HSS_INGEST_HOOKS="auto",
        VISION_EXTERNAL_CALLS="off",
        GIT_SHA="0123456789abcdef",
        SMP_SERVICE_NAME="capture-api",
    )
    uvicorn.run(create_app(settings=settings), host="127.0.0.1", port=port, log_level="warning")
    return 0


def _summarize(report: dict) -> dict:
    summary = {
        "ok": report["ok"],
        "stop": report.get("stop"),
        "captureId": report.get("captureId"),
        "runId": report.get("runId"),
        "finalCaptureStatus": report.get("finalCaptureStatus"),
        "errorCodes": report.get("errorCodes", []),
    }
    if report.get("missingReceiptFields"):
        summary["missingReceiptFields"] = report["missingReceiptFields"]
    if "errorDetail" in report:
        summary["errorDetail"] = report["errorDetail"]
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--receipt", type=Path, help="saved capture_flow_e2e receipt to recover")
    parser.add_argument("--workdir", type=Path, help="run directory (default: a fresh temp dir)")
    parser.add_argument(
        "--packet", type=Path, help="packet fixture (default: the receipt's packet dir, else the committed mini-arkit)"
    )
    parser.add_argument("--report", type=Path, help="report path (default: <workdir>/cold-restart-report.json)")
    parser.add_argument(
        "--stop", choices=sorted(STOP_POINTS), default="commit-lost", help="where the primary client dies"
    )
    parser.add_argument("--leg", choices=["primary", "resume"], help=argparse.SUPPRESS)
    parser.add_argument("--server-url", help=argparse.SUPPRESS)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.serve:
        return serve(args.workdir or Path(tempfile.mkdtemp(prefix="capture-cold-restart-")), args.port)

    if args.leg:
        if not (args.receipt and args.workdir and args.server_url):
            parser.error(f"--leg {args.leg} needs --receipt, --workdir and --server-url")
        if args.leg == "primary":
            report = run_primary_leg(args.receipt, args.workdir, args.server_url, args.stop, packet_dir=args.packet)
        else:
            report = run_resume_leg(args.receipt, args.workdir, args.server_url, packet_dir=args.packet)
        _write_json(args.workdir / f"leg-{args.leg}.json", report)
        print(json.dumps({"ok": report["ok"], "leg": args.leg, "errorCodes": report["errorCodes"]}))
        return 0 if report["ok"] else 1

    if not args.receipt:
        parser.error("--receipt is required")

    owned = args.workdir is None
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="capture-cold-restart-"))
    workdir.mkdir(parents=True, exist_ok=True)
    report = run_cold_restart(args.receipt, workdir, stop=args.stop, packet_dir=args.packet)
    report_path = args.report or workdir / "cold-restart-report.json"
    _write_json(report_path, report)
    print(json.dumps({**_summarize(report), "report": str(report_path)}))
    if owned:
        print(f"workdir: {workdir}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
