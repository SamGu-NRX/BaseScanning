"""Harness self-check: the runner, end to end, against an in-process fake.

The fake origin here is PLUMBING, clearly labeled. It proves the harness
runs and its receipts are well-formed and privacy-clean. It is NEVER
evidence about any backend; backend claims only ever come from runs
against a real local endpoint, with the transport label to prove it.

What is verified:
  1. the runner completes end to end (exit 0) including the FAKE_HUNTER
     fault round through the local proxy;
  2. every lifecycle.* and control.* check lands PASS except the
     deliberately NOT_RUN GPU_STUB lines, the restart-recovery control
     when no --restart-command is supplied, and the deliberately
     non-passing fault-round rows — each asserted non-passing for the
     right reason: a fault that fired without a matching detection row
     is itself a self-check failure;
  3. the fault oracles bite: truncated response framing, expired
     signed-URL 403, canned capture_not_found 404, and a retry_finalize
     event injection each produce the expected detection;
  4. when --restart-command is supplied, the restart-recovery control
     executes the command, wipes the fake's in-process state (simulated
     process memory loss), probes the pre-restart capture, and pushes a
     recovery capture through the full lifecycle under the same
     idempotency key;
  5. receipts exist, parse, and are internally consistent;
  6. the privacy sweep: no signed URL, no synthetic signature header
     value, no file content, and no request header material appears in
     any receipt; digests and field names do; the receipt writer's own
     post-write screen and the integrity verdict over the sealed run are
     consumed fail-closed (any finding or integrity problem fails the
     self-check).

Usage: hunter-selfcheck [--out /tmp/hli-selfcheck] [--restart-command '...']
Exit 0 when all assertions hold, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .adapter import HunterIngestAdapter
from .modes import Status, Transport
from .receipts import fresh_run_dir
from .sanitize import redact_text
from .scenarios import (
    TERMINAL_STATUSES,
    CapturePlan,
    PrefixedSink,
    gpu_stub_lines,
    record,
    step_observed,
)
from .scenarios.controls import run_controls
from .scenarios.lifecycle import run_lifecycle
from .traces import compare_trace

_TEMP = tempfile.gettempdir()

_TRACE_NAME = "expected-lifecycle-v1.jsonl"


def frozen_trace_candidates() -> tuple[Path, ...]:
    """Where the frozen trace may live, in priority order.

    First: inside the installed package (works for wheel installs once
    package-data ships it, and for editable installs via the src tree).
    Second: the repo checkout's tool-root traces/ directory — a compat
    symlink to the package copy while consumers (tests/test_receipts.py)
    still reference that path.
    """
    package_dir = Path(__file__).resolve().parent
    return (
        package_dir / "traces" / _TRACE_NAME,
        package_dir.parents[1] / "traces" / _TRACE_NAME,
    )


def frozen_trace_path() -> Path:
    """Resolve the frozen trace from the first candidate that exists."""
    candidates = frozen_trace_candidates()
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "frozen trace missing at " + " and at ".join(str(c) for c in candidates)
    )


class _FakeState:
    """In-memory capture state for the plumbing fake."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.captures: dict[str, dict] = {}
        self.counter = 0
        self.storage: dict[str, bytes] = {}

    def new_capture(self, idempotency_key: str | None) -> tuple[str, bool]:
        with self.lock:
            if idempotency_key:
                for cid, cap in self.captures.items():
                    if cap.get("idempotency_key") == idempotency_key:
                        return cid, False
            self.counter += 1
            cid = f"cap-{self.counter:04d}"
            self.captures[cid] = {
                "idempotency_key": idempotency_key,
                "files": {},
                "committed": [],
                "finalized": False,
            }
            return cid, True

    def hard_reset(self) -> None:
        """Simulate process memory loss: drop captures and stored bytes.

        The id counter is deliberately kept monotonic — a restarted backend
        must never re-mint an id it already used, so a replayed idempotency
        key after the wipe is observable as a fresh capture id.
        """
        with self.lock:
            self.captures.clear()
            self.storage.clear()


