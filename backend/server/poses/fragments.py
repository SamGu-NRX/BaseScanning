"""Which fragment is primary. The meter fragment beats a larger meterless one."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FragmentInfo:
    id: str
    frames: list[str]
    registered: int
    points3d: int
    has_meter: bool
    primary: bool = False


def choose_primary(fragments: list[FragmentInfo]) -> tuple[FragmentInfo | None, str]:
    """Return ``(primary, status_if_no_meter_or_ok)``.

    ``status`` is ``ok`` when the primary contains the meter, else
    ``no_meter_model``. ``None`` when there are no fragments.
    """
    if not fragments:
        return None, "failed"
    with_meter = [f for f in fragments if f.has_meter]
    pool = with_meter or fragments
    primary = max(pool, key=lambda f: (f.registered, f.points3d))
    for frag in fragments:
        frag.primary = frag is primary
    if primary.has_meter:
        return primary, "ok"
    return primary, "no_meter_model"
