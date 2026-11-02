"""Identifiability: which worlds survive an observation set, and what breaks ties.

The engine is small on purpose. Given a rig of keyframes and a finite grid of
wall worlds, every grid world is projected through the same finite camera model;
the worlds whose observation hash equals the true world's are the compatible
worlds. A fact is supported when all compatible worlds agree on it; when they
disagree, the fact is unknown to those observations, and the count of distinct
values reports how many different facts the observations still fit. The count is
grid-relative by construction: it says how many distinct fact values the tested
grid holds that stay compatible, never that all possible worlds are exhausted
(README.md, assumption A5).

A grid count of 1 is not, by itself, identification: a continuous family of
worlds can pass through the true one while the coarse grid contains no second
point of it. Whenever the grid leaves exactly one compatible world, the engine
therefore runs a family probe: a damped Gauss-Newton refit over the CONTINUOUS
fact space, started from a perturbed world. If the refit converges to a
different world whose observation hash still equals the true world's, the facts
that differ are downgraded to UNKNOWN with the continuous pair recorded. A
supported fact is one the grid agrees on AND the probe could not move.

Extra actions: an action adds a fixed micro-rig of two keyframes (a mini stereo
pair) to the observation set. Re-running compatibility on the extended rig says
whether the added views distinguish the worlds. Actions are the study's answer
to "what would settle it": only added observations can distinguish worlds.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .observe import Observation, observation_hash, observe
from .projection import Camera
from .worlds import WallWorld, grid_worlds

_FIELD = {
    "orientation_deg": "theta_deg",
    "distance_ft": "d_ft",
    "left_end_ft": "s0_ft",
    "right_end_ft": "s1_ft",
    "height_ft": "h_ft",
}
_FACT_KEYS = tuple(_FIELD.values())
# A fact counts as moved along the family past this delta (in feet or degrees).
_FAMILY_MIN_DELTA = 0.05
_PIXEL_TOL = 1e-6  # px; family members reproduce the observation exactly


@dataclass(frozen=True)
class FactVerdict:
    """One fact's fate under one observation set."""

    fact: str
    values: tuple[float, ...]  # distinct fact values among compatible worlds
    pair: tuple[str, str] | None  # true world and one differing compatible world
    family_pair: tuple[str, str] | None = None  # set when the probe found a family

    @property
    def supported(self) -> bool:
        return len(self.values) == 1 and self.family_pair is None

    @property
    def count(self) -> int:
        return len(self.values)


def _residual_vector(world: WallWorld, cameras: list[Camera], target: Observation) -> np.ndarray:
    """Concatenated pixel residuals of `world` against the target observation.

    Only landmarks the target actually sees contribute: each must land on the
    same pixel. Landmarks the target does not see contribute no residual —
    whether the refit kept them out of frame is decided afterwards, by the
    exact hash gate in ``probe_family`` (a member whose visibility pattern
    differs is rejected there, not fought by the optimizer).
    """
    obs = observe(world, cameras)
    res: list[float] = []
    for landmark, views in target.items():
        for cid, t in views.items():
            if t is None:
                continue
            p = obs.get(landmark, {}).get(cid)
            if p is None:
                res.extend((400.0, 400.0))  # seen by the target, missing here
            else:
                res.extend((p[0] - t[0], p[1] - t[1]))
    return np.array(res)


def _refit(
    start: WallWorld, cameras: list[Camera], target: Observation, iters: int = 30
) -> tuple[np.ndarray, np.ndarray]:
    """Damped Gauss-Newton over the five continuous facts; returns (params, residuals)."""
    x = np.array([getattr(start, k) for k in _FACT_KEYS], dtype=np.float64)
    r = _residual_vector(start, cameras, target)
    lam = 1e-2
    step = 1e-3
    for _ in range(iters):
        if np.max(np.abs(r)) < _PIXEL_TOL:
            break
        jac = np.empty((len(r), len(x)))
        for j in range(len(x)):
            xp, xm = x.copy(), x.copy()
            xp[j] += step
            xm[j] -= step
            wp = WallWorld(**dict(zip(_FACT_KEYS, xp, strict=True)))
            wm = WallWorld(**dict(zip(_FACT_KEYS, xm, strict=True)))
            rp = _residual_vector(wp, cameras, target)
            rm = _residual_vector(wm, cameras, target)
            jac[:, j] = (rp - rm) / (2 * step)
        a = jac.T @ jac + lam * np.eye(len(x))
        delta = np.linalg.solve(a, -(jac.T @ r))
        x_new = x + delta
        if x_new[4] <= 0.1:  # wall height stays positive
            lam *= 5.0
            continue
        r_new = _residual_vector(
            WallWorld(**dict(zip(_FACT_KEYS, x_new, strict=True))), cameras, target
        )
        if np.linalg.norm(r_new) < np.linalg.norm(r):
            x, r = x_new, r_new
            lam = max(lam * 0.5, 1e-6)
        else:
            lam *= 5.0
    return x, r


