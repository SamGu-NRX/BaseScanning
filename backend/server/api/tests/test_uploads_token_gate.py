"""Upload token routes fail closed without a signing secret.

Greptile finding on PR #247 (backend/server/api/routes/uploads.py:23): with
UPLOAD_URL_HMAC_SECRET unset on a gcs, r2, or local deployment, the token-only
routes verified tokens against an empty HMAC key, so anyone who knew a storage
key could forge a GET token and read protected capture files.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from server.api.app import create_app
from server.api.tests.test_ingest import _create
from server.common.backends.storage_volume import mint_token
from server.common.config import Settings


def _local_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        SMP_ENVIRONMENT="dev",
        STORAGE_BACKEND="local",
        STATE_BACKEND="memory",
        VOLUME_ROOT=str(tmp_path / "store"),
        HSS_INGEST_HOOKS="null",
        HSS_AUTH_MODE="static",
        CAPTURE_API_TOKENS=f"device:{'d' * 40}",
    )
    values.update(overrides)
    return Settings(**values)


def _forged_get_token(key: str) -> str:
    """A token an attacker mints with the empty key they know the verifier uses."""

    return mint_token(
        {"op": "get", "key": key, "md5": "", "ct": "", "exp": int(time.time()) + 600},
        b"",
    )


def test_token_routes_are_absent_without_a_secret(tmp_path) -> None:
    app = create_app(settings=_local_settings(tmp_path))
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/v1/uploads/{token}" not in paths


def test_forged_empty_key_token_cannot_read_files(tmp_path) -> None:
    """The regression test for the finding: fails against the old code, which
    served the forged token the file's bytes with a 200."""

    app = create_app(settings=_local_settings(tmp_path))
    app.state.deps.storage.put_bytes("captures/cap_x/packet.json", b"private-bytes", "application/json")
    response = TestClient(app).get(f"/v1/uploads/{_forged_get_token('captures/cap_x/packet.json')}")
    assert response.status_code == 404
    assert b"private-bytes" not in response.content


def test_local_test_secret_serves_token_routes(tmp_path) -> None:
    """A configured secret (the way every test opts in, per conftest.py) serves
    the routes; a token minted with that same key verifies. There is no
    empty-key path left in the code."""

    test_secret = "h" * 32
    settings = _local_settings(tmp_path, UPLOAD_URL_HMAC_SECRET=test_secret)
    app = create_app(settings=settings)
    app.state.deps.storage.put_bytes("captures/cap_x/packet.json", b"shared-bytes", "application/json")

    def mint(key: str) -> str:
        return mint_token(
            {"op": "get", "key": key, "md5": "", "ct": "", "exp": int(time.time()) + 600},
            test_secret.encode(),
        )

    response = TestClient(app).get(f"/v1/uploads/{mint('captures/cap_x/packet.json')}")
    assert response.status_code == 200
    assert response.content == b"shared-bytes"


def test_volume_without_a_secret_refuses_to_start(tmp_path) -> None:
    settings = Settings(
        STORAGE_BACKEND="volume",
        STATE_BACKEND="memory",
        VOLUME_ROOT=str(tmp_path / "vol"),
        CAPTURE_API_PUBLIC_URL="http://testserver",
    )
    with pytest.raises(RuntimeError, match="UPLOAD_URL_HMAC_SECRET"):
        create_app(settings=settings)


def test_delete_purges_the_shared_packet_cache(client, monkeypatch, tmp_path) -> None:
    """Greptile finding on PR #247 (backend/server/api/routes/captures.py:213):
    workers copy the packet to /vol/packet-cache/{capture_id}/{sha} and DELETE
    only removed object-storage prefixes, so photo copies survived a 204."""

    capture_id = _create(client, "packet-cache-purge")
    cache_root = tmp_path / "packet-cache"
    slot = cache_root / capture_id / ("a" * 64)
    (slot / "photos").mkdir(parents=True)
    (slot / "packet.json").write_bytes(b"{}")
    (slot / ".complete").write_bytes(b"ok")
    (slot / "photos" / "k0.jpg").write_bytes(b"jpeg")
    monkeypatch.setenv("HSS_PACKET_CACHE", str(cache_root))

    assert client.delete(f"/v1/captures/{capture_id}").status_code == 204
    assert not (cache_root / capture_id).exists()


def test_delete_reports_a_failed_packet_cache_purge(client, monkeypatch) -> None:
    capture_id = _create(client, "packet-cache-stuck")
    survivors = [f"/vol/packet-cache/{capture_id}/{'a' * 64}/packet.json"]
    events = []

    monkeypatch.setattr(
        "server.api.routes.captures.purge_packet_cache",
        lambda cid: (0, survivors) if cid == capture_id else (0, []),
    )
    monkeypatch.setattr(
        "server.api.routes.captures.log_event",
        lambda event, **fields: events.append((event, fields)),
    )

    assert client.delete(f"/v1/captures/{capture_id}").status_code == 204
    warnings = [fields for event, fields in events if event == "storage.warning"]
    assert any("could not be removed" in str(fields.get("msg")) for fields in warnings)
    deleted = [fields for event, fields in events if event == "capture.deleted"]
    assert deleted and "removed 0 file(s)" in str(deleted[0].get("msg"))
