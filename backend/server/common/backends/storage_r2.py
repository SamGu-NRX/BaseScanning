"""Cloudflare R2 via the S3 API. Lifecycle is wrangler, not Terraform."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime, timedelta

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from server.common.storage import ObjectMeta, PreconditionFailed, SignedUrl


def _etag_to_md5_b64(etag: str) -> str | None:
    cleaned = etag.strip('"')
    if "-" in cleaned:
        return None
    try:
        raw = binascii.unhexlify(cleaned)
    except binascii.Error:
        return None
    if len(raw) != 16:
        return None
    return base64.b64encode(raw).decode()


class R2Storage:
    def __init__(self, account_id: str, bucket: str, access_key_id: str, secret_access_key: str, client=None) -> None:
        self._bucket = bucket
        self._client = client or boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            region_name="auto",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(signature_version="s3v4"),
        )

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    def get_bytes(self, key: str) -> bytes:
        body = self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        return body

    def exists(self, key: str) -> bool:
        try:
            self.head(key)
        except FileNotFoundError:
            return False
        return True

    def head(self, key: str) -> ObjectMeta:
        try:
            meta = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                raise FileNotFoundError(key) from exc
            raise
        etag = str(meta.get("ETag", "")).strip('"')
        return ObjectMeta(
            bytes=int(meta["ContentLength"]),
            md5_b64=_etag_to_md5_b64(etag),
            generation=etag,
            content_type=meta.get("ContentType") or "application/octet-stream",
        )

    def signed_put_url(self, key: str, content_type: str, md5_b64: str, ttl_s: int) -> SignedUrl:
        url = self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key, "ContentMD5": md5_b64, "ContentType": content_type},
            ExpiresIn=ttl_s,
        )
        return SignedUrl(
            url=url,
            headers={"Content-Type": content_type, "Content-MD5": md5_b64},
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_s),
        )

    def signed_get_url(self, key: str, ttl_s: int) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=ttl_s,
        )

    def list_prefix(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token = None
        while True:
            kwargs = {"Bucket": self._bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = self._client.list_objects_v2(**kwargs)
            keys.extend(item["Key"] for item in page.get("Contents") or [])
            if not page.get("IsTruncated"):
                return keys
            token = page.get("NextContinuationToken")

    def delete_prefix(self, prefix: str) -> int:
        keys = self.list_prefix(prefix)
        if not keys:
            return 0
        self._client.delete_objects(
            Bucket=self._bucket,
            Delete={"Objects": [{"Key": key} for key in keys]},
        )
        return len(keys)

    def delete(self, key: str) -> bool:
        if not self.exists(key):
            return False
        self._client.delete_object(Bucket=self._bucket, Key=key)
        return True

    def copy(self, src: str, dst: str, *, if_generation_match: str | None = None) -> ObjectMeta:
        extra = {}
        if if_generation_match is not None:
            extra["CopySourceIfMatch"] = if_generation_match
        try:
            self._client.copy_object(
                Bucket=self._bucket,
                Key=dst,
                CopySource={"Bucket": self._bucket, "Key": src},
                **extra,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"PreconditionFailed", "412", "NoSuchKey", "404"}:
                raise PreconditionFailed(src) from exc
            raise
        return self.head(dst)

    def initiate_multipart(self, key: str, content_type: str) -> str:
        raise NotImplementedError("multipart ships in ws-01/multipart")

    def signed_part_url(self, key: str, upload_id: str, part_number: int, ttl_s: int) -> SignedUrl:
        raise NotImplementedError("multipart ships in ws-01/multipart")

    def complete_multipart(self, key: str, upload_id: str, parts: list[tuple[int, str]]) -> ObjectMeta:
        raise NotImplementedError("multipart ships in ws-01/multipart")

    def abort_multipart(self, key: str, upload_id: str) -> None:
        raise NotImplementedError("multipart ships in ws-01/multipart")
