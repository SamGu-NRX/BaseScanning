"""Register and commit in batches, shared by the lifecycle and the controls.

CaptureUploader sends at most ``min(Policy.batch, upload.maxBatch)`` files
per register or commit call. Both rounds here honor the same limit and
aggregate the answers by path, so a server that enforces its advertised
batch size sees the same requests from a control as from the lifecycle.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable

from ..adapter import CLIENT_REFUSALS, StepResult
from ..contract import decode_commit, decode_register
from ..modes import Status
from ..plan import PlanFile
from . import is_2xx, step_view

# CaptureUploader.Policy.batch.
DEFAULT_BATCH = 10


def batch_size(max_batch: int | None) -> int:
    return min(DEFAULT_BATCH, max_batch or DEFAULT_BATCH)


def error_status(result: StepResult) -> Status:
    """No answer is BLOCKED; a request the client refused to send is FAIL."""
    return Status.FAIL if result.error in CLIENT_REFUSALS else Status.BLOCKED


def _chunks(files: tuple[PlanFile, ...] | list[PlanFile], size: int):
    for start in range(0, len(files), size):
        yield files[start : start + size]


@dataclasses.dataclass
class Round:
    status: Status = Status.PASS
    batches: list[dict] = dataclasses.field(default_factory=list)
    violations: list[str] = dataclasses.field(default_factory=list)
    last: StepResult | None = None
    # register
    targets: dict[str, dict] = dataclasses.field(default_factory=dict)
    # paths the server acknowledged as committed (register state, or commit)
    committed: set[str] = dataclasses.field(default_factory=set)
    # commit
    not_found: set[str] = dataclasses.field(default_factory=set)
    mismatch: set[str] = dataclasses.field(default_factory=set)
    # every commit answer returned exactly its requested paths as committed
    exact: bool = True

    def _absorb(self, violations) -> None:
        self.violations += [v for v in violations if v not in self.violations]

    def settle(self, status: Status) -> None:
        """Keep the worse outcome: a confirmed FAIL is never lowered to BLOCKED."""
        if self.status is not Status.FAIL:
            self.status = status


def register_round(adapter, capture_id: str, files, size: int, declared: frozenset[str]) -> Round:
    out = Round()
    for chunk in _chunks(files, size):
        res = adapter.register(capture_id, [f.register_entry() for f in chunk])
        out.last = res
        if res.error:
            out.settle(error_status(res))
            out.batches.append(step_view(res))
            return out
        dec = (
            decode_register(res.parsed, [f.path for f in chunk], declared) if is_2xx(res) else None
        )
        out.batches.append(step_view(res, dec))
        if dec is None:
            out.settle(Status.FAIL)
            return out
        out._absorb(dec.violations)
        out.targets.update(dec.data["targets"])
        out.committed.update(dec.data["committed"])
        if not dec.ok:
            # An invalid answer decides the round; later batches are not sent.
            out.settle(Status.FAIL)
            return out
    return out


def exact_commit(requested: list[str], dec) -> bool:
    """The lifecycle's batch check: every requested path committed, none refused."""
    return (
        set(dec.data["committed"]) == set(requested)
        and not dec.data["notFound"]
        and not dec.data["mismatch"]
    )


def commit_round(
    adapter,
    capture_id: str,
    files,
    size: int,
    declared: frozenset[str],
    accept: Callable[[list[str], object], bool] = exact_commit,
) -> Round:
    """Commit in batches; ``accept(requested, decoded)`` judges each answer.

    The scenario supplies ``accept``, because what a valid answer may say
    depends on it: the lifecycle needs every path committed, a tamper
    control expects its victim refused. The first answer that is invalid or
    that ``accept`` rejects ends the round as FAIL, and no later batch is
    sent, so a later batch with no answer cannot turn it into BLOCKED.
    """
    out = Round()
    for chunk in _chunks(files, size):
        requested = [f.path for f in chunk]
        res = adapter.commit(capture_id, [f.commit_entry() for f in chunk])
        out.last = res
        if res.error:
            out.settle(error_status(res))
            out.batches.append(step_view(res))
            return out
        dec = decode_commit(res.parsed, requested, declared) if is_2xx(res) else None
        out.batches.append(step_view(res, dec))
        if dec is None:
            out.settle(Status.FAIL)
            out.exact = False
            return out
        out._absorb(dec.violations)
        out.committed.update(p for p in dec.data["committed"] if p in requested)
        out.not_found.update(dec.data["notFound"])
        out.mismatch.update(dec.data["mismatch"])
        if not (dec.ok and exact_commit(requested, dec)):
            out.exact = False
        if not dec.ok or not accept(requested, dec):
            out.settle(Status.FAIL)
            return out
    return out
