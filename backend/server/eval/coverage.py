"""Observed-coverage grids (unknown / observed free / observed occupied) and their band summary.

A voxel is observed free when a camera ray passed through it on the way to a surface the camera
saw, and observed occupied when the ray ended in it. Everything else is unknown. The grid is
built per camera so `views` counts distinct cameras. Producers call `build_grid` with their own
rays (to triangulated points, depth-map pixels, LiDAR returns); the ground-truth builder calls it
with rays to the laser surface.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from server.eval.chain import Chain, ground_y
from server.eval.frame import mat4_from_list
from server.eval.sceneio import CoverageGrid, load_points
from server.eval.visibility import Camera, VisibilityOracle

# The observation rule every measurement's coverage evidence is graded by (contract section 3.1).
MIN_VIEWS = 2
# Steepest viewing angle from the surface normal that still counts (0 = head-on, 90 = edge-on).
# Walls: PR #13's grazing limit. Ground is always seen obliquely from standing height, so it gets
# more; ground-dependent results are also reported under every GROUND_SENSITIVITY_DEG limit.
INCIDENCE_LIMITS_DEG = {"wall": 75.0, "ground": 80.0}
GROUND_SENSITIVITY_DEG = (75.0, 80.0)
# Ground rule variants, all reported side by side (open decision, contract section 3.1). The
# footprint rule is C1's: a view counts when one full-resolution pixel covers at most 3 in of ground
# along the viewing ray (d / (fx cos(incidence))), up to 88 deg.
GROUND_RULES = {
    "angle75": {"max_incidence_deg": 75.0},
    "angle80": {"max_incidence_deg": 80.0},
    "footprint3in": {"max_incidence_deg": 88.0, "max_footprint_m": 0.0762},
}
DEFAULT_GROUND_RULE = "angle80"
OBSERVED_SHARE = 0.9
SEEN_SHARE_UNOBSERVED = 0.5


def clamp_incidence_deg(value: float) -> float:
    """Clip a viewing angle to the schema's ``[0, 90]`` before rounding to 0.1°.

    A grazing angle just past 90 (the E2E π³ facade runs measured 90.2 after rounding)
    fails ``best_incidence_deg`` / ``worst_best_incidence_deg`` ``maximum: 90`` and the
    verdict is dropped.
    """
    return round(min(90.0, max(0.0, float(value))), 1)


WALL_PROBE_HEIGHTS = (0.15, 0.5, 1.0)  # m, up to the battery's height (39.5 in)
HEADROOM_M = 2.13  # 7 ft, the strictest headroom in the three profiles
OVERHEAD_OUT_M = 0.91  # 3 ft out from the wall
GROUND_MAX_OUT_M = 8.0
FACING_PROBE_Y = 0.91  # 3 ft
FACING_MAX_OUT_M = 18.3


def limit_for(surface: str, limits: dict | None = None) -> float | None:
    """Incidence limit of a surface kind; None for free space (any angle counts)."""
    return None if surface == "free" else (limits or INCIDENCE_LIMITS_DEG)[surface]


class MissingOccluderError(FileNotFoundError):
    """A declared occluder asset is missing, unreadable, or not a file (SD-61)."""


def load_occluders(scene: dict, base: Path, *, allow_missing_occluders: bool = False) -> tuple[np.ndarray | None, bool]:
    """Return ``(points, provisional)``.

    No occluder asset → ``(None, False)``. A declared asset that is not a file raises
    ``MissingOccluderError`` unless ``allow_missing_occluders`` (debug only), in which case the
    cloud is omitted and ``provisional`` is true.
    """
    asset = next((a for a in scene.get("assets", []) if a.get("id") == "occluders"), None)
    if asset is None:
        return None, False
    path = Path(base) / asset["path"]
    if not path.is_file():
        if not allow_missing_occluders:
            raise MissingOccluderError(f"occluder asset {asset['path']} is not a file under {base}")
        return None, True
    occ, _, _ = load_points(scene, base, asset_id="occluders")
    if occ is None:
        if not allow_missing_occluders:
            raise MissingOccluderError(f"occluder asset {asset['path']} is not readable under {base}")
        return None, True
    return occ, False


def require_declared_cloud(
    scene: dict,
    base: Path,
    *,
    asset_id: str | None = None,
    kind: str = "point_cloud",
    allow_missing_occluders: bool = False,
    label: str = "point cloud",
) -> tuple[np.ndarray | None, bool]:
    """Load the asset ``load_points`` would select.

    No matching asset returns ``(None, False)``. A declared asset whose file is
    missing or unreadable raises ``MissingOccluderError`` (SD-61: laser and recon
    PLYs are occluder geometry). ``allow_missing_occluders`` returns ``(None, True)``
    instead; the caller must not run visibility against an empty blocker set.
    """
    selected = next(
        (
            asset
            for asset in scene.get("assets") or []
            if (asset_id and asset.get("id") == asset_id) or (not asset_id and asset.get("kind") == kind)
        ),
        None,
    )
    if selected is None:
        return None, False
    points, _, _ = load_points(scene, base, kind=kind, asset_id=asset_id)
    if points is None:
        if not allow_missing_occluders:
            raise MissingOccluderError(
                f"{label} asset {selected.get('path')} is not a readable file under {base}"
            )
        return None, True
    return points, False


class CoverageQuery:
    """Coverage evidence for any queried region: how many input views saw it and at what angle.

    A probe counts as well observed when at least MIN_VIEWS cameras see it (in the image, in front,
    not occluded by the blocker surface) within its surface's incidence limit. Probes of free space
    (normal None) need MIN_VIEWS views at any angle."""

    def __init__(self, cams: list[Camera], blockers: np.ndarray, scale: float = 0.125, limits: dict | None = None):
        self.cams = cams
        self.oracle = VisibilityOracle(cams, blockers, scale=scale)
        self.centers = np.array([c.pose[:3, 3] for c in cams])
        self.fx = np.array([float(c.K[0]) for c in cams])
        self.limits = dict(limits or INCIDENCE_LIMITS_DEG)
        self.provisional = False

    @classmethod
    def from_scene(
        cls,
        scene: dict,
        base: Path,
        ids: list[str] | None = None,
        limits: dict | None = None,
        *,
        allow_missing_occluders: bool = False,
    ) -> "CoverageQuery":
        """Cameras = the scene's keyframes (optionally a subset); blockers = its point cloud plus
        any asset with id `occluders`. A declared occluder that is not a file is an error (SD-61)."""
        kfs = [k for k in scene.get("keyframes", []) if ids is None or k["id"] in ids]
        if not kfs:
            raise ValueError("coverage query needs keyframes")
        cams = [
            Camera(k["id"], mat4_from_list(k["pose"]), np.asarray(k["intrinsics"], float), k["w"], k["h"]) for k in kfs
        ]
        clouds: list[np.ndarray] = []
        provisional = False
        # A declared laser or recon PLY is occluder geometry (SD-61). It must fail
        # even when an occluders PLY is present; that file is not a substitute.
        pts, pts_provisional = require_declared_cloud(
            scene,
            base,
            kind="point_cloud",
            allow_missing_occluders=allow_missing_occluders,
            label="laser or recon",
        )
        provisional = provisional or pts_provisional
        if pts is None:
            pts, mesh_provisional = require_declared_cloud(
                scene,
                base,
                kind="mesh",
                allow_missing_occluders=allow_missing_occluders,
                label="mesh",
            )
            provisional = provisional or mesh_provisional
        if pts is not None:
            clouds.append(pts)
        occ, occ_provisional = load_occluders(scene, base, allow_missing_occluders=allow_missing_occluders)
        provisional = provisional or occ_provisional
        if occ is not None:
            clouds.append(occ)
        if not clouds:
            raise FileNotFoundError(f"no point cloud, mesh, or occluders file on disk under {base}")
        query = cls(cams, np.vstack(clouds), limits=limits)
        query.provisional = provisional
        return query

    def incidences(
        self, X: np.ndarray, normals: np.ndarray | None
    ) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
        """(seen [n, cams], incidence in degrees [n, cams] with inf where unseen, along-ray pixel
        footprint in metres [n, cams] = distance / (fx cos incidence)); the last two None without normals."""
        X = np.atleast_2d(np.asarray(X, float))
        seen = self.oracle.seen_by(X, None)
        if normals is None:
            return seen, None, None
        N = np.broadcast_to(np.asarray(normals, float), X.shape)
        to_cam = self.centers[None] - X[:, None]
        dist = np.linalg.norm(to_cam, axis=2)
        cosang = np.einsum("nck,nk->nc", to_cam, N) / dist
        inc = np.degrees(np.arccos(np.clip(cosang, -1, 1)))
        with np.errstate(divide="ignore"):
            fp = np.where(cosang > 1e-6, dist / (self.fx[None] * np.maximum(cosang, 1e-6)), np.inf)
        return seen, np.where(seen, inc, np.inf), np.where(seen, fp, np.inf)

    @staticmethod
    def summarize(
        seen: np.ndarray,
        inc: np.ndarray | None,
        limit: float | None,
        fp: np.ndarray | None = None,
        max_fp: float | None = None,
    ) -> dict[str, np.ndarray]:
        out = {"views_any": seen.sum(1)}
        if inc is None or limit is None:
            out["views_good"] = out["views_any"]
            out["best_incidence_deg"] = np.full(len(seen), np.nan)
            return out
        ok = inc <= limit
        if max_fp is not None and fp is not None:
            ok &= fp <= max_fp
            best_fp = fp.min(1)
            out["best_footprint_m"] = np.where(np.isfinite(best_fp), best_fp, np.nan)
        out["views_good"] = ok.sum(1)
        best = inc.min(1)
        out["best_incidence_deg"] = np.where(np.isfinite(best), best, np.nan)
        return out

    def per_probe(
        self,
        X: np.ndarray,
        normals: np.ndarray | None,
        surface: str = "wall",
        limit: float | None = None,
        max_fp: float | None = None,
    ) -> dict:
        seen, inc, fp = self.incidences(X, normals)
        lim = limit if limit is not None else (limit_for(surface, self.limits) if normals is not None else None)
        return self.summarize(seen, inc, lim, fp, max_fp)

    def evidence(
        self, X: np.ndarray, normals: np.ndarray | None, region: dict, surface: str, generated: np.ndarray | None = None
    ) -> dict:
        """Contract `coverage_evidence` for one queried region (probes X in the scene frame).
        Ground regions also carry `sensitivity`: the grade under every GROUND_RULES variant."""
        seen, inc, fp = self.incidences(X, normals)
        lim = limit_for(surface, self.limits) if normals is not None else None
        ev = grade(self.summarize(seen, inc, lim), region, generated, surface, lim)
        if surface == "ground" and inc is not None:
            ev["sensitivity"] = []
            for rid, r in GROUND_RULES.items():
                g = grade(
                    self.summarize(seen, inc, r["max_incidence_deg"], fp, r.get("max_footprint_m")),
                    region,
                    generated,
                    surface,
                    r["max_incidence_deg"],
                    r.get("max_footprint_m"),
                )
                ev["sensitivity"].append(
                    {
                        "rule_id": rid,
                        **r,
                        "status": g["status"],
                        "observed_share": g["observed_share"],
                        "views_median": g["views_median"],
                    }
                )
        return ev


def grade(
    pp: dict,
    region: dict,
    generated: np.ndarray | None = None,
    surface: str = "wall",
    limit: float | None = None,
    max_fp: float | None = None,
) -> dict:
    good = pp["views_good"] >= MIN_VIEWS
    seen = pp["views_any"] >= 1
    n = len(good)
    obs_share = float(good.mean()) if n else 0.0
    seen_share = float(seen.mean()) if n else 0.0
    gen_share = float(np.mean(generated)) if generated is not None and n else 0.0
    grazing_only = seen & (pp["views_good"] < MIN_VIEWS) & ~np.isnan(pp["best_incidence_deg"])
    if gen_share > 0.5:
        status = "generated"
    elif obs_share >= OBSERVED_SHARE:
        status = "observed"
    elif seen_share < SEEN_SHARE_UNOBSERVED:
        status = "unobserved"
    elif grazing_only.mean() >= (~seen).mean():
        status = "grazing"
    else:
        status = "partial"
    bi = pp["best_incidence_deg"]
    finite = bi[~np.isnan(bi)]
    return {
        "region": region,
        "status": status,
        "probes": int(n),
        "observed_share": round(obs_share, 3),
        "seen_share": round(seen_share, 3),
        "views_min": int(pp["views_good"].min()) if n else 0,
        "views_median": float(np.median(pp["views_good"])) if n else 0.0,
        "best_incidence_deg": clamp_incidence_deg(float(np.median(finite))) if len(finite) else None,
        "worst_best_incidence_deg": clamp_incidence_deg(float(finite.max())) if len(finite) else None,
        "rule": {
            "min_views": MIN_VIEWS,
            "surface": surface,
            "max_incidence_deg": limit,
            "observed_share": OBSERVED_SHARE,
            **({"max_footprint_m": max_fp} if max_fp is not None else {}),
        },
    }


def region_probes(scene: dict, region: dict, step: float = 0.1) -> tuple[np.ndarray, np.ndarray | None, str]:
    """Probe points, normals and surface kind of a contract region: {wall_id, s_m, height_m} = a
    patch of the wall face (wall); {wall_id, s_m, out_m} = the ground strip in front (ground);
    {wall_id, s_m, out_m, height_m} = the free-space volume in front (free, no normal)."""
    ch = Chain(scene)
    w = ch.by_id[region["wall_id"]]
    s0, s1 = region["s_m"]
    ss = np.arange(s0, s1 + 1e-9, step) if s1 - s0 > step else np.array([(s0 + s1) / 2])
    q = w.plan_at(ss)
    gy = ground_y(scene, q)
    n_out = np.array([w.outward[0], 0.0, w.outward[1]])
    if "out_m" not in region:
        h0, h1 = region.get("height_m", [0.0, HEADROOM_M])
        hs = np.arange(h0, h1 + 1e-9, step) if h1 - h0 > step else np.array([(h0 + h1) / 2])
        P = np.array([[x, g + h, z] for (x, z), g in zip(q, gy) for h in hs])
        return P, np.tile(n_out, (len(P), 1)), "wall"
    o0, o1 = region["out_m"]
    os_ = np.arange(max(o0, 0.05), o1 + 1e-9, step)
    if "height_m" not in region:
        P = np.array([[x + o * w.outward[0], g + 0.01, z + o * w.outward[1]] for (x, z), g in zip(q, gy) for o in os_])
        return P, np.tile([0.0, 1.0, 0.0], (len(P), 1)), "ground"
    h0, h1 = region["height_m"]
    hs = np.arange(h0, h1 + 1e-9, max(step, 0.25))
    P = np.array(
        [[x + o * w.outward[0], g + h, z + o * w.outward[1]] for (x, z), g in zip(q, gy) for o in os_ for h in hs]
    )
    return P, None, "free"


# SD-101. The 80° ground rule stays. A scene that flagged its ground plane and did not
# reanchor must not decide from incidence against that plane. Electro's kept plane is
# level and the cameras are already standing height (~1.5 m) above it, so a discarded
# high cluster does not blank an honest observation. Facade's kept plane is tilted and
# the cameras sit ~4.3 m above it: that is still the rejected datum.
_LEVEL_UP = 0.99
_STANDING_HEIGHT_M = (0.9, 2.5)
_FOOT_TOP_M = 0.40


def _scene_notes(scene: dict) -> list[str]:
    raw = (scene.get("provenance") or {}).get("notes") or []
    if isinstance(raw, str):
        return [raw]
    return [str(note) for note in raw]


def _plane_up(scene: dict) -> float:
    normal = np.asarray(((scene.get("ground") or {}).get("surface") or {}).get("normal") or [0.0, 1.0, 0.0], float)
    length = float(np.linalg.norm(normal)) or 1.0
    return float(normal[1] / length)


def _camera_centers(scene: dict) -> np.ndarray | None:
    centers = [mat4_from_list(kf["pose"])[:3, 3] for kf in scene.get("keyframes") or [] if kf.get("pose")]
    if not centers:
        return None
    return np.asarray(centers, float)


def _heights_above_plane(scene: dict, centers: np.ndarray) -> np.ndarray | None:
    """Camera height above the ground plane at each camera's own plan position."""
    if "ground" not in scene or len(centers) == 0:
        return None
    try:
        gy = ground_y(scene, np.asarray(centers, float)[:, [0, 2]])
    except (KeyError, ValueError, IndexError, ZeroDivisionError):
        return None
    return np.asarray(centers, float)[:, 1] - gy


