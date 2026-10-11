"""Scenario plumbing shared by lifecycle and control scenarios.

Scenarios are the only place PASS/FAIL is decided. Each check's oracle
names its public derivation; expectations refined from sanitized local-run
observations are marked provisional in their oracle string until the real
backend baseline confirms them.

Sink interface: scenarios and vocabulary emitters write through one
protocol — ``sink.append(line_dict)``. The runner supplies the
receipt-backed ``ChecksSink``; the self-check supplies a plain list and
converts afterwards. Never pass a bare callable where a sink is expected.
"""

from __future__ import annotations

import dataclasses
import hashlib

from ..adapter import StepResult
from ..modes import Status, Transport

# Public CaptureAPI.terminalStatuses (HouseScanKit/CaptureAPI.swift @ alpha
# 263827ef): polling stops on any of these.
TERMINAL_STATUSES = frozenset({"complete", "failed", "expired"})

# Public CaptureAPI.statusesWithoutOutcome: a result read under these
# statuses is final even with a null outcome.
STATUSES_WITHOUT_OUTCOME = frozenset({"failed", "expired"})


@dataclasses.dataclass
class CapturePlan:
    """One synthetic capture the scenarios will push through the lifecycle.

    Everything here is synthetic by construction (fixtures/) — no
    homeowner data, no real credentials.
    """

    packet_id: str
    create_request: dict
    # (path, bytes) pairs; paths are synthetic, contents are seeded noise.
    files: list[tuple[str, bytes]]
    roles: dict[str, str]
    packet: bytes

    @property
    def file_descriptors(self) -> list[dict]:
        return [
            {
                "path": path,
                "role": self.roles.get(path, "mesh"),
                # Digest of the content this plan uploads; a backend verifies
                # uploaded bytes against it at commit.
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
            for path, content in self.files
        ]


# GPU arms the contract names but this harness never executes without
# explicit operator authorization.
GPU_ARMS = ("objects", "vlm", "dense")


class PrefixedSink:
    """Sink adapter that rewrites check ids through a prefix.

    Lets a second round (e.g. the FAKE_HUNTER fault round) reuse the
    lifecycle scenarios under distinct check ids instead of overwriting
    the first round's receipt lines.
    """

    def __init__(self, sink, prefix: str) -> None:
        self._sink = sink
        self._prefix = prefix

    def append(self, line: dict) -> dict:
        line = {**line, "id": f"{self._prefix}{line['id']}"}
        self._sink.append(line)
        return line


def gpu_stub_lines(sink) -> None:
    """Named GPU arms as NOT_RUN vocabulary lines; never executed."""
    for arm in GPU_ARMS:
        sink.append(
            {
                "id": f"gpu-stub.{arm}",
                "transport": Transport.GPU_STUB.value,
                "oracle": (
                    "task vocabulary: GPU-stubbed arms are named, never executed without "
                    "explicit authorization"
                ),
                "expected": "only run when the operator authorizes the GPU arm",
                "observed": "not run: no GPU arm authorized in this run",
                "status": Status.NOT_RUN.value,
            }
        )


def unreachable_lines(detail: str, transport: Transport | None = None) -> list[dict]:
    """Vocabulary lines for a run that never got past the reachability gate."""
    return [
        {
            "id": cid,
            "transport": (transport or Transport.REAL_MIRROR_CPU).value,
            "oracle": "task vocabulary: blocked runs name their block",
            "expected": "lifecycle step reachable",
            "observed": detail,
            "status": Status.BLOCKED.value,
        }
        for cid in ("lifecycle.health", "lifecycle.create", "lifecycle.result")
    ]


def record(
    sink: object,
    transport: Transport,
    check_id: str,
    oracle: str,
    expected: str,
    observed: str | StepResult,
    status: Status,
    evidence: str | None = None,
) -> Status:
    # A StepResult is accepted so the raw Observation rides along in-process
    # (trace comparison); receipts keep only the sanitized `observed` string.
    step: StepResult | None = None
    if isinstance(observed, StepResult):
        step = observed
        observed = step_observed(step)
    sink.append(
        {
            "id": check_id,
            "transport": transport.value,
            "oracle": oracle,
            "expected": expected,
            "observed": observed,
            "status": status.value,
            "evidence": evidence,
            "observation": step.observation if step is not None else None,
        }
    )
    return status


def step_observed(result: StepResult) -> str:
    if result.error:
        return result.error
    obs = result.observation
    if obs is None:
        return "no observation"
    parts = [f"http {obs.http_status}"]
    if obs.body_sha256:
        # digest prefix only — the audit trail, never the bytes
        parts.append(f"body_sha256={obs.body_sha256[:12]}…")
    if obs.field_names:
        parts.append("fields=" + ",".join(obs.field_names))
    if obs.safe_values:
        flat = {k: v for k, v in obs.safe_values.items() if k != "events"}
        parts.append("values=" + repr(flat))
    if obs.detail:
        parts.append(obs.detail)
    return "; ".join(parts)