def build_fake_handler(base_url_holder: dict):
    """Build the plumbing-fake request handler (labeled, in-memory)."""

    class FakeHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:
            pass

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            state: _FakeState = self.server.state  # type: ignore[attr-defined]
            path = self.path.split("?", 1)[0]
            if path.endswith("/healthz"):
                self._json(200, {"status": "ok"})
                return
            if "/captures/" in path and path.endswith("/events"):
                cid = path.split("/captures/")[1].rsplit("/events", 1)[0]
                cap = state.captures.get(cid)
                if cap is None:
                    self._json(404, {"errors": [{"code": "result_not_ready"}]})
                    return
                status = "complete" if cap["finalized"] else "capturing"
                self._json(
                    200,
                    {
                        "status": status,
                        "next": 1,
                        "events": [
                            {"seq": 1, "type": "state", "at": "2026-01-01T00:00:00Z"}
                        ],
                    },
                )
                return
            if "/captures/" in path and path.endswith("/result"):
                cid = path.split("/captures/")[1].rsplit("/result", 1)[0]
                cap = state.captures.get(cid)
                if cap is None:
                    self._json(404, {"errors": [{"code": "result_not_ready"}]})
                    return
                self._json(
                    200,
                    {
                        "schema_version": "0.4",
                        "captureId": cid,
                        "runId": "run-selfcheck",
                        "decision": "synthetic",
                    },
                )
                return
            self._json(404, {"errors": [{"code": "harness_no_route"}]})

        def do_POST(self) -> None:  # noqa: N802
            state: _FakeState = self.server.state  # type: ignore[attr-defined]
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            path = self.path.split("?", 1)[0]
            if path.rstrip("/").endswith("/captures"):
                cid, _ = state.new_capture(self.headers.get("Idempotency-Key"))
                self._json(201, {"captureId": cid, "status": "capturing"})
                return
            if path.endswith("/files"):
                # Storage URLs are rooted at the host the request reached —
                # the proxy, when a fault round fronts the origin — so signed
                # PUTs traverse the same path the register answer came from.
                # Direct (unproxied) requests carry Host: origin, which
                # reproduces the historical holder-based URL exactly.
                host_header = self.headers.get("Host")
                base = f"http://{host_header}" if host_header else base_url_holder["base"]
                cid = path.split("/captures/")[1].rsplit("/files", 1)[0]
                request = json.loads(body.decode("utf-8") or "{}")
                entries = []
                for i, f in enumerate(request.get("files", [])):
                    cap = state.captures.setdefault(
                        cid, {"files": {}, "committed": [], "finalized": False, "order": []}
                    )
                    cap.setdefault("order", []).append(f["path"])
                    state.captures[cid]["files"][f["path"]] = {
                        "expected_sha256": f.get("sha256"),
                        "expected_bytes": f.get("bytes"),
                        "uploaded": None,
                    }
                    entries.append(
                        {
                            "path": f["path"],
                            "state": "awaiting_upload",
                            "upload": {
                                "method": "PUT",
                                "url": f"{base}/storage/{cid}/{i}",
                                "headers": {"X-Synthetic-Signature": "plumbing-fake-marker"},
                            },
                        }
                    )
                self._json(200, {"files": entries})
                return
            if path.endswith(":commit"):
                cid = path.split("/captures/")[1].rsplit(":commit", 1)[0].removesuffix("/files")
                request = json.loads(body.decode("utf-8") or "{}")
                cap = state.captures.get(cid)
                if cap is None:
                    self._json(404, {"errors": [{"code": "result_not_ready"}]})
                    return
                committed, mismatch, not_found = [], [], []
                for f in request.get("files", []):
                    stored = state.storage.get(f"{cid}/{f['path']}")
                    if stored is None:
                        not_found.append(f["path"])
                    elif f.get("sha256") and stored != bytes.fromhex(""):
                        import hashlib as _h

                        if _h.sha256(stored).hexdigest() == f["sha256"]:
                            committed.append(f["path"])
                        else:
                            mismatch.append(f["path"])
                    else:
                        committed.append(f["path"])
                self._json(
                    200, {"committed": committed, "notFound": not_found, "mismatch": mismatch}
                )
                return
            if path.endswith("/finalize"):
                cid = path.split("/captures/")[1].rsplit("/finalize", 1)[0]
                cap = state.captures.get(cid)
                if cap is None:
                    self._json(404, {"errors": [{"code": "result_not_ready"}]})
                    return
                cap["finalized"] = True
                self._json(200, {"status": "complete", "runId": "run-selfcheck", "etaS": 0})
                return
            self._json(404, {"errors": [{"code": "harness_no_route"}]})

        def do_PUT(self) -> None:  # noqa: N802
            state: _FakeState = self.server.state  # type: ignore[attr-defined]
            path = self.path.split("?", 1)[0]
            if path.startswith("/storage/"):
                parts = path.split("/")
                cid, file_idx = parts[2], parts[3]
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                # map index back to path via capture registration order
                cap = state.captures.get(cid, {})
                paths = cap.get("order", sorted(cap.get("files", {}).keys()))
                if int(file_idx) < len(paths):
                    p = paths[int(file_idx)]
                    state.storage[f"{cid}/{p}"] = body
                    cap["files"][p]["uploaded"] = len(body)
                self._json(200, {"ok": True})
                return
            self._json(404, {"errors": [{"code": "harness_no_route"}]})

    return FakeHandler


