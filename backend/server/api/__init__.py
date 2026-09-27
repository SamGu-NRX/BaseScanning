"""HTTP app stub. WS-01 implements create_app. This package does not import server.packet."""

from fastapi import FastAPI

from server.orchestrator import (
    cancel_capture,
    dispatch_early,
    on_confirmation,
    prewarm,
    start_run,
    sweep,
    validate_manifest,
)

__all__ = [
    "cancel_capture",
    "create_app",
    "dispatch_early",
    "on_confirmation",
    "prewarm",
    "start_run",
    "sweep",
    "validate_manifest",
]


def create_app() -> FastAPI:
    raise NotImplementedError("WS-01 implements server.api.create_app")
