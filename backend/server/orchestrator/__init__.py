"""Orchestrator entry points (SD-23)."""

from __future__ import annotations

from server.orchestrator.api import (
    cancel_capture,
    dispatch_early,
    on_confirmation,
    prewarm,
    start_run,
    sweep,
)

__all__ = [
    "cancel_capture",
    "dispatch_early",
    "on_confirmation",
    "prewarm",
    "start_run",
    "sweep",
    "validate_manifest",
]


def __getattr__(name: str):
    """Lazy re-export so importing the orchestrator does not import packet validation first."""
    if name == "validate_manifest":
        from server.packet.validate import validate_manifest

        return validate_manifest
    raise AttributeError(name)