def _median_in_standing_band(heights: np.ndarray) -> bool:
    low, high = _STANDING_HEIGHT_M
    return bool(low <= float(np.median(heights)) <= high)


def _any_outside_standing_band(heights: np.ndarray) -> bool:
    if len(heights) == 0:
        return False
    low, high = _STANDING_HEIGHT_M
    return bool(np.any((heights < low) | (heights > high)))


def _seeing_centers(query: CoverageQuery, probes: np.ndarray) -> np.ndarray:
    probes = np.atleast_2d(np.asarray(probes, float))
    if len(probes) == 0 or len(query.centers) == 0:
        return np.zeros((0, 3))
    seen = query.oracle.seen_by(probes, None)
    return query.centers[np.asarray(seen).any(axis=0)]


def ground_incidence_untrusted(scene: dict, seeing_centers: np.ndarray | None = None) -> bool:
    """True when ground incidence must not come back `observed`.

    The scene flagged `ground_plane_rejected` or `ground_base_unobserved` and did not
    record `ground_reanchored`. A level plane whose cameras sit at standing height is the
    datum that survived the guard (electro). A tilt, a median camera height outside
    0.9–2.5 m, or any camera that sees the region outside that band, means incidence is
    still using the rejected fit (facade). Height is the plane under each camera, not
    under a wall midpoint.
    """
    notes = _scene_notes(scene)
    flagged = any(note == "ground_base_unobserved" or note.startswith("ground_plane_rejected") for note in notes)
    if not flagged or "ground_reanchored" in notes:
        return False
    if _plane_up(scene) < _LEVEL_UP:
        return True
    centers = _camera_centers(scene)
    if centers is None:
        return True
    heights = _heights_above_plane(scene, centers)
    if heights is None or not _median_in_standing_band(heights):
        return True
    if seeing_centers is None:
        return False
    seen = np.atleast_2d(np.asarray(seeing_centers, float))
    if len(seen) == 0 or seen.shape[1] == 0:
        return False
    seen_heights = _heights_above_plane(scene, seen)
    if seen_heights is None:
        return True
    return _any_outside_standing_band(seen_heights)


