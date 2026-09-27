"""Process settings. Env names only; secret values stay optional until a service reads them."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from server.common.backends.state_memory import MemoryState
from server.common.backends.storage_local import LocalFsStorage
from server.common.state import StateStore
from server.common.storage import Storage

_STORE = "bc-5bc79926-909c-4cfb-aa62-24b3a44735e2"


def default_fixture_source() -> str:
    home = Path.home() / "Library/Application Support/Cursor/AgentStores/cursor_agent_stores"
    mac = home / _STORE / "files" / "internal"
    cloud = Path("/cursor/stores") / _STORE / "internal"
    if mac.is_dir():
        return str(mac)
    return str(cloud)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    SMP_PRODUCT_ID: str = "house-scanning"
    SMP_ENVIRONMENT: Literal["dev", "prod"] = "dev"
    SMP_SERVICE_NAME: Literal["capture-api", "early-worker", "pipeline", "sweeper"] = "capture-api"
    STORAGE_BACKEND: Literal["local", "gcs", "r2", "volume"] = "local"
    STATE_BACKEND: Literal["memory", "modal_dict", "postgres"] = "memory"
    STATE_DICT_NAME: str = "hss-dev-state"
    MODAL_APP_NAME: str = "house-scanning"
    GCP_PROJECT_ID: str | None = None
    GCS_BUCKET: str | None = None
    GCP_RUNTIME_SA_EMAIL: str | None = None
    GCP_WIF_AUDIENCE: str | None = None
    GCP_AUTH_MODE: Literal["wif", "adc_impersonate", "sa_key"] = "wif"
    VOLUME_ROOT: str | None = None
    CAPTURE_API_PUBLIC_URL: str | None = None
    R2_ACCOUNT_ID: str | None = None
    R2_BUCKET: str | None = None
    R2_ACCESS_KEY_ID: str | None = None
    R2_SECRET_ACCESS_KEY: str | None = Field(default=None, repr=False)
    OPENAI_API_KEY: str | None = Field(default=None, repr=False)
    XAI_API_KEY: str | None = Field(default=None, repr=False)
    CAPTURE_API_TOKENS: str | None = Field(default=None, repr=False)
    UPLOAD_URL_HMAC_SECRET: str | None = Field(default=None, repr=False)
    WEBHOOK_HMAC_SECRET: str | None = Field(default=None, repr=False)
    BASE_WEBHOOK_URL: str | None = None
    DATABASE_URL: str | None = Field(default=None, repr=False)
    APNS_KEY_P8: str | None = Field(default=None, repr=False)
    APNS_KEY_ID: str | None = None
    APNS_TEAM_ID: str | None = None
    GIT_SHA: str = "unknown"
    RULES_PROFILE_DEFAULT: str = "C"
    SHOW_REJECT_TO_HOMEOWNER: bool = False
    GROUND_RULE_DEFAULT: str = "angle80"
    HARD_STOP_MIN: int = 20
    CAPTURE_CHECK_TARGET_S: int = 30
    VISION_EXTERNAL_CALLS: Literal["on", "off"] | None = None
    FIXTURE_SOURCE: str = Field(default_factory=default_fixture_source)
    FIXTURE_CACHE: str = ".fixtures"

    @model_validator(mode="after")
    def vision_required_in_prod(self) -> Settings:
        if self.VISION_EXTERNAL_CALLS is None:
            if self.SMP_ENVIRONMENT == "prod":
                raise ValueError("VISION_EXTERNAL_CALLS is required when SMP_ENVIRONMENT=prod")
            self.VISION_EXTERNAL_CALLS = "on"
        return self


def load() -> Settings:
    return Settings()


def resolve_storage(settings: Settings) -> Storage:
    if settings.STORAGE_BACKEND == "local":
        root = Path(settings.VOLUME_ROOT) if settings.VOLUME_ROOT else Path(tempfile.mkdtemp(prefix="hss-local-"))
        return LocalFsStorage(root)
    raise NotImplementedError(f"WS-01 implements storage backend {settings.STORAGE_BACKEND}")


def resolve_state(settings: Settings) -> StateStore:
    if settings.STATE_BACKEND == "memory":
        return MemoryState()
    raise NotImplementedError(f"WS-01 implements state backend {settings.STATE_BACKEND}")
