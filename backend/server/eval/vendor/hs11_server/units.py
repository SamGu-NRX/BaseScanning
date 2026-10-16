"""Length conversions between ARKit's meters and the feet and inches of scene.json and the rules.

Every function takes a distance, so negative and non-finite input raises ValueError instead of
producing a length nobody can measure.
"""

import math
from decimal import ROUND_HALF_UP, Decimal

# Exact by definition (international yard and pound agreement, 1959).
METERS_PER_FOOT = 0.3048
INCHES_PER_FOOT = 12


def _require_length(value: float, name: str) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value!r}")


def m_to_ft(meters: float) -> float:
    _require_length(meters, "meters")
    return meters / METERS_PER_FOOT


def ft_to_m(feet: float) -> float:
    _require_length(feet, "feet")
    return feet * METERS_PER_FOOT


def in_to_ft(inches: float) -> float:
    _require_length(inches, "inches")
    return inches / INCHES_PER_FOOT


def ft_to_in(feet: float) -> float:
    _require_length(feet, "feet")
    return feet * INCHES_PER_FOOT


def format_ft_in(feet: float) -> str:
    """Format feet as whole feet and inches, for example "8 ft 4 in".

    Rounds to the nearest inch with halves rounding up, then splits, so 0.99 ft (11.88 in) reads
    "1 ft 0 in" rather than "0 ft 12 in". Python's round() sends halves to the even neighbour
    (round(4.5) == 4) and floor(x + 0.5) can misround next to a half, so the float inch count is
    converted exactly to Decimal and rounded there. Rounding the float product, not the exact
    product of feet and 12, keeps format_ft_in(in_to_ft(0.5)) at "0 ft 1 in": 0.5 / 12 is stored
    slightly below 1/24 ft, and multiplying by 12 in floating point lands back on 0.5.
    """
    _require_length(feet, "feet")
    total_inches = int(Decimal(feet * INCHES_PER_FOOT).to_integral_value(rounding=ROUND_HALF_UP))
    whole_feet, inches = divmod(total_inches, INCHES_PER_FOOT)
    return f"{whole_feet} ft {inches} in"
