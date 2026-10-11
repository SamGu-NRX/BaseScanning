"""Harness self-check: the runner, end to end, against an in-process fake.

The fake origin here is PLUMBING, clearly labeled. It proves the harness
runs and its receipts are well-formed and privacy-clean. It is NEVER
evidence about any backend; backend claims only ever come from runs
against a real local endpoint, with the transport label to prove it.

What is verified:
  1. the runner completes end to end (exit 0) including the FAKE_HUNTER
     fault round through the local proxy;
  2. every lifecycle.* and control.* check lands PASS except the
     deliberately NOT_RUN restart-recovery and GPU_STUB lines;
  3. receipts exist, parse, and are internally consistent;
  4. the privacy sweep: no signed URL, no synthetic signature header
     value, no file content, and no request header material appears in
     any receipt; digests and field names do.

Usage: hunter-selfcheck [--out /tmp/hli-selfcheck]
Exit 0 when all assertions hold, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .modes import Status, Transport
from .receipts import fresh_run_dir
from .scenarios import CapturePlan
from .scenarios.controls import run_controls
from .scenarios.lifecycle import run_lifecycle
from .traces import compare_trace

_TEMP = tempfile.gettempdir()


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
                base = base_url_holder["base"]
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


def selfcheck(out_root: str | None = None) -> tuple[int, list[str]]:
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
    transport = Transport.REAL_MIRROR_CPU
    sink: list = []

    run_lifecycle(client, plan, transport, sink, result_budget_s=15.0, poll_interval_s=0.2)
    run_controls(client, plan, transport, sink)

    # plumbing-fake FAKE_HUNTER round through the proxy
    from .faultproxy import serve

    proxy = serve(base, {"create": {"kind": "delay", "ms": 100}})
    try:
        proxied = _C(proxy.base_url, timeout_s=10.0)
        plan_fh = CapturePlan(
            packet_id=plan.packet_id + "-fh",
            create_request=dict(plan.create_request),
            files=list(plan.files),
            roles=dict(plan.roles),
            packet=plan.packet,
        )
        run_lifecycle(
            proxied,
            plan_fh,
            Transport.FAKE_HUNTER,
            sink,
            result_budget_s=15.0,
            poll_interval_s=0.2,
        )
    finally:
        proxy.stop()

    server.shutdown()

    # -- receipt assembly and assertions -----------------------------------
    out = Path(out_root or (f"{_TEMP}/hli-selfcheck"))
    run_dir = fresh_run_dir(out, "selfcheck")

    from .adapter import INTERFACE_VERSION
    from .receipts import Check, ChecksWriter, write_run_manifest, write_summary
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
        commands=["hunter-selfcheck"],
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

    for tid in (Transport.REAL_MIRROR_CPU.value, Transport.FAKE_HUNTER.value):
        for cid in (
            "lifecycle.health",
            "lifecycle.create",
            "lifecycle.register",
            "lifecycle.upload",
            "lifecycle.commit",
            "lifecycle.finalize",
            "lifecycle.events",
            "lifecycle.result",
        ):
            expect_pass(tid, cid)
    for cid in (
        "control.idempotent-create",
        "control.corrupt-upload",
        "control.truncated-upload",
        "control.wrong-capture",
    ):
        expect_pass(Transport.REAL_MIRROR_CPU.value, cid)
    expect_not_run(Transport.REAL_MIRROR_CPU.value, "control.restart-recovery")

    for arm in ("objects", "vlm", "dense"):
        got = statuses.get(("GPU_STUB", f"gpu-stub.{arm}"))
        # selfcheck drives scenarios directly (no runner GPU lines)
        if got is not None and got is not Status.NOT_RUN:
            problems.append(f"gpu-stub.{arm}: expected NOT_RUN, got {got}")

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
    # (provisional-line) mismatches are reported, never hard failures
    trace_path = Path(__file__).resolve().parents[2] / "traces" / "expected-lifecycle-v1.jsonl"
    if trace_path.exists():
        mismatches = compare_trace(checks, trace_path, transport=Transport.REAL_MIRROR_CPU)
        hard = [m for m in mismatches if not m.startswith("advisory: ")]
        advisory = [m for m in mismatches if m.startswith("advisory: ")]
        problems.extend(hard)
        notes_out.extend(advisory)
    else:
        problems.append(f"frozen trace missing at {trace_path}")

    write_summary(
        run_dir,
        checks,
        lead_questions=LEAD_QUESTIONS,
        blocked=problems,
        notes=notes_out,
    )

    print(f"selfcheck run dir: {run_dir}")
    counts: dict[str, int] = {}
    for c in checks:
        counts[c.status.value] = counts.get(c.status.value, 0) + 1
    print("counts: " + json.dumps(counts, sort_keys=True))
    return (1 if problems else 0), problems


def main() -> int:
    parser = argparse.ArgumentParser(prog="hunter-selfcheck", description=__doc__)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    code, problems = selfcheck(args.out)
    for p in problems:
        print(f"SELF-CHECK PROBLEM: {p}")
    print("selfcheck: PASS" if code == 0 else "selfcheck: FAIL")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
