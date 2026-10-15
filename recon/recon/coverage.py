"""Which wall, ground, facing and overhead cells were actually seen, by a depth test against the
reconstruction, in the server's terms (`coverage.observed` in scene.schema.json).

The app's coverage map credits a cell whose samples fall in a keyframe's view, range and angle;
it cannot tell a wall from a bush in front of it, and on ETH3D electro it claimed 1.1 ft of wall no
photo saw (experiments/evals README section 7 on t3/evals). A depth test against true depth
claimed nothing unseen (section 7b). Here a sample counts as seen by a frame only when also:
- the frame's own depth at the sample's pixel lies within the tolerance max(10 cm, 4% of the
  distance) of the sample's depth. Nearer means something hides the sample. Farther means no
  surface is there, only a clear view through it: the fitted wall line bridges doors and openings
  up to geometry.GAP_M, and a view through an opening to whatever lies behind it is not wall
  backing, nor is a view into a pit ground; and
- no occupied voxel of the fused reconstruction lies between the camera and the sample, short of
  that tolerance.
Cells are half-foot steps clipped to the fitted wall extent, and every sample sits inside it: a
surface beyond the fit's ends can never drive an observation, because the fitted wall line
establishes nothing there (a longer baseline an older scan left behind does not either). On
flat ground a clipped fit yields exactly the cells the old first-to-last grid did.

A cell is observed when every sample row was seen from two camera positions at least 0.25 m
apart, the app's bar for "covered". Wall samples sit on the wall's reconstructed face where that
face is a flat, full-height surface in front of the fitted plane (a pilaster), and on the plane
otherwise, so a bush in front of the wall hides the wall instead of standing in for it.

Facing and overhead space needs no surface: it is observed where the fused volume shows it seen
empty (every voxel observed, none occupied), up to the first occupied voxel (a measured gap or
clearance) or the first unobserved one (seen clear only that far).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from recon.capture import FEET, UP, Frame
from recon.depth import Depth
from recon.fusion import MAX_RANGE_M, Mesh, Volume
from recon.geometry import Ground, WallFrame

CELL_M = 0.1524  # half a foot, the app's cell
WALL_TOP_M = 1.9812  # 6.5 ft, the headroom the export's wall band claims
WALL_ROWS = 9  # about 0.25 m apart, so a box mounted between rows cannot slip through
GROUND_MAX_M = 3.048  # 10 ft out
FACING_MAX_M = 6.0
FACING_BAND_M = (0.3, 1.8)
OVERHEAD_OUT_M = (0.1, 0.56)  # above the battery's footprint (1.83 ft deep)
OVERHEAD_MAX_M = 3.0
MAX_ANGLE = np.radians(65)  # the app's limit on how oblique a view may be
IMAGE_MARGIN = 0.03
BASELINE_M = 0.25
HIDE_ABS_M, HIDE_REL = 0.10, 0.04


def cell_grid(lo: float, hi: float) -> tuple[np.ndarray, np.ndarray]:
    """Coverage cells as (starts, ends) in meters of s: half-foot steps clipped to the fitted
    extent [lo, hi]. Interior edges stay on the absolute half-foot grid, so a fit that begins and
    ends on the grid gets exactly the cells it always did; an unaligned end clips its boundary
    cell instead of letting a sample, and so an observation, fall outside the fit."""
    if hi <= lo:
        return np.zeros(0), np.zeros(0)
    inner = np.arange(np.ceil(lo / CELL_M - 1e-9) * CELL_M, hi, CELL_M)
    edges = np.r_[lo, inner[(inner > lo + 1e-9) & (inner < hi - 1e-9)], hi]
    return edges[:-1], edges[1:]


def _cell_ends(cells: np.ndarray, ends: np.ndarray | None) -> np.ndarray:
    """The right edge of every cell: `ends` when compute clipped the grid to the fit, else every
    cell is a full CELL_M wide, the shape a CellCoverage built by hand carries."""
    return cells + CELL_M if ends is None else np.asarray(ends)


def tolerance(distance: np.ndarray) -> np.ndarray:
    return np.maximum(HIDE_ABS_M, HIDE_REL * distance)


def seen_by(
    points: np.ndarray,
    normal: np.ndarray,
    frames: list[Frame],
    depths: dict[str, Depth],
    vol: Volume,
) -> np.ndarray:
    """(N, frames) bool: frame j saw point i. `normal` is (3,) or (N, 3)."""
    normal = np.broadcast_to(normal, points.shape)
    out = np.zeros((len(points), len(frames)), bool)
    for j, f in enumerate(frames):
        T = f.cam_to_world
        c = T[:3, 3]
        to_cam = c - points
        dist = np.linalg.norm(to_cam, axis=1)
        ok = (dist > 0.1) & (dist <= MAX_RANGE_M)
        ok &= np.einsum("ij,ij->i", to_cam, normal) / np.maximum(dist, 1e-9) >= np.cos(MAX_ANGLE)
        p = (points - c) @ T[:3, :3]
        z = -p[:, 2]
        ok &= z > 0.05
        fx, fy, cx, cy = f.intrinsics
        with np.errstate(divide="ignore", invalid="ignore"):
            u = cx + fx * p[:, 0] / z
            v = cy - fy * p[:, 1] / z
        mx, my = f.width * IMAGE_MARGIN, f.height * IMAGE_MARGIN
        ok &= (u >= mx) & (u <= f.width - mx) & (v >= my) & (v <= f.height - my)
        idx = np.flatnonzero(ok)
        if not len(idx):
            continue
        d = depths[f.id]
        h, w = d.depth.shape
        s = w / f.width
        du = np.clip((u[idx] * s).astype(np.int64), 0, w - 1)
        dv = np.clip((v[idx] * s).astype(np.int64), 0, h - 1)
        measured = d.depth[dv, du]
        tol = tolerance(dist[idx])
        # The frame's own depth must put a surface at the sample: unknown there, nearer (hidden)
        # or farther (seen through, no surface) by more than the tolerance is not seen.
        visible = np.isfinite(measured) & (np.abs(measured - z[idx]) <= tol)
        # The fused reconstruction: nothing occupied between the camera and the sample.
        keep = idx[visible]
        if len(keep):
            blocked = vol.blocked(c, points[keep], tol[visible] + vol.voxel)
            out[keep[~blocked], j] = True
    return out


def two_positions(saw: np.ndarray, centres: np.ndarray) -> np.ndarray:
    far = (np.linalg.norm(centres[:, None] - centres[None], axis=-1) >= BASELINE_M).astype(
        np.float32
    )
    s = saw.astype(np.float32)
    return ((s @ far) * s).sum(axis=-1) > 0


def face_offsets(
    wall: WallFrame, mesh: Mesh, cells: np.ndarray, ends: np.ndarray | None = None
) -> np.ndarray:
    """Per cell, how far a flat, full-height face stands in front of the fitted plane (0 if none).
    Flat: in at least 75% of the 10 cm height bins from 0.3 to 1.9 m, the front-most surface within
    0.6 m lies within 3 cm of their median. A bush or bin is neither, so it stays an occluder."""
    ends = _cell_ends(cells, ends)
    edges = np.r_[cells, ends[-1]] if len(cells) else cells
    loc = wall.local(mesh.vertices.astype(np.float64))
    s, h, out = loc[:, 0], loc[:, 1], loc[:, 2]
    keep = (out > -0.15) & (out < 0.6) & (h > 0.3) & (h < 1.9)
    col = np.searchsorted(edges, s[keep], side="right") - 1
    row = np.floor((h[keep] - 0.3) / 0.1).astype(np.int64)
    ok = (col >= 0) & (col < len(cells))
    front = np.full((len(cells), 16), -np.inf)
    np.maximum.at(front, (col[ok], row[ok]), out[keep][ok])
    offsets = np.zeros(len(cells))
    for c in range(len(cells)):
        f = front[c][np.isfinite(front[c])]
        if len(f) >= 12:
            m = float(np.median(f))
            if np.sum(np.abs(f - m) <= 0.03) >= 12 and m > HIDE_ABS_M:
                offsets[c] = m
    return offsets


@dataclass
class CellCoverage:
    cells: np.ndarray  # left edge of each cell, meters of s
    wall: np.ndarray  # bool per cell
    ground_out: np.ndarray  # meters out from the wall the ground was seen, contiguous from the foot
    facing_gap: np.ndarray  # meters from the wall's front to the first thing facing it, or NaN
    facing_clear: np.ndarray  # meters seen empty out from the wall's front
    overhead_clearance: np.ndarray  # meters up to the first thing overhead, NaN if none seen
    overhead_clear: np.ndarray  # meters seen empty above the footprint
    ends: np.ndarray | None = None  # right edge of each cell, meters of s


def wall_and_ground(
    wall: WallFrame,
    frames: list[Frame],
    depths: dict[str, Depth],
    vol: Volume,
    mesh: Mesh,
    cells: np.ndarray,
    ends: np.ndarray | None = None,
    ground: Ground | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ends = _cell_ends(cells, ends)
    centres = np.array([f.center for f in frames])
    faces = face_offsets(wall, mesh, cells, ends)
    widths = ends - cells
    alongs = np.stack([cells + 0.25 * widths, cells + 0.75 * widths], axis=1)  # (C, 2)
    heights = np.linspace(0.05, WALL_TOP_M, WALL_ROWS)
    S = np.broadcast_to(alongs[:, :, None], (len(cells), 2, WALL_ROWS))
    Hh = np.broadcast_to(heights[None, None, :], S.shape)
    offs = np.broadcast_to(faces[:, None, None], S.shape)
    wpts = wall.world(S, Hh, offs).reshape(-1, 3)
    wseen = two_positions(seen_by(wpts, wall.outward, frames, depths, vol), centres)
    wall_ok = wseen.reshape(len(cells), 2, WALL_ROWS).all(axis=(1, 2))

    # Half-foot steps from the foot, and GROUND_MAX_M itself: the steps alone end 0.1 m short,
    # and `ground_out` only ever reports a sampled distance, so 10 ft needs a sample at 10 ft.
    outs = np.append(np.arange(0.05, GROUND_MAX_M - 1e-6, CELL_M), GROUND_MAX_M)
    S = np.broadcast_to(alongs[:, :, None], (len(cells), 2, len(outs)))
    Oo = np.maximum(np.broadcast_to(outs[None, None, :], S.shape), faces[:, None, None] + 0.05)
    if ground is None:
        h = np.zeros_like(S)
    else:
        # The fitted ground, not the horizontal plane through the meter's foot: at (s, out) the
        # surface's height above the meter's ground is height_at there minus its height at the
        # meter, so on a slope the samples follow the ground instead of hanging above or below
        # it, where nothing was built and the depth test would read visible ground unobserved.
        flat = wall.world(S, np.zeros_like(S), Oo)
        h = ground.height_at(flat[..., 0], flat[..., 2]) - wall.ground_y
    gpts = wall.world(S, h, Oo).reshape(-1, 3)
    gseen = two_positions(seen_by(gpts, UP, frames, depths, vol), centres).reshape(
        len(cells), 2, len(outs)
    )
    row_ok = gseen.all(axis=1)  # (C, outs)
    ground_out = np.zeros(len(cells))
    for c in range(len(cells)):
        bad = np.flatnonzero(~row_ok[c])
        n = bad[0] if len(bad) else len(outs)
        ground_out[c] = outs[n - 1] if n > 0 else 0.0
    return wall_ok, ground_out, faces


OCCUPIED_SHARE = 0.2  # of a slab's voxels, over two consecutive steps, to count as something there
WALL_SEARCH_M = (-0.3, 1.0)


def _slab(wall: WallFrame, vol: Volume, across, heights, outs) -> tuple[float, bool]:
    """Occupied share and whether every voxel was observed, over a grid of wall coordinates."""
    S, Hh, Oo = np.meshgrid(across, heights, outs, indexing="ij")
    pts = wall.world(S, Hh, Oo).reshape(-1, 3)
    return float(vol.occupied(pts).mean()), bool(vol.observed(pts).all())


def wall_front(wall: WallFrame, vol: Volume, across: np.ndarray) -> float:
    """Where the reconstructed wall surface ends toward the homeowner: marching out from behind
    the fitted plane, the first step past the wall's occupied voxels. A noisy reconstruction
    smears a wall several inches thick; space inside that smear is neither free nor an obstacle."""
    heights = np.arange(0.5, FACING_BAND_M[1] + 1e-9, vol.voxel)
    front, inside = 0.0, False
    for out in np.arange(WALL_SEARCH_M[0], WALL_SEARCH_M[1], vol.voxel):
        share, _ = _slab(wall, vol, across, heights, [out])
        if share >= OCCUPIED_SHARE:
            inside, front = True, out
        elif inside:
            break
    return front + vol.voxel


def free_space(wall: WallFrame, vol: Volume, cells: np.ndarray, ends: np.ndarray | None = None):
    """Per cell: (facing gap or NaN, facing clear, overhead clearance or NaN, overhead clear).
    The facing gap and clear distance are measured out from the wall's reconstructed front, not
    from the fitted plane: in front of a pilaster 0.3 m proud, something 1.3 m out of the plane
    is 1.0 m from the face the battery would stand against. The front is the first free step
    past the wall's occupied voxels, so both read up to a voxel short, never long. Overhead
    clearance is a height above the ground over the footprint in front of that face."""
    step = vol.voxel
    n = len(cells)
    widths = _cell_ends(cells, ends) - cells
    facing_gap, facing_clear = np.full(n, np.nan), np.zeros(n)
    over_gap, over_clear = np.full(n, np.nan), np.zeros(n)
    fh = np.arange(FACING_BAND_M[0], FACING_BAND_M[1] + 1e-9, step)
    for c in range(n):
        across = cells[c] + widths[c] * np.array([0.25, 0.5, 0.75])
        front = wall_front(wall, vol, across)
        hits = 0
        for out in np.arange(front + step, front + FACING_MAX_M, step):
            share, seen = _slab(wall, vol, across, fh, [out])
            hits = hits + 1 if share >= OCCUPIED_SHARE else 0
            if hits == 2:
                facing_gap[c] = out - step - front
                break
            if not seen:
                break
            if hits == 0:
                facing_clear[c] = out - front
        oo = front + np.arange(OVERHEAD_OUT_M[0], OVERHEAD_OUT_M[1] + 1e-9, step)
        hits = 0
        for h in np.arange(0.3, OVERHEAD_MAX_M, step):
            share, seen = _slab(wall, vol, across, [h], oo)
            hits = hits + 1 if share >= OCCUPIED_SHARE else 0
            if hits == 2:
                over_gap[c] = h - step
                break
            if not seen:
                break
            if hits == 0:
                over_clear[c] = h
    return facing_gap, facing_clear, over_gap, over_clear


def compute(
    wall: WallFrame,
    frames: list[Frame],
    depths: dict[str, Depth],
    vol: Volume,
    mesh: Mesh,
    ground: Ground | None = None,
) -> CellCoverage:
    """`ground`, the fit the wall's ground height came from, lets the ground samples follow the
    surface off the horizontal plane through the meter's foot; the cells stop at the fitted wall
    extent, so no exported observation runs past what the fit established."""
    cells, ends = cell_grid(*wall.s_range)
    wall_ok, ground_out, _ = wall_and_ground(wall, frames, depths, vol, mesh, cells, ends, ground)
    fg, fc, og, oc = free_space(wall, vol, cells, ends)
    return CellCoverage(cells, wall_ok, ground_out, fg, fc, og, oc, ends)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index runs where mask is true."""
    runs, start = [], None
    for i, m in enumerate(np.append(mask, False)):
        if m and start is None:
            start = i
        elif not m and start is not None:
            runs.append((start, i))
            start = None
    return runs


