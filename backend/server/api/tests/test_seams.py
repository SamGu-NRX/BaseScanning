"""Seam and security fixes on the ingest API."""

from __future__ import annotations

import contextlib
import contextvars
import json
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from starlette.requests import Request

import server.orchestrator as orchestrator
from server.api.auth import authenticate, parse_tokens
from server.api.finalize import finish_ready
from server.api.finalize_checks import manifest_errors
from server.api.hooks import OrchestratorHooks
from server.api.tests.conftest import REVIEWER, create_body
from server.api.tests.test_ingest import _create, _file
from server.common.config import Settings
from server.contracts.jobs import RunRecord, StageRecord
from server.contracts.packet import Packet


def _no_none(value) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        return all(_no_none(item) for item in value.values())
    if isinstance(value, list):
        return all(_no_none(item) for item in value)
    return True


def test_photo_packet_recheck_does_not_insert_nulls(monkeypatch) -> None:
    root = Path(__file__).resolve().parents[3]
    raw = json.loads(
        (root / "data/fixtures/packets/examples/example-packet-photo-only.json").read_text(encoding="utf-8")
    )
    packet = Packet.model_validate(raw)
    seen: dict = {}
    original = orchestrator.validate_manifest

    def fake(payload):
        seen["payload"] = payload
        return original(payload)

    monkeypatch.setattr(orchestrator, "validate_manifest", fake)
    errors = manifest_errors(packet)
    assert _no_none(seen["payload"])
    assert errors == []


def test_omitted_flow_defaults(client) -> None:
    photo = create_body("packet-omit-photo")
    photo.pop("flow")
    created = client.post("/v1/captures", json=photo, headers={"Idempotency-Key": photo["packetId"]})
    assert created.status_code == 201, created.text
    capture_id = created.json()["captureId"]
    assert client.get(f"/v1/captures/{capture_id}").json()["flow"] == "photo"
    guided = create_body("packet-omit-guided", tier="arkit")
    guided.pop("flow")
    created = client.post("/v1/captures", json=guided, headers={"Idempotency-Key": guided["packetId"]})
    assert created.status_code == 201, created.text
    assert client.get(f"/v1/captures/{created.json()['captureId']}").json()["flow"] == "guided"


def test_finish_ready_keeps_orchestrator_status(client) -> None:
    capture_id = _create(client, "packet-keep-status")
    deps = client.app_state

    class Hooks:
        def on_finalized(self, capture_id, packet_sha256, packet_uri, parent_run_id=None):
            deps.state.set_status(capture_id, "manual_review")

    deps.hooks = Hooks()
    deps.state.put_kv(f"capturex:{capture_id}", {"promoted_at": "2026-09-26T00:00:00.000Z"})
    record = deps.state.get_capture(capture_id)
    body = finish_ready(deps, record, "ab" * 32, f"captures/{capture_id}/packet.json", "run_local")
    assert deps.state.get_capture(capture_id).status == "manual_review"
    assert body["status"] == "processing"
    assert client.get(f"/v1/captures/{capture_id}").json()["status"] == "manual_review"


def _services_module(monkeypatch):
    """The real WS-03 module when it is installed; otherwise the same bind contract."""
    try:
        import server.orchestrator.services as services
    except ImportError:
        services = _services_stub()
        monkeypatch.setitem(sys.modules, "server.orchestrator.services", services)
        monkeypatch.setattr(orchestrator, "services", services, raising=False)
        return services
    return services


def _services_stub():
    @dataclass
    class Services:
        state: object = None
        storage: object = None

    current_var: contextvars.ContextVar = contextvars.ContextVar("hss_orch_services", default=None)

    def current():
        bound = current_var.get()
        if bound is not None:
            return bound
        return Services()

    @contextlib.contextmanager
    def bind(services):
        token = current_var.set(services)
        try:
            yield services
        finally:
            current_var.reset(token)

    module = types.ModuleType("server.orchestrator.services")
    module.Services = Services
    module.current = current
    module.bind = bind
    return module


