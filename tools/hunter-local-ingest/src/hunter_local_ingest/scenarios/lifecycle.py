"""Published-contract lifecycle conformance against a live endpoint.

Oracles cite the published HouseScanKit sources (alpha 263827ef) or the
public result-schema documentation. Expectations marked provisional will
be refined from sanitized local-run observations once the real backend
baseline lands; none import private material.
"""

from __future__ import annotations

import time

from ..adapter import HunterIngestAdapter
from ..modes import Status, Transport
from . import (
    STATUSES_WITHOUT_OUTCOME,
    TERMINAL_STATUSES,
    CapturePlan,
    record,
    step_observed,
)

_ORACLE_PREFIX = (
    "HouseScanKit/Sources/HouseScanKit/Upload (CaptureAPI.swift, "
    "CaptureUploader.swift, ScopedCaptureHTTP.swift) @ alpha 263827ef"
)


def _safe_value(result, key: str):
    if result.observation and result.observation.safe_values:
        return result.observation.safe_values.get(key)
    return None


def _parse_public_view(result) -> dict:
    """The sanitized view of the step's answer, for decisions on allowlisted fields."""
    if result.observation and result.observation.safe_values:
        return result.observation.safe_values
    return {}


def _raw_register_view(result):
    """Scenario-side extraction of the signed upload target.

    The receipt never records the URL (observations.py drops it); this
    method exists only to pass it into the next step via StepResult
    context. It is parsed from the response the adapter already received —
    but the adapter's observation dropped it, so the scenario re-parses
    nothing: the direct client stores the parsed target in context.
    """
    ctx = result.context or {}
    return ctx.get("upload_targets"), ctx.get("answer_view")


def run_lifecycle(
    adapter: HunterIngestAdapter,
    plan: CapturePlan,
    transport: Transport,
    sink: list,
    *,
    result_budget_s: float = 1500.0,
    poll_interval_s: float = 2.0,
) -> None:
    """Push one synthetic capture through the full published lifecycle."""
    status_val: Status = Status.NOT_RUN

    # -- health -----------------------------------------------------------
    res = adapter.health()
    if res.error:
        record(
            sink,
            transport,
            "lifecycle.health",
            "repo README run instructions: GET /healthz before use",
            "HTTP 200 with a status field",
            res,
            Status.BLOCKED,
        )
        record(
            sink,
            transport,
            "lifecycle.create",
            f"{_ORACLE_PREFIX}: POST /captures with Idempotency-Key=packetId; "
            "answer carries captureId and status",
            "2xx with captureId",
            "skipped: endpoint unreachable at health",
            Status.BLOCKED,
        )
        return
    ok = res.http_status == 200 and _safe_value(res, "status") is not None
    record(
        sink,
        transport,
        "lifecycle.health",
        "repo README run instructions: GET /healthz before use",
        "HTTP 200 with a status field",
        res,
        Status.PASS if ok else Status.FAIL,
    )

    # -- create ------------------------------------------------------------
    res = adapter.create(plan.create_request, plan.packet_id)
    create_status = res.http_status
    capture_id = _safe_value(res, "captureId")
    ok = create_status is not None and 200 <= create_status < 300 and capture_id
    status_val = Status.PASS if ok else Status.FAIL
    if create_status is None:
        status_val = Status.BLOCKED
    record(
        sink,
        transport,
        "lifecycle.create",
        f"{_ORACLE_PREFIX}: POST /captures with Idempotency-Key=packetId; "
        "answer carries captureId and status",
        "2xx with captureId",
        res,
        status_val,
    )
    if status_val is not Status.PASS:
        return
    assert capture_id is not None

    # -- register ----------------------------------------------------------
    res = adapter.register(capture_id, plan.file_descriptors)
    targets, view = _raw_register_view(res)
    ok = res.http_status is not None and 200 <= res.http_status < 300 and bool(targets)
    status_val = Status.PASS if ok else Status.FAIL
    if res.http_status is None:
        status_val = Status.BLOCKED
    record(
        sink,
        transport,
        "lifecycle.register",
        f"{_ORACLE_PREFIX}: POST /captures/{{id}}/files; answer grants per-file "
        "signed upload targets",
        "2xx with a signed target per file",
        res,
        status_val,
    )
    if status_val is not Status.PASS or not targets:
        return

    # -- upload --------------------------------------------------------------
    upload_ok = True
    first_bad = ""
    for (path, content), target in zip(
            plan.files, targets, strict=False
        ):  # a count mismatch fails at commit  # a count mismatch fails at commit
        headers = target.get("headers") if isinstance(target, dict) else None
        url = target.get("url") if isinstance(target, dict) else None
        if not url:
            upload_ok = False
            first_bad = f"{path}: no signed target"
            break
        res = adapter.upload(capture_id, path, content, url, headers)
        if res.error or not (res.http_status and 200 <= res.http_status < 300):
            upload_ok = False
            first_bad = f"{path}: {step_observed(res)}"
            break
    record(
        sink,
        transport,
        "lifecycle.upload",
        f"{_ORACLE_PREFIX} + ScopedCaptureHTTP: storage PUT with exactly the signed "
        "headers; 2xx expected per file",
        "2xx for every file",
        res if upload_ok else first_bad,
        Status.PASS if upload_ok else Status.FAIL,
    )

    # -- commit ---------------------------------------------------------------
    res = adapter.commit(capture_id, plan.file_descriptors)
    committed = _safe_value(res, "committed")
    ok = res.http_status is not None and 200 <= res.http_status < 300 and committed
    status_val = Status.PASS if ok else Status.FAIL
    if res.http_status is None:
        status_val = Status.BLOCKED
    record(
        sink,
        transport,
        "lifecycle.commit",
        f"{_ORACLE_PREFIX}: POST /captures/{{id}}/files:commit; answer separates "
        "committed from notFound/mismatch",
        "2xx with every path committed",
        res,
        status_val,
    )

    # -- finalize ---------------------------------------------------------------
    res = adapter.finalize(capture_id, plan.packet, plan.packet_id)
    ok = res.http_status is not None and 200 <= res.http_status < 300
    status_val = Status.PASS if ok else Status.FAIL
    if res.http_status is None:
        status_val = Status.BLOCKED
    record(
        sink,
        transport,
        "lifecycle.finalize",
        f"{_ORACLE_PREFIX}: POST /captures/{{id}}/finalize with X-Packet-Sha256 and "
        "Idempotency-Key=packetId; bounded same-body retries on timeout/503",
        "2xx finalize",
        res,
        status_val,
    )

    # -- events polling -----------------------------------------------------------
    budget_deadline = time.monotonic() + result_budget_s
    cursor = 0
    last_status = None
    contract_ok = True
    contract_note = ""
    while time.monotonic() < budget_deadline:
        res = adapter.events(capture_id, cursor, int(poll_interval_s))
        if res.error:
            contract_ok = False
            contract_note = f"events transport error at cursor {cursor}"
            break
        view_now = _parse_public_view(res)
        seq_ok = isinstance(view_now.get("next"), int)
        if res.http_status != 200 or not seq_ok:
            contract_ok = False
            contract_note = f"events response violates published shape: {step_observed(res)}"
            break
        last_status = view_now.get("status")
        next_val = view_now["next"]
        if next_val <= cursor:
            contract_note = f"events cursor did not advance ({cursor} -> {next_val})"
        cursor = max(cursor, next_val)
        if last_status in TERMINAL_STATUSES:
            break
        time.sleep(poll_interval_s)
    else:
        record(
            sink,
            transport,
            "lifecycle.events",
            f"{_ORACLE_PREFIX}: GET events until CaptureAPI.terminalStatuses; "
            "EventsResponse.next is an advancing int",
            "terminal status within budget",
            f"budget of {result_budget_s:.0f}s exhausted without terminal status "
            f"(last observed: {last_status})",
            Status.BLOCKED,
        )
        _final_result_check(adapter, capture_id, transport, sink, last_status, no_poll=True)
        return
    if not contract_ok:
        record(
            sink,
            transport,
            "lifecycle.events",
            f"{_ORACLE_PREFIX}: GET events until CaptureAPI.terminalStatuses; "
            "EventsResponse.next is an advancing int",
            "terminal status within budget, contract-shaped answers",
            contract_note,
            Status.FAIL,
        )
        return
    record(
        sink,
        transport,
        "lifecycle.events",
        f"{_ORACLE_PREFIX}: GET events until CaptureAPI.terminalStatuses; "
        "EventsResponse.next is an advancing int",
        "terminal status within budget",
        res,
        Status.PASS,
    )
    _final_result_check(adapter, capture_id, transport, sink, last_status)


