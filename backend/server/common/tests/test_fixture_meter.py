"""Dev-only fixture meter pixels. Prod ignores both the capture field and the env."""

from __future__ import annotations

import pytest

from server.common.fixture_meter import (
    checked_fixture,
    env_anchor_label,
    env_meter_frames,
    fixture_models_from_extras,
    parse_meter_frames,
)


def test_parse_meter_frames_keeps_pixel_commas() -> None:
    rows = parse_meter_frames("k00012:1010,655,k00013:990,640")
    assert rows == [("k00012", 1010.0, 655.0), ("k00013", 990.0, 640.0)]


def test_env_override_is_dev_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HSS_COVERAGE_METER_FRAMES", "k9:1,2")
    monkeypatch.setenv("HSS_COVERAGE_ANCHOR_FROM_TAP", "reference_point")
    monkeypatch.delenv("SMP_ENVIRONMENT", raising=False)
    assert env_meter_frames() == ""
    assert env_anchor_label() == ""
    monkeypatch.setenv("SMP_ENVIRONMENT", "prod")
    assert env_meter_frames() == ""
    assert env_anchor_label() == ""
    monkeypatch.setenv("SMP_ENVIRONMENT", "dev")
    assert env_meter_frames() == "k9:1,2"
    assert env_anchor_label() == "reference_point"


def test_checked_fixture_is_stored_only_in_dev() -> None:
    stored = checked_fixture("dev", "k00023:989.573,712.173", "reference_point")
    assert stored == {"meter_frames": "k00023:989.573,712.173", "anchor_label": "reference_point"}
    assert checked_fixture("prod", "k00023:989.573,712.173", "reference_point") is None
    with pytest.raises(ValueError, match="meter frames"):
        checked_fixture("dev", "not-pixels", None)


def test_fixture_models_from_extras_stay_off_in_prod() -> None:
    extra = {"fixture_meter": {"meter_frames": "k0:1,2", "anchor_label": "reference_point"}}
    assert fixture_models_from_extras(extra, environment="dev") == {
        "meter_frames": "k0:1,2",
        "anchor_label": "reference_point",
    }
    assert fixture_models_from_extras(extra, environment="prod") is None
    assert fixture_models_from_extras({}, environment="dev") is None
