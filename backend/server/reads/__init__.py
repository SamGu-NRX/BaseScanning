"""Stage `reads`. WS-03 calls `run`; the early path calls `read_still` directly."""

from __future__ import annotations

import json
from pathlib import Path

from server.contracts.jobs import StageContext, StageResult
from server.reads.aggregate import build_reads, reads_status, write_reads
from server.reads.config import ReadsConfig
from server.reads.worker import read_still

STAGE = "reads"

__all__ = ["STAGE", "read_still", "reads_status", "run"]


def run(ctx: StageContext) -> StageResult:
    cfg = ReadsConfig.from_env(ctx.settings)
    cfg = ReadsConfig(
        **{
            **cfg.__dict__,
            "budget_usd_per_house": cfg.effective_budget(ctx.vlm_budget_usd),
        }
    )
    packet = _packet(ctx)
    reads = build_reads(ctx.capture_id, ctx.storage, cfg, packet)
    # Orchestrated reads are the base, not a mutable confirmation accumulator.
    # Criteria folds the immutable correction sequence onto its private copy.
    if ctx.inputs.get("immutable_corrections"):
        from server.orchestrator.corrections import import_legacy_corrections

        import_legacy_corrections(ctx.state, ctx.capture_id, ctx.state.get_run(ctx.run_id))
    else:
        from server.reads.pending import apply_pending

        reads = apply_pending(ctx.state, ctx.capture_id, reads)
    rel = write_reads(
        ctx.capture_id,
        ctx.run_id,
        ctx.storage,
        reads,
        work_dir=ctx.work_dir,
        attempt=ctx.attempt,
    )
    return StageResult(
        artifacts={"reads/reads.json": rel},
        metrics={"cost_usd": reads.cost_usd, "stills": float(len(reads.timings_s))},
        warnings=list(reads.warnings),
    )


def _packet(ctx: StageContext) -> dict | None:
    packet_dir = ctx.inputs.get("packet_dir")
    if not packet_dir:
        return None
    path = Path(packet_dir) / "packet.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())