def _depends_on_flagged_plane(surface: str, region: dict) -> bool:
    """Ground strips, and a wall band that is only the foot, take their height from the plane."""
    if surface == "ground":
        return True
    if surface != "wall":
        return False
    span = region.get("height_m")
    if not span or len(span) < 2:
        return False
    return float(span[1]) <= _FOOT_TOP_M


def _downgrade_observed(row: dict) -> dict:
    if row.get("status") != "observed":
        return row
    out = dict(row)
    out["status"] = "partial"
    return out


def withhold_untrusted_ground(
    scene: dict,
    ev: dict,
    surface: str,
    region: dict,
    seeing_centers: np.ndarray | None = None,
) -> dict:
    """Cap an untrusted ground or wall-foot grade at `partial`, never `observed`."""
    if not ground_incidence_untrusted(scene, seeing_centers) or not _depends_on_flagged_plane(surface, region):
        return ev
    ev = _downgrade_observed(ev)
    if ev.get("sensitivity"):
        ev["sensitivity"] = [_downgrade_observed(row) for row in ev["sensitivity"]]
    return ev


def query_region(scene: dict, base: Path, region: dict, query: CoverageQuery | None = None) -> dict:
    """What a measurement backend must attach to every answer about `region`."""
    q = query or CoverageQuery.from_scene(scene, base)
    P, N, surface = region_probes(scene, region)
    ev = q.evidence(P, N, region, surface)
    return withhold_untrusted_ground(scene, ev, surface, region, _seeing_centers(q, P))


