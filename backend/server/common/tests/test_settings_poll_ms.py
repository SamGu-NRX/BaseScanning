"""HSS_INGEST_EVENTS_POLL_MS must stay positive.

poll_events slices the events long-poll's wait with this setting. A zero or
negative value turns every asyncio.wait_for slice into an immediate timeout,
so a parked poll spins on the state store for its whole wait instead of
waiting one slice between reads (Codex P2 and CodeRabbit minor, PR #290).
The setting is rejected where it is declared, never silently clamped.
"""

import pytest
from pydantic import ValidationError

from server.common.config import Settings


@pytest.mark.parametrize("value", [0, -1, -500])
def test_non_positive_poll_ms_is_rejected(value: int) -> None:
    with pytest.raises(ValidationError, match="HSS_INGEST_EVENTS_POLL_MS"):
        Settings(HSS_INGEST_EVENTS_POLL_MS=value)


def test_positive_poll_ms_is_accepted() -> None:
    settings = Settings(HSS_INGEST_EVENTS_POLL_MS=250)
    assert settings.HSS_INGEST_EVENTS_POLL_MS == 250
