"""Modal-volume style storage. Signed PUTs are HMAC tokens on this API."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from server.common.backends.storage_local import LocalFsStorage
from server.common.storage import PreconditionFailed, SignedUrl


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def mint_token(payload: dict, secret: bytes) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    body = _b64url(raw)
    sig = hmac.new(secret, raw, hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read_token(token: str, secret: bytes) -> dict | None:
    if "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    try:
        raw = _b64url_decode(body)
    except (ValueError, TypeError):
        return None
    expected = hmac.new(secret, raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        payload["_expired"] = True
    return payload


class VolumeStorage(LocalFsStorage):
    def __init__(self, root: str | Path, public_url: str, hmac_secret: bytes) -> None:
        if not hmac_secret:
            # Refuse to sign tokens with an empty key: anyone who knows a storage
            # key could forge one. Tests opt in via UPLOAD_TOKENS_ALLOW_MISSING_SECRET.
            raise ValueError("VolumeStorage needs a non-empty UPLOAD_URL_HMAC_SECRET")
        super().__init__(Path(root))
        self.public_url = public_url.rstrip("/")
        self.hmac_secret = hmac_secret

    def _expires(self, ttl_s: int) -> tuple[int, datetime]:
        exp = int(time.time()) + ttl_s
        return exp, datetime.fromtimestamp(exp, UTC)

    def copy(self, src: str, dst: str, *, if_generation_match: str | None = None):
        try:
            return super().copy(src, dst, if_generation_match=if_generation_match)
        except ValueError as exc:
            if "generation mismatch" not in str(exc):
                raise
            raise PreconditionFailed(src) from exc

    def signed_put_url(self, key: str, content_type: str, md5_b64: str, ttl_s: int) -> SignedUrl:
        exp, stamp = self._expires(ttl_s)
        token = mint_token({"op": "put", "key": key, "md5": md5_b64, "ct": content_type, "exp": exp}, self.hmac_secret)
        return SignedUrl(
            url=f"{self.public_url}/v1/uploads/{token}",
            headers={"Content-Type": content_type, "Content-MD5": md5_b64},
            expires_at=stamp,
        )

    def signed_get_url(self, key: str, ttl_s: int) -> str:
        exp, _stamp = self._expires(ttl_s)
        token = mint_token({"op": "get", "key": key, "md5": "", "ct": "", "exp": exp}, self.hmac_secret)
        return f"{self.public_url}/v1/uploads/{token}"

    def signed_part_url(self, key: str, upload_id: str, part_number: int, ttl_s: int) -> SignedUrl:
        exp, stamp = self._expires(ttl_s)
        token = mint_token(
            {"op": "part", "key": key, "md5": "", "ct": "", "exp": exp, "uploadId": upload_id, "n": part_number},
            self.hmac_secret,
        )
        return SignedUrl(url=f"{self.public_url}/v1/uploads/{token}", headers={}, expires_at=stamp)
