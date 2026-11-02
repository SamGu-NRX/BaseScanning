"""Error bars a clearance check can assign, four ways.

A clearance here is the gap along one wall between two features: an opening, a
wall-mounted object, or the footprint of a placed battery. The check measures
the gap from the tracked scene and decides pass, fail or unsure against a rule
minimum (3 ft for opening clearance in the demo rules). The decision uses the
measured gap minus an error bar, so past a few feet from the meter the bar,
not the measured gap, decides the outcome.

All lengths are feet. Walked distance is the absolute chain distance from the
meter, which is what the server's drift term uses (server/rules.yaml
errors.drift_per_ft, value 0.16). Base errors are the rules' per-source
allowances: tap 0.3 ft, vlm 1.5 ft (server/rules.yaml errors.tap_ft, vlm_ft). Bases and the drift rate are day-1 estimates, with
the rate calibrated to S1 real-data evals; correlation assumptions here are
swept assumptions, not measured calibration -- the audit (audit.py) pins the
provenance and states the limitations per source.
"""

from __future__ import annotations

from dataclasses import dataclass

# Mirror of server/solver.py at_least, which decides every check. Kept as a
# literal copy so the study's decision accounting cannot drift from the
# shipped comparison semantics (EPS included).
EPS = 1e-9

DRIFT_PER_FT = 0.16  # errors.drift_per_ft in server/rules.yaml
TAP_BASE_FT = 0.3  # errors.tap_ft
VLM_BASE_FT = 1.5  # errors.vlm_ft

# Local-clearance anchors from experiments/sensor-budget ("both ends in one
# photo", p90 at a 6 ft span): 1.34 in worst case with LiDAR, 4.37 in without.
# These are modeled device budgets, not measurements; the study treats the
# no-LiDAR value as the anchor for the phones the product targets.
LOCAL_P90_IN_AT_6FT = {"lidar": 1.34, "no_lidar": 4.37}


@dataclass(frozen=True)
class Endpoint:
    """One end of the measured gap, as the rules see it."""

    walked_ft: float
    base_ft: float = TAP_BASE_FT

    def absolute_bar(self) -> float:
        return self.base_ft + DRIFT_PER_FT * self.walked_ft


def current_bar(a: Endpoint, b: Endpoint, separation_ft: float) -> float:
    """The shipped calculation: each endpoint's absolute bar, added.

    This is what server/solver.py assigns to a clearance check: an object's
    error plus the battery piece's error, each base plus drift at the
    endpoint's farthest walked distance. comparator.py reproduces it against
    both main and origin/t3/server on real scene shapes.
    """
    return a.absolute_bar() + b.absolute_bar()


def rate_only_bar(a: Endpoint, b: Endpoint, separation_ft: float) -> float:
    """The same walked-distance rate with the base allowances dropped.

    Separates the two parts of the shipped bar: how much comes from the
    per-source bases, how much from walked distance."""
    return DRIFT_PER_FT * (a.walked_ft + b.walked_ft)


def rate_on_gap_bar(a: Endpoint, b: Endpoint, separation_ft: float) -> float:
    """The shipped rate applied to the measured length instead of the walks.

    Same 0.16 ft/ft coefficient the rules carry, same per-tap bases, but the
    drift accumulates over the gap, not over how far each end sits from the
    meter. Needs no new data claim - it reinterprets the rules rate - which
    makes it the strongest bar available without new measurement."""
    return a.base_ft + b.base_ft + DRIFT_PER_FT * separation_ft


def scale_error_bar_factory(scale_per_ft: float):
    """A bar from the phone's measured scale error over the gap.

    Per-tap bases kept (a tap can land off the feature end), plus the class's
    between-walk scale spread applied to the span. This is the common-mode
    claim at its strongest: the error that survives in a difference is the
    scale error along the gap, nothing more. The committed data do not
    establish it; drift-anatomy A2 dropped the one-reference version and the
    audit's difference scan shows no direct measurement exists."""

    def bar(a: Endpoint, b: Endpoint, separation_ft: float) -> float:
        return 2 * TAP_BASE_FT + scale_per_ft * separation_ft

    return bar


def local_clearance_bar(a: Endpoint, b: Endpoint, separation_ft: float) -> float:
    """A bar from measuring the gap itself, both ends in one photo.

    Anchored to the sensor-budget estimate, scaled linearly from its 6 ft
    span. The study treats this as the target a protocol would have to
    confirm on the target phones, not as an available option today."""
    rate = LOCAL_P90_IN_AT_6FT["no_lidar"] / 72.0
    return rate * separation_ft


def common_mode_oracle_bar(scale_error: float, separation_ft: float) -> float:
    """The bar left if a walk's scale error were known exactly: |eps| * c.

    Under a pure common transform (one scale, one rotation, one translation
    per session) a rigid transform changes no clearance and a scale error
    changes a clearance by exactly scale * length. This is the floor the
    relative-error model approaches when the residual field is common. No
    capture reports a walk's scale error; nothing here proposes using it."""
    return abs(scale_error) * separation_ft


def at_least(value: float, error: float, threshold: float) -> str:
    """Mirror of server/solver.py at_least (line 168): a three-way decider.

    PASS when value - error > threshold; FAIL when value + error < threshold;
    UNSURE in between, on either line, or when the bar covers both sides."""
    if value - error - threshold > EPS:
        return "pass"
    if threshold - (value + error) > EPS:
        return "fail"
    return "unsure"


def decide(measured_ft: float, bar_ft: float, threshold_ft: float = 3.0) -> str:
    """Clearance outcome from a measured gap and a bar; at_least under its clearance name."""
    return at_least(measured_ft, bar_ft, threshold_ft)


MODELS = {
    "current": current_bar,
    "rate_only": rate_only_bar,
    "rate_on_gap": rate_on_gap_bar,
    "local_clearance": local_clearance_bar,
}