def _ft(m: float) -> float:
    return round(m / FEET, 3)


def observed(cov: CellCoverage, ground_levels_ft=(2.0, 4.0, 6.0, 8.0, 10.0)) -> list[dict]:
    """The coverage as scene.json `coverage.observed` entries (feet, s from the meter).

    Ground is reported at a few depths so every clearance's request can be met by one entry;
    `out_ft` is always a distance the samples cleared, rounded down. A span stops at the fitted
    extent: its end cells may be partial ones, clipped to where the fit established the wall."""
    ends = _cell_ends(cov.cells, cov.ends)
    span = lambda a, b: [_ft(cov.cells[a]), _ft(ends[b - 1])]  # noqa: E731
    entries = [{"band": "wall", "span_ft": span(a, b)} for a, b in _runs(cov.wall)]
    for level in ground_levels_ft:
        for a, b in _runs(cov.ground_out >= level * FEET - 1e-9):
            entries.append({"band": "ground", "span_ft": span(a, b), "out_ft": level})
    has_gap = np.isfinite(cov.facing_gap)
    for a, b in _runs(has_gap):
        entries.append({"band": "facing", "span_ft": span(a, b)})
    clear_ft = np.floor(cov.facing_clear / FEET * 2) / 2  # half feet, rounded down
    for a, b in _runs(~has_gap & (clear_ft > 0)):
        entries.append(
            {"band": "facing", "span_ft": span(a, b), "out_ft": float(clear_ft[a:b].min())}
        )
    has_over = np.isfinite(cov.overhead_clearance)
    for a, b in _runs(has_over):
        entries.append({"band": "overhead", "span_ft": span(a, b)})
    oclear_ft = np.floor(cov.overhead_clear / FEET * 2) / 2
    for a, b in _runs(~has_over & (oclear_ft > 0)):
        entries.append(
            {"band": "overhead", "span_ft": span(a, b), "out_ft": float(oclear_ft[a:b].min())}
        )
    return entries


def measurements(
    cov: CellCoverage, wall_id: str, plus_minus_ft: float
) -> tuple[list[dict], list[dict]]:
    """scene.json `facing` and `overheads` entries: runs of cells whose measured gap or clearance
    stays within half a foot, each reporting its smallest value."""

    def runs_of(values: np.ndarray) -> list[tuple[int, int]]:
        out = []
        for a, b in _runs(np.isfinite(values)):
            start = a
            for i in range(a + 1, b + 1):
                if i == b or abs(values[i] - values[start:i].min()) > 0.5 * FEET:
                    out.append((start, i))
                    start = i
        return out

    def entry(a, b, key, values):
        return {
            "wall_id": wall_id,
            "span_ft": [_ft(cov.cells[a]), _ft(_cell_ends(cov.cells, cov.ends)[b - 1])],
            key: _ft(float(values[a:b].min())),
            "plus_minus_ft": plus_minus_ft,
        }

    facing = [entry(a, b, "depth_ft", cov.facing_gap) for a, b in runs_of(cov.facing_gap)]
    overheads = [
        entry(a, b, "clearance_ft", cov.overhead_clearance)
        for a, b in runs_of(cov.overhead_clearance)
    ]
    return facing, overheads
