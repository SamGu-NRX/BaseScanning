"""Process settings. Env names only; secret values stay optional until a service reads them."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
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
    # Missing or unknown values are coerced to prod so the privacy guard fails closed.
    SMP_ENVIRONMENT: Literal["dev", "prod"] = "prod"

    @field_validator("SMP_ENVIRONMENT", mode="before")
    @classmethod
    def unknown_environment_is_prod(cls, value: object) -> object:
        if isinstance(value, str) and value in ("dev", "prod"):
            return value
        return "prod"

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
    # Off in every environment unless the process sets VISION_EXTERNAL_CALLS=on (SD-67, SD-77).
    VISION_EXTERNAL_CALLS: Literal["on", "off"] = "off"
    FIXTURE_SOURCE: str = Field(default_factory=default_fixture_source)
    FIXTURE_CACHE: str = ".fixtures"
    # Ingest API knobs. WS-00's Settings stops at the platform fields above.
    HSS_INGEST_URL_TTL_S: int = 3600
    HSS_INGEST_FINALIZE_WINDOW_H: int = 24
    HSS_INGEST_MAX_FILES: int = 2000
    HSS_INGEST_MAX_BATCH: int = 50
    HSS_INGEST_MAX_SINGLE_PUT_BYTES: int = 16777216
    HSS_INGEST_PART_BYTES: int = 8388608
    HSS_INGEST_MAX_FILE_BYTES: int = 268435456
    HSS_INGEST_MAX_CAPTURE_BYTES: int = 1073741824
    HSS_INGEST_MAX_OPEN_CAPTURES: int = 20
    HSS_INGEST_RESULT_URL_TTL_S: int = 900
    HSS_INGEST_EVENTS_MAX_WAIT_S: int = 25
    HSS_INGEST_EVENTS_POLL_MS: int = 500
    HSS_INGEST_SIGN_CONCURRENCY: int = 16
    HSS_INGEST_PROMOTE_CONCURRENCY: int = 32
    HSS_INGEST_HOOKS: Literal["auto", "null"] = "null"
    HSS_AUTH_MODE: Literal["static", "jwt", "none"] = "static"
    HSS_AUTH_JWKS_URL: str | None = None
    HSS_AUTH_ISSUER: str | None = None
    HSS_AUTH_AUDIENCE: str = "capture-api"
    HSS_API_MIN_CONTAINERS: int = Field(default=1)


def load() -> Settings:
    settings = Settings()
    if settings.STATE_DICT_NAME == "hss-dev-state":
        settings.STATE_DICT_NAME = f"hss-{settings.SMP_ENVIRONMENT}-state"
    if settings.GCS_BUCKET is None:
        settings.GCS_BUCKET = f"hss-captures-{settings.SMP_ENVIRONMENT}"
    return settings


def resolve_storage(settings: Settings) -> Storage:
    if settings.STORAGE_BACKEND == "local":
        root = Path(settings.VOLUME_ROOT) if settings.VOLUME_ROOT else Path(tempfile.mkdtemp(prefix="hss-local-"))
        return LocalFsStorage(root)
    if settings.STORAGE_BACKEND == "volume":
        if not settings.VOLUME_ROOT or not settings.CAPTURE_API_PUBLIC_URL or not settings.UPLOAD_URL_HMAC_SECRET:
            raise RuntimeError("volume storage needs VOLUME_ROOT, CAPTURE_API_PUBLIC_URL, and UPLOAD_URL_HMAC_SECRET")
        from server.common.backends.storage_volume import VolumeStorage

        return VolumeStorage(
            Path(settings.VOLUME_ROOT),
            settings.CAPTURE_API_PUBLIC_URL,
            settings.UPLOAD_URL_HMAC_SECRET.encode(),
        )
    if settings.STORAGE_BACKEND == "gcs":
        from server.common.backends.gcp_auth import credentials_for
        from server.common.backends.storage_gcs import GcsStorage

        return GcsStorage(
            bucket_name=settings.GCS_BUCKET or "",
            credentials=credentials_for(settings),
            sa_email=settings.GCP_RUNTIME_SA_EMAIL or "",
        )
    if settings.STORAGE_BACKEND == "r2":
        from server.common.backends.storage_r2 import R2Storage

        return R2Storage(
            account_id=settings.R2_ACCOUNT_ID or "",
            bucket=settings.R2_BUCKET or "",
            access_key_id=settings.R2_ACCESS_KEY_ID or "",
            secret_access_key=settings.R2_SECRET_ACCESS_KEY or "",
        )
    raise NotImplementedError(f"WS-01 implements storage backend {settings.STORAGE_BACKEND}")


def resolve_state(settings: Settings) -> StateStore:
    if settings.STATE_BACKEND == "memory":
        return MemoryState()
    if settings.STATE_BACKEND == "modal_dict":
        from server.common.backends.state_modal_dict import ModalDictState

        return ModalDictState.from_name(settings.STATE_DICT_NAME)
    raise NotImplementedError("STATE_BACKEND=postgres is WS-12; it is not built in WS-01")
