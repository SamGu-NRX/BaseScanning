"""hunter-local-ingest: run the published-client checks against one endpoint.

  hunter-local-ingest --endpoint http://127.0.0.1:8000/v1 \\
      --plan-file plan.json --backend-mode REAL_API --out runs

Exit codes:
  0  every executed check passed and none was blocked (NOT_RUN rows, such
     as the GPU arms, are listed in the summary)
  1  at least one check failed
  2  usage error
  3  the plan file was refused
  4  no check failed, but at least one was blocked (an unreachable
     endpoint, a step that never answered)
  5  the receipts themselves failed: a value outside the export schema, a
     privacy-screen finding, a missing artifact or an integrity problem.
     This outranks every other code.

Earlier versions used 2 for both a usage error and a blocked run, so a
caller could not tell a typo from an unreachable backend.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .adapter import INTERFACE_VERSION
from .export import LEAD_QUESTIONS, MANIFEST_SCHEMA, ORIGIN_NOTE, SANITIZATION_NOTE, ExportError
from .faultproxy import wait_tcp
from .http_client import API_TOKEN_ENV, DirectHttpClient
from .modes import Status, Transport
from .netpolicy import EndpointError, parse_api_base
from .plan import LoadedPlan, PlanError, load_plan
from .provenance import git_state, runtime, source_sha256
from .receipts import (
    ChecksSink,
    ChecksWriter,
    context_for,
    fresh_run_dir,
    utc_now,
    write_run_manifest,
    write_summary,
)
from .sanitize import redact_argv, safe_failure
from .scenarios import gpu_stub_lines, record, unreachable_lines
from .scenarios.controls import run_controls, run_restart_control
from .scenarios.faults import blocked_fault_round, run_fault_round
from .scenarios.lifecycle import LifecycleOutcome, run_lifecycle
from .traces import TRACE_CHECK_ID, compare_trace, load_trace

PROGRAM = "hunter-local-ingest"
EXIT_PASS, EXIT_FAIL, EXIT_USAGE, EXIT_PLAN, EXIT_BLOCKED, EXIT_RECEIPTS = 0, 1, 2, 3, 4, 5

# Every flag and the type of its value, for argv redaction and its re-check.
FLAG_KINDS = {
    "--endpoint": "endpoint",
    "--plan-file": "path",
    "--out": "path",
    "--label": "label",
    "--backend-mode": "enum:REAL_API|FAKE_HUNTER",
    "--backend-ref": "ref",
    "--client-ref": "ref",
    "--seed": "int",
    "--result-budget-s": "float",
    "--poll-interval-s": "float",
    "--events-wait-s": "int",
    "--max-retries": "int",
    "--retry-backoff-s": "float",
    "--restart-argv-json": "argv-json",
    "--restart-timeout-s": "float",
    "--with-faults": "flag",
    "--api-token-env": "flag",
    "-h": "flag",
    "--help": "flag",
}
_REQUIRED = ("--endpoint", "--plan-file", "--backend-mode")
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
_REF_RE = re.compile(r"^[0-9a-f]{7,40}$")


def _bounded(kind, low, high):
    def parse(text: str):
        try:
            value = kind(text)
        except ValueError:
            raise argparse.ArgumentTypeError("invalid") from None
        if isinstance(value, float) and not math.isfinite(value):
            raise argparse.ArgumentTypeError("invalid")
        if not (low <= value <= high):
            raise argparse.ArgumentTypeError("invalid")
        return value

    return parse


def _pattern(regex: re.Pattern[str]):
    def parse(text: str) -> str:
        if not regex.fullmatch(text):
            raise argparse.ArgumentTypeError("invalid")
        return text

    return parse


def _argv_json(text: str) -> list[str]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        raise argparse.ArgumentTypeError("invalid") from None
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 32
        or not all(isinstance(v, str) and v and "\0" not in v and len(v) <= 4096 for v in value)
    ):
        raise argparse.ArgumentTypeError("invalid")
    return value


class _Parser(argparse.ArgumentParser):
    """argparse whose errors never quote the offending value."""

    def __init__(self, argv: Sequence[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self._argv = list(argv)

    def error(self, message: str):  # argparse override
        self.print_usage(sys.stderr)
        category = _usage_category(message, self._argv)
        print(f"{PROGRAM}: {safe_failure(self._argv, category, FLAG_KINDS)}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _usage_category(message: str, argv: Sequence[str]) -> str:
    if message.startswith("the following arguments are required"):
        present = {tok.partition("=")[0] for tok in argv}
        missing = [flag for flag in _REQUIRED if flag not in present]
        return f"usage:missing:{missing[0]}" if missing else "usage:missing"
    if message.startswith("unrecognized arguments"):
        return "usage:unrecognized-arguments"
    match = re.match(r"^argument (--?[a-z][a-z0-9-]*)", message)
    if match and match.group(1) in FLAG_KINDS:
        return f"usage:invalid-value:{match.group(1)}"
    return "usage:invalid"


def build_parser(argv: Sequence[str]) -> argparse.ArgumentParser:
    # No abbreviations: the retired --api-token must never resolve to
    # --api-token-env with its value left behind as a stray argument.
    parser = _Parser(
        argv,
        prog=PROGRAM,
        description="Published-client checks against one endpoint.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--endpoint", required=True, help="capture API base URL, e.g. http://127.0.0.1:8000/v1"
    )
    parser.add_argument(
        "--plan-file", required=True, help="plan from fixtures/gen_synthetic_packet.py"
    )
    parser.add_argument(
        "--out", default="runs", help="root for run directories (created if absent)"
    )
    parser.add_argument(
        "--label",
        default="run",
        type=_pattern(_LABEL_RE),
        help="run directory label: letters, digits, . _ -",
    )
    parser.add_argument(
        "--backend-mode",
        required=True,
        choices=["REAL_API", "FAKE_HUNTER"],
        help="what the operator declares the endpoint to be; recorded as declared, never verified",
    )
    parser.add_argument("--backend-ref", type=_pattern(_REF_RE), help="backend commit (hex)")
    parser.add_argument("--client-ref", type=_pattern(_REF_RE), help="client commit (hex)")
    parser.add_argument(
        "--seed",
        type=_bounded(int, 0, 2**31 - 1),
        help="if given, must equal the plan's generator seed",
    )
    parser.add_argument("--result-budget-s", type=_bounded(float, 0.1, 86400), default=1500.0)
    parser.add_argument("--poll-interval-s", type=_bounded(float, 0.0, 3600), default=2.0)
    parser.add_argument(
        "--events-wait-s",
        type=_bounded(int, 0, 3600),
        default=20,
        help="events long-poll wait; 20 is CaptureUploader.Policy.eventsWait",
    )
    parser.add_argument("--max-retries", type=_bounded(int, 0, 10), default=2)
    parser.add_argument("--retry-backoff-s", type=_bounded(float, 0.0, 600), default=1.0)
    parser.add_argument(
        "--restart-argv-json",
        type=_argv_json,
        help='restart command as a JSON array, run without a shell: ["cmd", "arg"]',
    )
    parser.add_argument("--restart-timeout-s", type=_bounded(float, 1.0, 3600), default=120.0)
    parser.add_argument(
        "--with-faults",
        action="store_true",
        help="also run the FAKE_HUNTER fault round through a local proxy",
    )
    parser.add_argument(
        "--api-token-env",
        action="store_true",
        help=(
            f"read the capture API bearer token from ${API_TOKEN_ENV}, which a secret"
            " manager should place in this process's environment; sent on API routes"
            " of --endpoint only, never on storage PUTs or to the fault proxy, never"
            " recorded. Without this flag the variable is not read."
        ),
    )
    return parser


def _manifest(args, argv: Sequence[str], loaded: LoadedPlan, run_id: str, label: str) -> dict:
    plan = loaded.plan
    return {
        "schema": MANIFEST_SCHEMA,
        "run_id": run_id,
        "label": label,
        "created_utc": utc_now(),
        "endpoint": parse_api_base(args.endpoint).label,
        "backend": {
            "declared_mode": args.backend_mode,
            "verified": False,
            "declared_ref": args.backend_ref,
        },
        "client": {
            "package_version": __version__,
            "interface": INTERFACE_VERSION,
            "source_sha256": source_sha256(),
            "git": git_state(),
            "declared_ref": args.client_ref,
        },
        "fixture": {
            "generator": "gen_synthetic_packet",
            "format_version": loaded.format_version,
            "seed": loaded.seed,
            "plan_file_sha256": loaded.plan_file_sha256,
            "plan_sha256": loaded.plan_sha256,
            "packet_id": plan.packet_id,
            "packet_sha256": loaded.packet_sha256,
            "create_sha256": loaded.create_sha256,
            "files": [
                {
                    "path": f.path,
                    "bytes": len(f.content),
                    "sha256": f.sha256,
                    "role": f.role,
                    "contentType": f.content_type,
                }
                for f in plan.files
            ],
        },
        "command": redact_argv(argv, FLAG_KINDS),
        "restart": {
            "configured": bool(args.restart_argv_json),
            "argv": redact_argv(args.restart_argv_json or []),
            "timeout_s": args.restart_timeout_s,
        },
        "runtime": runtime(),
        "settings": {
            "result_budget_s": args.result_budget_s,
            "poll_interval_s": args.poll_interval_s,
            "events_wait_s": args.events_wait_s,
            "max_retries": args.max_retries,
            "retry_backoff_s": args.retry_backoff_s,
            "with_faults": args.with_faults,
        },
        "origin_note": ORIGIN_NOTE,
        "sanitization": SANITIZATION_NOTE,
    }


def _exception_category(exc: BaseException) -> str:
    name = type(exc).__name__
    known = {"AssertionError", "KeyError", "TypeError", "ValueError", "OSError", "RuntimeError"}
    return f"exception:{name}" if name in known else "exception:other"


def _refuse_retired_flag(argv: Sequence[str]) -> None:
    """--api-token VALUE put the credential on the command line, where process
    listings and shell history keep it. It is refused in every spelling, with
    the value never echoed (safe_failure renders it as ***)."""
    if any(tok == "--api-token" or tok.startswith("--api-token=") for tok in argv):
        print(
            f"{PROGRAM}: {safe_failure(argv, 'usage:retired-flag:--api-token', FLAG_KINDS)}",
            file=sys.stderr,
        )
        print(
            f"{PROGRAM}: pass --api-token-env and provide ${API_TOKEN_ENV} through the"
            " process environment",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_USAGE)


def _api_token(args, argv: Sequence[str]) -> tuple[str | None, int | None]:
    """The token, read only when --api-token-env is given; or an exit code."""
    if not args.api_token_env:
        return None, None
    token = os.environ.get(API_TOKEN_ENV)
    code = "auth_credential_unavailable" if not token else None
    if code is None:
        try:
            # checks the RFC 6750 b64token shape; opens no connection
            DirectHttpClient(args.endpoint, api_token=token)
        except EndpointError as exc:
            code = exc.code
    if code is not None:
        print(f"{PROGRAM}: {safe_failure(argv, f'credential:{code}', FLAG_KINDS)}", file=sys.stderr)
        return None, EXIT_USAGE
    return token, None


def run(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _refuse_retired_flag(argv)
    args = build_parser(argv).parse_args(argv)

    try:
        parse_api_base(args.endpoint)
    except EndpointError as exc:
        print(
            f"{PROGRAM}: {safe_failure(argv, f'endpoint:{exc.code}', FLAG_KINDS)}", file=sys.stderr
        )
        return EXIT_USAGE
    token, failed = _api_token(args, argv)
    if failed is not None:
        return failed
    try:
        loaded = load_plan(Path(args.plan_file), args.seed)
    except PlanError as exc:
        # field and problem are this package's fixed text; never the value
        print(f"{PROGRAM}: error: plan refused: {exc.field}: {exc.problem}", file=sys.stderr)
        return EXIT_PLAN
    transport = Transport(args.backend_mode)
    run_dir, run_id = fresh_run_dir(Path(args.out), args.label)
    plan = loaded.plan.for_run(run_id)
    ctx = context_for(plan.declared_paths, plan.packet_id, plan.nonce)
    receipts_error: str | None = None
    try:
        write_run_manifest(
            run_dir, _manifest(args, argv, loaded, run_id, args.label), ctx, FLAG_KINDS
        )
    except ExportError as exc:
        receipts_error = f"export {exc.artifact}{exc.pointer}: {exc.category}"

    writer = ChecksWriter(run_dir, ctx)
    sink = ChecksSink(writer)

    def client_for(url: str, **overrides) -> DirectHttpClient:
        return DirectHttpClient(
            url,
            max_retries=overrides.get("max_retries", args.max_retries),
            retry_backoff_s=overrides.get("retry_backoff_s", args.retry_backoff_s),
            # the credential belongs to the operator's declared endpoint alone
            api_token=token if url == args.endpoint else None,
        )

    if receipts_error is None:
        try:
            _run_checks(args, plan, transport, sink, client_for, authenticated=token is not None)
        except ExportError as exc:
            receipts_error = f"export {exc.artifact}{exc.pointer}: {exc.category}"
        except Exception as exc:  # a harness bug: recorded, never swallowed
            record(
                sink,
                transport,
                "harness.internal-error",
                Status.FAIL,
                {"note": "harness-exception", "exception": _exception_category(exc)},
            )
        finally:
            writer.close()
    else:
        writer.close()

    report = write_summary(
        run_dir,
        run_id,
        sink.checks,
        declared_backend=args.backend_mode,
        lead_questions=LEAD_QUESTIONS,
        ctx=ctx,
    )
    counts: dict[str, int] = {}
    for check in sink.checks:
        counts[check.status.value] = counts.get(check.status.value, 0) + 1
    print(f"run dir: {run_dir}")
    print(f"verdict: {report.verdict} counts: {json.dumps(counts, sort_keys=True)}")
    print(f"chain head: {report.chain_head}")
    if receipts_error or not report.receipts_ok:
        problems = [receipts_error] if receipts_error else []
        problems += [f"screen {f}" for f in report.screen_findings]
        problems += [f"integrity {p}" for p in report.integrity]
        print(f"receipts: FAILED ({len(problems)} problem(s))", file=sys.stderr)
        for problem in problems[:20]:
            print(f"  {problem}", file=sys.stderr)
        return EXIT_RECEIPTS
    print("receipts: screened clean, chained, sealed")
    if report.verdict == "FAIL":
        return EXIT_FAIL
    if report.verdict == "BLOCKED":
        return EXIT_BLOCKED
    return EXIT_PASS


def _run_checks(args, plan, transport: Transport, sink, client_for, *, authenticated: bool) -> None:
    baseline = LifecycleOutcome()
    if not wait_tcp(args.endpoint, timeout_s=10.0):
        unreachable_lines(sink, transport)
    else:
        client = client_for(args.endpoint)
        baseline = run_lifecycle(
            client,
            plan,
            transport,
            sink,
            result_budget_s=args.result_budget_s,
            poll_interval_s=args.poll_interval_s,
            events_wait_s=args.events_wait_s,
        )
        run_controls(client, plan, transport, sink)
        if args.with_faults and authenticated:
            # The proxy never gets the credential, and its requests sent
            # without one would read as endpoint failures. The arm is
            # BLOCKED: authenticated fault coverage is not provided.
            blocked_fault_round(sink, "auth_fault_proxy_not_authorized")
        elif args.with_faults:
            run_fault_round(
                args.endpoint,
                plan,
                sink,
                client_factory=client_for,
                result_budget_s=min(args.result_budget_s, 120.0),
                poll_interval_s=args.poll_interval_s,
                events_wait_s=args.events_wait_s,
            )
    comparison = compare_trace(sink.checks, load_trace(), transport, plan.declared_paths)
    record(sink, transport, TRACE_CHECK_ID, comparison.status, comparison.public())
    if baseline.capture_id is not None:
        run_restart_control(
            client_for(args.endpoint),
            baseline,
            transport,
            sink,
            restart_argv=args.restart_argv_json,
            timeout_s=args.restart_timeout_s,
            wait_ready=lambda: wait_tcp(args.endpoint, timeout_s=60.0),
        )
    else:
        note = "restart-not-configured" if not args.restart_argv_json else "baseline-incomplete"
        status = Status.NOT_RUN if not args.restart_argv_json else Status.BLOCKED
        record(sink, transport, "control.restart-recovery", status, {"note": note})
    gpu_stub_lines(sink)


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
