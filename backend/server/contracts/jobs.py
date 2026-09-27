"""Capture and run records. Stage entry context is not a JSON document."""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field

from server.contracts._base import IsoTime, JsonInt, JsonNum, RelPath, Sha256Hex, Strict
from server.contracts.packet import FileRole, Tier

CaptureStatus = Literal[
    "uploading",
    "awaiting_files",
    "processing",
    "needs_views",
    "complete",
    "manual_review",
    "failed",
    "expired",
]
StageName = Literal[
    "validate",
    "poses",
    "objects",
    "reads",
    "scale",
    "dense",
    "coverage",
    "scene",
    "objects_3d",
    "criteria",
    "result",
]
EarlyJobName = Literal["early_reads", "early_qa", "capture_check"]
EventType = Literal[
    "files_committed",
    "hint",
    "capture_check",
    "stage",
    "verdict_ready",
    "retake_request",
    "review_started",
    "review_completed",
    "failed",
    "confirmation",
]
StageStatus = Literal["pending", "running", "done", "failed", "stale", "skipped"]
Branch = Literal["lidar", "image"]
FileState = Literal["pending", "committed", "mismatch"]
Flow = Literal["guided", "photo"]
Profile = Literal["A", "B", "C", "S"]

# Dependency edges. Optional stages degrade the run instead of failing it.
STAGE_DEPS: dict[str, tuple[str, ...]] = {
    "validate": (),
    "poses": ("validate",),
    "objects": ("validate",),
    "reads": ("validate",),
    "scale": ("poses", "reads"),
    "dense": ("poses", "scale"),
    "coverage": ("dense",),
    "scene": ("coverage", "objects"),
    "objects_3d": ("scene", "objects"),
    "criteria": ("scene", "reads", "objects_3d"),
    "result": ("criteria",),
}
OPTIONAL_STAGES: frozenset[str] = frozenset({"objects", "reads", "objects_3d"})

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class CaptureRecord(Strict):
    capture_id: str
    packet_id: str
    parent_capture_id: str | None = None
    status: CaptureStatus
    tier: Tier
    flow: Flow
    device_model: str
    order_ref: str | None = None
    owner_principal: str
    created_at: IsoTime
    finalize_by: IsoTime
    packet_sha256: str | None = None
    run_id: str | None = None


class FileRecord(Strict):
    capture_id: str
    path: RelPath
    role: FileRole
    bytes: JsonInt
    sha256: str
    md5: str
    content_type: str
    state: FileState
    generation: str | None = None
    key: str | None = None
    committed_at: IsoTime | None = None
    meta: dict[str, Any] | None = None


class CommittedFile(Strict):
    path: RelPath
    role: FileRole
    sha256: Sha256Hex
    bytes: JsonInt
    generation: str
    key: str
    meta: dict[str, Any] | None = None


class RunRecord(Strict):
    run_id: str
    capture_id: str
    packet_sha256: str
    parent_run_id: str | None = None
    status: StageStatus
    branch: Branch
    flow: Flow
    tier: Tier
    profile_default: Profile
    created_at: IsoTime
    deadline_at: IsoTime
    started_at: IsoTime | None = None
    finished_at: IsoTime | None = None
    artifacts: dict[str, str]
    outcome_kind: str | None = None
    failure_code: str | None = None
    degraded: list[StageName] = Field(default_factory=list)
    spend_usd: dict[str, float] = Field(default_factory=dict)


class StageRecord(Strict):
    run_id: str
    stage: StageName
    status: StageStatus
    attempt: JsonInt = Field(ge=1)
    failures: JsonInt = 0
    respawns: JsonInt = 0
    heartbeat: IsoTime
    started_at: IsoTime | None = None
    finished_at: IsoTime | None = None
    gpu: str | None = None
    worker_call_id: str | None = None
    substep: str | None = None
    artifact_uri: str | None = None
    error: str | None = None
    error_code: str | None = None
    metrics: dict[str, float] | None = None


class EventRecord(Strict):
    capture_id: str
    seq: JsonInt
    type: EventType
    at: IsoTime
    data: dict[str, Any]
    run_id: str | None = None
    stage: StageName | None = None


class StageContext(Strict):
    """In-process stage input. `storage`, `state` and `settings` are the common protocols."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    capture_id: str
    run_id: str
    stage: StageName
    attempt: int
    branch: Branch
    flow: Flow
    tier: Tier
    work_dir: Path
    inputs: dict[str, str]
    deadline_at: IsoTime
    heartbeat: Callable[[str], None]
    gpu: str | None = None
    vlm_budget_usd: float
    parent_run_id: str | None = None
    models: dict[str, Any] | None = None
    storage: Any
    state: Any
    settings: Any


class StageResult(Strict):
    artifacts: dict[str, str]
    metrics: dict[str, JsonNum]
    warnings: list[str]


def format_ulid(timestamp_ms: int, randomness: bytes) -> str:
    """26-character Crockford ULID. `randomness` is 10 bytes. Deterministic for those inputs."""

    if len(randomness) != 10:
        raise ValueError("ULID randomness is 10 bytes")
    if not 0 <= timestamp_ms < 2**48:
        raise ValueError("ULID timestamp out of range")
    value = (timestamp_ms << 80) | int.from_bytes(randomness, "big")
    chars = [""] * 26
    for index in range(25, -1, -1):
        chars[index] = _ULID_ALPHABET[value & 31]
        value >>= 5
    return "".join(chars)


def new_ulid() -> str:
    return format_ulid(int(time.time() * 1000), os.urandom(10))


def new_capture_id() -> str:
    return "cap_" + new_ulid()


def new_confirmation_id() -> str:
    return "cfm_" + new_ulid()


def run_id_for_packet_bytes(packet_bytes: bytes) -> str:
    """`run_` plus the first 16 hex chars of sha256(packet.json bytes as stored)."""

    return "run_" + hashlib.sha256(packet_bytes).hexdigest()[:16]
