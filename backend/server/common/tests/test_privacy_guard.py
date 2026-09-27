"""A missing or unknown SMP_ENVIRONMENT fails closed, the same as prod."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.common.config import Settings


def test_missing_environment_requires_an_explicit_vision_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMP_ENVIRONMENT", raising=False)
    monkeypatch.delenv("VISION_EXTERNAL_CALLS", raising=False)
    with pytest.raises(ValidationError, match="VISION_EXTERNAL_CALLS"):
        Settings()


def test_unknown_environment_behaves_as_prod() -> None:
    with pytest.raises(ValidationError, match="VISION_EXTERNAL_CALLS"):
        Settings(SMP_ENVIRONMENT="staging")
    settings = Settings(SMP_ENVIRONMENT="", VISION_EXTERNAL_CALLS="off")
    assert settings.SMP_ENVIRONMENT == "prod"
    assert settings.VISION_EXTERNAL_CALLS == "off"
    allowed = Settings(SMP_ENVIRONMENT="nope", VISION_EXTERNAL_CALLS="on")
    assert allowed.SMP_ENVIRONMENT == "prod"
    assert allowed.VISION_EXTERNAL_CALLS == "on"


def test_explicit_dev_still_defaults_vision_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VISION_EXTERNAL_CALLS", raising=False)
    settings = Settings(SMP_ENVIRONMENT="dev")
    assert settings.SMP_ENVIRONMENT == "dev"
    assert settings.VISION_EXTERNAL_CALLS == "on"


def test_explicit_prod_requires_the_vision_decision() -> None:
    with pytest.raises(ValidationError, match="VISION_EXTERNAL_CALLS"):
        Settings(SMP_ENVIRONMENT="prod")
    settings = Settings(SMP_ENVIRONMENT="prod", VISION_EXTERNAL_CALLS="off")
    assert settings.VISION_EXTERNAL_CALLS == "off"
