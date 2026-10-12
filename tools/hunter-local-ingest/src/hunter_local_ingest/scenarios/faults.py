"""The FAKE_HUNTER fault round: injected conditions and the client's reaction.

Each check gets its own proxy, so hit counts belong to that check alone,
and its own derived packet id, so no fault lands on the baseline capture.
Every row is labeled FAKE_HUNTER whatever the endpoint behind the proxy is:
the injected condition is the harness's, not the backend's.
"""

from __future__ import annotations

from collections.abc import Callable

from ..contract import decode_create
from ..faultproxy import serve
from ..http_client import DirectHttpClient
from ..modes import Status, Transport
from ..plan import CapturePlan
from . import PrefixedSink, is_2xx, record, step_view
from .lifecycle import run_lifecycle

FAKE = Transport.FAKE_HUNTER


def run_fault_round(
    origin_url: str,
    plan: CapturePlan,
    sink,
    *,
    client_factory: Callable[..., DirectHttpClient],
    result_budget_s: float,
    poll_interval_s: float,
    events_wait_s: int,
) -> None:
    # 1. The whole lifecycle through a slow proxy: the proxy itself is
    # transparent, so a failure here is not the fault's doing.
    proxy = serve(origin_url, {"create": {"kind": "delay", "ms": 100}})
    try:
        run_lifecycle(
            client_factory(proxy.base_url),
            plan.derive("fh"),
            FAKE,
            PrefixedSink(sink, "fault."),
            result_budget_s=result_budget_s,
            poll_interval_s=poll_interval_s,
            events_wait_s=events_wait_s,
        )
    finally:
        proxy.stop()

    _create_fault(
        origin_url,
        plan.derive("f-retry"),
        sink,
        client_factory,
        check_id="fault.retry-transient",
        faults={"create": {"kind": "status", "code": 503, "retry_after": 0, "times": 1}},
        max_retries=2,
        expect=lambda res, dec, hits: (
            dec is not None and dec.ok and res.attempts == 2 and hits == 2
        ),
    )
    _create_fault(
        origin_url,
        plan.derive("f-501"),
        sink,
        client_factory,
        check_id="fault.no-retry-501",
        faults={"create": {"kind": "status", "code": 501}},
        max_retries=2,
        expect=lambda res, dec, hits: res.http_status == 501 and res.attempts == 1 and hits == 1,
    )
    _create_fault(
        origin_url,
        plan.derive("f-abort"),
        sink,
        client_factory,
        check_id="fault.abort",
        faults={"create": {"kind": "abort"}},
        max_retries=1,
        expect=lambda res, dec, hits: (
            res.error is not None
            and res.error.startswith("transport:")
            and res.attempts == 2
            and hits == 2
        ),
    )
    _create_fault(
        origin_url,
        plan.derive("f-trunc"),
        sink,
        client_factory,
        check_id="fault.truncated-response",
        faults={"create": {"kind": "truncate_response", "bytes": 5}},
        max_retries=0,
        expect=lambda res, dec, hits: (
            res.error == "transport:incomplete-read" and res.attempts == 1 and hits == 1
        ),
    )


def _create_fault(origin_url, plan, sink, client_factory, *, check_id, faults, max_retries, expect):
    proxy = serve(origin_url, faults)
    try:
        client = client_factory(proxy.base_url, max_retries=max_retries, retry_backoff_s=0.0)
        res = client.create(plan.create_body, plan.packet_id)
    finally:
        proxy.stop()
    hits = proxy.hit_counts.get("create", 0)
    dec = decode_create(res.parsed) if is_2xx(res) else None
    ok = bool(expect(res, dec, hits))
    record(
        sink,
        FAKE,
        check_id,
        Status.PASS if ok else Status.FAIL,
        {**step_view(res, dec), "proxy_hits": hits},
        res.observation,
    )