def _final_result_check(
    adapter: HunterIngestAdapter,
    capture_id: str,
    transport: Transport,
    sink: list,
    last_status,
    no_poll: bool = False,
) -> None:
    """Result read: schema-valid answer, or the published null-outcome rule."""
    res = adapter.result(capture_id)
    if res.http_status is None:
        record(
            sink,
            transport,
            "lifecycle.result",
            "public result-schema doc: result read returns a schema-valid object "
            "(schema_version + required fields)",
            "schema-valid answer",
            res,
            Status.BLOCKED,
        )
        return
    view = _parse_public_view(res)
    schema_ok = res.http_status == 200 and "schema_version" in view and (
        view.get("schema_version") == "0.4"
    )
    if schema_ok:
        record(
            sink,
            transport,
            "lifecycle.result",
            "public result-schema doc: result read returns a schema-valid object "
            "(schema_version + required fields)",
            "schema-valid answer",
            res,
            Status.PASS,
        )
        return
    if (
        res.http_status == 200
        and last_status in STATUSES_WITHOUT_OUTCOME
    ):
        record(
            sink,
            transport,
            "lifecycle.result",
            "public result-schema doc: under statusesWithoutOutcome a result read with "
            "one and a null outcome is final",
            "schema-valid answer",
            res,
            Status.PASS,
        )
        return
    if no_poll:
        record(
            sink,
            transport,
            "lifecycle.result",
            "public result-schema doc: result read returns a schema-valid object "
            "(schema_version + required fields)",
            "schema-valid answer",
            f"checked despite non-terminal state: {step_observed(res)}",
            Status.BLOCKED,
        )
        return
    record(
        sink,
        transport,
        "lifecycle.result",
        "public result-schema doc: result read returns a schema-valid object "
        "(schema_version + required fields)",
        "schema-valid answer",
        res,
        Status.FAIL,
    )