def coverage_rule(limits: dict | None = None) -> dict:
    return {
        "min_views": MIN_VIEWS,
        "max_incidence_deg": dict(limits or INCIDENCE_LIMITS_DEG),
        "observed_share": OBSERVED_SHARE,
    }


def set_wall_observed_fraction(scene: dict, query: CoverageQuery) -> None:
    """walls[].observed_fraction = share of the face 0-2.13 m high meeting the observation rule."""
    by_id = {w["id"]: w for w in scene["walls"]}
    for cw in Chain(scene).walls:
        ev = query_region(
            scene, None, {"wall_id": cw.id, "s_m": [cw.s0 + 0.05, cw.s1 - 0.05], "height_m": [0.05, HEADROOM_M]}, query
        )
        by_id[cw.id]["observed_fraction"] = ev["observed_share"]


def _in_grid_sample_span(origin, dirs, lengths, lo, hi_ex, step, voxel):
    """Half-voxel sample indices whose points can fall inside ``[lo, hi_ex)``.

    The span is one sample wider than the ray/box slab. ``build_grid`` still
    drops anything outside, so the kept voxels match marching ``t = 0, step, ...``
    all the way to ``length - 0.75 * voxel``.
    """
    t_max = np.maximum(lengths - 0.75 * voxel, 0.0)
    t_enter = np.zeros(len(lengths))
    t_exit = t_max.copy()
    miss = np.zeros(len(lengths), dtype=bool)
    for axis in range(3):
        component = dirs[:, axis]
        parallel = np.abs(component) < 1e-15
        miss |= parallel & ((origin[axis] < lo[axis]) | (origin[axis] >= hi_ex[axis]))
        denom = np.where(parallel, 1.0, component)
        t_lo = (lo[axis] - origin[axis]) / denom
        t_hi = (hi_ex[axis] - origin[axis]) / denom
        near = np.minimum(t_lo, t_hi)
        far = np.maximum(t_lo, t_hi)
        t_enter = np.maximum(t_enter, np.where(parallel, -np.inf, near))
        t_exit = np.minimum(t_exit, np.where(parallel, np.inf, far))
    t0 = np.maximum(0.0, t_enter - step)
    t1 = np.minimum(t_max, np.maximum(t_exit, t_enter) + step)
    live = (~miss) & (t_enter < t_exit)
    k0 = np.zeros(len(lengths), np.int64)
    k1 = np.zeros(len(lengths), np.int64)
    if np.any(live):
        k0[live] = np.ceil(t0[live] / step - 1e-9).astype(np.int64)
        k_full = np.floor((t_max[live] - 1e-15) / step).astype(np.int64) + 1
        k_margin = np.floor(t1[live] / step + 1e-9).astype(np.int64) + 1
        k1[live] = np.minimum(k_full, k_margin)
    k0 = np.maximum(k0, 0)
    k1 = np.maximum(k1, k0)
    return k0, k1


