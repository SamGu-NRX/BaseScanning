"""Procedural scenes with known geometry: walls with openings, pilasters, occluders, ground.

Every scene lives in one world frame, metres, +y up: the main wall runs along x with its outer
face at z = 0, the cameras stand at z > 0 (the outward side), and the meter sits on the face at
x = 0, so true s equals x. Solids carry triangles for rendering and ray casting, and the
occluders know whether a point is inside them. Ground patches carry a surface height function
(flat or sloped) and a type label; nothing here is estimated - it is the reference.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

UP = np.array([0.0, 1.0, 0.0])
WALL_T = 0.15  # wall thickness, behind the face


def box_tris(lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """(12, 3, 3) triangles of an axis-aligned box."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    v = np.array(
        [
            [lo[0], lo[1], lo[2]],
            [hi[0], lo[1], lo[2]],
            [hi[0], hi[1], lo[2]],
            [lo[0], hi[1], lo[2]],
            [lo[0], lo[1], hi[2]],
            [hi[0], lo[1], hi[2]],
            [hi[0], hi[1], hi[2]],
            [lo[0], hi[1], hi[2]],
        ]
    )
    quads = [
        (0, 1, 2, 3),  # z = lo
        (5, 4, 7, 6),  # z = hi
        (4, 0, 3, 7),  # x = lo
        (1, 5, 6, 2),  # x = hi
        (3, 2, 6, 7),  # y = hi
        (4, 5, 1, 0),  # y = lo
    ]
    tris = []
    for a, b, c, d in quads:
        tris.append(v[[a, b, c]])
        tris.append(v[[a, c, d]])
    return np.array(tris)


@dataclass
class Box:
    """An axis-aligned box: a wall segment, a pilaster, a bin, a rail, a fence panel."""

    lo: np.ndarray
    hi: np.ndarray
    label: str

    def tris(self) -> np.ndarray:
        return box_tris(self.lo, self.hi)

    def contains(self, pts: np.ndarray) -> np.ndarray:
        pts = np.atleast_2d(pts)
        return np.all((pts >= self.lo) & (pts <= self.hi), axis=-1)


