"""Control scenarios: does the client harness catch what it claims to catch.

Every check here has a deliberately prepared wrong condition. The control
PASSES when the harness (client + this scenario logic) detects the wrong
condition, and FAILS when it sails through — a FAIL means the harness is
blind, whatever the backend did.

`captureId` values for wrong-capture reads are minted in-harness (prefixed
`bogus-`), never scraped from any real system.
"""

from __future__ import annotations

import hashlib

from ..adapter import HunterIngestAdapter
from ..modes import Status, Transport
from . import CapturePlan, record, step_observed


def _safe_list(result, key: str) -> list:
    if result.observation and result.observation.safe_values:
        value = result.observation.safe_values.get(key)
        return value if isinstance(value, list) else []
    return []


def _safe_value(result, key: str):
    if result.observation and result.observation.safe_values:
        return result.observation.safe_values.get(key)
    return None


def run_controls(
    adapter: HunterIngestAdapter,
    plan_a: CapturePlan,
    transport: Transport,
    sink: list,
    *,
    restart_command: str | None = None,
) -> None:
    """Client-harness controls around idempotency, byte integrity, capture isolation."""

    # -- control: idempotent create -----------------------------------------
    res1 = adapter.create(plan_a.create_request, plan_a.packet_id)
    res2 = (
        adapter.create(plan_a.create_request, plan_a.packet_id)
        if not res1.error
        else res1
    )
    first_id = _safe_value(res1, "captureId")
    second_id = _safe_value(res2, "captureId")
    if res1.error or res2.error:
        observed = res1.error or res2.error or ""
        record(
            sink,
            transport,
            "control.idempotent-create",
            "published CaptureUploader: create runs once per packetId under "
            "Idempotency-Key=packetId; a repeated create must not mint a second capture "
            "(provisional until backend baseline confirms answer codes)",
            "same captureId (or the published replay code) on repeat",
            f"transport: {observed}",
            Status.BLOCKED,
        )
    elif first_id and second_id and first_id == second_id:
        record(
            sink,
            transport,
            "control.idempotent-create",
            "published CaptureUploader: create runs once per packetId under "
            "Idempotency-Key=packetId; a repeated create must not mint a second capture "
            "(provisional until backend baseline confirms answer codes)",
            "same captureId (or the published replay code) on repeat",
            f"both creates returned captureId (identical: {first_id == second_id}); "
            f"second create status {res2.http_status}",
            Status.PASS,
        )
    else:
        record(
            sink,
            transport,
            "control.idempotent-create",
            "published CaptureUploader: create runs once per packetId under "
            "Idempotency-Key=packetId; a repeated create must not mint a second capture "
            "(provisional until backend baseline confirms answer codes)",
            "same captureId (or the published replay code) on repeat",
            f"create #1 -> {first_id}, create #2 -> {second_id} (status {res2.http_status})",
            Status.FAIL,
        )

    # -- control: corrupted upload rejected at commit -------------------------
    status_val, observed = _integrity_control(adapter, plan_a, corrupt=True)
    record(
        sink,
        transport,
        "control.corrupt-upload",
        "CommitResponse separates committed from mismatch/notFound; flipped bytes "
        "must never be reported committed",
        "corrupt file NOT committed (mismatch/notFound/error)",
        observed,
        status_val,
    )

    # -- control: truncated upload rejected at commit -------------------------
    status_val, observed = _integrity_control(adapter, plan_a, corrupt=False, truncate=True)
    record(
        sink,
        transport,
        "control.truncated-upload",
        "CommitResponse separates committed from mismatch/notFound; a truncated "
        "upload must never be reported committed",
        "truncated file NOT committed (mismatch/notFound/error)",
        observed,
        status_val,
    )

    # -- control: wrong-capture isolation -------------------------------------
    bogus = f"bogus-{plan_a.packet_id[-8:]}"
    res = adapter.result(bogus)
    res_status = res.http_status
    ok = res.error is None and res_status is not None and res_status >= 400
    if res.error:
        record(
            sink,
            transport,
            "control.wrong-capture",
            "CaptureAPI.captureGone handling: reads against a nonexistent capture "
            "return a non-2xx answer, never data (provisional)",
            "non-2xx on result read for a bogus id",
            f"transport: {res.error}",
            Status.BLOCKED,
        )
    else:
        record(
            sink,
            transport,
            "control.wrong-capture",
            "CaptureAPI.captureGone handling: reads against a nonexistent capture "
            "return a non-2xx answer, never data (provisional)",
            "non-2xx on result read for a bogus id",
            f"result read returned {res_status}",
            Status.PASS if ok else Status.FAIL,
        )

    # -- control: restart recovery ----------------------------------------------
    if restart_command:
        record(
            sink,
            transport,
            "control.restart-recovery",
            "published run guidance: a restarted local backend must still answer "
            "for existing captures",
            "post-restart events read answers 2xx for the earlier capture",
            "not exercised in this run: restart harness lands with the baseline run",
            Status.NOT_RUN,
        )
    else:
        record(
            sink,
            transport,
            "control.restart-recovery",
            "published run guidance: a restarted local backend must still answer "
            "for existing captures",
            "post-restart events read answers 2xx for the earlier capture",
            "not run: no --restart-command given",
            Status.NOT_RUN,
        )


def _integrity_control(
    adapter: HunterIngestAdapter,
    plan: CapturePlan,
    *,
    corrupt: bool,
    truncate: bool = False,
) -> tuple[Status, str]:
    """Full mini-lifecycle for one tampered file; decision made at commit."""
    res = adapter.create(plan.create_request, plan.packet_id)
    capture_id = _safe_value(res, "captureId")
    if res.error or not capture_id:
        return Status.BLOCKED, f"create step unavailable: {step_observed(res)}"

    descriptors = []
    contents: dict[str, bytes] = {}
    for path, content in plan.files:
        # Descriptors describe the ORIGINAL content (what registration
        # promised); the PUT carries tampered bytes — the network-corruption
        # simulation the control exists for.
        tampered = content
        if corrupt:
            tampered = bytes([content[0] ^ 0xFF]) + content[1:]
        if truncate:
            tampered = tampered[:-1]
        contents[path] = tampered
        descriptors.append(
            {
                "path": path,
                "role": plan.roles.get(path, "keyframe"),
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        )

    res = adapter.register(capture_id, descriptors)
    ctx = res.context or {}
    targets = ctx.get("upload_targets") or []
    if res.error or not targets:
        return Status.BLOCKED, f"register step unavailable: {step_observed(res)}"

    for (path, _), target in zip(
            plan.files, targets, strict=False
        ):  # a count mismatch fails at commit  # a count mismatch fails at commit
        res = adapter.upload(
            capture_id, path, contents[path], target.get("url", ""), target.get("headers")
        )
        if res.error or not (res.http_status and 200 <= res.http_status < 300):
            return Status.BLOCKED, f"upload step unavailable for {path}: {step_observed(res)}"

    res = adapter.commit(capture_id, descriptors)
    if res.error:
        return Status.BLOCKED, f"commit transport error: {res.error}"
    committed = _safe_list(res, "committed")
    mismatch = _safe_list(res, "mismatch")
    not_found = _safe_list(res, "notFound")
    res_status = res.http_status
    if res_status and 400 <= res_status < 500:
        return Status.PASS, f"commit answered {res_status} for the tampered file"
    if not committed:
        return Status.PASS, f"commit answered {res_status}, committed={committed}, " \
            f"mismatch={mismatch}, notFound={not_found}"
    return Status.FAIL, (
        f"tampered file reported committed={committed} "
        f"(mismatch={mismatch}, notFound={not_found})"
    )