def build_grid(
    ray_sets, lo: np.ndarray, hi: np.ndarray, voxel: float, occupied_ok=None, max_samples: int = 12_000_000
) -> CoverageGrid:
    """ray_sets: iterable of (origin (3,), ends (M, 3), end_is_surface (M,) bool or None), one per
    camera. Free space is marked up to 0.75 voxel before each end; the end voxel is marked occupied
    when end_is_surface is true for that ray.

    Samples are the same half-voxel steps as a full march. Rays are clipped to the
    grid first: a far point still marks the voxels its ray crosses inside the box,
    and does not allocate samples for the rest of the segment.
    """
    lo = np.asarray(lo, float)
    shape = tuple(int(v) for v in np.ceil((np.asarray(hi, float) - lo) / voxel))
    n = int(np.prod(shape))
    free = np.zeros(n, bool)
    occ = np.zeros(n, bool)
    views = np.zeros(n, np.uint8)
    seen = np.zeros(n, bool)
    step = voxel * 0.5
    dims = np.array(shape, float)
    hi_ex = lo + dims * voxel

    def lin(points):
        ijk = np.floor((points - lo) / voxel).astype(np.int64)
        ok = np.all((ijk >= 0) & (ijk < dims), axis=1)
        ijk = ijk[ok]
        if len(ijk) == 0:
            return np.zeros(0, np.int64)
        return (ijk[:, 0] * shape[1] + ijk[:, 1]) * shape[2] + ijk[:, 2]

    def mark_free(origin, dirs, lengths, k0, k1):
        """Samples ``k0 + j`` for each ray. ``j`` starts at 0, so a far entry does not pad the rest."""
        counts = k1 - k0
        if len(counts) == 0 or int(counts.max()) <= 0:
            return
        offset = 0
        remaining = counts.copy()
        while int(remaining.max()) > 0:
            width = min(max_samples, int(remaining.max()))
            kk = k0[:, None] + offset + np.arange(width)
            active = kk < k1[:, None]
            t = kk * step
            active &= t < (lengths[:, None] - 0.75 * voxel)
            if np.any(active):
                points = origin + dirs[:, None, :] * t[..., None]
                idx = lin(points[active])
                if len(idx):
                    free[idx] = True
                    seen[idx] = True
            offset += width
            remaining = np.maximum(remaining - width, 0)

    for origin, ends, surf in ray_sets:
        o = np.asarray(origin, float)
        ends = np.asarray(ends, float)
        if len(ends) == 0:
            continue
        lengths = np.linalg.norm(ends - o, axis=1)
        dirs = (ends - o) / np.maximum(lengths, 1e-9)[:, None]
        k0, k1 = _in_grid_sample_span(o, dirs, lengths, lo, hi_ex, step, voxel)
        counts = k1 - k0
        order = np.argsort(counts, kind="mergesort")
        ends, dirs, lengths = ends[order], dirs[order], lengths[order]
        k0, k1, counts = k0[order], k1[order], counts[order]
        surf = None if surf is None else np.asarray(surf, bool)[order]
        a = 0
        n_rays = len(ends)
        while a < n_rays:
            if counts[a] <= 0:
                a += 1
                continue
            if int(counts[a]) > max_samples:
                b = a + 1
            else:
                lo_b = a + 1
                hi_b = n_rays
                while lo_b < hi_b:
                    mid = (lo_b + hi_b + 1) // 2
                    if (mid - a) * int(counts[mid - 1]) <= max_samples:
                        lo_b = mid
                    else:
                        hi_b = mid - 1
                b = lo_b
            mark_free(o, dirs[a:b], lengths[a:b], k0[a:b], k1[a:b])
            a = b
        end_idx = lin(ends if surf is None else ends[surf])
        if len(end_idx):
            occ[end_idx] = True
            seen[end_idx] = True
        hit = np.flatnonzero(seen)
        if len(hit):
            views[hit] = np.minimum(views[hit].astype(np.int32) + 1, 255).astype(np.uint8)
            seen[hit] = False
    state = np.zeros(n, np.uint8)
    state[free] = CoverageGrid.FREE
    state[occ] = CoverageGrid.OCCUPIED
    return CoverageGrid(state.reshape(shape), views.reshape(shape), lo, voxel)