def build_plan(seed: int = 1) -> CapturePlan:
    """Small in-code synthetic plan (same spirit as the fixture generator)."""

    files = []
    for i in range(2):
        path = f"synthetic/selfcheck{i}.syn"
        content = (f"synthetic-noise-{seed}-{i}-" * 64).encode("utf-8")
        files.append((path, content))
    packet = json.dumps(
        {"schema_version": "0.4", "packet_id": f"selfcheck-{seed}", "note": "synthetic"}
    ).encode("utf-8")
    return CapturePlan(
        packet_id=f"selfcheck-{seed}",
        create_request={"source": "selfcheck-synthetic", "schema_version": "0.4"},
        files=files,
        roles={p: "mesh" for p, _ in files},
        packet=packet,
    )


_FAULT_ROUND_SPECS: tuple[dict, ...] = (
    {
        "prefix": "trunc.",
        "faults": {"commit": {"kind": "truncate_response", "bytes": 8}},
        "step": "commit",
        "signature": "IncompleteRead",
        "expect_not_pass": ("lifecycle.commit",),
    },
    {
        "prefix": "xurl.",
        "faults": {"upload": {"kind": "expired_url"}},
        "step": "upload",
        "signature": "http 403",
        "expect_not_pass": ("lifecycle.upload", "lifecycle.commit"),
    },
    {
        "prefix": "nf404.",
        "faults": {"commit": {"kind": "capture_not_found"}},
        "step": "commit",
        "signature": "http 404",
        "expect_not_pass": ("lifecycle.commit",),
    },
    {
        "prefix": "rfin.",
        "faults": {"events": {"kind": "retry_finalize", "recover_after": 1}},
        "step": "events",
        "signature": None,
        "expect_not_pass": (),
        "require_event_type": "failed",
    },
)


def selfcheck_manifest_commands(restart_command: str | None) -> list[str]:
    """The manifest's commands field; a restart command is redacted first."""
    commands = ["hunter-selfcheck"]
    if restart_command:
        commands.append("--restart-command=" + redact_text(restart_command))
    return commands


def _safe(result, key: str):
    obs = result.observation
    if obs is None:
        return None
    return (obs.safe_values or {}).get(key)


def fault_round_problems(
    sink_lines: list[dict],
    injected_counts: dict[str, int],
    spec: dict,
) -> list[str]:
    """Evaluate one fault round: the fault must have FIRED and been DETECTED.

    A round that records the faulted step as PASS is a blind spot (the
    harness met the fault and reported success); a round whose fault never
    fired proves nothing. Both are self-check problems.
    """
    prefix = spec["prefix"]
    step = spec["step"]
    problems: list[str] = []
    if injected_counts.get(step, 0) <= 0:
        problems.append(
            f"{prefix}: fault never fired at {step} (injected: {injected_counts})"
        )
        return problems
    line = next((e for e in sink_lines if e["id"] == f"{prefix}lifecycle.{step}"), None)
    if line is None:
        problems.append(f"{prefix}: no receipt row for lifecycle.{step}")
        return problems
    signature = spec.get("signature")
    if signature is not None:
        non_pass = line["status"] in (Status.FAIL.value, Status.BLOCKED.value)
        if not non_pass or signature not in str(line["observed"]):
            problems.append(
                f"{prefix}: {step} expected a non-pass row naming {signature!r}, "
                f"got {line['status']} ({line['observed']!r})"
            )
    require_event_type = spec.get("require_event_type")
    if require_event_type is not None:
        observation = line.get("observation")
        events = (observation.safe_values or {}).get("events") if observation else None
        types = (
            [e.get("type") for e in events if isinstance(e, dict)]
            if isinstance(events, list)
            else []
        )
        if require_event_type not in types:
            problems.append(
                f"{prefix}: events observation lacks a {require_event_type!r} event "
                f"(saw types: {types})"
            )
    return problems


