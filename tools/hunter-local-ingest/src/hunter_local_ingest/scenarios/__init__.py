"""Scenario plumbing shared by the lifecycle, control and fault scenarios.

Scenarios are the only place PASS/FAIL is decided. A check line is
``{id, transport, status, observed, observation}``: the oracle and
expectation come from scenarios/oracles.py by id, ``observed`` is built
only from decoder public views and package constants, and
``observation`` is the in-process Observation behind it, which the
frozen-trace comparison reads and receipts never serialize.

Sink interface: anything with ``.append(line_dict)``. The runner supplies
the receipt-backed sink; tests may pass a plain list.
"""

from __future__ import annotations

from ..adapter import StepResult
from ..contract import decode_errors
from ..modes import Status, Transport
from ..observations import Observation
from .oracles import GPU, LIFECYCLE, oracle_for

LIFECYCLE_IDS = tuple(LIFECYCLE)


class PrefixedSink:
    """Rewrites check ids through a prefix, so a second lifecycle round
    (the fault round through the proxy) keeps its own receipt lines."""

    def __init__(self, sink, prefix: str) -> None:
        self._sink = sink
        self._prefix = prefix

    def append(self, line: dict) -> None:
        self._sink.append({**line, "id": f"{self._prefix}{line['id']}"})


def record(
    sink,
    transport: Transport,
    check_id: str,
    status: Status,
    observed: dict,
    observation: Observation | None = None,
) -> Status:
    oracle_for(check_id)  # an unregistered id fails here, at the scenario
    sink.append(
        {
            "id": check_id,
            "transport": transport.value,
            "status": status.value,
            "observed": observed,
            "observation": observation,
        }
    )
    return status


def step_view(result: StepResult, decoded=None) -> dict:
    """The public record of one step: HTTP facts, decoded view, violations."""
    view: dict = {"http": result.public()}
    if decoded is not None:
        view["contract"] = decoded.public
        view["violations"] = list(decoded.violations)
    elif (
        result.observation is not None
        and result.http_status is not None
        and not (200 <= result.http_status < 300)
    ):
        errors = decode_errors(result.parsed)
        if errors.ok:
            view["errors"] = errors.public
    return view


def is_2xx(result: StepResult) -> bool:
    status = result.http_status
    return status is not None and 200 <= status < 300


def skip_rest(sink, transport: Transport, ids, note: str, prefix: str = "") -> None:
    """Steps that depend on one that did not complete are BLOCKED, named."""
    for check_id in ids:
        record(sink, transport, prefix + check_id, Status.BLOCKED, {"note": note})


def gpu_stub_lines(sink) -> None:
    """Named GPU arms as NOT_RUN rows; this harness never executes them."""
    for check_id in GPU:
        record(sink, Transport.GPU_STUB, check_id, Status.NOT_RUN, {"note": "gpu-not-authorized"})


def unreachable_lines(sink, transport: Transport) -> None:
    """Every main-round check BLOCKED, named, when the endpoint never answered."""
    skip_rest(sink, transport, LIFECYCLE_IDS, "endpoint-unreachable")
    skip_rest(
        sink,
        transport,
        (
            "control.idempotent-create",
            "control.corrupt-upload",
            "control.truncated-upload",
            "control.wrong-capture",
        ),
        "endpoint-unreachable",
    )
