"""SD-61: a coverage score needs its laser and recon clouds on disk.

Skipping a missing PLY and scoring with no occluders marks unseen space as seen.
``allow_missing`` is debug-only and marks the result provisional. Gates reject that.
"""

from __future__ import annotations

from pathlib import Path


class MissingOccluderGeometry(RuntimeError):
    def __init__(self, missing: list[str]):
        self.missing = list(missing)
        listed = ", ".join(self.missing)
        super().__init__(
            f"missing occluder geometry: {listed}. "
            "A score without those clouds overstates view counts. "
            "Debug only: --allow-missing-occluders marks the output provisional, and gates reject it."
        )


def _ply_assets(scene: dict) -> list[dict]:
    out = []
    for asset in scene.get("assets") or []:
        path = str(asset.get("path") or "")
        kind = asset.get("kind")
        if asset.get("id") in {"laser", "occluders", "points"} or kind in {"point_cloud", "mesh"}:
            if path.endswith(".ply") or asset.get("format") == "ply":
                out.append(asset)
    return out


def missing_occluder_files(*scenes: tuple[dict, Path]) -> list[str]:
    """Declared laser, occluder, and recon PLYs that are not on disk."""
    missing: list[str] = []
    for scene, base in scenes:
        base = Path(base)
        declared = _ply_assets(scene)
        if not declared:
            missing.append(str(base / "points.ply"))
            continue
        for asset in declared:
            path = base / asset["path"]
            if not path.is_file():
                missing.append(str(path))
    return missing


def check_occluder_geometry(*scenes: tuple[dict, Path], allow_missing: bool = False) -> dict:
    """Raise when a scoring cloud is missing. The debug flag returns ``provisional: true``."""
    missing = missing_occluder_files(*scenes)
    if not missing:
        return {"provisional": False, "missing": []}
    if allow_missing:
        return {"provisional": True, "missing": missing}
    raise MissingOccluderGeometry(missing)


def gate_rejects_provisional(result: dict) -> bool:
    """Every gate rejects a score produced without its occluder clouds."""
    return bool(result.get("provisional"))