def run_restart_control(
    adapter: HunterIngestAdapter,
    plan: CapturePlan,
    *,
    restart_command: str,
    reset_state: Callable[[], None] | None = None,
    poll_interval_s: float = 0.2,
    result_budget_s: float = 15.0,
) -> tuple[Status, str]:
    """Execute a real restart and prove the client handles both fates.

    The restart runs the operator's command (shell form, 60s cap) and, when
    reset_state is given, wipes the fake's in-process state — the simulated
    process memory loss. The control then requires one of the two published
    outcomes: the restarted backend still answers for the earlier capture,
    or it answers 404 and a full lifecycle re-send under the SAME
    idempotency key completes. Anything else fails the control.
    """
    notes: list[str] = []

    res = adapter.health()
    if res.error:
        return Status.BLOCKED, f"endpoint unreachable pre-restart: {step_observed(res)}"
    res = adapter.create(plan.create_request, plan.packet_id)
    capture_id = _safe(res, "captureId")
    if res.error or not capture_id:
        return Status.BLOCKED, f"create unavailable pre-restart: {step_observed(res)}"
    res = adapter.register(capture_id, plan.file_descriptors)
    if res.error or not (res.http_status and 200 <= res.http_status < 300):
        return Status.BLOCKED, f"register unavailable pre-restart: {step_observed(res)}"
    targets = (res.context or {}).get("upload_targets") or []
    for (path, content), target in zip(plan.files, targets, strict=False):
        url = target.get("url") if isinstance(target, dict) else None
        if not url:
            return Status.BLOCKED, f"pre-restart: no signed target for {path}"
        res = adapter.upload(capture_id, path, content, url, target.get("headers"))
        if res.error or not (res.http_status and 200 <= res.http_status < 300):
            return (
                Status.BLOCKED,
                f"pre-restart upload failed ({path}): {step_observed(res)}",
            )

    try:
        completed = subprocess.run(
            restart_command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Status.BLOCKED, "restart command did not finish within 60s"
    notes.append(f"restart command exit={completed.returncode}")
    if reset_state is not None:
        reset_state()
        notes.append("in-process fake state wiped (simulated process memory loss)")
    if completed.returncode != 0:
        notes.append("restart command failed; recovery not attempted")
        return Status.BLOCKED, "; ".join(notes)

    res = adapter.events(capture_id, 0, int(poll_interval_s))
    if res.error:
        notes.append(f"old-capture probe transport error: {res.error}")
        return Status.BLOCKED, "; ".join(notes)
    if res.http_status is not None and 200 <= res.http_status < 300:
        notes.append(
            f"old capture {capture_id} answers 2xx post-restart (state preserved)"
        )
    elif res.http_status == 404:
        notes.append(f"old capture {capture_id} answers 404 post-restart (state loss)")
    else:
        notes.append(f"old-capture probe unexpected: {step_observed(res)}")
        return Status.BLOCKED, "; ".join(notes)

    # recovery under the SAME idempotency key
    res = adapter.create(plan.create_request, plan.packet_id)
    recovery_id = _safe(res, "captureId")
    if res.error or not recovery_id:
        return (
            Status.FAIL,
            "; ".join(notes + [f"recovery create failed: {step_observed(res)}"]),
        )
    notes.append(f"recovery capture {recovery_id}")

    res = adapter.register(recovery_id, plan.file_descriptors)
    if res.error or not (res.http_status and 200 <= res.http_status < 300):
        return (
            Status.FAIL,
            "; ".join(notes + [f"recovery register failed: {step_observed(res)}"]),
        )
    targets = (res.context or {}).get("upload_targets") or []
    for (path, content), target in zip(plan.files, targets, strict=False):
        url = target.get("url") if isinstance(target, dict) else None
        if not url:
            return Status.FAIL, "; ".join(notes + [f"recovery: no signed target for {path}"])
        res = adapter.upload(recovery_id, path, content, url, target.get("headers"))
        if res.error or not (res.http_status and 200 <= res.http_status < 300):
            return (
                Status.FAIL,
                "; ".join(notes + [f"recovery upload failed ({path}): {step_observed(res)}"]),
            )

    res = adapter.commit(recovery_id, plan.file_descriptors)
    committed = _safe(res, "committed")
    if res.error or not (res.http_status and 200 <= res.http_status < 300 and committed):
        return (
            Status.FAIL,
            "; ".join(notes + [f"recovery commit failed: {step_observed(res)}"]),
        )

    res = adapter.finalize(recovery_id, plan.packet, plan.packet_id)
    if res.error or not (res.http_status and 200 <= res.http_status < 300):
        return (
            Status.FAIL,
            "; ".join(notes + [f"recovery finalize failed: {step_observed(res)}"]),
        )

    deadline = time.monotonic() + result_budget_s
    terminal = False
    status_now = None
    while time.monotonic() < deadline:
        res = adapter.events(recovery_id, 0, int(poll_interval_s))
        if res.error:
            return (
                Status.FAIL,
                "; ".join(notes + [f"recovery events transport error: {res.error}"]),
            )
        status_now = _safe(res, "status")
        if status_now in TERMINAL_STATUSES:
            terminal = True
            break
        time.sleep(poll_interval_s)
    if not terminal:
        return (
            Status.FAIL,
            "; ".join(
                notes
                + [f"recovery events never reached a terminal status (last: {status_now!r})"]
            ),
        )

    res = adapter.result(recovery_id)
    if res.error or not (res.http_status and 200 <= res.http_status < 300):
        return (
            Status.FAIL,
            "; ".join(notes + [f"recovery result read failed: {step_observed(res)}"]),
        )
    notes.append("recovery lifecycle complete; result read 2xx")
    return Status.PASS, "; ".join(notes)


def selfcheck(
    out_root: str | None = None, restart_command: str | None = None
) -> tuple[int, list[str]]:
    problems: list[str] = []
    notes_out: list[str] = []

    holder = {"base": ""}
    state = _FakeState()
    handler = build_fake_handler(holder)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]

    from .http_client import DirectHttpClient as _C

    # base URL needs the port; the handler closes over the holder dict
    base = f"http://127.0.0.1:{port}"
    holder["base"] = base
    client = _C(base, timeout_s=10.0)

    plan = build_plan()
    # The in-process fake is FAKE_HUNTER-class plumbing infrastructure, never
    # REAL_MIRROR_CPU: it implements the same published lifecycle, but no row
    # it produces may read as real-backend evidence.
    transport = Transport.FAKE_HUNTER
    sink: list = []

    run_lifecycle(client, plan, transport, sink, result_budget_s=15.0, poll_interval_s=0.2)
    run_controls(client, plan, transport, sink)

    def plan_variant(suffix: str) -> CapturePlan:
        return CapturePlan(
            packet_id=plan.packet_id + suffix,
            create_request=dict(plan.create_request),
            files=list(plan.files),
            roles=dict(plan.roles),
            packet=plan.packet,
        )

    # fault-proxy FAKE_HUNTER rounds; prefixed ids keep these lines distinct
    # from the direct round's.
    from .faultproxy import serve

    proxy = serve(base, {"create": {"kind": "delay", "ms": 100}})
    try:
        proxied = _C(proxy.base_url, timeout_s=10.0)
        run_lifecycle(
            proxied,
            plan_variant("-fh"),
            Transport.FAKE_HUNTER,
            PrefixedSink(sink, "fault."),
            result_budget_s=15.0,
            poll_interval_s=0.2,
        )
    finally:
        proxy.stop()

    # decisive fault rounds: each oracle must fire AND be detected — a round
    # whose fault never fired, or whose faulted step still reads PASS, is a
    # self-check problem (blind-spot proof).
    for spec in _FAULT_ROUND_SPECS:
        proxy = serve(base, spec["faults"])
        try:
            proxied = _C(proxy.base_url, timeout_s=10.0)
            run_lifecycle(
                proxied,
                plan_variant("-" + spec["prefix"].rstrip(".")),
                Transport.FAKE_HUNTER,
                PrefixedSink(sink, spec["prefix"]),
                result_budget_s=15.0,
                poll_interval_s=0.2,
            )
        finally:
            proxy.stop()
        problems.extend(fault_round_problems(sink, proxy.injected_counts, spec))

    # GPU arms: named, never executed (NOT_RUN vocabulary rows).
    gpu_stub_lines(sink)

    # restart-recovery (while the fake is still serving): execute the
    # operator's restart command, wipe the fake's in-process state (the
    # simulated memory loss), and require one of the two published
    # outcomes. The NOT_RUN stub row run_controls wrote is replaced by the
    # real result.
    if restart_command:
        sink[:] = [
            line
            for line in sink
            if not (
                line["id"] == "control.restart-recovery"
                and line["transport"] == Transport.FAKE_HUNTER.value
            )
        ]
        status_r, observed_r = run_restart_control(
            _C(base, timeout_s=10.0),
            plan,
            restart_command=restart_command,
            reset_state=state.hard_reset,
        )
        record(
            sink,
            Transport.FAKE_HUNTER,
            "control.restart-recovery",
            "published CaptureUploader: a restarted backend either still answers "
            "for existing captures (2xx) or answers capture_not_found, which the "
            "client recovers by re-creating under the same idempotency key and "
            "re-sending every file",
            "restart executes; the pre-restart capture's fate is observed; the "
            "recovery lifecycle completes",
            f"command {redact_text(restart_command)!r}; {observed_r}",
            status_r,
        )

    server.shutdown()

    # -- receipt assembly and assertions -----------------------------------
    out = Path(out_root or (f"{_TEMP}/hli-selfcheck"))
    run_dir = fresh_run_dir(out, "selfcheck")

    from .adapter import INTERFACE_VERSION
    from .receipts import (
        Check,
        ChecksWriter,
        verify_run_integrity,
        write_run_manifest,
        write_summary,
    )
    from .runner import LEAD_QUESTIONS, ORIGIN_NOTE

    checks = [
        Check(
            id=line["id"],
            transport=Transport(line["transport"]),
            oracle=line["oracle"],
            expected=line["expected"],
            observed=line["observed"],
            status=Status(line["status"]),
            evidence=line.get("evidence"),
            observation=line.get("observation"),
        )
        for line in sink
    ]
    write_run_manifest(
        run_dir,
        endpoint=base,
        backend_ref=None,
        backend_mode="plumbing-fake (self-check only; never evidence)",
        client_ref=None,
        adapter_interface=INTERFACE_VERSION,
        fixture_identity="selfcheck build_plan",
        seed=1,
        commands=selfcheck_manifest_commands(restart_command),
        label="selfcheck",
        origin_note=ORIGIN_NOTE,
    )
    writer = ChecksWriter(run_dir)
    for c in checks:
        writer.write(c)
    writer.close()

    statuses = {(c.transport.value, c.id): c.status for c in checks}

    def expect_pass(tid: str, cid: str) -> None:
        got = statuses.get((tid, cid))
        if got is not Status.PASS:
            line = next(
                (e for e in sink if e["id"] == cid and e["transport"] == tid), None
            )
            problems.append(f"{tid}/{cid}: expected PASS, got {got} ({line})")

    def expect_not_run(tid: str, cid: str) -> None:
        got = statuses.get((tid, cid))
        if got is not Status.NOT_RUN:
            problems.append(f"{tid}/{cid}: expected NOT_RUN, got {got}")

    _LIFECYCLE_IDS = (
        "lifecycle.health",
        "lifecycle.create",
        "lifecycle.register",
        "lifecycle.upload",
        "lifecycle.commit",
        "lifecycle.finalize",
        "lifecycle.events",
        "lifecycle.result",
    )
    for cid in _LIFECYCLE_IDS:
        expect_pass(Transport.FAKE_HUNTER.value, cid)
    for cid in _LIFECYCLE_IDS:
        expect_pass(Transport.FAKE_HUNTER.value, "fault." + cid)

    def expect_not_pass(tid: str, cid: str) -> None:
        got = statuses.get((tid, cid))
        if got not in (Status.FAIL, Status.BLOCKED):
            line = next(
                (e for e in sink if e["id"] == cid and e["transport"] == tid), None
            )
            problems.append(f"{tid}/{cid}: expected FAIL/BLOCKED, got {got} ({line})")

    for spec in _FAULT_ROUND_SPECS:
        for cid in _LIFECYCLE_IDS:
            if cid in spec["expect_not_pass"]:
                expect_not_pass(Transport.FAKE_HUNTER.value, spec["prefix"] + cid)
            else:
                expect_pass(Transport.FAKE_HUNTER.value, spec["prefix"] + cid)
    for cid in (
        "control.idempotent-create",
        "control.corrupt-upload",
        "control.truncated-upload",
        "control.wrong-capture",
    ):
        expect_pass(Transport.FAKE_HUNTER.value, cid)
    if restart_command:
        got = statuses.get((Transport.FAKE_HUNTER.value, "control.restart-recovery"))
        if got is not Status.PASS:
            problems.append(f"restart-recovery: expected PASS, got {got}")
    else:
        # without a restart command the control stays honestly NOT_RUN
        expect_not_run(Transport.FAKE_HUNTER.value, "control.restart-recovery")

    for arm in ("objects", "vlm", "dense"):
        expect_not_run(Transport.GPU_STUB.value, f"gpu-stub.{arm}")

    # privacy sweep over the receipts
    receipt_text = (run_dir / "checks.jsonl").read_text(encoding="utf-8")
    for forbidden in (
        "X-Synthetic-Signature",  # signed-header value from the plumbing fake
        "plumbing-fake-marker",
        "/storage/",  # signed URL path fragment
        "synthetic-noise",  # file content
        "content_hex",
    ):
        if forbidden in receipt_text:
            problems.append(f"privacy: receipt contains {forbidden!r}")
    if "body_sha256=" not in receipt_text:
        problems.append("privacy: receipts missing body digests (digests must be present)")

    # frozen expected trace comparison (REAL_MIRROR_CPU round); advisory
    # (provisional-line) mismatches are reported, never hard failures.
    # Resolved package-relative first so a non-editable (wheel) install
    # finds the trace it ships; the repo-root fallback keeps checkouts
    # that predate the move working.
    try:
        trace_path = frozen_trace_path()
    except FileNotFoundError as exc:
        problems.append(str(exc))
    else:
        mismatches = compare_trace(checks, trace_path, transport=Transport.FAKE_HUNTER)
        hard = [m for m in mismatches if not m.startswith("advisory: ")]
        advisory = [m for m in mismatches if m.startswith("advisory: ")]
        problems.extend(hard)
        notes_out.extend(advisory)

    # Receipt screening + integrity (fail-closed): write_summary screens
    # every artifact post-write, records the outcome in privacy-screen.json,
    # chains and seals the run. Its findings are consumed here and the
    # integrity verdict over the sealed receipts is asserted too.
    problems.extend(
        write_summary(
            run_dir,
            checks,
            lead_questions=LEAD_QUESTIONS,
            blocked=problems,
            notes=notes_out,
        )
    )
    problems.extend(verify_run_integrity(run_dir))

    print(f"selfcheck run dir: {run_dir}")
    counts: dict[str, int] = {}
    for c in checks:
        counts[c.status.value] = counts.get(c.status.value, 0) + 1
    print("counts: " + json.dumps(counts, sort_keys=True))

    return (1 if problems else 0), problems


def main() -> int:
    parser = argparse.ArgumentParser(prog="hunter-selfcheck", description=__doc__)
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--restart-command",
        default=None,
        help="shell command executed for the restart-recovery control; the "
        "in-process fake state is wiped to simulate process memory loss",
    )
    args = parser.parse_args()
    code, problems = selfcheck(args.out, restart_command=args.restart_command)
    for p in problems:
        print(f"SELF-CHECK PROBLEM: {redact_text(p)}")
    print("selfcheck: PASS" if code == 0 else "selfcheck: FAIL")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
