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
from .receipts import Check, ChecksWriter, fresh_run_dir, write_run_manifest, write_summary
from .scenarios import CapturePlan
from .scenarios.controls import run_controls
from .scenarios.lifecycle import run_lifecycle

GPU_STUB_ARMS = ("objects", "vlm", "dense")

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
    raw = json.loads(path.read_text(encoding="utf-8"))
    try:
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


def _unreachable_lines(detail: str) -> list[dict]:
    """BLOCKED receipts when the endpoint never came up."""
    return [
        {
            "id": "lifecycle.health",
            "transport": "REAL_MIRROR_CPU",
            "oracle": "repo README run instructions: GET /healthz before use",
            "expected": "HTTP 200 with a status field",
            "observed": detail,
            "status": "BLOCKED",
            "evidence": None,
        },
        {
            "id": "lifecycle.create",
            "transport": "REAL_MIRROR_CPU",
            "oracle": "published CaptureUploader: create with Idempotency-Key=packetId",
            "expected": "2xx with captureId",
            "observed": detail,
            "status": "BLOCKED",
            "evidence": None,
        },
        {
            "id": "lifecycle.result",
            "transport": "REAL_MIRROR_CPU",
            "oracle": "public result-schema doc",
            "expected": "schema-valid answer",
            "observed": detail,
            "status": "BLOCKED",
            "evidence": None,
        },
    ]


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hunter-local-ingest", description=__doc__)
    parser.add_argument("--endpoint", required=True, help="capture API base URL (…/v1)")
    parser.add_argument("--plan-file", required=True, help="plan JSON from the fixture generator")
    parser.add_argument("--out", default="runs", help="output root for run directories")
    parser.add_argument("--label", default="run", help="label for the run directory")
    parser.add_argument(
        "--backend-mode", default="unverified", help="backend mode string for the manifest"
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

    checks: list[Check] = []
    writer = ChecksWriter(run_dir)

    def sink(line: dict) -> None:
        checks.append(
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
        )
        writer.write(checks[-1])

    # endpoint reachability gate
    if not wait_tcp(args.endpoint, timeout_s=10.0):
        detail = f"endpoint {args.endpoint} unreachable (TCP) within 10s"
        for line in _unreachable_lines(detail):
            checks.append(
                Check(
                    id=line["id"],
                    transport=Transport(line["transport"]),
                    oracle=line["oracle"],
                    expected=line["expected"],
                    observed=line["observed"],
                    status=Status(line["status"]),
                )
            )
            writer.write(checks[-1])
        _gpu_stub_lines(sink)
        write_summary(run_dir, checks, lead_questions=LEAD_QUESTIONS, blocked=[detail])
        writer.close()
        print(f"BLOCKED: {detail}")
        return 2



    client = DirectHttpClient(args.endpoint)

    # REAL_MIRROR_CPU round: the real (or GPU-stubbed) local backend.
    run_lifecycle(
        client,
        plan,
        Transport.REAL_MIRROR_CPU,
        sink,
        result_budget_s=args.result_budget_s,
        poll_interval_s=args.poll_interval_s,
    )
    run_controls(
        client,
        plan,
        Transport.REAL_MIRROR_CPU,
        sink,
        restart_command=args.restart_command,
    )

    # GPU_STUB vocabulary lines: named arms, never executed unless authorized.
    _gpu_stub_lines(sink)

    # FAKE_HUNTER fault round through the proxy, same client, labeled. A
    # distinct packet id keeps it an independent capture rather than an
    # accidental replay of the real round.
    if args.with_faults:
        plan_fh = CapturePlan(
            packet_id=plan.packet_id + "-fh",
            create_request=dict(plan.create_request),
            files=list(plan.files),
            roles=dict(plan.roles),
            packet=plan.packet,
        )
        proxy = serve(args.endpoint, {"create": {"kind": "delay", "ms": 300}})
        try:
            proxied = DirectHttpClient(proxy.base_url)
            run_lifecycle(
                proxied,
                plan_fh,
                Transport.FAKE_HUNTER,
                sink,
                result_budget_s=min(args.result_budget_s, 120.0),
                poll_interval_s=args.poll_interval_s,
            )
        finally:
            proxy.stop()

    writer.close()

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


def _gpu_stub_lines(sink) -> None:
    for arm in GPU_STUB_ARMS:
        sink(
            {
                "id": f"gpu-stub.{arm}",
                "transport": "GPU_STUB",
                "oracle": (
                    "task vocabulary: GPU-stubbed arms are named, never executed without "
                    "explicit authorization"
                ),
                "expected": "only run when the operator authorizes the GPU arm",
                "observed": "not run: no GPU arm authorized in this run",
                "status": "NOT_RUN",
                "evidence": None,
            }
        )


def main() -> int:
    try:
        return run()
    except SystemExit as exc:  # argparse + plan errors
        code = exc.code if isinstance(exc.code, int) else 3
        return code


if __name__ == "__main__":
    raise SystemExit(main())
