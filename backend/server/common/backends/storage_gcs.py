"""GCS backend. V4 signed URLs go through IAM signBlob; no service-account key."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from google.cloud import storage as gcs

from server.common.backends.gcp_auth import refresh_if_needed
from server.common.storage import ObjectMeta, PreconditionFailed, SignedUrl


class GcsStorage:
    def __init__(self, bucket_name: str, credentials, sa_email: str, client=None) -> None:
        self._credentials = credentials
        self._sa_email = sa_email
        self._client = client or gcs.Client(credentials=credentials, project=None)
        self._bucket = self._client.bucket(bucket_name)

    def _blob(self, key: str):
        return self._bucket.blob(key)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self._blob(key).upload_from_string(data, content_type=content_type)

    def get_bytes(self, key: str) -> bytes:
        return self._blob(key).download_as_bytes()

    def exists(self, key: str) -> bool:
        return self._blob(key).exists()

    def head(self, key: str) -> ObjectMeta:
        blob = self._blob(key)
        blob.reload()
        return ObjectMeta(
            bytes=int(blob.size or 0),
            md5_b64=blob.md5_hash,
            generation=str(blob.generation),
            content_type=blob.content_type or "application/octet-stream",
        )

    def _signed(self, key: str, method: str, ttl_s: int, content_type: str | None, md5_b64: str | None) -> str:
        refresh_if_needed(self._credentials)
        blob = self._blob(key)
        kwargs = {
            "version": "v4",
            "expiration": timedelta(seconds=ttl_s),
            "method": method,
            "service_account_email": self._sa_email,
            "access_token": self._credentials.token,
        }
        if content_type is not None:
            kwargs["content_type"] = content_type
        if md5_b64 is not None:
            kwargs["content_md5"] = md5_b64
        return blob.generate_signed_url(**kwargs)

    def signed_put_url(self, key: str, content_type: str, md5_b64: str, ttl_s: int) -> SignedUrl:
        url = self._signed(key, "PUT", ttl_s, content_type, md5_b64)
        return SignedUrl(
            url=url,
            headers={"Content-Type": content_type, "Content-MD5": md5_b64},
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_s),
        )

    def signed_get_url(self, key: str, ttl_s: int) -> str:
        return self._signed(key, "GET", ttl_s, None, None)

    def list_prefix(self, prefix: str) -> list[str]:
        return [blob.name for blob in self._client.list_blobs(self._bucket, prefix=prefix)]

    def delete_prefix(self, prefix: str) -> int:
        blobs = list(self._client.list_blobs(self._bucket, prefix=prefix))
        if not blobs:
            return 0
        self._bucket.delete_blobs(blobs)
        return len(blobs)

    def delete(self, key: str) -> bool:
        blob = self._blob(key)
        if not blob.exists():
            return False
        blob.delete()
        return True

    def copy(self, src: str, dst: str, *, if_generation_match: str | None = None) -> ObjectMeta:
        blob = self._blob(src)
        kwargs = {}
        if if_generation_match is not None:
            kwargs["source_generation"] = int(if_generation_match)
        try:
            new_blob = self._bucket.copy_blob(blob, self._bucket, dst, **kwargs)
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 404 or "precondition" in str(exc).lower():
                raise PreconditionFailed(src) from exc
            raise
        return ObjectMeta(
            bytes=int(new_blob.size or 0),
            md5_b64=new_blob.md5_hash,
            generation=str(new_blob.generation),
            content_type=new_blob.content_type or "application/octet-stream",
        )

    def initiate_multipart(self, key: str, content_type: str) -> str:
        raise NotImplementedError("multipart ships in ws-01/multipart")

    def signed_part_url(self, key: str, upload_id: str, part_number: int, ttl_s: int) -> SignedUrl:
        raise NotImplementedError("multipart ships in ws-01/multipart")

    def complete_multipart(self, key: str, upload_id: str, parts: list[tuple[int, str]]) -> ObjectMeta:
        raise NotImplementedError("multipart ships in ws-01/multipart")

    def abort_multipart(self, key: str, upload_id: str) -> None:
        raise NotImplementedError("multipart ships in ws-01/multipart")
