"""Single-house log-scale posterior. Same likelihood as the A9 Monte Carlo, one draw."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

GRID = np.arange(-0.35, 0.35 + 1e-9, 0.0005)
EPS_OUT = 0.03


@dataclass
class Source:
    name: str
    log_obs: float
    sd: float
    offsets: np.ndarray | None = None
    weights: np.ndarray | None = None
    student: bool = False

    def loglik(self) -> np.ndarray:
        theta = GRID
        sd = max(float(self.sd), 1e-6)
        observed = float(self.log_obs)
        if self.offsets is None:
            z = (observed - theta) / sd
            if self.student:
                nu = 4.0
                core = -(nu + 1) / 2 * np.log1p(z * z / nu)
            else:
                core = -0.5 * z * z
            core = core - np.log(sd)
            return np.logaddexp(np.log(1 - EPS_OUT) + core, np.log(EPS_OUT))
        weights = np.asarray(self.weights, float)
        weights = weights / max(float(weights.sum()), 1e-12)
        acc = np.full(theta.shape, -np.inf)
        for offset, weight in zip(np.asarray(self.offsets, float), weights, strict=True):
            z = (observed - theta - float(offset)) / sd
            acc = np.logaddexp(acc, np.log(max(weight, 1e-12)) - 0.5 * z * z - np.log(sd))
        return np.logaddexp(np.log(1 - EPS_OUT) + acc, np.log(EPS_OUT))


@dataclass
class Posterior:
    log_est: float
    lo: float
    hi: float
    bound_95: float

    @property
    def scale(self) -> float:
        return float(np.exp(self.log_est))


def fuse(sources: list[Source], *, s_ref: float | None = None) -> Posterior:
    """Posterior of log(scale / s_ref). ``scale_factor = s_ref × exp(median)``.

    The grid is a log-correction around the reference, not an absolute log-scale.
    A COLMAP model whose metric scale is 3 or 10 stays on that scale; it is not
    pulled back to 1.
    """

    if not sources:
        raise ValueError("fuse needs at least one source")
    if s_ref is None or s_ref <= 0:
        s_ref = float(np.exp(sources[0].log_obs))
    log_ref = float(np.log(s_ref))
    relative = [
        Source(
            source.name,
            float(source.log_obs) - log_ref,
            source.sd,
            source.offsets,
            source.weights,
            source.student,
        )
        for source in sources
    ]
    log_post = np.zeros(GRID.shape)
    for source in relative:
        log_post = log_post + source.loglik()
    weights = np.exp(log_post - log_post.max())
    weights /= weights.sum()
    cumulative = np.cumsum(weights)
    correction = float(GRID[int(np.argmax(cumulative >= 0.5))])
    lo = float(GRID[int(np.argmax(cumulative >= 0.025))])
    hi = float(GRID[int(np.argmax(cumulative >= 0.975))])
    half = (float(np.exp(hi)) - float(np.exp(lo))) / 2
    return Posterior(log_ref + correction, lo, hi, 1.1 * half)


def conflict(anchor: Posterior, checks: Posterior, *, max_check_bound: float = 0.04, k_sigma: float = 3.0) -> bool:
    if checks.bound_95 >= max_check_bound:
        return False
    gap = abs(anchor.log_est - checks.log_est)
    return gap > k_sigma * float(np.hypot(anchor.bound_95 / 2, checks.bound_95 / 2))
