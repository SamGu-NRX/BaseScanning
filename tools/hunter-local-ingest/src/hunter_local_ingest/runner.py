"""Runner: end-to-end harness execution with full receipts.

Usage (against a live local endpoint):
  python -m hunter_local_ingest.runner \\
      --endpoint http://127.0.0.1:8000/v1 \\
      --plan-file runs/fixtures/synthetic-plan.json \\
      --backend-mode REAL_MIRROR_CPU \\
      --backend-ref <commit-sha> \\
      --client-ref <harness-sha> \\
      --seed 1 --out runs --label real-local

Exit codes: 0 all checks passed; 1 at least one FAIL (or BLOCKED on a
passing-capable check); 2 endpoint unreachable / harness blocked; 3 bad
invocation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .adapter import INTERFACE_VERSION
from .faultproxy import serve, wait_tcp
from .http_client import DirectHttpClient
from .modes import Status, Transport
from .receipts import (
    ChecksSink,
    ChecksWriter,
    fresh_run_dir,
    write_run_manifest,
    write_summary,
)
from .scenarios import (
    CapturePlan,
    PrefixedSink,
    derived_control_identity,
    gpu_stub_lines,
    record,
    unreachable_lines,
)
from .scenarios.controls import run_controls
from .scenarios.lifecycle import run_lifecycle
from .traces import compare_trace, preferred_trace_path

ORIGIN_NOTE = (
    "Derived from published client behavior in HouseScanKit upload sources @ alpha "
    "263827ef and public result-schema documentation; refined only by sanitized "
    "local-run observations. No private source, schema, spec, log or credential "
    "material appears in this package or its receipts."
)

LEAD_QUESTIONS = [
    "Amendment semantics: does the published contract allow amending a registered "
    "capture, and with which route/headers? (open question, no invented field or enum)",
    "Local-run auth mode: which credential form does the local backend expect from the "
    "published client? (open question, no invented header)",
    "Clock/expiry authority: whose clock governs capture expiry on the local backend? "
    "(open question, no invented retry budget)",
    "Homeowner-text redaction: confirm the receipts' value allowlist matches the "
    "privacy bar for any homeowner-adjacent response fields. (open question)",
]


def _load_plan(path: Path, seed: int) -> CapturePlan:
    try:
        # JSONDecodeError is a ValueError, so malformed JSON exits 3 too.
        raw = json.loads(path.read_text(encoding="utf-8"))
        plan_raw = raw["plan"]
        files = [(f["path"], bytes.fromhex(f["content_hex"])) for f in plan_raw["files"]]
        return CapturePlan(
            packet_id=plan_raw["packet_id"],
            create_request=plan_raw["create_request"],
            files=files,
            roles=plan_raw.get("roles", {}),
            packet=bytes.fromhex(plan_raw["packet_hex"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(
            f"3: plan file {path} does not match the generator schema ({exc}); "
            "produce it with fixtures/gen_synthetic_packet.py"
        ) from exc


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hunter-local-ingest", description=__doc__)
    parser.add_argument("--endpoint", required=True, help="capture API base URL (…/v1)")
    parser.add_argument("--plan-file", required=True, help="plan JSON from the fixture generator")
    parser.add_argument("--out", default="runs", help="output root for run directories")
    parser.add_argument("--label", default="run", help="label for the run directory")
    parser.add_argument(
        "--backend-mode",
        required=True,
        choices=[
            m.value
            for m in (
                Transport.REAL_MIRROR_CPU,
                Transport.FAKE_HUNTER,
                Transport.REAL_API,
                Transport.PREPARED,
            )
        ],
        help=(
            "honest transport class of the endpoint under test; the operator declares it "
            "and every receipt row of the main round carries it"
        ),
    )
    parser.add_argument("--backend-ref", default=None, help="commit SHA of the backend under test")
    parser.add_argument("--client-ref", default=None, help="commit SHA of the harness client")
    parser.add_argument("--seed", type=int, default=1, help="fixture seed for reproducibility")
    parser.add_argument("--result-budget-s", type=float, default=1500.0, help="polling budget")
    parser.add_argument("--poll-interval-s", type=float, default=2.0)
    parser.add_argument(
        "--restart-command", default=None, help="command that restarts the local backend"
    )
    parser.add_argument(
        "--with-faults", action="store_true", help="also run the FAKE_HUNTER fault round"
    )
    args = parser.parse_args(argv)

    plan = _load_plan(Path(args.plan_file), args.seed)
    round_transport = Transport(args.backend_mode)

    run_dir = fresh_run_dir(Path(args.out), args.label)
    commands = [
        "hunter-local-ingest " + " ".join(argv or sys.argv[1:]),
    ]
    write_run_manifest(
        run_dir,
        endpoint=args.endpoint,
        backend_ref=args.backend_ref,
        backend_mode=args.backend_mode,
        client_ref=args.client_ref,
        adapter_interface=INTERFACE_VERSION,
        fixture_identity=f"gen_synthetic_packet seed={args.seed}",
        seed=args.seed,
        commands=commands,
        label=args.label,
        origin_note=ORIGIN_NOTE,
    )

    writer = ChecksWriter(run_dir)
    sink = ChecksSink(writer)

    # endpoint reachability gate
    if not wait_tcp(args.endpoint, timeout_s=10.0):
        detail = f"endpoint {args.endpoint} unreachable (TCP) within 10s"
        for line in unreachable_lines(detail, round_transport):
            sink.append(line)
        gpu_stub_lines(sink)
        write_summary(run_dir, sink.checks, lead_questions=LEAD_QUESTIONS, blocked=[detail])
        writer.close()
        print(f"BLOCKED: {detail}")
        return 2

    client = DirectHttpClient(args.endpoint)

    # Main round: the endpoint under test, at the transport class the
    # operator declared for it.
    run_lifecycle(
        client,
        plan,
        round_transport,
        sink,
        result_budget_s=args.result_budget_s,
        poll_interval_s=args.poll_interval_s,
    )
    run_controls(
        client,
        plan,
        round_transport,
        sink,
        restart_command=args.restart_command,
    )

    # Frozen-trace conformance on a real run (recon §3q): diff the main
    # round's receipts against the newest frozen trace. A hard
    # disagreement FAILs the run; advisory (provisional-line)
    # disagreements are recorded without flipping the verdict; a missing
    # trace is NOT_RUN, never a silent pass. FAKE_HUNTER rounds are
    # excluded — the selfcheck owns fake-vs-trace comparison.
    if round_transport is not Transport.FAKE_HUNTER:
        trace_path = preferred_trace_path(Path(__file__).resolve().parents[2] / "traces")
        if trace_path.exists():
            problems = compare_trace(sink.checks, trace_path, round_transport)
            hard = [p for p in problems if not p.startswith("advisory:")]
            advisory = [p for p in problems if p.startswith("advisory:")]
            oracle = (
                f"run receipts match the frozen trace {trace_path.name} "
                "(status class + pinned response fields)"
            )
            if hard:
                record(
                    sink,
                    round_transport,
                    "trace.conformance",
                    oracle,
                    "no hard disagreements with the frozen trace",
                    f"{len(hard)} hard: " + "; ".join(hard[:5]),
                    Status.FAIL,
                )
            else:
                record(
                    sink,
                    round_transport,
                    "trace.conformance",
                    oracle,
                    "no hard disagreements with the frozen trace",
                    "; ".join(advisory) if advisory else "no disagreements",
                    Status.PASS,
                )
        else:
            record(
                sink,
                round_transport,
                "trace.conformance",
                "run receipts compared against the frozen trace",
                f"frozen trace present at {trace_path}",
                "frozen trace missing on this install",
                Status.NOT_RUN,
            )

    # GPU_STUB vocabulary lines: named arms, never executed unless authorized.
    gpu_stub_lines(sink)

    # FAKE_HUNTER fault round through the proxy, same client, labeled. A
    # distinct packet id keeps it an independent capture rather than an
    # accidental replay of the real round; the prefixed sink keeps its
    # receipt lines distinct from the main round's.
    if args.with_faults:
        # Isolated identity (recon §3s): the fault round creates its own
        # capture; the frozen packet's packetId is rewritten to match so
        # the finalize body names the same capture as the create.
        fh_request, fh_id = derived_control_identity(plan, "-fh")
        packet_obj = json.loads(plan.packet.decode("utf-8"))
        packet_obj["packetId"] = fh_id
        fh_packet = json.dumps(packet_obj, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        plan_fh = CapturePlan(
            packet_id=fh_id,
            create_request=fh_request,
            files=list(plan.files),
            roles=dict(plan.roles),
            packet=fh_packet,
        )
        proxy = serve(args.endpoint, {"create": {"kind": "delay", "ms": 300}})
        try:
            proxied = DirectHttpClient(proxy.base_url)
            run_lifecycle(
                proxied,
                plan_fh,
                Transport.FAKE_HUNTER,
                PrefixedSink(sink, "fault."),
                result_budget_s=min(args.result_budget_s, 120.0),
                poll_interval_s=args.poll_interval_s,
            )
        finally:
            proxy.stop()

        # Named arm that cannot run yet (recon §3t): the fault proxy fronts
        # plain HTTP origins only. DirectHttpClient opens raw HTTP(S)
        # connections with no CONNECT-tunnel client, and the proxy neither
        # terminates TLS nor forwards an upstream CONNECT — so an HTTPS
        # origin cannot be fronted, and a truncated-response framing fault
        # over TLS is unreachable until both exist. Recorded BLOCKED, never
        # silently skipped, so no fault receipt can claim https coverage.
        sink.append(
            {
                "id": "fault.https-proxy",
                "transport": Transport.FAKE_HUNTER.value,
                "oracle": (
                    "fault-suite coverage: an HTTPS origin fronted by the fault"
                    " proxy must reproduce the same fault kinds over TLS"
                ),
                "expected": "the https-proxy fault arm runs",
                "observed": (
                    "BLOCKED until a CONNECT-tunneling client transport and a"
                    " TLS-capable fault proxy exist: the fault proxy fronts"
                    " plain HTTP origins only (no TLS termination, no upstream"
                    " CONNECT), and DirectHttpClient has no proxy support"
                ),
                "status": Status.BLOCKED.value,
            }
        )

    writer.close()

    checks = sink.checks
    any_fail = any(c.status is Status.FAIL for c in checks)
    any_blocked = any(c.status is Status.BLOCKED for c in checks)
    blocked_notes = [
        f"{c.id}: {c.observed}" for c in checks if c.status is Status.BLOCKED
    ]
    write_summary(
        run_dir,
        checks,
        lead_questions=LEAD_QUESTIONS,
        blocked=blocked_notes,
    )
    print(f"run complete: {run_dir}")
    counts = {}
    for c in checks:
        counts[c.status.value] = counts.get(c.status.value, 0) + 1
    print("counts: " + json.dumps(counts, sort_keys=True))
    if any_fail:
        return 1
    if any_blocked:
        return 2
    return 0


def main() -> int:
    try:
        return run()
    except SystemExit as exc:  # argparse + plan errors
        if isinstance(exc.code, int):
            return exc.code
        if exc.code:
            # The loader's diagnostic would otherwise vanish with the exit.
            print(exc.code, file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