_GENERIC_START_OFFSETS = (
    {"theta_deg": 0.5, "d_ft": 0.5, "s0_ft": -0.4, "s1_ft": 0.6, "h_ft": 0.4},
    {"theta_deg": -0.4, "d_ft": 0.7, "s0_ft": 0.5, "s1_ft": -0.5, "h_ft": -0.3},
)


def probe_family(
    true_world: WallWorld, cameras: list[Camera], target: Observation | None = None
) -> dict[str, WallWorld]:
    """Walk the continuous family through the true world, if one exists.

    Phase 1 finds one member: a damped Gauss-Newton refit from a generic
    perturbed start. If it converges to a hash-equal world different from the
    truth, the family exists. Phase 2 travels along it by continuation —
    repeatedly refitting from a chord extrapolation — to reach members far
    from the truth, so facts that move slowly near the start still register.

    Returns {field: member} for every fact the farthest member moved by more
    than _FAMILY_MIN_DELTA. A fact absent from the dict did not move along the
    sampled family, i.e. the observations pin it (together with the grid count
    of 1, it is reported supported). A fact that varies shares the true
    observation EXACTLY with the true world — the hash is checked, not
    approximated.
    """
    target = target if target is not None else observe(true_world, cameras)
    base_hash = observation_hash(target)
    true_kwargs = {k: getattr(true_world, k) for k in _FACT_KEYS}

    first: dict[str, float] | None = None
    for offsets in _GENERIC_START_OFFSETS:
        start_kwargs = {
            k: v + d
            for k, v, d in zip(_FACT_KEYS, true_kwargs.values(), offsets.values(), strict=True)
        }
        x, r = _refit(WallWorld(**start_kwargs), cameras, target)
        if np.max(np.abs(r)) >= _PIXEL_TOL:
            continue
        found = dict(zip(_FACT_KEYS, x, strict=True))
        if max(abs(found[k] - true_kwargs[k]) for k in _FACT_KEYS) <= _FAMILY_MIN_DELTA:
            continue
        if observation_hash(observe(WallWorld(**found), cameras)) != base_hash:
            continue
        first = found
        break
    if first is None:
        return {}

    prev, cur = true_kwargs, first
    for _ in range(5):
        nxt = {k: cur[k] + 1.3 * (cur[k] - prev[k]) for k in _FACT_KEYS}
        if nxt["h_ft"] <= 0.1:
            break
        x, r = _refit(WallWorld(**nxt), cameras, target)
        if np.max(np.abs(r)) >= _PIXEL_TOL:
            break
        found = dict(zip(_FACT_KEYS, x, strict=True))
        if observation_hash(observe(WallWorld(**found), cameras)) != base_hash:
            break
        prev, cur = cur, found

    members: dict[str, WallWorld] = {}
    for k in _FACT_KEYS:
        if abs(cur[k] - true_kwargs[k]) > _FAMILY_MIN_DELTA:
            members[k] = WallWorld(**cur)
    return members


@dataclass(frozen=True)
class Compatibility:
    """The outcome of one observation set against the grid."""

    observation: Observation
    observation_hash: str
    compatible_count: int
    grid_count: int
    verdicts: tuple[FactVerdict, ...]

    def verdict(self, fact: str) -> FactVerdict:
        return next(v for v in self.verdicts if v.fact == fact)

    @property
    def unknown_facts(self) -> tuple[str, ...]:
        return tuple(v.fact for v in self.verdicts if not v.supported)


def evaluate(
    true_world: WallWorld,
    cameras: list[Camera],
    grid: dict,
    grid_worlds_list: list[WallWorld] | None = None,
) -> Compatibility:
    """Project the true world and every grid world through the rig; compare.

    When exactly one grid world is compatible, the family probe runs over the
    continuous fact space: facts the probe moved are downgraded to UNKNOWN
    (grid count 1 was an artifact of a continuous family, not identification).
    """
    obs = observe(true_world, cameras)
    base = observation_hash(obs)
    worlds = grid_worlds_list if grid_worlds_list is not None else list(grid_worlds(grid))
    compatible = [w for w in worlds if observation_hash(observe(w, cameras)) == base]
    family_members = probe_family(true_world, cameras, obs) if len(compatible) == 1 else {}
    verdicts = []
    for fact, field in _FIELD.items():
        values = tuple(sorted({getattr(w, field) for w in compatible}))
        other = next(
            (w for w in compatible if getattr(w, field) != getattr(true_world, field)), None
        )
        pair = (true_world.describe(), other.describe()) if other is not None else None
        family_pair = None
        if field in family_members:
            family_pair = (true_world.describe(), family_members[field].describe())
        verdicts.append(FactVerdict(fact=fact, values=values, pair=pair, family_pair=family_pair))
    return Compatibility(
        observation=obs,
        observation_hash=base,
        compatible_count=len(compatible),
        grid_count=len(worlds),
        verdicts=tuple(verdicts),
    )
