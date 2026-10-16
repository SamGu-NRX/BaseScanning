"""Twelve-stage graph: the brief's eleven stages (SD-08, SD-10) plus the real CPU placement
stage after `objects_3d`. The GPU and vision stages stay stubbed in dev (`HSS_ORCH_STAGE_STUBS=1`);
`placement` is `stubs_exempt` because it is deterministic CPU work with no GPU, network or
credentials — running it for real on the stub path is the point.
"""

from __future__ import annotations

from dataclasses import dataclass

from server.contracts.jobs import StageName, StageStatus

STAGE_NAMES: tuple[StageName, ...] = (
    "validate",
    "poses",
    "objects",
    "reads",
    "scale",
    "dense",
    "coverage",
    "scene",
    "objects_3d",
    "placement",
    "criteria",
    "result",
)
TERMINAL_STAGE: frozenset[StageStatus] = frozenset({"done", "failed", "skipped"})

GPU_BY_ATTEMPT: tuple[str, str, str] = ("L4", "L4", "A100-40GB")


@dataclass(frozen=True)
class StageSpec:
    name: StageName
    deps: tuple[tuple[StageName, str], ...]
    where: str
    timeout_s: int
    max_attempts: int
    gpu_by_attempt: tuple[str, ...]
    optional: bool
    artifacts: tuple[str, ...]
    module: str
    entry: str = "run"
    # True for a stage whose real implementation must run even when stage stubs are
    # enabled: it is deterministic CPU work that needs no GPU, network or credentials.
    stubs_exempt: bool = False


def _cpu(
    name: StageName,
    deps: tuple[tuple[StageName, str], ...],
    timeout: int,
    artifacts: tuple[str, ...],
    *,
    optional: bool = False,
    module: str | None = None,
    entry: str = "run",
    stubs_exempt: bool = False,
) -> StageSpec:
    return StageSpec(
        name=name,
        deps=deps,
        where="cpu",
        timeout_s=timeout,
        max_attempts=2,
        gpu_by_attempt=(),
        optional=optional,
        artifacts=artifacts,
        module=module or name,
        entry=entry,
    )


def _gpu(
    name: StageName, deps: tuple[tuple[StageName, str], ...], timeout: int, artifacts: tuple[str, ...]
) -> StageSpec:
    return StageSpec(
        name=name,
        deps=deps,
        where="gpu",
        timeout_s=timeout,
        max_attempts=3,
        gpu_by_attempt=GPU_BY_ATTEMPT,
        optional=False,
        artifacts=artifacts,
        module=name,
    )


_D = "done"
_T = "terminal"

STAGES: dict[StageName, StageSpec] = {
    spec.name: spec
    for spec in (
        _cpu(
            "validate",
            (),
            60,
            ("packet/validation.json", "packet/normalized.json", "packet/capture_check.json"),
            module="packet",
        ),
        _gpu(
            "poses",
            (("validate", _D),),
            420,
            (
                "poses/cameras.json",
                "poses/sparse.ply",
                "poses/tracks.npz",
                "poses/registration_report.json",
                "poses/sparse/0/",
            ),
        ),
        # CLIP, SigLIP and OWLv2. Remote spawn uses Pipeline / GPU_IMAGE (24 GiB), not run_cpu_stage.
        # The CPU function has no memory request; that container was killed mid-pass on the facade run.
        StageSpec(
            name="objects",
            deps=(("validate", _D),),
            where="gpu",
            timeout_s=900,
            max_attempts=3,
            gpu_by_attempt=GPU_BY_ATTEMPT,
            optional=True,
            artifacts=("objects/objects.json",),
            module="objects",
        ),
        _cpu("reads", (("validate", _D),), 420, ("reads/reads.json",), optional=True),
        _cpu("scale", (("poses", _D), ("reads", _T)), 60, ("scale/scale.json",)),
        _gpu(
            "dense",
            (("poses", _D), ("scale", _D)),
            300,
            ("dense/dense.ply", "dense/depth/", "dense/dense_report.json"),
        ),
        _cpu(
            "coverage",
            (("dense", _D),),
            120,
            ("coverage/coverage.npz", "coverage/bands.json", "coverage/points.ply", "coverage/scene_draft.json"),
        ),
        _cpu(
            "scene",
            (("coverage", _D), ("objects", _T)),
            120,
            ("scene/scene.json", "scene/build_report.json"),
        ),
        _cpu(
            "objects_3d",
            (("scene", _D), ("objects", _T)),
            120,
            ("objects/objects_3d.json",),
            optional=True,
            module="objects",
            entry="run_3d",
        ),
        # The real placement stage (server/placement/): the repo's unchanged top-level
        # solver behind `POST /v1/placements`, run on the wire-format scene the bridge
        # converts from the run's merged scene. Placement waits for `objects_3d` to be
        # terminal (not just `scene` done) so it only ever solves the scene the
        # coordinator has already merged — a missing or failed objects_3d stage refuses
        # placement instead of implying no hazards. Optional: it adds a decision to the
        # run and degrades (`degraded:placement`, capture `manual_review`) instead of
        # sinking it. It is exempt from stage stubs — the only real stage on the dev
        # path — because it is deterministic CPU work.
        StageSpec(
            name="placement",
            deps=(("scene", _D), ("objects_3d", _T)),
            where="cpu",
            timeout_s=120,
            max_attempts=2,
            gpu_by_attempt=(),
            optional=True,
            artifacts=("placement/placement.json", "placement/scene.json"),
            module="placement",
            stubs_exempt=True,
        ),
        _cpu(
            "criteria",
            (("scene", _D), ("reads", _T), ("objects_3d", _T)),
            180,
            (
                "verdicts/verdict.A.json",
                "verdicts/verdict.B.json",
                "verdicts/verdict.C.json",
                "verdicts/verdict.S.json",
                "verdicts/criteria_report.json",
                "verdicts/placement.C.json",
            ),
        ),
        _cpu(
            "result",
            (("criteria", _D), ("placement", _T)),
            60,
            ("result/result.json", "result/result.internal.json", "result/review.html", "result/preview.ply"),
            # The real composer is deterministic CPU work (verdicts, scene and the
            # catalogue are all local), so the stub journey publishes a real
            # reviewer page instead of a placeholder — that page is what the
            # reviewer sees, and the page itself discloses which stages were
            # stand-ins.
            stubs_exempt=True,
        ),
    )
}


