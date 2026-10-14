"""Compare sweep outcomes without scanning every run for each sampled start."""

from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass
from typing import Protocol


def numeric_key(value: int | float) -> str:
    """Hash numbers as exact strings so adversarial JSON integers cannot collide cheaply.

    Python hashes integers modulo a public prime. Rational form also keeps equal integer
    and float inputs in one cache entry without rounding distinct large integers together.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    numerator, denominator = value.as_integer_ratio()
    return f"{numerator}/{denominator}"


class Meter(Protocol):
    def charge(self, n: int = 1) -> None: ...


def _charge(meter: Meter | None, n: int = 1) -> None:
    if meter is not None:
        meter.charge(n)


def _bisect(values: list[float], value: float, meter: Meter | None, *, right=False) -> int:
    lo, hi = 0, len(values)
    while lo < hi:
        _charge(meter)
        mid = (lo + hi) // 2
        if values[mid] < value or (right and values[mid] == value):
            lo = mid + 1
        else:
            hi = mid
    return lo


@dataclass
class _WallIndex:
    endpoints: list[float]
    at_endpoint: list[str | None]
    after_endpoint: list[str | None]
    starts: list[float]
    start_outcomes: list[str]
    ends: list[float]
    end_outcomes: list[str]


class SweepIndex:
    """Index closed intervals, choosing the first original run when intervals overlap.

    Endpoint outcomes and the outcomes between endpoints are stored separately. This keeps
    an interval's inclusive endpoint, including EPS expansion, without changing overlap order.
    """

    def __init__(self, result: dict, meter: Meter | None = None, eps: float = 1e-6):
        self.meter = meter
        groups: dict[str, list[dict]] = {}
        for run in result.get("sweep", []):
            _charge(meter)
            groups.setdefault(run["wall_id"], []).append(run)
        self.walls = {wall: self._build(runs, eps) for wall, runs in groups.items()}

    def _build(self, runs: list[dict], eps: float) -> _WallIndex:
        events = []
        starts, ends = {}, {}
        for rank, run in enumerate(runs):
            _charge(self.meter)
            a, b = run["start_ft"]
            events.extend(((a - eps, 0, rank), (b + eps, 1, rank)))
            starts.setdefault(numeric_key(a), (a, run["outcome"]))
            ends.setdefault(numeric_key(b), (b, run["outcome"]))
        events.sort()
        active = [False] * len(runs)
        heap: list[int] = []
        endpoints, at_endpoint, after_endpoint = [], [], []

        def first() -> str | None:
            while heap and not active[heap[0]]:
                _charge(self.meter)
                heapq.heappop(heap)
            return runs[heap[0]]["outcome"] if heap else None

        pos = 0
        while pos < len(events):
            value = events[pos][0]
            ending = []
            while pos < len(events) and events[pos][0] == value:
                _charge(self.meter)
                _, kind, rank = events[pos]
                if kind == 0:
                    active[rank] = True
                    heapq.heappush(heap, rank)
                else:
                    ending.append(rank)
                pos += 1
            endpoints.append(value)
            at_endpoint.append(first())
            for rank in ending:
                _charge(self.meter)
                active[rank] = False
            after_endpoint.append(first())
        start_entries, end_entries = sorted(starts.values()), sorted(ends.values())
        return _WallIndex(
            endpoints,
            at_endpoint,
            after_endpoint,
            [value for value, _ in start_entries],
            [outcome for _, outcome in start_entries],
            [value for value, _ in end_entries],
            [outcome for _, outcome in end_entries],
        )

    def outcome_at(self, wall_id: str, start_ft: float) -> str | None:
        _charge(self.meter)
        wall = self.walls.get(wall_id)
        if wall is None:
            return None
        i = _bisect(wall.endpoints, start_ft, self.meter)
        if i < len(wall.endpoints) and wall.endpoints[i] == start_ft:
            return wall.at_endpoint[i]
        return wall.after_endpoint[i - 1] if i else None

    def outcomes_near(self, wall_id: str, start_ft: float) -> tuple[set[str], str]:
        if (inside := self.outcome_at(wall_id, start_ft)) is not None:
            return {inside}, inside
        wall = self.walls.get(wall_id)
        if wall is None:
            return set(), "no run on its wall"
        left = _bisect(wall.ends, start_ft, self.meter) - 1
        right = _bisect(wall.starts, start_ft, self.meter, right=True)
        if left < 0 or right == len(wall.starts):
            return {"fail"}, "past the original sweep"
        a, b = wall.end_outcomes[left], wall.start_outcomes[right]
        return {a, b}, f"unswept beside {a} and {b}"


def probe_starts(*results: dict, meter: Meter | None = None) -> list[tuple[str, float]]:
    points = []
    for result in results:
        for run in result.get("sweep", []):
            _charge(meter, 3)
            a, b = run["start_ft"]
            # Halving first avoids overflowing the sum of two admitted finite endpoints.
            points.extend(
                ((run["wall_id"], a), (run["wall_id"], b), (run["wall_id"], a / 2 + b / 2))
            )
    return [point for point, _ in itertools.groupby(sorted(points))]


def more_error_problems(
    before: dict, after: dict, change: str, meter: Meter | None = None
) -> list[str]:
    original, changed = SweepIndex(before, meter), SweepIndex(after, meter)
    problems = []
    for wall_id, s in probe_starts(before, after, meter=meter):
        _charge(meter)
        b = changed.outcome_at(wall_id, s)
        allowed, was = original.outcomes_near(wall_id, s)
        if b is None or b == "unsure" or b in allowed or (not allowed and b == "fail"):
            continue
        problems.append(f"{change}: start {wall_id} {s:.3f} went from {was} to {b}")
    if before["decision"] != "pass" and after["decision"] == "pass":
        problems.append(f"{change}: the decision became pass")
    return problems


def less_coverage_problems(
    before: dict, after: dict, change: str, meter: Meter | None = None
) -> list[str]:
    original, changed = SweepIndex(before, meter), SweepIndex(after, meter)
    problems = []
    for wall_id, s in probe_starts(before, after, meter=meter):
        _charge(meter)
        if changed.outcome_at(wall_id, s) != "pass":
            continue
        allowed, was = original.outcomes_near(wall_id, s)
        if "pass" not in allowed:
            problems.append(f"{change}: start {wall_id} {s:.3f} went from {was} to pass")
    if before["decision"] != "pass" and after["decision"] == "pass":
        problems.append(f"{change}: the decision became pass")
    return problems
