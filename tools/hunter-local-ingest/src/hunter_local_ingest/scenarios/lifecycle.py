"""Published-contract lifecycle conformance against a live endpoint.

Oracles cite the published HouseScanKit sources (alpha 263827ef). The
decisions follow the published client exactly: statuses terminal per
CaptureAPI.terminalStatuses, register answers matched BY PATH
(CaptureUploader.swift:460), strict commit membership, result reads
bound to the finalize's runId (CaptureUploader.swift:663-665), and
bounded not-ready reads honoring Retry-After. None of it imports
private material.

Deferred strictness (owned by the faults child's self-check fake seam):
requiring the result body to carry ``status``/``viewsNeeded``/
``memberActions`` would fail the in-repo plumbing fake's minimal body;
the binding/decode/null-outcome rules below are enforced now, the
required-field presence check lands with the fake's conformant body.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from ..adapter import HunterIngestAdapter
from ..http_client import parse_expires_at
from ..modes import Status, Transport
from . import (
    BATCH_SIZE,
    MAX_UNACKNOWLEDGED,
    STATUSES_WITHOUT_OUTCOME,
    TERMINAL_STATUSES,
    CapturePlan,
    record,
    register_round,
    step_observed,
    strict_commit_verdict,
    verdict_events_binding,
)

_ORACLE_PREFIX = (
    "HouseScanKit/Sources/HouseScanKit/Upload (CaptureAPI.swift, "
    "CaptureUploader.swift, ScopedCaptureHTTP.swift) @ alpha 263827ef"
)

# Published bounds (CaptureUploader.swift).
MAX_REFUSED_PUTS = 6  # 403 expired-signature re-register bound
MAX_BAD_DIGEST_RETRIES = 1  # first 400 retries the same sealed bytes once
EXPIRY_MARGIN_S = 60.0  # requeue URLs within 60 s of expiry (:25-27)
MAX_CONCURRENT_PUTS = 3  # maxConcurrentPuts (:16)
EVENTS_CURSOR_STALL_BOUND = 10  # non-advancing EventsResponse.next bound
MAX_NOT_READY_RESULTS = 10  # result_not_ready read bound


def _safe_value(result, key: str):
    if result.observation and result.observation.safe_values:
        return result.observation.safe_values.get(key)
    return None


def _parse_public_view(result) -> dict:
    """The sanitized view of the step's answer, for decisions on allowlisted fields."""
    if result.observation and result.observation.safe_values:
        return result.observation.safe_values
    return {}


def _ctx(result) -> dict:
    return result.context or {}


def _register_batched(
    adapter: HunterIngestAdapter, capture_id: str, descriptors: list[dict], max_batch: int
):
    """Register in ~10-file batches capped by the server's maxBatch
    (Policy.batch :17-19, CreateResponse.upload.maxBatch). Returns
    (targets_by_path, failed_step_result, last_step_result)."""
    targets: dict = {}
    last_res = None
    for start in range(0, len(descriptors), max_batch):
        chunk = descriptors[start : start + max_batch]
        res = adapter.register(capture_id, chunk)
        last_res = res
        if res.error or not (res.http_status and 200 <= res.http_status < 300):
            return None, res, last_res
        targets.update(_ctx(res).get("register_targets") or {})
    return targets, None, last_res


