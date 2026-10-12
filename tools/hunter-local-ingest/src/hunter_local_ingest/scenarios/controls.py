"""Controls: prepared wrong conditions the client must detect.

A control PASSes when the wrong condition is detected and the rest of the
capture behaves as the contract says. Each control runs under its own
derived packet id (plan.derive), so it never touches the baseline capture.

The integrity controls tamper with one file and leave the others intact.
They require the intact files to come back committed and the tampered one
to be refused, so a server that commits nothing cannot pass.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence

from ..adapter import HunterIngestAdapter
from ..contract import (
    decode_create,
    decode_errors,
    decode_events,
    decode_result,
    digest12,
    pub_id,
)
from ..http_client import child_env
from ..modes import Status, Transport
from ..plan import CapturePlan, PlanFile
from . import is_2xx, record, step_view
from .lifecycle import LifecycleOutcome
from .steps import batch_size, commit_round, error_status, register_round


def run_controls(
    adapter: HunterIngestAdapter, plan: CapturePlan, transport: Transport, sink
) -> None:
    _idempotent_create(adapter, plan.derive("idem"), transport, sink)
    _integrity(
        adapter,
        plan.derive("corrupt"),
        transport,
        sink,
        "control.corrupt-upload",
        lambda b: bytes([b[0] ^ 0xFF]) + b[1:],
    )
    _integrity(
        adapter,
        plan.derive("truncated"),
        transport,
        sink,
        "control.truncated-upload",
        lambda b: b[:-1],
    )
    _wrong_capture(adapter, plan, transport, sink)


def _idempotent_create(adapter, plan: CapturePlan, transport, sink) -> None:
    first = adapter.create(plan.create_body, plan.packet_id)
    second = adapter.create(plan.create_body, plan.packet_id) if not first.error else first
    if first.error or second.error:
        record(
            sink,
            transport,
            "control.idempotent-create",
            error_status(first if first.error else second),
            {"first": step_view(first), "second": step_view(second)},
        )
        return
    d1 = decode_create(first.parsed) if is_2xx(first) else None
    d2 = decode_create(second.parsed) if is_2xx(second) else None
    id1 = d1.data["captureId"] if d1 is not None and d1.ok else None
    id2 = d2.data["captureId"] if d2 is not None and d2.ok else None
    same = id1 is not None and id1 == id2
    record(
        sink,
        transport,
        "control.idempotent-create",
        Status.PASS if same else Status.FAIL,
        {"first": step_view(first, d1), "second": step_view(second, d2), "same_capture": same},
        second.observation,
    )


def _integrity(
    adapter, plan: CapturePlan, transport, sink, check_id: str, tamper: Callable[[bytes], bytes]
) -> None:
    def stop(status: Status, view: dict) -> None:
        """The control could not reach its verdict. Why decides the status:
        no answer is BLOCKED; a refused request or a malformed or wrong answer
        is FAIL, never hidden as a run that did not happen."""
        record(sink, transport, check_id, status, view)

    res = adapter.create(plan.create_body, plan.packet_id)
    if res.error:
        stop(error_status(res), {"step": "create", **step_view(res)})
        return
    dec = decode_create(res.parsed) if is_2xx(res) else None
    capture_id = dec.data["captureId"] if dec is not None and dec.ok else None
    if not capture_id:
        stop(Status.FAIL, {"step": "create", **step_view(res, dec)})
        return
    declared = plan.declared_paths
    size = batch_size(dec.data["maxBatch"])
    # Registration promises the ORIGINAL bytes; the PUT carries tampered ones.
    reg = register_round(adapter, capture_id, plan.files, size, declared)
    if reg.status is not Status.PASS or reg.committed:
        # A fresh capture cannot hold committed files before any upload.
        status = reg.status if reg.status is not Status.PASS else Status.FAIL
        stop(status, {"step": "register", "batches": reg.batches, "violations": reg.violations})
        return
    victim: PlanFile = plan.files[0]
    tampered_status = None
    for f in plan.files:
        target = reg.targets[f.path]
        body = tamper(f.content) if f is victim else f.content
        put = adapter.upload(target["url"], body, target["headers"])
        if put.error:
            stop(error_status(put), {"step": "upload", **step_view(put)})
            return
        if f is victim:
            tampered_status = put.http_status
        elif not is_2xx(put):
            # storage refused a file whose bytes match their digest
            stop(Status.FAIL, {"step": "upload", **step_view(put)})
            return
    com = commit_round(adapter, capture_id, plan.files, size, declared)
    if com.status is Status.BLOCKED:
        stop(Status.BLOCKED, {"step": "commit", "batches": com.batches})
        return
    reported = victim.path in com.mismatch | com.not_found
    put_refused = tampered_status is not None and not (200 <= tampered_status < 300)
    intact = {f.path for f in plan.files if f is not victim}
    # An intact file must be acknowledged and never also refused, whatever
    # the decoder allowed.
    intact_negative = bool(intact & (com.mismatch | com.not_found))
    ok = (
        com.status is Status.PASS
        and intact <= com.committed
        and not intact_negative
        and victim.path not in com.committed
        and (reported or put_refused)
    )
    record(
        sink,
        transport,
        check_id,
        Status.PASS if ok else Status.FAIL,
        {
            "batches": com.batches,
            "violations": com.violations,
            "tampered": victim.path,
            "tampered_put_status": tampered_status,
            "tampered_committed": victim.path in com.committed,
            "tampered_reported": reported,
            "intact_committed": intact <= com.committed,
            "intact_reported_negative": intact_negative,
        },
        com.last.observation if com.last is not None else None,
    )


def _wrong_capture(adapter, plan: CapturePlan, transport, sink) -> None:
    # An id the harness invents: it has never been sent to any server.
    bogus = f"bogus-{digest12(plan.packet_id + ':wrong-capture')}"
    res = adapter.result(bogus)
    if res.error:
        record(sink, transport, "control.wrong-capture", error_status(res), step_view(res))
        return
    codes = decode_errors(res.parsed).data["codes"]
    ok = res.http_status == 404 and "capture_not_found" in codes
    record(
        sink,
        transport,
        "control.wrong-capture",
        Status.PASS if ok else Status.FAIL,
        {**step_view(res), "capture": pub_id(bogus)},
        res.observation,
    )


def run_restart_control(
    adapter: HunterIngestAdapter,
    baseline: LifecycleOutcome,
    transport: Transport,
    sink,
    *,
    restart_argv: Sequence[str] | None,
    timeout_s: float,
    wait_ready: Callable[[], bool],
) -> None:
    """Restart the backend with the operator's command, then re-read the baseline.

    The command runs without a shell, with stdin, stdout and stderr
    discarded: its output is never evidence. Only its exit code is kept.
    """
    check_id = "control.restart-recovery"
    if not restart_argv:
        record(sink, transport, check_id, Status.NOT_RUN, {"note": "restart-not-configured"})
        return
    if not (baseline.result_passed and baseline.capture_id and baseline.terminal_status):
        record(sink, transport, check_id, Status.BLOCKED, {"note": "baseline-incomplete"})
        return
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, no shell
            list(restart_argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_env(),  # the API credential is never handed to it
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        record(sink, transport, check_id, Status.BLOCKED, {"note": "restart-timeout"})
        return
    except OSError:
        record(sink, transport, check_id, Status.BLOCKED, {"note": "restart-failed"})
        return
    if proc.returncode != 0:
        record(
            sink,
            transport,
            check_id,
            Status.BLOCKED,
            {"note": "restart-failed", "exit_code": proc.returncode},
        )
        return
    if not wait_ready():
        record(
            sink, transport, check_id, Status.BLOCKED, {"note": "endpoint-not-back", "exit_code": 0}
        )
        return
    events = adapter.events(baseline.capture_id, 0, 0)
    edec = decode_events(events.parsed, after=0, run_id=baseline.run_id) if is_2xx(events) else None
    result = adapter.result(baseline.capture_id)
    rdec = decode_result(result.parsed) if is_2xx(result) else None
    if events.error or result.error:
        record(
            sink,
            transport,
            check_id,
            error_status(events if events.error else result),
            {"exit_code": 0, "after_restart": [step_view(events), step_view(result)]},
        )
        return
    status_ok = edec is not None and edec.ok and edec.data["status"] == baseline.terminal_status
    run_ok = (
        rdec is not None
        and rdec.ok
        and rdec.data["runId"] == baseline.run_id
        and rdec.data["status"] == baseline.terminal_status
    )
    record(
        sink,
        transport,
        check_id,
        Status.PASS if status_ok and run_ok else Status.FAIL,
        {
            "exit_code": 0,
            "status_matches": status_ok,
            "run_matches": run_ok,
            "after_restart": [step_view(events, edec), step_view(result, rdec)],
        },
        result.observation,
    )
