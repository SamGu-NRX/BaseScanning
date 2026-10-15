"""Object keys. Uploads expire; captures/ is what every later stage reads."""

from __future__ import annotations


def upload_key(capture_id: str, path: str) -> str:
    return f"uploads/{capture_id}/{path}"


def final_key(capture_id: str, path: str) -> str:
    return f"captures/{capture_id}/{path}"


def packet_key(capture_id: str, amendment_id: str | None = None) -> str:
    if amendment_id:
        return f"captures/{capture_id}/amendments/{amendment_id}/packet.json"
    return f"captures/{capture_id}/packet.json"