@dataclass
class Ellipsoid:
    """Foliage: returns depth like a bush crown but is not flat or full height."""

    center: np.ndarray
    radii: np.ndarray
    label: str = "bush"

    def tris(self) -> np.ndarray:
        n = 48  # an icosphere would be nicer; a coarse uv sphere renders the same at 8 mm noise
        us = np.linspace(0, 2 * np.pi, n, endpoint=False)
        vs = np.linspace(0, np.pi, n // 2)
        vv, uu = np.meshgrid(vs, us, indexing="ij")  # (len(vs), len(us))
        grid = np.stack(
            [
                np.sin(vv) * np.cos(uu) * self.radii[0],
                np.cos(vv) * self.radii[1],
                np.sin(vv) * np.sin(uu) * self.radii[2],
            ],
            axis=-1,
        ) + self.center
        tris = []
        for i in range(len(us) - 1):
            for j in range(len(vs) - 1):
                a, b = grid[j, i], grid[j, i + 1]
                c, d = grid[j + 1, i], grid[j + 1, i + 1]
                tris += [[a, b, d], [a, d, c]]
        return np.array(tris)

    def contains(self, pts: np.ndarray) -> np.ndarray:
        d = (np.atleast_2d(pts) - self.center) / self.radii
        return (d**2).sum(axis=-1) <= 1.0 + 1e-9


@dataclass
class GroundPatch:
    """A ground surface with a height function. `height_at` is the truth the judge measures
    claims against; the triangles stand in for the surface in rendering and ray casting."""

    x0: float
    x1: float
    z0: float
    z1: float
    kind: str  # a scene.schema.json ground type: concrete, lawn, deck, ...
    y0: float = 0.0  # height at the patch's near edge (z0)
    slope_dz: float = 0.0  # dy per metre of +z (a falling apron is negative)
    measured: bool = True  # False = attested only as a 2D outline: no measured height exists

    def height_at(self, x, z) -> np.ndarray:
        x, z = np.asarray(x, float), np.asarray(z, float)
        if not self.measured:
            return np.full(x.shape, np.nan)  # a 2D polygon supplies no measured ground height
        return np.full(x.shape, self.y0) + self.slope_dz * (z - self.z0)

    def tris(self) -> np.ndarray:
        top = np.array(
            [
                [self.x0, self.height_at(self.x0, self.z0)[()], self.z0],
                [self.x1, self.height_at(self.x1, self.z0)[()], self.z0],
                [self.x1, self.height_at(self.x1, self.z1)[()], self.z1],
                [self.x0, self.height_at(self.x0, self.z1)[()], self.z1],
            ]
        )
        skirt = 0.3
        bottom = top - np.array([0.0, skirt, 0.0])
        v = np.vstack([top, bottom])
        quads = [(0, 1, 2, 3), (7, 6, 5, 4), (4, 5, 1, 0), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
        tris = []
        for a, b, c, d in quads:
            tris.append(v[[a, b, c]])
            tris.append(v[[a, c, d]])
        return np.array(tris)


@dataclass
class Scene:
    """A physical scene. `wall` is the main wall the phone marks; everything else is context."""

    name: str
    wall_x0: float  # true wall face span along x, face at z = 0
    wall_x1: float
    wall_h: float = 2.6
    openings: list[tuple[float, float, float, float]] = field(default_factory=list)
    # (s0, s1, y0, y1) in metres: a hole through the wall (a door or window frame; nothing fills it)
    fills: dict[int, str] = field(default_factory=dict)
    # opening index -> "door" (a closed door fills the opening with a flush panel) or "glass"
    pilasters: list[tuple[float, float, float]] = field(default_factory=list)
    # (centre_s, width, proud): flat, full-height faces in front of the wall plane
    occluders: list[Box | Ellipsoid] = field(default_factory=list)
    # anything standing in front of the wall: bins, bushes, rails, fences
    return_wall: tuple[float, float] | None = None  # an L-corner: wall along z at wall_x1, to z2
    return_z2: float = 2.5
    arc_chord: tuple[float, float] | None = None  # a curved section replacing the wall over [x0,x1]
    arc_radius: float = 60.0
    grounds: list[GroundPatch] = field(default_factory=list)
    back_wall_z: float | None = None  # a fence or wall facing the house, parallel, z > 0

    # ---- construction ---------------------------------------------------------------
    def build_solids(self) -> list[Box | Ellipsoid]:
        out: list[Box | Ellipsoid] = list(self.occluders)
        if self.arc_chord is None:
            segs = self._wall_segments()
            for lo, hi in segs:
                out.append(
                    Box(
                        np.array([lo, 0.0, -WALL_T]),
                        np.array([hi, self.wall_h, 0.0]),
                        "wall",
                    )
                )
        else:
            out.extend(self._arc_solids())
        if self.return_wall is not None:
            z2 = self.return_z2
            out.append(
                Box(
                    np.array([self.wall_x1, 0.0, 0.0]),
                    np.array([self.wall_x1 + WALL_T, self.wall_h, z2]),
                    "return-wall",
                )
            )
        if self.back_wall_z is not None:
            out.append(
                Box(
                    np.array([self.wall_x0 - 1.0, 0.0, self.back_wall_z]),
                    np.array([self.wall_x1 + 1.0, 1.8, self.back_wall_z + 0.08]),
                    "back-fence",
                )
            )
        for s, width, proud in self.pilasters:
            out.append(
                Box(
                    np.array([s - width / 2, 0.0, -WALL_T]),
                    np.array([s + width / 2, self.wall_h, proud]),
                    "pilaster",
                )
            )
        return out

    def _wall_segments(self) -> list[tuple[float, float]]:
        """Full-height x spans between openings; each opening also yields a lintel (and, when the
        opening does not reach the ground, a sill) so the hole is a true hole."""
        cuts = sorted((o[0], o[1]) for o in self.openings)
        segs, x = [], self.wall_x0
        for a, b in cuts:
            if a > x:
                segs.append((x, a))
            x = b
        if x < self.wall_x1:
            segs.append((x, self.wall_x1))
        return segs

    def _opening_solids(self) -> list[Box]:
        """Lintels and sills, and closed doors: a fill keeps the wall plane continuous."""
        out = []
        for i, (a, b, y0, y1) in enumerate(self.openings):
            kind = self.fills.get(i)
            if kind == "door":
                out.append(
                    Box(np.array([a, y0, -WALL_T]), np.array([b, y1, 0.0]), "door"),  # type: ignore[arg-type]
                )
                continue
            if y1 < self.wall_h:  # lintel above
                out.append(Box(np.array([a, y1, -WALL_T]), np.array([b, self.wall_h, 0.0]), "wall"))
            if y0 > 0:  # sill below
                out.append(Box(np.array([a, 0.0, -WALL_T]), np.array([b, y0, 0.0]), "wall"))
        return out

    def _arc_solids(self) -> list[Box | Ellipsoid | YRotBox]:
        """A circular boundary: the wall bows toward the cameras over [x0, x1] with radius
        `arc_radius` (convex toward the cameras, like a bowed facade): the chord ends stay on
        z = 0 and the middle sits `sagitta` in front. Each chord is a thin rotated box, so
        rendering, ray casting and containment share one geometry."""
        x0, x1 = self.arc_chord
        r = self.arc_radius
        cx, dx = (x0 + x1) / 2, x1 - x0
        sag = r - np.sqrt(r**2 - (dx / 2) ** 2)
        zc = sag - r  # circle centre behind the arc; the near side passes through (x0..x1)
        n = 24
        xs = np.linspace(x0, x1, n + 1)
        zs = zc + np.sqrt(np.maximum(r**2 - (xs - cx) ** 2, 0.0))
        out: list[Box | Ellipsoid | YRotBox] = []
        for i in range(n):
            p0 = np.array([xs[i], 0.0, zs[i]])
            p1 = np.array([xs[i + 1], 0.0, zs[i + 1]])
            mid = (p0 + p1) / 2
            d = p1 - p0
            length = float(np.linalg.norm(d)) + 2e-3  # overlap neighbours so no gap shows
            yaw = np.arctan2(d[0], d[2])  # local +z is the chord's outward normal (toward camera)
            out.append(
                YRotBox(
                    np.array([-length / 2, 0.0, -WALL_T]),
                    np.array([length / 2, self.wall_h, 0.0]),
                    mid,
                    yaw,
                    "arc",
                )
            )
        return out

    # ---- queries --------------------------------------------------------------------
    def all_tris(self) -> np.ndarray:
        parts = [s.tris() for s in self.build_solids()]
        parts += [o.tris() for o in self._opening_solids()]
        parts += [g.tris() for g in self.grounds]
        return np.concatenate(parts, axis=0)

    def true_ground_height(self, x, z) -> np.ndarray:
        """The physical ground height at (x, z): the patch covering the point, else NaN."""
        x, z = np.broadcast_arrays(np.asarray(x, float), np.asarray(z, float))
        out = np.full(x.shape, np.nan)
        best = np.full(x.shape, -np.inf)
        for g in self.grounds:
            inside = (x >= g.x0) & (x <= g.x1) & (z >= g.z0) & (z <= g.z1)
            h = g.height_at(x, z)
            take = inside & (h > best)
            out[take] = h[take]
            best[take] = h[take]
        return out

    def opening_at(self, s: float, y: float) -> bool:
        """True when (s, y) on the wall face lies inside an opening that is not filled."""
        for i, (a, b, y0, y1) in enumerate(self.openings):
            if a <= s <= b and y0 <= y <= y1 and self.fills.get(i) != "door":
                return True
        return False


class YRotBox:
    """A box rotated about the y axis by `yaw` around plan point `pivot`. Local +z is the box's
    outward face (toward the cameras); local x runs along the wall."""

    def __init__(self, lo: np.ndarray, hi: np.ndarray, pivot: np.ndarray, yaw: float, label: str):
        self.lo, self.hi, self.pivot, self.yaw, self.label = lo, hi, pivot, yaw, label

    def _to_local(self, pts: np.ndarray) -> np.ndarray:
        c, s = np.cos(self.yaw), np.sin(self.yaw)
        # world = pivot + R(yaw) @ local, R = [[c, s], [-s, c]] in (x, z)
        d = np.atleast_2d(pts)[:, [0, 2]] - self.pivot[[0, 2]]
        lx = c * d[:, 0] - s * d[:, 1]
        lz = s * d[:, 0] + c * d[:, 1]
        return np.stack([lx, np.atleast_2d(pts)[:, 1], lz], axis=-1)

    def _to_world_pts(self, local: np.ndarray) -> np.ndarray:
        c, s = np.cos(self.yaw), np.sin(self.yaw)
        wx = self.pivot[0] + c * local[:, 0] + s * local[:, 2]
        wz = self.pivot[2] - s * local[:, 0] + c * local[:, 2]
        return np.stack([wx, local[:, 1], wz], axis=-1)

    def tris(self) -> np.ndarray:
        v = np.array(
            [
                [self.lo[0], self.lo[1], self.lo[2]],
                [self.hi[0], self.lo[1], self.lo[2]],
                [self.hi[0], self.hi[1], self.lo[2]],
                [self.lo[0], self.hi[1], self.lo[2]],
                [self.lo[0], self.lo[1], self.hi[2]],
                [self.hi[0], self.lo[1], self.hi[2]],
                [self.hi[0], self.hi[1], self.hi[2]],
                [self.lo[0], self.hi[1], self.hi[2]],
            ]
        )
        w = self._to_world_pts(v)
        quads = [(0, 1, 2, 3), (5, 4, 7, 6), (4, 0, 3, 7), (1, 5, 6, 2), (3, 2, 6, 7), (4, 5, 1, 0)]
        tris = []
        for a, b, c, d in quads:
            tris.append(w[[a, b, c]])
            tris.append(w[[a, c, d]])
        return np.array(tris)

    def contains(self, pts: np.ndarray) -> np.ndarray:
        local = self._to_local(np.atleast_2d(pts))
        return np.all((local >= self.lo) & (local <= self.hi), axis=-1)


def default_grounds(scene: Scene, concrete_span: tuple[float, float]) -> list[GroundPatch]:
    """Lawn everywhere 12 m out, concrete under and in front of the battery span."""
    lawn = GroundPatch(scene.wall_x0 - 4, scene.wall_x1 + 4, -6.0, 12.0, "lawn")
    conc = GroundPatch(concrete_span[0], concrete_span[1], -0.2, 4.0, "concrete")
    return [lawn, conc]
