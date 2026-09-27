"""Clock for staleness, the hard stop, debounce and timestamps.

``Clock()`` follows wall time, so a long process keeps moving. ``advance`` pins
the clock and steps it, which is how tests cross a boundary without sleeping.
``Clock(start)`` stays on that instant until ``advance``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class Clock:
    def __init__(self, start: datetime | None = None) -> None:
        self._offset = timedelta(0)
        if start is None:
            self._anchor: datetime | None = None
        else:
            self._anchor = start if start.tzinfo else start.replace(tzinfo=UTC)

    def now(self) -> datetime:
        base = self._anchor if self._anchor is not None else datetime.now(UTC)
        return base + self._offset

    def iso(self) -> str:
        return self.now().isoformat()

    def advance(self, seconds: float) -> None:
        if self._anchor is None:
            self._anchor = datetime.now(UTC)
        self._offset += timedelta(seconds=seconds)


def parse_iso(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment
