"""Register and commit in batches, shared by the lifecycle and the controls.

CaptureUploader sends at most ``min(Policy.batch, upload.maxBatch)`` files
per register or commit call. Both rounds here honor the same limit and
aggregate the answers by path, so a server that enforces its advertised
batch size sees the same requests from a control as from the lifecycle.
"""

from __future__ import annotations

import dataclasses

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


def register_round(adapter, capture_id: str, files, size: int, declared: frozenset[str]) -> Round:
    out = Round()
    for chunk in _chunks(files, size):
        res = adapter.register(capture_id, [f.register_entry() for f in chunk])
        out.last = res
        if res.error:
            out.status = error_status(res)
            out.batches.append(step_view(res))
            return out
        dec = (
            decode_register(res.parsed, [f.path for f in chunk], declared) if is_2xx(res) else None
        )
        out.batches.append(step_view(res, dec))
        if dec is None:
            out.status = Status.FAIL
            return out
        out._absorb(dec.violations)
        out.targets.update(dec.data["targets"])
        out.committed.update(dec.data["committed"])
        if not dec.ok:
            out.status = Status.FAIL
    return out


def commit_round(adapter, capture_id: str, files, size: int, declared: frozenset[str]) -> Round:
    out = Round()
    for chunk in _chunks(files, size):
        requested = [f.path for f in chunk]
        res = adapter.commit(capture_id, [f.commit_entry() for f in chunk])
        out.last = res
        if res.error:
            out.status = error_status(res)
            out.batches.append(step_view(res))
            return out
        dec = decode_commit(res.parsed, requested, declared) if is_2xx(res) else None
        out.batches.append(step_view(res, dec))
        if dec is None:
            out.status = Status.FAIL
            out.exact = False
            return out
        out._absorb(dec.violations)
        out.committed.update(p for p in dec.data["committed"] if p in requested)
        out.not_found.update(dec.data["notFound"])
        out.mismatch.update(dec.data["mismatch"])
        exact = (
            dec.ok
            and set(dec.data["committed"]) == set(requested)
            and not dec.data["notFound"]
            and not dec.data["mismatch"]
        )
        if not exact:
            out.exact = False
        if not dec.ok:
            out.status = Status.FAIL
    return out
