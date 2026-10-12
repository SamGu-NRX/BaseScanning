"""Published-contract lifecycle conformance against a live endpoint.

Each step's oracle is in scenarios/oracles.py and cites the published
HouseScanKit source it comes from. A step that gets no answer is BLOCKED;
an answer that breaks the contract is FAIL; steps that depend on an
incomplete step are recorded BLOCKED with the reason, never left out.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable

from ..adapter import HunterIngestAdapter
from ..contract import (
    STATUSES_WITHOUT_OUTCOME,
    TERMINAL_STATUSES,
    decode_create,
    decode_errors,
    decode_events,
    decode_finalize,
    decode_health,
    decode_result,
)
from ..modes import Status, Transport
from ..plan import CapturePlan
from . import LIFECYCLE_IDS, is_2xx, record, skip_rest, step_view
from .steps import batch_size, commit_round, error_status, register_round

# CaptureUploader.Policy defaults.
MAX_FINALIZE_RETRIES = 3
MAX_NOT_READY_RESULTS = 10


@dataclasses.dataclass
class LifecycleOutcome:
    """What later checks (restart recovery) need from the baseline capture."""

    capture_id: str | None = None
    run_id: str | None = None
    terminal_status: str | None = None
    result_passed: bool = False


def _skip_note(status: Status) -> str:
    """Why the steps after an incomplete one are BLOCKED."""
    if status is Status.BLOCKED:
        return "skipped-after-blocked-step"
    return "skipped-after-failed-step"


def _rest(after: str) -> tuple[str, ...]:
    return LIFECYCLE_IDS[LIFECYCLE_IDS.index(after) + 1 :]


def run_lifecycle(
    adapter: HunterIngestAdapter,
    plan: CapturePlan,
    transport: Transport,
    sink,
    *,
    result_budget_s: float = 1500.0,
    poll_interval_s: float = 2.0,
    events_wait_s: int = 20,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> LifecycleOutcome:
    """Push one synthetic capture through the published lifecycle."""
    outcome = LifecycleOutcome()
    declared = plan.declared_paths

    def stop(after: str, note: str) -> LifecycleOutcome:
        skip_rest(sink, transport, _rest(after), note)
        return outcome

    # -- health (preflight) --------------------------------------------------
    res = adapter.health()
    if res.error:
        record(
            sink,
            transport,
            "lifecycle.health",
            Status.BLOCKED,
            {**step_view(res), "note": "endpoint-unreachable"},
        )
        return stop("lifecycle.health", "endpoint-unreachable")
    dec = decode_health(res.parsed)
    ok = is_2xx(res) and dec.ok
    record(
        sink,
        transport,
        "lifecycle.health",
        Status.PASS if ok else Status.FAIL,
        step_view(res, dec),
        res.observation,
    )

    # -- create ---------------------------------------------------------------
    res = adapter.create(plan.create_body, plan.packet_id)
    if res.error:
        record(sink, transport, "lifecycle.create", error_status(res), step_view(res))
        return stop("lifecycle.create", _skip_note(error_status(res)))
    dec = decode_create(res.parsed) if is_2xx(res) else None
    capture_id = dec.data["captureId"] if dec is not None and dec.ok else None
    status = Status.PASS if capture_id else Status.FAIL
    record(sink, transport, "lifecycle.create", status, step_view(res, dec), res.observation)
    if status is not Status.PASS:
        return stop("lifecycle.create", "skipped-after-failed-step")
    outcome.capture_id = capture_id
    size = batch_size(dec.data["maxBatch"])

    # -- register, in batches, matched by path ---------------------------------
    reg = register_round(adapter, capture_id, plan.files, size, declared)
    # A file the server already holds (a replayed or resumed capture) comes
    # back committed and is neither uploaded nor committed again.
    at_register = sorted(reg.committed)
    record(
        sink,
        transport,
        "lifecycle.register",
        reg.status,
        {"batches": reg.batches, "violations": reg.violations},
        reg.last.observation if reg.last is not None else None,
    )
    if reg.status is not Status.PASS:
        return stop("lifecycle.register", _skip_note(reg.status))

    # -- upload: each file to its own target, exactly the signed headers -------
    uploads: list[dict] = []
    uploaded: set[str] = set()
    status = Status.PASS
    last = None
    for f in plan.files:
        if f.path in reg.committed:
            continue
        target = reg.targets[f.path]
        res = adapter.upload(target["url"], f.content, target["headers"])
        last = res
        uploads.append({"path": f.path, **res.public()})
        if res.error and res.error.startswith("transport:"):
            status = Status.BLOCKED if status is Status.PASS else status
        elif not is_2xx(res):
            status = Status.FAIL
        else:
            uploaded.add(f.path)
    record(
        sink,
        transport,
        "lifecycle.upload",
        status,
        {"uploads": uploads, "committed_at_register": at_register},
        last.observation if last is not None else None,
    )
    if status is not Status.PASS:
        return stop("lifecycle.upload", _skip_note(status))

    # -- commit: exactly the uploaded set comes back committed ------------------
    com = commit_round(
        adapter, capture_id, [f for f in plan.files if f.path in uploaded], size, declared
    )
    committed = reg.committed | com.committed
    status = com.status
    if status is Status.PASS and (not com.exact or committed != declared):
        status = Status.FAIL
    record(
        sink,
        transport,
        "lifecycle.commit",
        status,
        {
            "batches": com.batches,
            "violations": com.violations,
            "committed_at_register": at_register,
            "committed_total": len(committed),
            "declared": len(declared),
        },
        com.last.observation if com.last is not None else None,
    )
    if status is not Status.PASS:
        return stop("lifecycle.commit", _skip_note(status))

    # -- finalize ----------------------------------------------------------------
    res = adapter.finalize(capture_id, plan.packet, plan.packet_id)
    if res.error:
        record(sink, transport, "lifecycle.finalize", error_status(res), step_view(res))
        return stop("lifecycle.finalize", _skip_note(error_status(res)))
    dec = decode_finalize(res.parsed, declared) if is_2xx(res) else None
    run_id = dec.data["runId"] if dec is not None and dec.ok and dec.data["missing"] == [] else None
    status = Status.PASS if run_id else Status.FAIL
    record(sink, transport, "lifecycle.finalize", status, step_view(res, dec), res.observation)
    if status is not Status.PASS:
        return stop("lifecycle.finalize", "skipped-after-failed-step")
    outcome.run_id = run_id

    # -- events until a terminal status ------------------------------------------
    deadline = clock() + result_budget_s
    cursor = 0
    answers = 0
    retries = 0
    view: dict = {}
    status = None
    note = None
    last_obs = None
    terminal = None
    while clock() < deadline:
        res = adapter.events(capture_id, cursor, events_wait_s)
        answers += 1
        if res.error:
            status, view = error_status(res), step_view(res)
            break
        last_obs = res.observation
        dec = decode_events(res.parsed, after=cursor, run_id=run_id) if is_2xx(res) else None
        view = step_view(res, dec)
        if dec is None or not dec.ok:
            status = Status.FAIL
            break
        cursor = max(cursor, dec.data["next"])
        if dec.data["retry_finalize"]:
            # CaptureUploader.pollEvents: the server lost the finalize; send the
            # same frozen packet again, a bounded number of times.
            if retries >= MAX_FINALIZE_RETRIES:
                status, note = Status.FAIL, "retry-finalize-limit"
                break
            retries += 1
            again = adapter.finalize(capture_id, plan.packet, plan.packet_id)
            fdec = decode_finalize(again.parsed, declared) if is_2xx(again) else None
            if again.error:
                status, view = error_status(again), step_view(again)
                break
            if fdec is None or not fdec.ok:
                status, view = Status.FAIL, step_view(again, fdec)
                break
            run_id = fdec.data["runId"]
            outcome.run_id = run_id
            continue
        if dec.data["status"] in TERMINAL_STATUSES:
            terminal = dec.data["status"]
            status = Status.PASS
            break
        if dec.data["events"] == 0:
            sleep(poll_interval_s)
    if status is None:
        status, note = Status.BLOCKED, "budget-exhausted"
    observed = {"answers": answers, "finalize_retries": retries, "last": view}
    if note:
        observed["note"] = note
    record(sink, transport, "lifecycle.events", status, observed, last_obs)
    if status is not Status.PASS:
        return stop("lifecycle.events", _skip_note(status))
    outcome.terminal_status = terminal

    # -- result ------------------------------------------------------------------
    not_ready = 0
    while True:
        res = adapter.result(capture_id)
        if res.error:
            record(sink, transport, "lifecycle.result", error_status(res), step_view(res))
            return outcome
        if res.http_status == 404 and "result_not_ready" in decode_errors(res.parsed).data["codes"]:
            ready = False
            dec = None
        elif not is_2xx(res):
            record(
                sink, transport, "lifecycle.result", Status.FAIL, step_view(res), res.observation
            )
            return outcome
        else:
            dec = decode_result(res.parsed)
            ready = not (
                dec.ok
                and not dec.data["outcome_present"]
                and dec.data["status"] not in STATUSES_WITHOUT_OUTCOME
            )
        if not ready:
            not_ready += 1
            if not_ready > MAX_NOT_READY_RESULTS:
                record(
                    sink,
                    transport,
                    "lifecycle.result",
                    Status.FAIL,
                    {
                        **step_view(res, dec),
                        "not_ready_reads": not_ready,
                        "note": "not-ready-limit",
                    },
                    res.observation,
                )
                return outcome
            sleep(poll_interval_s)
            continue
        break
    observed = {
        **step_view(res, dec),
        "not_ready_reads": not_ready,
        "terminal_status": terminal,
        "run_matches": dec.data["runId"] == run_id,
        "status_matches": dec.data["status"] == terminal,
    }
    status = Status.PASS
    if not dec.ok:
        status = Status.FAIL
    elif dec.data["runId"] != run_id:
        status = Status.FAIL
        observed["note"] = "result-runid-mismatch"
    elif dec.data["status"] != terminal:
        status = Status.FAIL
        observed["note"] = "result-status-mismatch"
    record(sink, transport, "lifecycle.result", status, observed, res.observation)
    outcome.result_passed = status is Status.PASS
    return outcome