def test_finalize_hook_binds_api_services(monkeypatch) -> None:
    # The real services.current() builds from process env. Dev keeps that call from raising.
    monkeypatch.setenv("SMP_ENVIRONMENT", "dev")
    monkeypatch.setenv("VISION_EXTERNAL_CALLS", "on")
    services = _services_module(monkeypatch)
    seen = {}

    def start_run(*args, **kwargs):
        del args, kwargs
        live = services.current()
        seen["state"] = live.state
        seen["storage"] = live.storage

    monkeypatch.setattr(orchestrator, "start_run", start_run)
    api_state = object()
    api_storage = object()
    hooks = OrchestratorHooks(
        Settings(SMP_ENVIRONMENT="dev", VISION_EXTERNAL_CALLS="on"),
        api_state,
        api_storage,
    )
    hooks.on_finalized("cap_1", "ab" * 32, "captures/cap_1/packet.json")
    assert seen["state"] is api_state
    assert seen["storage"] is api_storage


def test_device_result_hides_verdict_and_internal(client) -> None:
    capture_id = _create(client, "packet-hidden-verdict")
    deps = client.app_state
    result_key = f"captures/{capture_id}/result/result.json"
    verdict_key = f"captures/{capture_id}/verdicts/verdict.C.json"
    internal_key = f"captures/{capture_id}/result/result.internal.json"
    deps.storage.put_bytes(
        result_key,
        json.dumps({"ready": True, "verdictUrl": "verdicts/verdict.C.json"}).encode(),
        "application/json",
    )
    hidden = json.dumps({"criterion": "c5_clearance", "outcome": "fail"}).encode()
    deps.storage.put_bytes(verdict_key, hidden, "application/json")
    deps.storage.put_bytes(internal_key, hidden, "application/json")
    deps.state.put_run(
        RunRecord(
            run_id="run_hidden",
            capture_id=capture_id,
            packet_sha256="a" * 64,
            status="done",
            branch="image",
            flow="photo",
            tier="photo_only",
            profile_default="C",
            created_at="2026-09-26T00:00:00.000Z",
            deadline_at="2026-09-26T01:00:00.000Z",
            artifacts={
                "result/result.json": result_key,
                "verdicts/verdict.C.json": verdict_key,
                "result/result.internal.json": internal_key,
            },
        )
    )
    deps.state.upsert_stage(
        StageRecord(run_id="run_hidden", stage="result", status="done", attempt=1, heartbeat="2026-09-26T00:00:00.000Z")
    )
    deps.settings.SHOW_REJECT_TO_HOMEOWNER = False
    body = client.get(f"/v1/captures/{capture_id}/result")
    assert body.status_code == 200, body.text
    encoded = body.text
    assert "c5_clearance" not in encoded
    assert "fail" not in encoded
    assert body.json()["verdictUrl"] is None
    deps.settings.SHOW_REJECT_TO_HOMEOWNER = True
    shown = client.get(f"/v1/captures/{capture_id}/result")
    assert shown.json()["verdictUrl"] is None
    assert "c5_clearance" not in shown.text
    reviewer = client.get(
        f"/v1/captures/{capture_id}/result",
        headers={"Authorization": f"Bearer {REVIEWER}"},
    )
    assert reviewer.status_code == 200, reviewer.text
    assert isinstance(reviewer.json()["verdictUrl"], str)
    assert "c5_clearance" not in reviewer.text


def test_delete_removes_prefix_orphans(client) -> None:
    capture_id = _create(client, "packet-delete-prefix")
    deps = client.app_state
    root = Path(deps.settings.VOLUME_ROOT)
    orphan = root / "captures" / capture_id / "runs" / "r1" / "packet.json"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"pii")
    work = root / "work" / capture_id / "note.txt"
    work.parent.mkdir(parents=True)
    work.write_text("pii")
    deps.state.put_run(
        RunRecord(
            run_id="run_delete",
            capture_id=capture_id,
            packet_sha256="a" * 64,
            status="done",
            branch="image",
            flow="photo",
            tier="photo_only",
            profile_default="C",
            created_at="2026-09-26T00:00:00.000Z",
            deadline_at="2026-09-26T01:00:00.000Z",
            artifacts={},
        )
    )
    run_work = root / "work" / "run_delete" / "packet.json"
    run_work.parent.mkdir(parents=True)
    run_work.write_bytes(b"pii")
    other = root / "captures" / "cap_other" / "keep.txt"
    other.parent.mkdir(parents=True)
    other.write_text("keep")
    deleted = client.delete(f"/v1/captures/{capture_id}")
    assert deleted.status_code == 204, deleted.text
    assert not orphan.exists()
    assert not work.exists()
    assert not run_work.exists()
    assert other.read_text() == "keep"