def _runs(ss, ok, step):
    out = []
    for s, k in zip(ss, ok):
        if not k:
            continue
        if out and abs(out[-1][1] - (s - step / 2)) < 1e-6:
            out[-1][1] = s + step / 2
        else:
            out.append([s - step / 2, s + step / 2])
    return out


def bands_from_grid(
    grid: CoverageGrid,
    scene: dict,
    step: float = 0.25,
    query: CoverageQuery | None = None,
    ground_limit: float | None = None,
    only: tuple[str, ...] | None = None,
    ground_max_fp: float | None = None,
) -> list[dict]:
    """PR #11-style band summary (in meters) of what the grid observed along each chain wall.
    With a `query`, the wall and ground bands also require the observation rule on the surface
    itself (>= MIN_VIEWS views within the surface's incidence limit; `ground_limit` overrides the
    ground one), so a face seen only edge-on or a ground strip only skimmed is not a band; each
    such band records its views and best incidence. `only` restricts the bands computed."""
    want = set(only or ("wall", "ground", "overhead", "facing"))
    chain = Chain(scene)
    bands = []
    for w in chain.walls:
        ss = np.arange(w.s0 + step / 2, w.s1, step)
        if len(ss) == 0:
            continue
        q = w.plan_at(ss)
        gy = ground_y(scene, q)
        n_out = np.array([w.outward[0], 0.0, w.outward[1]])

        def pts(out, y):
            p = q + out * w.outward
            return np.c_[p[:, 0], gy + y, p[:, 1]]

        def obs(out, y):
            return grid.observed_at(pts(out, y))

        if "wall" not in want:
            wall_ok = np.zeros(len(ss), bool)
        else:
            wall_ok = np.all([obs(0.1, y) for y in WALL_PROBE_HEIGHTS], axis=0)
        wall_views = np.full(len(ss), np.nan)
        wall_inc = np.full(len(ss), np.nan)
        if query is not None and "wall" in want:
            per = [query.per_probe(pts(0.01, y), n_out, "wall") for y in WALL_PROBE_HEIGHTS]
            good = np.all([pp["views_good"] >= MIN_VIEWS for pp in per], axis=0)
            wall_ok &= good
            wall_views = np.min([pp["views_good"] for pp in per], axis=0).astype(float)
            wall_inc = np.nanmax([pp["best_incidence_deg"] for pp in per], axis=0)
        for a, b in _runs(ss, wall_ok, step):
            e = {"wall_id": w.id, "band": "wall", "s_m": [round(a, 3), round(b, 3)]}
            if query is not None:
                k = (ss > a) & (ss < b)
                e["views"] = int(np.nanmin(wall_views[k]))
                e["best_incidence_deg"] = clamp_incidence_deg(float(np.nanmax(wall_inc[k])))
            bands.append(e)

        if want <= {"wall"}:
            continue
        outs = np.arange(0.2, GROUND_MAX_OUT_M + 1e-9, 0.1)
        g = np.stack([obs(d, 0.05) for d in outs], 1)
        g_views = g_inc = None
        if query is not None:
            per = [
                query.per_probe(pts(d, 0.01), np.array([0.0, 1.0, 0.0]), "ground", ground_limit, ground_max_fp)
                for d in outs
            ]
            g &= np.stack([pp["views_good"] >= MIN_VIEWS for pp in per], 1)
            g_views = np.stack([pp["views_good"] for pp in per], 1)
            g_inc = np.stack([pp["best_incidence_deg"] for pp in per], 1)
        ground_out = np.where(g.all(1), GROUND_MAX_OUT_M, outs[np.argmin(g, 1)] - 0.1)
        ground_out = np.where(g[:, 0], ground_out, 0.0)
        n0 = len(bands)
        if "ground" in want:
            _emit_quantized(bands, w.id, "ground", ss, ground_out, step, min_out=0.3)
        if query is not None and "ground" in want:
            for e in bands[n0:]:
                k = (ss > e["s_m"][0]) & (ss < e["s_m"][1])
                reach = outs <= e.get("out_m", GROUND_MAX_OUT_M) + 1e-9
                e["views"] = int(g_views[np.ix_(k, reach)].min())
                e["best_incidence_deg"] = clamp_incidence_deg(float(np.nanmax(g_inc[np.ix_(k, reach)])))

        if not want & {"overhead", "facing"}:
            continue
        hs = np.arange(0.2, 3.0 + 1e-9, 0.1)
        cols = np.stack([np.all([obs(d, h) for d in (0.2, 0.5, OVERHEAD_OUT_M)], axis=0) for h in hs], 1)
        over_h = np.where(cols.all(1), 3.0, hs[np.argmin(cols, 1)] - 0.1)
        over_h = np.where(cols[:, 0], over_h, 0.0)
        _emit_quantized(bands, w.id, "overhead", ss, over_h, step, min_out=HEADROOM_M, cap=3.0)

        fo = np.arange(0.2, FACING_MAX_OUT_M, 0.1)
        f = np.stack([obs(d, FACING_PROBE_Y) for d in fo], 1)
        face_out = np.where(f.all(1), FACING_MAX_OUT_M, fo[np.argmin(f, 1)] - 0.1)
        face_out = np.where(f[:, 0], face_out, 0.0)
        _emit_quantized(bands, w.id, "facing", ss, face_out, step, min_out=0.3)
    return bands


