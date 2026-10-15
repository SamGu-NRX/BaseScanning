"""Criteria engine: C1 decides, the chain solver fills unsure cells, C2 vetoes, C3 downgrades."""

import json
from pathlib import Path

from server.contracts.jobs import StageContext, StageResult
from server.criteria.config import CriteriaConfig
from server.criteria.run import CriteriaInputError, evaluate_scene

STAGE = "criteria"

# §3.9 coverage names. The scene directory may already hold copies; missing
# scene-relative files are filled from these work-dir keys.
_COVERAGE_FILES = (
    "coverage.npz",
    "bands.json",
    "points.ply",
    "scene_draft.json",
    "coverage_report.json",
)

__all__ = ["STAGE", "CriteriaConfig", "CriteriaInputError", "evaluate_scene", "run"]


def run(ctx: StageContext) -> StageResult:
    """Read the §3.9 scene, objects, reads, and coverage artifacts and write verdicts."""
    ctx.heartbeat("evaluate")
    scene_path = _artifact(ctx, "scene/scene.json")
    if scene_path is None:
        raise CriteriaInputError("scene_invalid")
    scene_dir, reads = _prepare_inputs(ctx, scene_path)
    out = Path(ctx.work_dir) / "verdicts"
    verdicts = evaluate_scene(
        scene_dir,
        out,
        reads=reads,
        view_requests_path=_artifact(ctx, "objects/view_requests.json"),
    )
    warnings: list[str] = []
    report_path = out / "criteria_report.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text())
        warnings = [str(item) for item in report.get("degraded") or []]
    artifacts = {f"verdicts/{path.name}": f"verdicts/{path.name}" for path in sorted(out.iterdir()) if path.is_file()}
    return StageResult(
        artifacts=artifacts,
        metrics={"verdicts": float(len(verdicts))},
        warnings=warnings,
    )


def _artifact(ctx: StageContext, key: str) -> Path | None:
    raw = ctx.inputs.get(key)
    path = Path(raw) if raw else Path(ctx.work_dir) / key
    return path if path.is_file() else None


def _link(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        return
    dest.symlink_to(src.resolve())


def _merge_objects(existing: list, incoming: list) -> list:
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for obj in existing:
        if isinstance(obj, dict) and obj.get("id"):
            by_id[obj["id"]] = obj
            order.append(obj["id"])
    for obj in incoming:
        if not isinstance(obj, dict) or not obj.get("id"):
            continue
        if obj["id"] not in by_id:
            order.append(obj["id"])
        by_id[obj["id"]] = obj
    return [by_id[item] for item in order]


def _prepare_inputs(ctx: StageContext, scene_path: Path) -> tuple[Path, Path | None]:
    src_dir = scene_path.parent
    try:
        scene = json.loads(scene_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CriteriaInputError("scene_invalid") from exc
    objects_path = _artifact(ctx, "objects/objects_3d.json")
    if objects_path is not None:
        try:
            doc = json.loads(objects_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise CriteriaInputError("scene_invalid") from exc
        incoming = doc.get("objects") if isinstance(doc, dict) else None
        if isinstance(incoming, list):
            scene["objects"] = _merge_objects(list(scene.get("objects") or []), incoming)
    dest = Path(ctx.work_dir) / "criteria-input"
    dest.mkdir(parents=True, exist_ok=True)
    if src_dir.resolve() != dest.resolve():
        for child in src_dir.iterdir():
            if child.name == "scene.json":
                continue
            _link(child, dest / child.name)
    for name in _COVERAGE_FILES:
        published = _artifact(ctx, f"coverage/{name}")
        if published is None:
            continue
        _link(published, dest / name)
        _link(published, dest / "coverage" / name)
    scale = _artifact(ctx, "scale/scale.json")
    if scale is not None:
        _link(scale, dest / "scale.json")
    (dest / "scene.json").write_text(json.dumps(scene) + "\n")
    return dest, _artifact(ctx, "reads/reads.json")
