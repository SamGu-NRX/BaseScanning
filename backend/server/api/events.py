"""Long-poll. An in-process event wakes waiters; the store is still the source of the rows."""

from __future__ import annotations

import asyncio
import time

from server.api.deps import Deps
from server.api.errors import fail
from server.contracts.api import Event, EventsResponse
from server.contracts.jobs import CaptureRecord


def emit(deps: Deps, capture_id: str, type: str, data: dict):
    record = deps.state.append_event(capture_id, type, data)
    waiter = deps.wake.get(capture_id)
    if waiter is not None:
        waiter.set()
    return record


def _waiter(deps: Deps, capture_id: str) -> asyncio.Event:
    loop = asyncio.get_running_loop()
    waiter = deps.wake.get(capture_id)
    bound = getattr(waiter, "_loop", None) if waiter is not None else None
    if waiter is None or (bound is not None and bound is not loop):
        waiter = asyncio.Event()
        deps.wake[capture_id] = waiter
    return waiter


async def poll_events(deps: Deps, record: CaptureRecord, after: int, wait_s: float) -> EventsResponse:
    current = deps.polls.get(record.capture_id, 0)
    if current >= 8:
        raise fail(429, "too_many_polls", "/query/wait", "too many polls", **{"Retry-After": "1"})
    deps.polls[record.capture_id] = current + 1
    started = time.perf_counter()
    try:
        deadline = time.monotonic() + wait_s
        waiter = _waiter(deps, record.capture_id)
        # Only same-process server.api.events.emit sets the wake event. The
        # orchestrator's server.orchestrator.events.emit writes straight to state
        # storage, and a separate worker or Modal deployment shares no process with
        # this loop, so its writers can never set the waiter. Wait in slices of
        # HSS_INGEST_EVENTS_POLL_MS and re-read the store between slices, so the
        # poll observes events from any writer while an in-process emit still
        # wakes it the moment the waiter is set. With ModalDictState the store
        # reads are synchronous remote calls, so every read runs in a worker
        # thread (asyncio.to_thread): on the event loop, each slice would park
        # the whole app for the length of its read.
        slice_s = deps.settings.HSS_INGEST_EVENTS_POLL_MS / 1000
        while True:
            rows = await asyncio.to_thread(deps.state.events_after, record.capture_id, after, limit=100)
            timed_out = time.monotonic() >= deadline
            if rows or wait_s == 0 or timed_out:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(waiter.wait(), timeout=min(slice_s, remaining))
            except TimeoutError:
                pass
            waiter.clear()
        rows = await asyncio.to_thread(deps.state.events_after, record.capture_id, after, limit=100)
        fresh = await asyncio.to_thread(deps.state.get_capture, record.capture_id) or record
        events = [Event(seq=row.seq, type=row.type, at=row.at, data=row.data) for row in rows]
        nxt = events[-1].seq if events else after
        return EventsResponse(status=fresh.status, next=nxt, events=events)
    finally:
        deps.polls[record.capture_id] = deps.polls.get(record.capture_id, 1) - 1
        from server.common.logging import log_event

        log_event(
            "events.poll",
            after=after,
            wait_s=wait_s,
            returned=int((time.perf_counter() - started) * 1000),
            capture_id=record.capture_id,
        )
