"""Knobs for the result stage. WS-00 Settings names first, then HSS_RESULT_*."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CATALOGUE = Path("ios-kit/prompts/views-needed-prompts.en-US.json")


def _bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ResultConfig:
    profile: str = "C"
    show_reject: bool = False
    max_views: int = 6
    signed_url_ttl_s: int = 900
    preview_max_points: int = 200_000
    meter_autofill: str = "barcode"
    threejs_url: str = "https://unpkg.com/three@0.160.0/build/three.module.js"
    catalogue_path: Path = DEFAULT_CATALOGUE
    webhook_url: str | None = None


def _from_mapping(source: object, name: str):
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def load_config(settings: object | None = None, environ: dict[str, str] | None = None) -> ResultConfig:
    """Settings attributes win, then the process environment, then the dataclass defaults."""

    env = environ if environ is not None else os.environ

    def pick(setting: str, env_name: str, default, cast):
        value = _from_mapping(settings, setting)
        if value is None and env_name in env and env[env_name] != "":
            value = env[env_name]
        if value is None:
            return default
        return cast(value)

    return ResultConfig(
        profile=str(pick("RULES_PROFILE_DEFAULT", "RULES_PROFILE_DEFAULT", "C", str)),
        show_reject=bool(pick("SHOW_REJECT_TO_HOMEOWNER", "SHOW_REJECT_TO_HOMEOWNER", False, _bool)),
        max_views=int(pick("HSS_RESULT_MAX_VIEWS", "HSS_RESULT_MAX_VIEWS", 6, int)),
        signed_url_ttl_s=int(pick("HSS_RESULT_SIGNED_URL_TTL_S", "HSS_RESULT_SIGNED_URL_TTL_S", 900, int)),
        preview_max_points=int(pick("HSS_RESULT_PREVIEW_MAX_POINTS", "HSS_RESULT_PREVIEW_MAX_POINTS", 200_000, int)),
        meter_autofill=str(pick("HSS_RESULT_METER_AUTOFILL", "HSS_RESULT_METER_AUTOFILL", "barcode", str)),
        threejs_url=str(
            pick(
                "HSS_RESULT_REVIEW_THREEJS_URL",
                "HSS_RESULT_REVIEW_THREEJS_URL",
                "https://unpkg.com/three@0.160.0/build/three.module.js",
                str,
            )
        ),
        catalogue_path=Path(str(pick("HSS_RESULT_PROMPTS_PATH", "HSS_RESULT_PROMPTS_PATH", DEFAULT_CATALOGUE, str))),
        webhook_url=pick("BASE_WEBHOOK_URL", "BASE_WEBHOOK_URL", None, str),
    )
