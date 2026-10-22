"""The upload final commit and DELETE share a fence across API instances."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from server.api.app import create_app
from server.api.tests.conftest import TOKEN, create_body, md5_b64, sha256_hex
from server.common.backends.state_modal_dict import ModalDictState
from server.common.config import Settings


def _settings(root: Path) -> Settings:
    return Settings(
        STORAGE_BACKEND="volume",
        STATE_BACKEND="memory",
        VOLUME_ROOT=str(root),
        CAPTURE_API_PUBLIC_URL="http://testserver",
        CAPTURE_API_TOKENS=f"device:{TOKEN}",
        UPLOAD_URL_HMAC_SECRET="h" * 32,
        HSS_INGEST_HOOKS="null",
        HSS_AUTH_MODE="static",
        GIT_SHA="0123456789abcdef",
        VISION_EXTERNAL_CALLS="off",
        SMP_SERVICE_NAME="capture-api",
    )


def test_delete_cannot_finish_while_another_instance_commits_upload(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "shared-storage")
    app_a = create_app(settings=settings)
    app_b = create_app(settings=settings)
    shared_state: dict[str, object] = {}
    app_a.state.deps.state = ModalDictState(shared_state)
    app_b.state.deps.state = ModalDictState(shared_state)
    client_a, client_b = TestClient(app_a), TestClient(app_b)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    data, path = b"stale upload bytes", "photos/stale.jpg"
    created = client_a.post(
        "/v1/captures", json=create_body("shared-delete"), headers={**auth, "Idempotency-Key": "shared-delete"}
    )
    assert created.status_code == 201, created.text
    capture_id = created.json()["captureId"]
    registered = client_a.post(
        f"/v1/captures/{capture_id}/files",
        json={
            "files": [
                {
                    "path": path,
                    "bytes": len(data),
                    "sha256": sha256_hex(data),
                    "md5": md5_b64(data),
                    "role": "still",
                    "contentType": "image/jpeg",
                }
            ]
        },
        headers=auth,
    )
    assert registered.status_code == 200, registered.text
    target = registered.json()["files"][0]["upload"]
    entered, release = threading.Event(), threading.Event()
    original_commit = app_a.state.deps.storage.commit_tmp

    def paused_commit(*args, **kwargs):
        entered.set()
        assert release.wait(5), "the upload did not receive its resume signal"
        return original_commit(*args, **kwargs)

    app_a.state.deps.storage.commit_tmp = paused_commit
    upload_result, delete_result = [], []
    upload_thread = threading.Thread(
        target=lambda: upload_result.append(client_a.put(target["url"], content=data, headers=target["headers"]))
    )
    upload_thread.start()
    assert entered.wait(5), "the upload did not reach its final commit"
    delete_thread = threading.Thread(
        target=lambda: delete_result.append(client_b.delete(f"/v1/captures/{capture_id}", headers=auth))
    )
    delete_thread.start()
    time.sleep(0.1)
    delete_blocked = not delete_result
    release.set()
    upload_thread.join(timeout=5)
    delete_thread.join(timeout=5)
    assert not upload_thread.is_alive() and not delete_thread.is_alive()
    assert delete_blocked, "DELETE entered the shared fence before the upload released it"
    assert upload_result and upload_result[0].status_code == 200, upload_result[0].text
    assert delete_result and delete_result[0].status_code == 204, delete_result[0].text
    assert not app_b.state.deps.storage.exists(f"uploads/{capture_id}/{path}")
