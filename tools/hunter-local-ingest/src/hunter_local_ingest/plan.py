"""The synthetic capture plan: strict loading, digest binding, control identities.

A plan file comes from fixtures/gen_synthetic_packet.py. Loading checks
every field against the generator's format and the published request
types, and recomputes every digest from the bytes actually read, so the
manifest's fixture identity describes the bytes the run sent. A plan that
fails any check is refused with a ``PlanError`` naming the field and the
problem, never the value.

Controls and fault checks each get their own packet id (``derive``), so a
tampered upload or an injected fault can never land on the baseline
capture: under ``Idempotency-Key=packetId`` a shared id would make the
server hand back the baseline capture.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import re
from pathlib import Path

from .contract import FORMAT_VERSION, ROLES, TIERS

PLAN_FORMAT = "hunter-local-ingest-plan"
PLAN_FORMAT_VERSION = 2

PACKET_ID_RE = re.compile(r"^synthetic-[0-9]{6}$")
# Exactly the paths the generator writes. A plan that names any other path
# is refused, so a receipt can quote plan paths verbatim.
PATH_RE = re.compile(r"^(synthetic/scan[0-9]{2}\.bin|streams/(arkit_poses|imu_raw)\.csv\.gz)$")
CONTENT_TYPES = {"application/octet-stream", "application/gzip"}
_DEVICE_TEXT_RE = re.compile(r"^[A-Za-z0-9 ._()-]{1,40}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

# Suffixes of the packet ids derived for controls and fault checks.
DERIVED_SUFFIXES = (
    "idem",
    "corrupt",
    "truncated",
    "fh",
    "f-retry",
    "f-501",
    "f-abort",
    "f-trunc",
)

MAX_FILES = 64
MAX_PLAN_BYTES = 64 * 1024 * 1024


class PlanError(ValueError):
    """A plan the harness refuses. ``field`` and ``problem`` are fixed text."""

    def __init__(self, field: str, problem: str) -> None:
        super().__init__(f"{field}: {problem}")
        self.field = field
        self.problem = problem


@dataclasses.dataclass(frozen=True)
class PlanFile:
    path: str
    role: str
    content_type: str
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    @property
    def md5_b64(self) -> str:
        return base64.b64encode(hashlib.md5(self.content, usedforsecurity=False).digest()).decode()

    def register_entry(self) -> dict:
        """CaptureAPI.RegisterFile; ``meta`` is nil for a non-image file and omitted."""
        return {
            "path": self.path,
            "bytes": len(self.content),
            "sha256": self.sha256,
            "md5": self.md5_b64,
            "role": self.role,
            "contentType": self.content_type,
        }

    def commit_entry(self) -> dict:
        """CaptureAPI.CommitFile."""
        return {"path": self.path, "sha256": self.sha256}


@dataclasses.dataclass(frozen=True)
class CapturePlan:
    packet_id: str
    create_request: dict
    files: tuple[PlanFile, ...]
    packet: bytes

    @property
    def create_body(self) -> bytes:
        # CaptureUploader.start encodes the create body once with sorted keys.
        return json.dumps(self.create_request, sort_keys=True, separators=(",", ":")).encode()

    @property
    def declared_paths(self) -> frozenset[str]:
        return frozenset(f.path for f in self.files)

    def derive(self, suffix: str) -> CapturePlan:
        """The same files under a packet id of their own, for one control."""
        if suffix not in DERIVED_SUFFIXES:
            raise ValueError(f"unknown derived suffix {suffix!r}")
        packet_id = f"{self.packet_id}-{suffix}"
        packet = json.loads(self.packet)
        packet["packetId"] = packet_id
        return CapturePlan(
            packet_id=packet_id,
            create_request={**self.create_request, "packetId": packet_id},
            files=self.files,
            packet=json.dumps(packet, sort_keys=True, separators=(",", ":")).encode(),
        )

    def packet_ids(self) -> frozenset[str]:
        return frozenset({self.packet_id, *(f"{self.packet_id}-{s}" for s in DERIVED_SUFFIXES)})


@dataclasses.dataclass(frozen=True)
class LoadedPlan:
    plan: CapturePlan
    seed: int
    format_version: int
    plan_file_sha256: str
    plan_sha256: str
    packet_sha256: str
    create_sha256: str


def canonical_digest(document: dict) -> str:
    """sha256 over the plan with ``generator.sha256_of_plan`` set to None."""
    clone = json.loads(json.dumps(document))
    clone["generator"]["sha256_of_plan"] = None
    blob = json.dumps(clone, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _need(cond: bool, field: str, problem: str) -> None:
    if not cond:
        raise PlanError(field, problem)


def _dict(value: object, field: str, keys: set[str]) -> dict:
    _need(isinstance(value, dict), field, "not an object")
    assert isinstance(value, dict)
    _need(set(value) == keys, field, "unexpected or missing keys")
    return value


def _hex(value: object, field: str) -> bytes:
    _need(isinstance(value, str), field, "not a string")
    try:
        return bytes.fromhex(value)  # type: ignore[arg-type]
    except ValueError:
        raise PlanError(field, "not hex") from None


def load_plan(path: Path, expected_seed: int | None = None) -> LoadedPlan:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise PlanError("plan-file", "not found") from None
    except OSError:
        raise PlanError("plan-file", "unreadable") from None
    _need(len(raw) <= MAX_PLAN_BYTES, "plan-file", "too large")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise PlanError("plan-file", "not JSON") from None

    _dict(document, "plan-file", {"format", "format_version", "generator", "plan"})
    _need(document["format"] == PLAN_FORMAT, "format", "not a hunter-local-ingest plan")
    _need(document["format_version"] == PLAN_FORMAT_VERSION, "format_version", "unsupported")
    generator = _dict(
        document["generator"],
        "generator",
        {"name", "seed", "mesh_count", "mesh_kb", "sha256_of_plan"},
    )
    _need(generator["name"] == "gen_synthetic_packet", "generator.name", "unknown generator")
    seed = generator["seed"]
    _need(
        isinstance(seed, int) and not isinstance(seed, bool) and 0 <= seed < 2**31,
        "generator.seed",
        "not a seed",
    )
    if expected_seed is not None:
        _need(seed == expected_seed, "generator.seed", "differs from --seed")
    recorded = generator["sha256_of_plan"]
    _need(
        isinstance(recorded, str) and bool(_HEX64_RE.match(recorded)),
        "generator.sha256_of_plan",
        "not a digest",
    )
    _need(
        canonical_digest(document) == recorded,
        "generator.sha256_of_plan",
        "does not match the plan contents",
    )

    body = _dict(
        document["plan"],
        "plan",
        {"packet_id", "create_request", "files", "packet_hex", "packet_sha256"},
    )
    packet_id = body["packet_id"]
    _need(
        isinstance(packet_id, str) and bool(PACKET_ID_RE.match(packet_id)),
        "plan.packet_id",
        "not a synthetic packet id",
    )
    _need(packet_id == f"synthetic-{seed:06d}", "plan.packet_id", "does not match the seed")

    create = _dict(
        body["create_request"],
        "plan.create_request",
        {"packetId", "formatVersion", "tier", "flow", "device"},
    )
    _need(create["packetId"] == packet_id, "plan.create_request.packetId", "differs from packet_id")
    _need(
        create["formatVersion"] == FORMAT_VERSION,
        "plan.create_request.formatVersion",
        "not the published format version",
    )
    _need(create["tier"] in TIERS, "plan.create_request.tier", "not a Packet04.Tier")
    _need(create["flow"] == "guided", "plan.create_request.flow", "not the published flow")
    device = _dict(
        create["device"], "plan.create_request.device", {"model", "systemVersion", "appVersion"}
    )
    for key, value in device.items():
        _need(
            isinstance(value, str) and bool(_DEVICE_TEXT_RE.match(value)),
            f"plan.create_request.device.{key}",
            "not synthetic device text",
        )

    files_raw = body["files"]
    _need(
        isinstance(files_raw, list) and 2 <= len(files_raw) <= MAX_FILES,
        "plan.files",
        "needs 2 to 64 files",
    )
    files: list[PlanFile] = []
    seen: set[str] = set()
    for i, entry in enumerate(files_raw):
        field = f"plan.files[{i}]"
        entry = _dict(
            entry, field, {"path", "role", "contentType", "bytes", "sha256", "md5", "content_hex"}
        )
        fpath = entry["path"]
        _need(
            isinstance(fpath, str) and bool(PATH_RE.match(fpath)),
            f"{field}.path",
            "not a generator path",
        )
        _need(fpath not in seen, f"{field}.path", "duplicate")
        seen.add(fpath)
        _need(entry["role"] in ROLES, f"{field}.role", "not a Packet04.Role")
        _need(entry["contentType"] in CONTENT_TYPES, f"{field}.contentType", "unsupported")
        content = _hex(entry["content_hex"], f"{field}.content_hex")
        _need(len(content) > 0, f"{field}.content_hex", "empty")
        pf = PlanFile(fpath, entry["role"], entry["contentType"], content)
        _need(entry["bytes"] == len(content), f"{field}.bytes", "does not match the content")
        _need(entry["sha256"] == pf.sha256, f"{field}.sha256", "does not match the content")
        _need(entry["md5"] == pf.md5_b64, f"{field}.md5", "does not match the content")
        files.append(pf)

    packet = _hex(body["packet_hex"], "plan.packet_hex")
    packet_sha = hashlib.sha256(packet).hexdigest()
    _need(body["packet_sha256"] == packet_sha, "plan.packet_sha256", "does not match the packet")
    try:
        parsed = json.loads(packet.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PlanError("plan.packet_hex", "not a JSON packet") from None
    _need(isinstance(parsed, dict), "plan.packet_hex", "not a JSON object")
    _need(parsed.get("format") == "house-capture-packet", "packet.format", "not Packet04")
    _need(parsed.get("formatVersion") == FORMAT_VERSION, "packet.formatVersion", "not 0.4")
    _need(parsed.get("packetId") == packet_id, "packet.packetId", "differs from packet_id")
    _need(parsed.get("source") == {"kind": "synthetic"}, "packet.source", "not synthetic")
    listed = [
        {"path": f.path, "bytes": len(f.content), "sha256": f.sha256, "role": f.role} for f in files
    ]
    _need(parsed.get("files") == listed, "packet.files", "differs from the plan's files")

    plan = CapturePlan(
        packet_id=packet_id, create_request=create, files=tuple(files), packet=packet
    )
    return LoadedPlan(
        plan=plan,
        seed=seed,
        format_version=PLAN_FORMAT_VERSION,
        plan_file_sha256=hashlib.sha256(raw).hexdigest(),
        plan_sha256=recorded,
        packet_sha256=packet_sha,
        create_sha256=hashlib.sha256(plan.create_body).hexdigest(),
    )
