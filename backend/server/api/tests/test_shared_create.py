"""Two capture-api processes sharing one dict must create one capture."""

from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from server.api.app import create_app
from server.api.tests.conftest import TOKEN, create_body
from server.common.backends.state_modal_dict import ModalDictState
from server.common.config import Settings


class _SharedMap:
    """Racy assignment. ``put(skip_if_exists=True)`` returns False when the key exists.

    Modal Dict returns False for an existing key even when the stored value is identical.
    """

    def __init__(self, *, parties: int = 0) -> None:
        self.data: dict[str, object] = {}
        self.lock = threading.Lock()
        self.barrier = threading.Barrier(parties) if parties else None

    def get(self, key: str, default=None):
        if self.barrier is not None and key.startswith("idem:create:") and key not in self.data:
            try:
                self.barrier.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
        return self.data.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self.data

    def __setitem__(self, key: str, value: object) -> None:
        self.data[key] = value

    def __delitem__(self, key: str) -> None:
        self.data.pop(key, None)

    def put(self, key: str, value: object, *, skip_if_exists: bool = False) -> bool:
        if self.barrier is not None and skip_if_exists and key.startswith("idem:create:"):
            try:
                self.barrier.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
        with self.lock:
            if skip_if_exists and key in self.data:
                return False
            self.data[key] = value
            return True

    def keys(self):
        return list(self.data)

    def pop(self, key: str, default=None):
        return self.data.pop(key, default)


def _settings(tmp_path) -> Settings:
    return Settings(
        STORAGE_BACKEND="volume",
        STATE_BACKEND="memory",
        VOLUME_ROOT=str(tmp_path / "vol"),
        CAPTURE_API_PUBLIC_URL="http://testserver",
        CAPTURE_API_TOKENS=f"device:{TOKEN}",
        UPLOAD_URL_HMAC_SECRET="h" * 32,
        HSS_INGEST_HOOKS="null",
        HSS_AUTH_MODE="static",
        GIT_SHA="0123456789abcdef",
        VISION_EXTERNAL_CALLS="off",
        SMP_SERVICE_NAME="capture-api",
    )


def _client(tmp_path, mapping: _SharedMap) -> TestClient:
    app = create_app(settings=_settings(tmp_path))
    app.state.deps.state = ModalDictState(mapping)
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    return client


def test_identical_claim_loses_and_a_different_value_is_kept() -> None:
    state = ModalDictState(_SharedMap())
    assert state.put_if_absent("once", {"ok": True}) is True
    assert state.put_if_absent("once", {"ok": True}) is False
    assert state.get_kv("once") == {"ok": True}
    assert state.put_if_absent("once", {"ok": False}) is False
    assert state.get_kv("once") == {"ok": True}


def test_identical_create_retry_returns_the_existing_capture(tmp_path) -> None:
    client = _client(tmp_path, _SharedMap())
    body = create_body("packet-retry")
    first = client.post("/v1/captures", json=body, headers={"Idempotency-Key": body["packetId"]})
    again = client.post("/v1/captures", json=body, headers={"Idempotency-Key": body["packetId"]})
    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["captureId"] == first.json()["captureId"]


def test_different_create_body_is_rejected(tmp_path) -> None:
    client = _client(tmp_path, _SharedMap())
    body = create_body("packet-conflict")
    other = create_body("packet-conflict", device={"model": "iPad", "systemVersion": "18", "appVersion": "1"})
    first = client.post("/v1/captures", json=body, headers={"Idempotency-Key": body["packetId"]})
    conflict = client.post("/v1/captures", json=other, headers={"Idempotency-Key": other["packetId"]})
    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["errors"][0]["code"] == "idempotency_conflict"
    assert client.app.state.deps.state.get_capture(first.json()["captureId"]) is not None


def test_two_api_containers_share_one_create(tmp_path) -> None:
    mapping = _SharedMap(parties=2)
    serial = iter(range(1, 10))
    lock = threading.Lock()

    def new_id(prefix: str) -> str:
        with lock:
            return f"{prefix}{next(serial)}"

    clients = []
    for _ in range(2):
        app = create_app(settings=_settings(tmp_path), new_id=new_id)
        app.state.deps.state = ModalDictState(mapping)
        client = TestClient(app)
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        clients.append(client)

    body = create_body("packet-shared-create")
    statuses: list[int] = []
    ids: list[str] = []
    errors: list[BaseException] = []

    def post(client: TestClient) -> None:
        try:
            response = client.post("/v1/captures", json=body, headers={"Idempotency-Key": body["packetId"]})
            statuses.append(response.status_code)
            if response.status_code in {200, 201}:
                ids.append(response.json()["captureId"])
        except BaseException as exc:  # noqa: BLE001 - surface the worker error in the assertion
            errors.append(exc)

    threads = [threading.Thread(target=post, args=(client,)) for client in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sorted(statuses) == [200, 201]
    assert len(set(ids)) == 1
    open_ids = [rec.capture_id for rec in clients[0].app.state.deps.state.list_open_captures()]
    assert open_ids == ids[:1]