def run_lifecycle(
    adapter: HunterIngestAdapter,
    plan: CapturePlan,
    transport: Transport,
    sink: list,
    *,
    result_budget_s: float = 1500.0,
    poll_interval_s: float = 2.0,
    events_wait_s: float = 20.0,
) -> None:
    """Push one synthetic capture through the full published lifecycle.

    ``events_wait_s`` is the published long-poll wait (Policy.eventsWait =
    20); ``poll_interval_s`` is the pacing floor between polls
    (minimumPollInterval = 2.0). Tests override both for speed.
    """
    status_val: Status = Status.NOT_RUN

    # -- health -----------------------------------------------------------
    res = adapter.health()
    if res.error:
        record(
            sink,
            transport,
            "lifecycle.health",
            "repo README run instructions: GET /healthz before use",
            "2xx with a status field",
            res,
            Status.BLOCKED,
        )
        for cid in (
            "lifecycle.create",
            "lifecycle.register",
            "lifecycle.upload",
            "lifecycle.commit",
            "lifecycle.finalize",
            "lifecycle.events",
            "lifecycle.result",
        ):
            record(
                sink,
                transport,
                cid,
                "skipped: endpoint unreachable at health",
                "step reachable",
                "skipped: endpoint unreachable at health",
                Status.BLOCKED,
            )
        return
    ok = res.http_status is not None and 200 <= res.http_status < 300 and (
        _safe_value(res, "status") is not None
    )
    record(
        sink,
        transport,
        "lifecycle.health",
        "repo README run instructions: GET /healthz before use",
        "2xx with a status field",
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
    limits = _ctx(res).get("create_limits") or {}
    server_batch = limits.get("maxBatch")
    max_batch = (
        min(BATCH_SIZE, server_batch)
        if isinstance(server_batch, int) and server_batch > 0
        else BATCH_SIZE
    )

    # -- register (path-matched rounds; unacknowledged files re-register) --
    descriptors = plan.file_descriptors
    planned_paths = [d["path"] for d in descriptors]
    targets, reg_fail, reg_res = _register_batched(adapter, capture_id, descriptors, max_batch)
    reg_status = Status.FAIL
    if reg_fail is not None:
        reg_status = Status.BLOCKED if reg_fail.http_status is None else Status.FAIL
        record(
            sink,
            transport,
            "lifecycle.register",
            f"{_ORACLE_PREFIX}: POST /captures/{{id}}/files matched BY PATH; "
            "answer grants per-file signed targets; unacknowledged files re-register",
            "2xx with a PUT+url target per planned path",
            reg_fail,
            reg_status,
        )
        return
    round_plan = register_round(descriptors, targets)
    rounds = 0
    while round_plan.unacknowledged and rounds < MAX_UNACKNOWLEDGED:
        rounds += 1
        pending = [d for d in descriptors if d["path"] in set(round_plan.unacknowledged)]
        more, reg_fail, reg_res = _register_batched(adapter, capture_id, pending, max_batch)
        if reg_fail is not None:
            break
        targets.update(more or {})
        round_plan = register_round(descriptors, targets)
    reg_ok = (
        not round_plan.unacknowledged
        and not round_plan.method_refusals
        and not round_plan.duplicated
        and reg_fail is None
    )
    detail = reg_res if reg_ok else (
        f"unacknowledged: {sorted(round_plan.unacknowledged)}; "
        f"method refusals: {round_plan.method_refusals}; "
        f"unexpected answer paths: {sorted(round_plan.duplicated)}"
    )
    record(
        sink,
        transport,
        "lifecycle.register",
        f"{_ORACLE_PREFIX}: POST /captures/{{id}}/files matched BY PATH; "
        "answer grants per-file signed targets; unacknowledged files re-register "
        f"(bounded {MAX_UNACKNOWLEDGED})",
        "2xx with a PUT+url target per planned path",
        detail,
        Status.PASS if reg_ok else Status.FAIL,
    )
    if not reg_ok:
        return

    # -- upload (expiry requeue, exact signed headers, 3 concurrent PUTs) --
    upload_failures = _run_uploads(adapter, capture_id, plan, round_plan)
    record(
        sink,
        transport,
        "lifecycle.upload",
        f"{_ORACLE_PREFIX} + ScopedCaptureHTTP: storage PUT with exactly the "
        "signed headers (no default Content-Type, never an Authorization); "
        "3 concurrent PUTs; one BadDigest retry on the first 400; 403 "
        "re-registers (bounded 6); URLs expiring within 60 s are re-queued",
        "2xx for every planned file",
        upload_failures or res,
        Status.PASS if not upload_failures else Status.FAIL,
    )

    # -- commit (STRICT membership: every planned path committed) ----------
    res = adapter.commit(capture_id, descriptors)
    membership = _ctx(res).get("commit_membership")
    if membership is None and res.http_status is not None and 200 <= res.http_status < 300:
        # Non-direct adapters may not stash the view; fall back to the
        # sanitized safe_values (committed list only).
        committed = _safe_value(res, "committed")
        membership = {"committed": committed or []}
    if res.http_status is None:
        commit_status: Status = Status.BLOCKED
        commit_detail = res
    elif membership is None:
        commit_status = Status.FAIL
        commit_detail = "commit answer carried no membership view"
    else:
        commit_ok, commit_detail_text = strict_commit_verdict(planned_paths, membership)
        commit_status = Status.PASS if commit_ok else Status.FAIL
        commit_detail = commit_detail_text if not commit_ok else res
    record(
        sink,
        transport,
        "lifecycle.commit",
        f"{_ORACLE_PREFIX}: POST /captures/{{id}}/files:commit with per-file "
        "path+sha256; STRICT membership — every planned path committed and "
        "notFound/mismatch empty; a partial commit FAILS",
        "every planned path in committed; notFound/mismatch empty",
        commit_detail,
        commit_status,
    )

    # -- finalize ------------------------------------------------------------
    res = adapter.finalize(capture_id, plan.packet, plan.packet_id)
    finalize_run_id = _ctx(res).get("finalize_run_id")
    ok = res.http_status is not None and 200 <= res.http_status < 300
    status_val = Status.PASS if ok else Status.FAIL
    if res.http_status is None:
        status_val = Status.BLOCKED
    record(
        sink,
        transport,
        "lifecycle.finalize",
        f"{_ORACLE_PREFIX}: POST /captures/{{id}}/finalize with X-Packet-Sha256 "
        "(lowercase hex) and Idempotency-Key=packetId; the answer's runId binds "
        "the result read",
        "2xx finalize",
        res,
        status_val,
    )

    # -- events polling (published long-poll) ---------------------------------
    budget_deadline = time.monotonic() + result_budget_s
    cursor = 0
    last_status = None
    stalls = 0
    contract_ok = True
    contract_note = ""
    binding_violations: list[str] = []
    while time.monotonic() < budget_deadline:
        started = time.monotonic()
        res = adapter.events(capture_id, cursor, int(events_wait_s))
        if res.error:
            contract_ok = False
            contract_note = f"events transport error at cursor {cursor}: {res.error}"
            break
        view_now = _parse_public_view(res)
        seq_ok = isinstance(view_now.get("next"), int)
        if res.http_status is None or not (200 <= res.http_status < 300) or not seq_ok:
            contract_ok = False
            contract_note = f"events response violates published shape: {step_observed(res)}"
            break
        last_status = view_now.get("status")
        binding_violations.extend(
            verdict_events_binding(
                _ctx(res).get("verdict_ready_run_ids") or [], finalize_run_id
            )
        )
        next_val = view_now["next"]
        if next_val <= cursor:
            stalls += 1
            contract_note = (
                f"events cursor stalled {stalls}x ({cursor} -> {next_val})"
            )
            if stalls >= EVENTS_CURSOR_STALL_BOUND:
                contract_ok = False
                break
        else:
            stalls = 0
            contract_note = ""
        cursor = max(cursor, next_val)
        if last_status in TERMINAL_STATUSES:
            break
        # Pacing: fast, non-terminal answers sleep to the published
        # minimumPollInterval (CaptureUploader.swift:635-640).
        elapsed = time.monotonic() - started
        time.sleep(max(0.0, poll_interval_s - elapsed))
    else:
        record(
            sink,
            transport,
            "lifecycle.events",
            f"{_ORACLE_PREFIX}: GET events (long-poll wait={events_wait_s:.0f}s) "
            "until CaptureAPI.terminalStatuses — complete, manual_review, failed, "
            "expired, needs_views; EventsResponse.next is an advancing int",
            "terminal status within budget",
            f"budget of {result_budget_s:.0f}s exhausted without terminal status "
            f"(last observed: {last_status})",
            Status.BLOCKED,
        )
        _final_result_check(
            adapter, capture_id, transport, sink, last_status, finalize_run_id, no_poll=True
        )
        return
    if binding_violations:
        record(
            sink,
            transport,
            "lifecycle.events",
            f"{_ORACLE_PREFIX}: verdict_ready events bind data.runId nil-or-equal "
            "to the finalize's runId (CaptureUploader.swift:624)",
            "no foreign-run verdict events",
            "; ".join(binding_violations),
            Status.FAIL,
        )
        _final_result_check(
            adapter, capture_id, transport, sink, last_status, finalize_run_id
        )
        return
    if not contract_ok:
        record(
            sink,
            transport,
            "lifecycle.events",
            f"{_ORACLE_PREFIX}: GET events (long-poll wait={events_wait_s:.0f}s) "
            "until CaptureAPI.terminalStatuses — complete, manual_review, failed, "
            "expired, needs_views; EventsResponse.next is an advancing int",
            "terminal status within budget, contract-shaped answers",
            contract_note,
            Status.FAIL,
        )
        return
    record(
        sink,
        transport,
        "lifecycle.events",
        f"{_ORACLE_PREFIX}: GET events (long-poll wait={events_wait_s:.0f}s) "
        "until CaptureAPI.terminalStatuses — complete, manual_review, failed, "
        "expired, needs_views; EventsResponse.next is an advancing int",
        "terminal status within budget",
        res,
        Status.PASS,
    )
    _final_result_check(adapter, capture_id, transport, sink, last_status, finalize_run_id)


def _run_uploads(
    adapter: HunterIngestAdapter, capture_id: str, plan: CapturePlan, round_plan
) -> str | None:
    """PUT every planned file per the published storage rules.

    Returns None on success, or the first failure detail in plan order.
    """
    content_by_path = dict(plan.files)
    to_upload = dict(round_plan.to_upload)

    # Expiry pre-pass: re-register any signed URL within the published
    # 60 s margin before its PUT (requeueExpiring, CaptureUploader.swift
    # :721-726). Serial: re-registration is shared state.
    for path in list(to_upload):
        expires = parse_expires_at(to_upload[path].get("expires_at") or "")
        if expires is None or expires - time.time() >= EXPIRY_MARGIN_S:
            continue
        pending = [d for d in plan.file_descriptors if d["path"] == path]
        more, _reg_fail = _register_batched(adapter, capture_id, pending, BATCH_SIZE)
        fresh = (more or {}).get(path)
        if isinstance(fresh, dict) and isinstance(fresh.get("upload"), dict):
            to_upload[path] = fresh["upload"]

    def put_one(item) -> tuple[str, str | None]:
        path, target = item
        content = content_by_path[path]
        bad_digest = 0
        while True:
            res = adapter.upload(
                capture_id, path, content, target["url"], target.get("headers") or {}
            )
            if res.error:
                return path, step_observed(res)
            status = res.http_status
            if status is not None and 200 <= status < 300:
                return path, None
            if status == 400 and bad_digest < MAX_BAD_DIGEST_RETRIES:
                # One BadDigest retry of the same sealed bytes
                # (CaptureUploader.swift:563-590).
                bad_digest += 1
                continue
            return path, f"{path}: {step_observed(res)}"

    results: dict[str, str | None] = {}
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_PUTS) as pool:
        for path, err in pool.map(put_one, to_upload.items()):
            results[path] = err

    # 403 expired-signature: re-register for a fresh URL, bounded
    # (maxRefusedPuts = 6, CaptureUploader.swift). Serial.
    refused = 0
    for path in [p for p, err in results.items() if err and "403" in err]:
        while refused < MAX_REFUSED_PUTS:
            refused += 1
            pending = [d for d in plan.file_descriptors if d["path"] == path]
            more, reg_fail, _ = _register_batched(adapter, capture_id, pending, BATCH_SIZE)
            fresh = (more or {}).get(path)
            if reg_fail is not None or not isinstance(fresh, dict) or not fresh.get("url"):
                break
            _, err = put_one((path, fresh))
            results[path] = err
            if err is None:
                break

    for path in round_plan.already_committed:
        results[path] = None

    first_fail = None
    for descriptor in plan.file_descriptors:
        path = descriptor["path"]
        outcome = results.get(path, "no signed target")
        if outcome is not None and first_fail is None:
            first_fail = outcome
    return first_fail


