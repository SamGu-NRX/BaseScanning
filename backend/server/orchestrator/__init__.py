"""Coordinator stubs. WS-03 fills these. Stages are called by name, not through adapters."""

from __future__ import annotations

from typing import Literal

from server.contracts.api import ConfirmationRequest
from server.contracts.jobs import CommittedFile, RunRecord, Tier
from server.packet.validate import validate_manifest

__all__ = [
    "cancel_capture",
    "dispatch_early",
    "on_confirmation",
    "prewarm",
    "start_run",
    "sweep",
    "validate_manifest",
]


def start_run(
    capture_id: str,
    packet_sha256: str,
    packet_uri: str,
    *,
    parent_run_id: str | None = None,
) -> RunRecord:
    raise NotImplementedError("WS-03 implements server.orchestrator.start_run")


def prewarm(capture_id: str, *, tier: Tier, expected_keyframes: int | None = None) -> None:
    raise NotImplementedError("WS-03 implements server.orchestrator.prewarm")


def dispatch_early(capture_id: str, files: list[CommittedFile]) -> None:
    raise NotImplementedError("WS-03 implements server.orchestrator.dispatch_early")


def on_confirmation(
    capture_id: str,
    confirmation: ConfirmationRequest,
    confirmation_id: str,
    *,
    by: Literal["device", "reviewer"],
) -> None:
    raise NotImplementedError("WS-03 implements server.orchestrator.on_confirmation")


def cancel_capture(capture_id: str) -> None:
    raise NotImplementedError("WS-03 implements server.orchestrator.cancel_capture")


def sweep() -> int:
    raise NotImplementedError("WS-03 implements server.orchestrator.sweep")