# Artifact keys each stage reads from the run. A trailing slash is a directory.
# Packet images are separate: only stages that open frame files download them.
STAGE_INPUTS: dict[StageName, tuple[str, ...]] = {
    "validate": (),
    "poses": (),
    "objects": (),
    "reads": (),
    "scale": (
        "poses/cameras.json",
        "poses/sparse.ply",
        "poses/tracks.npz",
        "poses/registration_report.json",
        "reads/reads.json",
        "early/meter_identity.json",
        "objects/objects.json",
    ),
    "dense": (
        "poses/cameras.json",
        "poses/sparse.ply",
        "poses/tracks.npz",
        "poses/registration_report.json",
        "scale/scale.json",
    ),
    "coverage": (
        "dense/cloud.npz",
        "dense/dense_report.json",
        "dense/cameras_w.json",
        "dense/tracks.npz",
        "dense/depth/",
        "poses/cameras.json",
        "scale/scale.json",
    ),
    "scene": (
        "coverage/",
        "dense/dense.ply",
        "dense/flagged.ply",
        "poses/sparse.ply",
        "objects/objects.json",
    ),
    "objects_3d": (
        "scene/scene.json",
        "objects/objects.json",
        "poses/registration_report.json",
    ),
    "placement": (
        # The run's merged source scene (metres) and the objects_3d artifact. The
        # coordinator's `_merge_if_ready` merges detected objects into scene/scene.json
        # before `_spawn_ready` can start placement, and placement's deps require
        # objects_3d terminal, so hydration always carries the merged bytes here. The
        # objects_3d artifact is readiness evidence only — placement never merges it.
        "scene/scene.json",
        "objects/objects_3d.json",
    ),
    "criteria": (
        "scene/",
        "coverage/",
        "objects/objects_3d.json",
        "objects/view_requests.json",
        "reads/reads.json",
        "scale/scale.json",
    ),
    "result": (
        "scene/",
        "verdicts/",
        "reads/reads.json",
        "scale/scale.json",
    ),
}

# Stages that open committed frame files. The rest only need packet.json.
PACKET_FILE_STAGES = frozenset({"validate", "poses", "objects", "scale", "dense"})


def stage_needs_packet_files(stage: StageName) -> bool:
    return stage in PACKET_FILE_STAGES


def select_stage_artifacts(stage: StageName, artifacts: dict[str, str]) -> dict[str, str]:
    """Keep the keys this stage reads. Everything else stays in storage."""

    patterns = STAGE_INPUTS[stage]
    selected: dict[str, str] = {}
    for key, storage_key in artifacts.items():
        if _input_match(key, patterns):
            selected[key] = storage_key
    return selected


def _input_match(key: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        if pattern.endswith("/"):
            if key.startswith(pattern) or f"{key.rstrip('/')}/" == pattern:
                return True
        elif key == pattern:
            return True
    return False


def gpu_for_attempt(spec: StageSpec, attempt: int) -> str | None:
    if not spec.gpu_by_attempt:
        return None
    index = min(max(attempt, 1), len(spec.gpu_by_attempt)) - 1
    return spec.gpu_by_attempt[index]


def is_ready(spec: StageSpec, status: dict[StageName, StageStatus]) -> bool:
    if status.get(spec.name) != "pending":
        return False
    for dep, kind in spec.deps:
        dep_status = status.get(dep, "pending")
        if kind == "done" and dep_status != "done":
            return False
        if kind == "terminal" and dep_status not in TERMINAL_STAGE:
            return False
    return True


def ready_stages(status: dict[StageName, StageStatus]) -> list[StageName]:
    return [name for name in STAGE_NAMES if is_ready(STAGES[name], status)]