def ground_sensitivity(grid: CoverageGrid, scene: dict, query: CoverageQuery) -> list[dict]:
    """Ground bands under every GROUND_RULES variant (scene coverage.sensitivity)."""
    return [
        {
            "surface": "ground",
            "rule_id": rid,
            **r,
            "bands": bands_from_grid(
                grid,
                scene,
                query=query,
                ground_limit=r["max_incidence_deg"],
                only=("ground",),
                ground_max_fp=r.get("max_footprint_m"),
            ),
        }
        for rid, r in GROUND_RULES.items()
    ]


def _emit_quantized(bands, wall_id, band, ss, vals, step, min_out, cap=None, q=0.5):
    """Merge stations into runs whose value quantizes the same; each run keeps its minimum."""
    cur = None
    for s, v in zip(ss, vals):
        if v < min_out:
            cur = None
            continue
        key = np.floor(v / q)
        if cur and cur["key"] == key and abs(cur["b"] - (s - step / 2)) < 1e-6:
            cur["b"] = s + step / 2
            cur["v"] = min(cur["v"], v)
        else:
            cur = {"key": key, "a": s - step / 2, "b": s + step / 2, "v": v}
            bands.append(cur)
        cur["band"], cur["wall_id"] = band, wall_id
    for i, c in enumerate(bands):
        if isinstance(c, dict) and "key" in c:
            e = {"wall_id": c["wall_id"], "band": c["band"], "s_m": [round(c["a"], 3), round(c["b"], 3)]}
            if not (cap is not None and c["v"] >= cap):
                e["out_m"] = round(float(c["v"]), 2)
            bands[i] = e
