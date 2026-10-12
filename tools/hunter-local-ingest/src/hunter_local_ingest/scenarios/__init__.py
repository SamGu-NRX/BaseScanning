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

import base64
import dataclasses
import hashlib

from ..adapter import StepResult
from ..modes import Status, Transport

# Public CaptureAPI.terminalStatuses (HouseScanKit/CaptureAPI.swift:150 @ alpha
# 263827ef): polling stops on any of these. manual_review and needs_views are
# TERMINAL outcomes — a capture can legitimately end in either.
TERMINAL_STATUSES = frozenset(
    {"complete", "manual_review", "failed", "expired", "needs_views"}
)

# Public CaptureAPI.statusesWithoutOutcome: a result read under these
# statuses is final even with a null outcome.
STATUSES_WITHOUT_OUTCOME = frozenset({"failed", "expired"})

# Public Packet04.Role vocabulary (Packet04.swift:235-236). There is NO
# "mesh" role in the published contract; every fixture-driven register
# body must draw roles from this set.
ROLE_VOCABULARY = frozenset(
    {"keyframe", "depth", "confidence", "features", "still", "stream", "other"}
)

# Published RegisterFile requires md5 (base64 of the 16-byte MD5,
# Packet04Producer.swift:13-14) and contentType (CaptureAPI.swift:48-55).
DEFAULT_CONTENT_TYPE = "application/octet-stream"
DEFAULT_ROLE = "keyframe"


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
                # Published Role vocabulary only; a plan that names an
                # unpublished role keeps it (the self-check's plumbing fake
                # plans are D-owned) — fixture-driven plans never do.
                "role": self.roles.get(path, DEFAULT_ROLE),
                # Digest of the content this plan uploads; a backend verifies
                # uploaded bytes against it at commit.
                "sha256": hashlib.sha256(content).hexdigest(),
                # Base64 of the 16-byte MD5, as the register call and the
                # storage PUT's Content-MD5 want it
                # (Packet04Producer.swift:13-14).
                "md5": base64.b64encode(hashlib.md5(content).digest()).decode(
                    "ascii"
                ),
                "contentType": DEFAULT_CONTENT_TYPE,
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
        for cid in (
            "lifecycle.health",
            "lifecycle.create",
            "lifecycle.register",
            "lifecycle.upload",
            "lifecycle.commit",
            "lifecycle.finalize",
            "lifecycle.events",
            "lifecycle.result",
        )
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


# ---------------------------------------------------------------------------
# Pure published-contract oracles (deliberately side-effect-free so the
# decisive checks are unit-testable without a server).
# ---------------------------------------------------------------------------

MAX_UNACKNOWLEDGED = 5  # CaptureUploader.swift:70-73 (per-file bound)
BATCH_SIZE = 10  # Policy.batch: register/commit send about every 10 files


@dataclasses.dataclass
class RegisterRound:
    """Classification of one register answer against the plan (by PATH).

    The published client matches answer files by ``file.path``
    (CaptureUploader.swift:460), never by response position. Response
    order is irrelevant here by construction.
    """

    to_upload: dict  # path -> signed target dict (method PUT + url)
    already_committed: list  # paths the answer reports state == "committed"
    unacknowledged: list  # planned paths absent from the answer
    method_refusals: list  # (path, reason) targets without PUT+url
    duplicated: list  # answer paths that are not in the plan


def register_round(descriptors: list[dict], answer_targets: dict) -> RegisterRound:
    """Classify a register answer keyed by path against the planned files."""
    planned = [d["path"] for d in descriptors]
    to_upload: dict = {}
    already_committed: list = []
    method_refusals: list = []
    duplicated = [p for p in answer_targets if p not in set(planned)]

    for path in planned:
        target = answer_targets.get(path)
        if target is None:
            continue  # unacknowledged
        if target.get("state") == "committed":
            already_committed.append(path)
            continue
        method = target.get("method")
        if method is not None and method != "PUT":
            method_refusals.append((path, "upload_method_not_put"))
            continue
        if not target.get("url"):
            method_refusals.append((path, "upload_method_missing_url"))
            continue
        to_upload[path] = target

    unacknowledged = [
        p
        for p in planned
        if p not in answer_targets and p not in already_committed
    ]
    return RegisterRound(
        to_upload=to_upload,
        already_committed=already_committed,
        unacknowledged=unacknowledged,
        method_refusals=method_refusals,
        duplicated=duplicated,
    )


def strict_commit_verdict(planned_paths: list[str], membership: dict) -> tuple[bool, str]:
    """Published commit semantics: success requires EVERY planned path in
    ``committed`` with empty ``notFound``/``mismatch`` (CaptureUploader.swift
    re-queues anything not committed; a checker records the partial commit
    as the failure it is). Returns (ok, detail)."""
    committed = membership.get("committed") or []
    not_found = membership.get("notFound") or []
    mismatch = membership.get("mismatch") or []
    committed_set = set(committed)
    planned_set = set(planned_paths)
    missing = [p for p in planned_paths if p not in committed_set]
    unexpected = [p for p in committed if p not in planned_set]
    if not missing and not not_found and not mismatch and not unexpected:
        return True, f"all {len(planned_paths)} planned paths committed"
    parts = []
    if missing:
        parts.append(f"not committed: {sorted(missing)}")
    if not_found:
        parts.append(f"notFound: {sorted(not_found)}")
    if mismatch:
        parts.append(f"mismatch: {sorted(mismatch)}")
    if unexpected:
        parts.append(f"unexpected committed paths: {sorted(unexpected)}")
    return False, "; ".join(parts)


def verdict_events_binding(verdict_ready_run_ids: list, finalize_run_id) -> list[str]:
    """verdict_ready events bind data.runId nil-or-equal to the finalize's
    runId — a verdict for another run is not this capture's result
    (CaptureUploader.swift:624)."""
    violations = []
    for run_id in verdict_ready_run_ids:
        if run_id is None:
            continue
        if finalize_run_id is None or run_id != finalize_run_id:
            violations.append(
                f"verdict_ready runId {run_id!r} != finalize {finalize_run_id!r}"
            )
    return violations


def derived_control_identity(plan: CapturePlan, suffix: str) -> tuple[dict, str]:
    """Isolated identity for a control round (recon §3s).

    Same create shape as the plan, but the packetId names the control's
    own capture: no control mints or mutates the baseline round's
    capture. The id stays inside the published packetId pattern
    (Packet04.swift:282-284) by bounding the base length to 64.
    """
    packet_id = (plan.packet_id + suffix)[:64]
    request = dict(plan.create_request) if isinstance(plan.create_request, dict) else {}
    request["packetId"] = packet_id
    return request, packet_id
