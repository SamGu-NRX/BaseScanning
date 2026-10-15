"""The e2e uploader speaks the real ingest contract, without network or model calls."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from fastapi.testclient import TestClient

from scripts import e2e_dev
from server.api.app import create_app
from server.common.config import Settings


@pytest.fixture
def local_upload(tmp_path, monkeypatch):
    root = tmp_path / "packet"
    shutil.copytree(Path(__file__).resolve().parents[1] / "data/fixtures/packets/mini-arkit", root)
    packet = json.loads((root / "packet.json").read_text())
    settings = Settings(
        SMP_ENVIRONMENT="dev",
        HSS_AUTH_MODE="none",
        VISION_EXTERNAL_CALLS="off",
        STORAGE_BACKEND="local",
        STATE_BACKEND="memory",
        VOLUME_ROOT=str(tmp_path / "store"),
        HSS_INGEST_HOOKS="null",
    )
    app = create_app(settings=settings)
    storage = app.state.deps.storage
    requests = []
    with TestClient(app) as client:

        def request(url, token, method, body=None, headers=None, *, authorize=True, timeout=e2e_dev.READ_TIMEOUT_S):
            parsed = urlsplit(url)
            if parsed.scheme == "file":
                assert method == "PUT" and not authorize
                key = Path(unquote(parsed.path)).relative_to(storage.root).as_posix()
                storage.put_bytes(key, body, headers["Content-Type"])
                return 200, b""
            requests.append((parsed.path, None if body is None else json.loads(body)))
            response = client.request(method, parsed.path, content=body, headers=headers)
            return response.status_code, response.content

        monkeypatch.setattr(e2e_dev, "_request", request)
        yield root, packet, client, requests


@pytest.mark.parametrize(("extra_files", "max_batch"), [(0, 50), (51, 50), (10, 7)])
def test_guided_upload_accepts_streams_and_batches(local_upload, extra_files, max_batch):
    root, packet, client, requests = local_upload
    client.app.state.deps.settings.HSS_INGEST_MAX_BATCH = max_batch
    for index in range(extra_files):
        path = f"extra-{index}.bin"
        (root / path).write_bytes(b"synthetic")
        packet["files"].append({"path": path, "role": "other"})

    capture_id = e2e_dev.upload("http://testserver", "", root, packet)

    status = client.get(f"/v1/captures/{capture_id}").json()
    assert status["filesCommitted"] == len(packet["files"])
    assert status["status"] == "processing"
    registrations = [body["files"] for path, body in requests if path.endswith("/files")]
    commits = [body["files"] for path, body in requests if path.endswith("/files:commit")]
    assert sum(map(len, registrations)) == len(packet["files"])
    assert sum(map(len, commits)) == len(packet["files"])
    assert all(0 < len(batch) <= max_batch for batch in registrations + commits)
    streams = [item for batch in registrations for item in batch if item["role"] == "stream"]
    assert streams and all(item["contentType"] == "application/gzip" for item in streams)


def test_upload_resumes_after_files_were_committed(local_upload, monkeypatch):
    root, packet, client, _ = local_upload
    request = e2e_dev._request

    def interrupted(url, *args, **kwargs):
        if url.endswith("/finalize"):
            raise TimeoutError("synthetic interruption before finalize")
        return request(url, *args, **kwargs)

    monkeypatch.setattr(e2e_dev, "_request", interrupted)
    with pytest.raises(TimeoutError):
        e2e_dev.upload("http://testserver", "", root, packet)

    monkeypatch.setattr(e2e_dev, "_request", request)
    capture_id = e2e_dev.upload("http://testserver", "", root, packet)
    assert client.get(f"/v1/captures/{capture_id}").json()["filesCommitted"] == len(packet["files"])
