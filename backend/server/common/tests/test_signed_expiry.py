"""Signed upload expiry is a datetime, which the route formats as IsoTime."""

from datetime import datetime
from pathlib import Path

from server.common.backends.storage_volume import VolumeStorage


def test_volume_put_expiry_is_datetime(tmp_path: Path) -> None:
    store = VolumeStorage(tmp_path, "http://testserver", b"h" * 32)
    signed = store.signed_put_url("uploads/c1/a.jpg", "image/jpeg", "md5", 60)
    assert isinstance(signed.expires_at, datetime)
    assert signed.expires_at.tzinfo is not None