def _record_result(
    sink, transport, oracle: str, observed, status: Status
) -> None:
    record(
        sink, transport, "lifecycle.result", oracle, "published-shaped result", observed, status
    )


def _final_result_check(
    adapter: HunterIngestAdapter,
    capture_id: str,
    transport: Transport,
    sink: list,
    last_status,
    finalize_run_id,
    no_poll: bool = False,
) -> None:
    """Result read per the published client: bounded not-ready reads, 2xx
    object decode, runId binding (CaptureUploader.swift:658-672), the
    statusesWithoutOutcome null-outcome rule, terminal acceptance."""
    oracle = (
        f"{_ORACLE_PREFIX} + CaptureResult: bounded result_not_ready reads "
        f"(max {MAX_NOT_READY_RESULTS}, honoring Retry-After); 2xx body must be "
        "a JSON object; runId binds to the finalize's runId "
        "(result_for_another_run otherwise); under statusesWithoutOutcome a "
        "null outcome is final; terminal statuses accept"
    )
    res = None
    for read in range(MAX_NOT_READY_RESULTS):
        res = adapter.result(capture_id)
        if res.error and res.http_status is None:
            _record_result(sink, transport, oracle, res, Status.BLOCKED)
            return
        codes = _ctx(res).get("error_codes") or []
        if res.http_status == 404 and "result_not_ready" in codes:
            if read < MAX_NOT_READY_RESULTS - 1:
                time.sleep(min(max(res.retry_after_s or 0.0, 0.0), 2.0))
            continue
        break
    assert res is not None
    if res.http_status is None:
        _record_result(sink, transport, oracle, res, Status.BLOCKED)
        return
    view = _ctx(res).get("result_view")
    if not (200 <= res.http_status < 300) or view is None:
        _record_result(sink, transport, oracle, res, Status.FAIL)
        return
    head_run_id = view.get("run_id")
    if head_run_id is not None and head_run_id != finalize_run_id:
        record(
            sink,
            transport,
            "lifecycle.result",
            oracle,
            "published-shaped result",
            f"result_for_another_run: result runId {head_run_id!r} != finalize "
            f"runId {finalize_run_id!r} (CaptureUploader.swift:663-665)",
            Status.FAIL,
        )
        return
    body_status = view.get("status")
    if body_status in STATUSES_WITHOUT_OUTCOME and view.get("outcome_present") is False:
        _record_result(sink, transport, oracle, res, Status.PASS)
        return
    if body_status is None:
        # Minimal body: decode + binding enforced; required-field presence
        # (status/viewsNeeded/memberActions) lands with the self-check
        # fake's conformant body (deferred seam, disclosed in the PR).
        if no_poll:
            record(
                sink,
                transport,
                "lifecycle.result",
                oracle,
                "published-shaped result",
                f"checked despite non-terminal state ({last_status}): {step_observed(res)}",
                Status.BLOCKED,
            )
            return
        _record_result(sink, transport, oracle, res, Status.PASS)
        return
    if body_status in TERMINAL_STATUSES:
        _record_result(sink, transport, oracle, res, Status.PASS)
        return
    # Non-terminal or unknown-passthrough body status: the published client
    # keeps polling; the bounded loop treats exhaustion as not-ready.
    detail = f"result never reached a published terminal state (status {body_status!r})"
    record(
        sink,
        transport,
        "lifecycle.result",
        oracle,
        "published-shaped result",
        f"checked despite non-terminal state: {detail}" if no_poll else detail,
        Status.BLOCKED if no_poll else Status.FAIL,
    )