def test_ids_reject_traversal(client) -> None:
    bad = "../secret1"
    body = create_body(bad)
    created = client.post("/v1/captures", json=body, headers={"Idempotency-Key": bad})
    assert created.status_code == 422, created.text
    assert client.app_state.state.get_kv(f"idem:create:{bad}") is None
    capture_id = _create(client, "packet-amend-safe")
    client.app_state.state.set_status(capture_id, "needs_views")
    amendment = "amd/../xxxx"
    response = client.post(
        f"/v1/captures/{capture_id}/amendments",
        json={"amendmentId": amendment, "answers": []},
        headers={"Idempotency-Key": amendment},
    )
    assert response.status_code == 422, response.text
    assert client.app_state.state.get_kv(f"amend:{capture_id}:{amendment}") is None


def test_upload_checks_token_before_the_body(client, monkeypatch) -> None:
    import server.api.app as appmod

    calls = {"n": 0}
    original = appmod._buffer_body

    async def wrapped(request, limit):
        calls["n"] += 1
        return await original(request, limit)

    monkeypatch.setattr(appmod, "_buffer_body", wrapped)

    def explode(self):
        raise AssertionError("body was read")

    monkeypatch.setattr(Request, "stream", explode)
    response = client.put("/v1/uploads/not-a-token", content=b"x" * 40)
    assert response.status_code == 403
    assert calls["n"] == 0


def test_upload_rejects_a_different_length(client) -> None:
    capture_id = _create(client, "packet-upload-len")
    data = b"abcd"
    registered = client.post(
        f"/v1/captures/{capture_id}/files",
        json={"files": [_file("stills/s1.jpg", data)]},
    )
    assert registered.status_code == 200, registered.text
    item = registered.json()["files"][0]
    target = item["upload"]
    headers = dict(target["headers"])
    headers["content-length"] = "10"
    put = client.put(urlparse(target["url"]).path, content=b"x" * 10, headers=headers)
    assert put.status_code == 400, put.text
    assert client.app_state.storage.exists(f"uploads/{capture_id}/stills/s1.jpg") is False


def test_upload_cap_returns_429(client) -> None:
    capture_id = _create(client, "packet-upload-cap")
    data = b"abcd"
    registered = client.post(
        f"/v1/captures/{capture_id}/files",
        json={"files": [_file("stills/s1.jpg", data)]},
    )
    item = registered.json()["files"][0]
    target = item["upload"]

    class Closed:
        async def acquire(self):
            raise TimeoutError

        def release(self):
            return None

    assert client.app_state.upload_slots._value == 4
    client.app_state.upload_slots = Closed()
    put = client.put(urlparse(target["url"]).path, content=data, headers=target["headers"])
    assert put.status_code == 429, put.text


def test_subject_prefix_is_not_a_reviewer(monkeypatch) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    public_pem = key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    settings = Settings(
        SMP_ENVIRONMENT="dev",
        VISION_EXTERNAL_CALLS="on",
        HSS_AUTH_MODE="jwt",
        HSS_AUTH_JWKS_URL="https://idp.test/jwks",
        HSS_AUTH_ISSUER="https://idp.test",
        HSS_AUTH_AUDIENCE="capture-api",
    )

    class _Signing:
        def __init__(self, material: bytes) -> None:
            self.key = material

    class _Jwks:
        def __init__(self, _url: str) -> None:
            self.url = _url

        def get_signing_key_from_jwt(self, _token: str) -> _Signing:
            return _Signing(public_pem)

    monkeypatch.setattr("server.api.auth.PyJWKClient", _Jwks)
    prefixed = jwt.encode(
        {"sub": "reviewer-mallory", "aud": "capture-api", "iss": "https://idp.test"},
        private_pem,
        algorithm="RS256",
    )
    assert authenticate(f"Bearer {prefixed}", settings, {}).reviewer is False
    listed = jwt.encode(
        {"sub": "ada", "roles": ["reviewer"], "aud": "capture-api", "iss": "https://idp.test"},
        private_pem,
        algorithm="RS256",
    )
    assert authenticate(f"Bearer {listed}", settings, {}).reviewer is True
    tokens = parse_tokens(f"reviewer-mallory:{'a' * 32},reviewer:{'b' * 32},reviewer:ada:{'c' * 32}")
    assert tokens["a" * 32].reviewer is False
    assert tokens["b" * 32].reviewer is True
    assert tokens["c" * 32].reviewer is True
    assert tokens["c" * 32].label == "ada"
