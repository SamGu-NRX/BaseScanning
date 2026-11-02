"""Per-walk error fields for the simulation.

One walk places a feature at true chain distance s from the meter; the scene
records a tracked position

    s' = s * (1 + eps + g(s))

eps is the walk's scale error, one draw per walk from a measured distribution.
g(s) is a residual per foot, a Gaussian field over s with 1-sigma k (per foot)
and correlation exp(-|ds| / ell) between features. ell is the correlation
length of the residual. It is the parameter the committed data does not
measure: the drift datasets fix how far one position can be off, and the
anatomy study fixes how scale varies within a walk, but no committed dataset
records the joint error of two taps on one walk against an independent
reference. The simulation holds every measured quantity fixed and sweeps ell.

Under this model a rigid transform (rotation and translation, no scale)
changes no clearance, and the difference error of two features separated by c
is |c * (1 + eps) + s2*g(s2) - s1*g(s1) - c|, which tends to |eps + g| * c as
ell grows and to a walked-distance-driven value as ell shrinks. Any graded
kernel sits between those two extremes for a fixed marginal spread, so the
swept ell brackets the unmeasured middle.

Marginal calibration: absolute error at walked distance w is w * |eps + g(w)|,
with 1-sigma w * sqrt(scale_sd^2 + k^2). k is taken as the largest per-distance
value implied by the committed pooled p90 over the fit distances, which
overstates absolute error at the longer distances. That bias works against the
relaxed models, which is the direction a clearance study should err.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

P90_Z = 1.645


@dataclass(frozen=True)
class PhoneClass:
    """A phone class with its measured per-walk scale distribution and the
    committed pooled absolute-position p90 it must reproduce."""

    name: str
    note: str
    scale_mu: float
    scale_sd: float
    pos_p90_in: dict[float, float]
    fit_distances_ft: tuple[float, ...]
    source: str


MODERN = PhoneClass(
    name="modern",
    note="MARViN iPhone 14 Pro Max, trusted bar+church walks; walk scale SD 1.47%",
    scale_mu=0.0,
    scale_sd=0.0147,
    pos_p90_in={10.0: 8.6, 20.0: 13.4, 30.0: 18.5},
    fit_distances_ft=(10.0, 20.0, 30.0),
    source="experiments/evals/results/modern_arkit.json",
)

PHONE_2018 = PhoneClass(
    name="phone_2018",
    note="ADVIO iPhone 6s; per-walk scale in [-17%, -5%]; GPS-rescaled truth",
    scale_mu=-0.11,
    scale_sd=0.0346,
    pos_p90_in={3.0: 18.63, 10.0: 55.7, 20.0: 94.61, 30.0: 132.58},
    fit_distances_ft=(10.0, 20.0, 30.0),
    source="experiments/evals/results/advio_drift.json",
)


def implied_k(cls: PhoneClass, walked_ft: float) -> float:
    """Residual 1-sigma per foot implied by one published p90, no error floor."""
    p90_ft = cls.pos_p90_in[walked_ft] / 12.0
    var = (p90_ft / (P90_Z * walked_ft)) ** 2 - cls.scale_sd**2
    return math.sqrt(max(var, 0.0))


def calibrated_k(cls: PhoneClass) -> float:
    """The residual spread the simulation uses: the largest implied value over
    the fit distances."""
    return max(implied_k(cls, w) for w in cls.fit_distances_ft)


def tracked_pair(
    rng: np.random.Generator,
    cls: PhoneClass,
    k: float,
    s1: float,
    s2: float,
    ell_ft: float,
    n: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Tracked positions of two features under one walk, n draws, plus the
    walk's scale error eps for the oracle bar.

    ell_ft = inf makes the residual one draw per walk (common per foot); as
    ell_ft shrinks, the two residuals decorrelate."""
    eps = rng.normal(cls.scale_mu, cls.scale_sd, size=n)
    rho = 1.0 if math.isinf(ell_ft) else math.exp(-abs(s2 - s1) / ell_ft)
    z1 = rng.standard_normal(n)
    z2 = rho * z1 + math.sqrt(max(0.0, 1.0 - rho * rho)) * rng.standard_normal(n)
    return s1 * (1.0 + eps + k * z1), s2 * (1.0 + eps + k * z2), eps


def tracked_pair_cross_session(
    rng: np.random.Generator,
    cls: PhoneClass,
    k: float,
    s1: float,
    s2: float,
    n: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Two features from two independent walks: independent scale and fields.

    This is what a merged scene looks like when one feature was captured in an
    earlier session: nothing is shared, not even the walk's scale."""

    def one(s: float) -> np.ndarray:
        eps = rng.normal(cls.scale_mu, cls.scale_sd, size=n)
        z = rng.standard_normal(n)
        return s * (1.0 + eps + k * z)

    return one(s1), one(s2)
