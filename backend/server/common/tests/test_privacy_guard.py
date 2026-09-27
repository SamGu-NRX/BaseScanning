"""Environment and vision-call defaults fail closed."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.common.config import Settings


def test_missing_or_unknown_environment_behaves_as_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMP_ENVIRONMENT", raising=False)
    monkeypatch.delenv("VISION_EXTERNAL_CALLS", raising=False)
    missing = Settings()
    assert missing.SMP_ENVIRONMENT == "prod"
    blank = Settings(SMP_ENVIRONMENT="")
    unknown = Settings(SMP_ENVIRONMENT="staging")
    assert blank.SMP_ENVIRONMENT == "prod"
    assert unknown.SMP_ENVIRONMENT == "prod"


def test_vision_defaults_off_unless_explicitly_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VISION_EXTERNAL_CALLS", raising=False)
    monkeypatch.delenv("SMP_ENVIRONMENT", raising=False)
    assert Settings().VISION_EXTERNAL_CALLS == "off"
    for environment in ("dev", "prod", "staging", ""):
        settings = Settings(SMP_ENVIRONMENT=environment)
        assert settings.VISION_EXTERNAL_CALLS == "off"
        enabled = Settings(SMP_ENVIRONMENT=environment, VISION_EXTERNAL_CALLS="on")
        assert enabled.VISION_EXTERNAL_CALLS == "on"
    monkeypatch.setenv("SMP_ENVIRONMENT", "dev")
    assert Settings().VISION_EXTERNAL_CALLS == "off"
    monkeypatch.setenv("VISION_EXTERNAL_CALLS", "on")
    assert Settings().VISION_EXTERNAL_CALLS == "on"
    monkeypatch.setenv("VISION_EXTERNAL_CALLS", "yes")
    with pytest.raises(ValidationError):
        Settings()
