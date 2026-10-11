"""Scenario plumbing shared by lifecycle and control scenarios.

Scenarios are the only place PASS/FAIL is decided. Each check's oracle
names its public derivation; expectations refined from sanitized local-run
observations are marked provisional in their oracle string until the real
backend baseline confirms them.
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


def record(
    sink: list,
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
