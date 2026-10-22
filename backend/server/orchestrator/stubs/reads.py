"""Stub ``reads`` stage. Meter number is the synthetic placeholder, never a real plate."""

from __future__ import annotations

import json
from pathlib import Path

from server.contracts.jobs import StageContext, StageResult
from server.orchestrator.stubs._common import json_bytes, write_files

_STUB = Path(__file__).resolve().parents[1] / "tests" / "data" / "reads.stub.json"


def run(ctx: StageContext) -> StageResult:
    raw = json.loads(_STUB.read_text()) if _STUB.is_file() else {"meter_number": "000000000"}
    body: dict = {
        "format": "base-reads/v0",
        "capture_id": ctx.capture_id,
        "facts": [],
        "meter_number": raw["meter_number"],
    }
    # The reads stage is the application point for confirmations accepted before
    # reads existed; the stub path applies the same persisted slots so an early
    # answer reaches the composed result in stub mode too. Without pending slots
    # the stub body is byte-identical to the old shape.
    try:
        from server.reads.pending import pending_slots
        from server.reads.types import ConfirmBlock, MeterRead, Reads

        rows = pending_slots(ctx.state, ctx.capture_id)
        if rows or ctx.inputs.get("immutable_corrections"):
            reads = Reads(
                capture_id=ctx.capture_id,
                status="complete",
                meter=MeterRead(
                    status="auto_fill",
                    value=str(raw["meter_number"]),
                    core=str(raw["meter_number"]),
                    confidence=0.5,
                ),
                confirm=ConfirmBlock(candidates=[str(raw["meter_number"])]),
            )
            from server.reads.pending import apply_pending

            if ctx.inputs.get("immutable_corrections"):
                from server.orchestrator.corrections import import_legacy_corrections

                import_legacy_corrections(ctx.state, ctx.capture_id, ctx.state.get_run(ctx.run_id))
            else:
                reads = apply_pending(ctx.state, ctx.capture_id, reads)
            body = reads.model_dump(mode="json")
    except ImportError:
        pass
    return write_files(ctx, {"reads/reads.json": json_bytes(body)})
