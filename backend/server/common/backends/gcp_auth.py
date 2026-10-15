"""Keyless GCP credentials. sa_key is the documented fallback and is never created here."""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime

import google.auth
import google.auth.impersonated_credentials
from google.auth import identity_pool
from google.auth.transport.requests import Request

from server.common.config import Settings


class ModalTokenSupplier:
    """Supplies MODAL_IDENTITY_TOKEN. The value is never logged."""

    def get_subject_token(self, context, request):
        token = os.environ.get("MODAL_IDENTITY_TOKEN", "")
        if not token:
            raise RuntimeError("MODAL_IDENTITY_TOKEN is not set")
        return token


def token_expiry(token: str) -> datetime | None:
    try:
        payload = token.split(".")[1]
        pad = "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload + pad))
        exp = claims.get("exp")
        if exp is None:
            return None
        return datetime.fromtimestamp(int(exp), UTC)
    except (IndexError, ValueError, json.JSONDecodeError):
        return None


def credentials_for(settings: Settings):
    mode = settings.GCP_AUTH_MODE
    if mode == "wif":
        creds = identity_pool.Credentials(
            audience=settings.GCP_WIF_AUDIENCE,
            subject_token_type="urn:ietf:params:oauth:token-type:jwt",
            token_url="https://sts.googleapis.com/v1/token",
            subject_token_supplier=ModalTokenSupplier(),
            service_account_impersonation_url=(
                "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
                f"{settings.GCP_RUNTIME_SA_EMAIL}:generateAccessToken"
            ),
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        return creds
    if mode == "adc_impersonate":
        source, _project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        return google.auth.impersonated_credentials.Credentials(
            source_credentials=source,
            target_principal=settings.GCP_RUNTIME_SA_EMAIL,
            target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
    # sa_key: GOOGLE_APPLICATION_CREDENTIALS only. This process never writes a key.
    creds, _project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return creds


def refresh_if_needed(creds) -> None:
    if not getattr(creds, "valid", False):
        creds.refresh(Request())
