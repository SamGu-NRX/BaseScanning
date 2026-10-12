"""Every check this harness can emit, with its oracle and expectation.

A receipt line's ``oracle`` and ``expected`` come from this table by check
id, never from the caller, so they are constants of the package. The export
validator (export.py) rejects an id that is not here.

``HSK`` abbreviates ios/HouseScanKit/Sources/HouseScanKit at alpha 263827ef.
"""

from __future__ import annotations

HSK = "HouseScanKit @ alpha 263827ef"

LIFECYCLE: dict[str, tuple[str, str]] = {
    "lifecycle.health": (
        "tools/integration-viewer reads GET /v1/healthz; HouseScanKit never calls it",
        "an HTTP 2xx answer with status ok",
    ),
    "lifecycle.create": (
        f"{HSK} CaptureUploader.create: POST captures with Idempotency-Key=packetId; "
        "the answer decodes as CaptureAPI.CreateResponse",
        "2xx, a captureId and a published capture status",
    ),
    "lifecycle.register": (
        f"{HSK} CaptureUploader.register: the answer decodes as RegisterResponse and is "
        "applied by path; a target needs method PUT and a URL that passes uploadURLProblem",
        "every requested path answered exactly once, no other path, each committed or "
        "given a usable PUT target",
    ),
    "lifecycle.upload": (
        f"{HSK} CaptureUploader.put + ScopedCaptureHTTP.upload: exactly the signed headers, "
        "never Authorization",
        "2xx for every file that got a target",
    ),
    "lifecycle.commit": (
        f"{HSK} CaptureUploader.commit: only a commit acknowledges a file; CommitResponse "
        "carries committed, notFound and mismatch",
        "committed equals the uploaded set; notFound and mismatch empty; every declared "
        "path committed by register or commit",
    ),
    "lifecycle.finalize": (
        f"{HSK} CaptureUploader.finalize: X-Packet-Sha256 and Idempotency-Key=packetId; "
        "the answer decodes as FinalizeResponse",
        "2xx, a runId, a published status, no missing file",
    ),
    "lifecycle.events": (
        f"{HSK} CaptureUploader.pollEvents: EventsResponse until CaptureAPI.terminalStatuses; "
        "next never moves back; a verdict names this run; retry_finalize re-sends the packet",
        "a terminal status within the budget, every answer contract-shaped",
    ),
    "lifecycle.result": (
        f"{HSK} CaptureUploader.fetchResult: the whole body decodes as CaptureResult.Response, "
        "its runId is the finalized run, and a null outcome is final only under "
        "statusesWithoutOutcome",
        "a decodable result for this run whose status matches the terminal event status",
    ),
}

CONTROLS: dict[str, tuple[str, str]] = {
    "control.idempotent-create": (
        f"{HSK} CaptureUploader.start: create is bound to Idempotency-Key=packetId, so a "
        "repeated create with the same bytes names the same capture",
        "both creates 2xx with the same captureId",
    ),
    "control.corrupt-upload": (
        f"{HSK} CommitResponse separates committed from mismatch/notFound; one file's bytes "
        "are flipped after its digest was registered",
        "intact files committed; the flipped file in mismatch or notFound, or its PUT "
        "refused, and never committed",
    ),
    "control.truncated-upload": (
        f"{HSK} CommitResponse separates committed from mismatch/notFound; one file loses "
        "its last byte after its digest was registered",
        "intact files committed; the truncated file in mismatch or notFound, or its PUT "
        "refused, and never committed",
    ),
    "control.wrong-capture": (
        f"{HSK} CaptureUploader.captureGone: an unknown capture answers 404 capture_not_found",
        "404 with error code capture_not_found for an id the harness invented",
    ),
    "control.restart-recovery": (
        "operator restart command, then the baseline capture is read again over the "
        "published events and result routes",
        "after a successful restart the capture still reports its terminal status and run",
    ),
}

FAULTS: dict[str, tuple[str, str]] = {
    "fault.retry-transient": (
        f"{HSK} CaptureUploader.isTransient: a 503 is retried with the same body",
        "the proxy's single 503 is followed by a second attempt that creates the capture",
    ),
    "fault.no-retry-501": (
        f"{HSK} CaptureUploader.isTransient excludes 501",
        "exactly one attempt and a reported non-2xx answer",
    ),
    "fault.abort": (
        "an answer-less connection is a transport failure, retried a bounded number of times",
        "a transport error category after 1 + max_retries attempts, no capture id taken",
    ),
    "fault.truncated-response": (
        "a body shorter than its Content-Length is a transport failure, never a parsed answer",
        "a transport error category, no capture id taken",
    ),
}

TRACE: dict[str, tuple[str, str]] = {
    "trace.expected-lifecycle-v2": (
        "frozen expected trace traces/expected-lifecycle-v2.jsonl, written from the "
        "published contract",
        "every traced check present and PASS with the traced status class and fields",
    ),
}

GPU: dict[str, tuple[str, str]] = {
    f"gpu-stub.{arm}": (
        "GPU-dependent arms are named and never executed by this harness",
        "not run",
    )
    for arm in ("objects", "vlm", "dense")
}

HARNESS: dict[str, tuple[str, str]] = {
    "harness.internal-error": (
        "the harness itself raised while running a scenario",
        "no exception",
    ),
}

# The fault round re-runs the lifecycle through the proxy under this prefix.
FAULT_LIFECYCLE_PREFIX = "fault."

ORACLES: dict[str, tuple[str, str]] = {
    **LIFECYCLE,
    **{FAULT_LIFECYCLE_PREFIX + k: v for k, v in LIFECYCLE.items()},
    **CONTROLS,
    **FAULTS,
    **TRACE,
    **GPU,
    **HARNESS,
}


def oracle_for(check_id: str) -> tuple[str, str]:
    try:
        return ORACLES[check_id]
    except KeyError:
        raise KeyError(f"unregistered check id {check_id!r}") from None
