"""Gates from the iOS contract brief."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import socket
import tarfile
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np
import pytest
import uvicorn
from fastapi.testclient import TestClient
from ios_kit.amend import subset_packet
from ios_kit.ar import box_pose_arkit, frame_to_arkit
from ios_kit.fake_api import create_app
from ios_kit.prompts import _drop_zero_offset, load_catalogue, render, select_prompt
from ios_kit.replay import _resolve, run
from ios_kit.schemas import validate_packet_schema_only, validate_result

from server.contracts.packet import Packet

KIT = Path(__file__).resolve().parents[1]
EXAMPLES = KIT / "examples"
MINI = KIT / "fixtures" / "mini-arkit"


def _similarity(rng: np.random.Generator) -> list[float]:
    raw = rng.normal(size=(3, 3))
    rotation, _ = np.linalg.qr(raw)
    if np.linalg.det(rotation) < 0:
        rotation[:, 0] *= -1
    scale = float(rng.uniform(0.8, 1.25))
    matrix = np.eye(4)
    matrix[:3, :3] = scale * rotation
    matrix[:3, 3] = rng.normal(size=3)
    return [float(value) for value in matrix.reshape(16, order="F")]


def test_frame_to_arkit_is_the_inverse_of_random_similarities() -> None:
    rng = np.random.default_rng(14)
    for _ in range(100):
        values = _similarity(rng)
        source = np.array(values, dtype=np.float64).reshape((4, 4), order="F")
        inverse = np.array(frame_to_arkit(values), dtype=np.float64).reshape((4, 4), order="F")
        assert np.allclose(inverse @ source, np.eye(4), atol=1e-9)
        assert np.allclose(source @ inverse, np.eye(4), atol=1e-9)


def test_box_pose_scales_size_by_inverse_similarity() -> None:
    rng = np.random.default_rng(15)
    size = [0.7874, 1.0033, 0.5588]
    for _ in range(20):
        values = _similarity(rng)
        scale = float(np.linalg.norm(np.array(values, dtype=np.float64).reshape((4, 4), order="F")[:3, 0]))
        _pose, arkit_size = box_pose_arkit(values, [1.0, 2.0, 3.0], size, 10.0)
        assert np.allclose(arkit_size, [item / scale for item in size], atol=1e-9)


def test_subset_packet_validates() -> None:
    packet = json.loads((EXAMPLES / "packets" / "example-packet.json").read_text())
    amended = subset_packet(packet, {"k00002", "k00003"}, {"s1"}, packet["packetId"])
    assert amended["parentPacketId"] == packet["packetId"]
    assert amended["packetId"] != packet["packetId"]
    assert validate_packet_schema_only(amended) == []
    Packet.model_validate(amended)


def test_select_prompt_prefers_the_specific_entry_and_falls_back() -> None:
    catalogue = load_catalogue()
    grazing = select_prompt(catalogue, kind="band", band="wall", cause="grazing")
    broad = select_prompt(catalogue, kind="band", band="wall", cause="unobserved")
    generic = select_prompt(catalogue, kind="region", band="facing")
    assert grazing.prompt_id == "vn.wall.grazing"
    assert broad.prompt_id == "vn.wall.unobserved"
    assert generic.prompt_id == "vn.generic"


def test_render_raises_when_a_variable_is_missing() -> None:
    catalogue = load_catalogue()
    entry = select_prompt(catalogue, kind="band", band="ground")
    with pytest.raises(KeyError):
        render(entry, side="at your meter")


def test_render_drops_zero_offset_at_the_meter() -> None:
    catalogue = load_catalogue()
    ground = select_prompt(catalogue, kind="band", band="ground")
    centred = render(ground, side="at your meter", s_ft=0.0, out_ft=3.7)
    assert "0 ft" not in centred.body
    assert "at your meter" in centred.body
    offset = render(ground, side="to the right of your meter", s_ft=4.3, out_ft=3.7)
    assert "14 ft along" in offset.body
    generic = select_prompt(catalogue, kind="region")
    centred_generic = render(generic, side="at your meter", s_ft=0.0)
    assert centred_generic.body == "Photograph the area at your meter, from about 10 ft back."


def test_example_results_validate() -> None:
    for path in sorted((EXAMPLES / "results").glob("*.json")):
        assert validate_result(json.loads(path.read_text())) == [], path.name


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        tmp_path,
        verdict_delay_s=0,
        stage_interval_s=0,
        hint_delay_s=0,
        capture_check_delay_s=0,
    )
    return TestClient(app)


def _create(client: TestClient, packet_id: str = "packet-0001", version: str = "0.4", key: str = "once"):
    body = {
        "packetId": packet_id,
        "formatVersion": version,
        "tier": "arkit",
        "flow": "guided",
        "device": {"model": "iPhone", "systemVersion": "16.0", "appVersion": "ios-kit"},
    }
    return client.post(
        "/v1/captures", headers={"Authorization": "Bearer" + " " + "dev", "Idempotency-Key": key}, json=body
    )


def test_fake_conflict_and_validation_paths(tmp_path: Path) -> None:
    client = _client(tmp_path)
    rejected = _create(client, version="0.3")
    assert rejected.status_code == 422
    assert rejected.json()["errors"][0]["code"] == "format_version_unsupported"
    created = _create(client)
    assert created.status_code == 201
    again = _create(client, packet_id="packet-0002")
    assert again.status_code == 409
    assert again.json()["errors"][0]["code"] == "idempotency_conflict"
    capture = created.json()["captureId"]
    headers = {"Authorization": "Bearer" + " " + "dev"}
    unknown = client.post(
        f"/v1/captures/{capture}/files",
        headers=headers,
        json={"files": [{"path": "a.jpg", "role": "not_a_role", "bytes": 1, "sha256": "ab"}]},
    )
    assert unknown.status_code == 422
    assert unknown.json()["errors"][0]["code"] == "role_unknown"
    digest = "a" * 64
    first = client.post(
        f"/v1/captures/{capture}/files",
        headers=headers,
        json={
            "files": [
                {
                    "path": "a.jpg",
                    "role": "keyframe",
                    "bytes": 1,
                    "md5": "m",
                    "sha256": digest,
                    "contentType": "image/jpeg",
                }
            ]
        },
    )
    assert first.status_code == 200
    clash = client.post(
        f"/v1/captures/{capture}/files",
        headers=headers,
        json={"files": [{"path": "a.jpg", "role": "keyframe", "bytes": 1, "sha256": "b" * 64}]},
    )
    assert clash.status_code == 409
    assert clash.json()["errors"][0]["code"] == "sha256_conflict"
    raw = b'{"formatVersion":"0.4"}'
    bad = client.post(
        f"/v1/captures/{capture}/finalize",
        headers={**headers, "X-Packet-Sha256": "c" * 64, "Content-Type": "application/json"},
        content=raw,
    )
    assert bad.status_code == 409
    assert bad.json()["errors"][0]["code"] == "packet_sha_conflict"
    packet = json.dumps({"formatVersion": "0.4", "stills": [{"id": "s1"}], "files": [{"path": "missing.jpg"}]}).encode()
    held = client.post(
        f"/v1/captures/{capture}/finalize",
        headers={**headers, "Content-Type": "application/json", "X-Packet-Sha256": hashlib.sha256(packet).hexdigest()},
        content=packet,
    )
    assert held.status_code == 202
    missing_value = client.post(
        f"/v1/captures/{capture}/confirmations",
        headers=headers,
        json={"fieldId": "meter_number", "kind": "confirmed", "imageId": "s1", "clientAt": "2026-09-26T00:00:00Z"},
    )
    assert missing_value.status_code == 422
    assert missing_value.json()["errors"][0]["code"] == "value_required"
    ok = client.post(
        f"/v1/captures/{capture}/confirmations",
        headers=headers,
        json={
            "fieldId": "meter_number",
            "kind": "confirmed",
            "value": "000000000",
            "imageId": "s1",
            "clientAt": "2026-09-26T00:00:00Z",
        },
    )
    assert ok.status_code == 201
    assert ok.json()["by"] == "device"
    conflict = client.post(
        f"/v1/captures/{capture}/confirmations",
        headers=headers,
        json={
            "fieldId": "meter_number",
            "kind": "corrected",
            "value": "111111111",
            "imageId": "s1",
            "clientAt": "2026-09-26T00:00:01Z",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["errors"][0]["code"] == "confirmation_conflict"
    events = client.get(f"/v1/captures/{capture}/events", headers=headers, params={"after": 0, "wait": 0})
    assert any(row["type"] == "confirmation" and row["data"]["by"] == "device" for row in events.json()["events"])


def test_tap_replay_is_rejected(tmp_path: Path) -> None:
    folder = tmp_path / "mini"
    shutil.copytree(MINI, folder)
    packet_path = folder / "packet.json"
    packet = json.loads(packet_path.read_text())
    packet["taps"][0]["pixel"] = [0.0, 0.0]
    packet_path.write_text(json.dumps(packet))
    client = _client(tmp_path / "root")
    headers = {"Authorization": "Bearer" + " " + "dev"}
    created = client.post(
        "/v1/captures",
        headers={**headers, "Idempotency-Key": packet["packetId"]},
        json={
            "packetId": packet["packetId"],
            "formatVersion": "0.4",
            "tier": packet["session"]["tier"],
            "flow": "guided",
            "device": {"model": "iPhone", "systemVersion": "16.0", "appVersion": "ios-kit"},
        },
    )
    assert created.status_code == 201
    capture = created.json()["captureId"]
    for row in packet["files"]:
        data = (folder / row["path"]).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        md5 = base64.b64encode(hashlib.md5(data).digest()).decode("ascii")
        registered = client.post(
            f"/v1/captures/{capture}/files",
            headers=headers,
            json={
                "files": [
                    {
                        "path": row["path"],
                        "role": row["role"],
                        "bytes": len(data),
                        "md5": md5,
                        "sha256": digest,
                        "contentType": "application/octet-stream",
                    }
                ]
            },
        )
        assert registered.status_code == 200, registered.text
        upload = registered.json()["files"][0]["upload"]
        put = client.put(upload["url"], headers={"Content-MD5": md5}, content=data)
        assert put.status_code == 200
        committed = client.post(
            f"/v1/captures/{capture}/files:commit",
            headers=headers,
            json={"files": [{"path": row["path"], "sha256": digest}]},
        )
        assert committed.status_code == 200
    raw = packet_path.read_bytes()
    finalized = client.post(
        f"/v1/captures/{capture}/finalize",
        headers={**headers, "Content-Type": "application/json", "X-Packet-Sha256": hashlib.sha256(raw).hexdigest()},
        content=raw,
    )
    assert finalized.status_code == 422
    assert any(item["code"] == "tap_replay" for item in finalized.json()["errors"])


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start(port: int, root: Path) -> None:
    app = create_app(root, verdict_delay_s=0, stage_interval_s=0, hint_delay_s=0, capture_check_delay_s=0)
    thread = threading.Thread(
        target=lambda: uvicorn.run(app, host="127.0.0.1", port=port, log_level="error"),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/healthz", timeout=0.2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("fake api did not start")


def test_replay_round_trip_records_the_contract_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    port = _free_port()
    _start(port, tmp_path / "root")
    monkeypatch.setenv("CAPTURE_API_TOKEN", "dev-token")
    finished = run(
        MINI,
        f"http://127.0.0.1:{port}/v1",
        variant=None,
        rate=0,
        concurrency=1,
        finalize_at="last",
        until="verdict",
        timeout_s=30,
        out_dir=tmp_path / "out",
        token="dev-token",
    )
    assert validate_result(finished.result or {}) == []
    kinds = [row.get("type") for row in finished.events]
    assert "files_committed" in kinds
    assert "capture_check" in kinds
    assert "hint" in kinds
    assert "verdict_ready" in kinds
    assert "confirmation" in kinds
    hint = next(row for row in finished.events if row.get("type") == "hint")
    assert hint["data"]["code"] == "meter_number_confirm"
    assert hint["data"]["confirm"]["preselected"] == "000000000"
    stages = [row["data"]["stage"] for row in finished.events if row.get("type") == "stage"]
    assert stages == [
        "validate",
        "poses",
        "objects",
        "reads",
        "scale",
        "dense",
        "coverage",
        "scene",
        "objects_3d",
        "criteria",
        "result",
    ]
    assert finished.timings["last_commit_to_capture_check_s"] <= 30


def test_replay_finds_variant_packet_one_folder_inside_a_tar(tmp_path: Path) -> None:
    house = tmp_path / "electro"
    house.mkdir()
    (house / "packet.arkit-nonlidar.json").write_text('{"packetId": "mini-arkit"}')
    archive = tmp_path / "eth3d-electro.tar"
    with tarfile.open(archive, "w") as packed:
        packed.add(house, arcname="electro")
    found, variant = _resolve(archive, "arkit-nonlidar")
    assert found.parent.name == "electro"
    assert variant == "arkit-nonlidar"
    assert json.loads(found.read_text())["packetId"] == "mini-arkit"


def test_ground_and_close_up_prompts_resolve() -> None:
    catalogue = load_catalogue()
    left = select_prompt(catalogue, hint_code="ground_downward_left")
    right = select_prompt(catalogue, hint_code="ground_downward_right")
    assert left.prompt_id == "capture_check.ground_downward_left"
    assert right.prompt_id == "capture_check.ground_downward_right"
    rendered = render(left, side="to the left of your meter")
    assert (
        rendered.body
        == "From chest height, point the phone down at the ground in front of the wall to the left of your meter."
    )
    assert select_prompt(catalogue, kind="close_up", target_class="meter").prompt_id == "vn.close_up.meter"
    assert select_prompt(catalogue, kind="close_up", target_class="panel").prompt_id == "vn.close_up.panel"
    assert select_prompt(catalogue, kind="close_up").prompt_id == "vn.close_up.object"
    assert select_prompt(catalogue, capture_check_code="keyframe_cadence").prompt_id == "capture_check.keyframe_cadence"
    assert select_prompt(catalogue, hint_code="keyframe_cadence").prompt_id == "capture_check.keyframe_cadence"
    ground = select_prompt(catalogue, kind="confirm_attribute", target_class="ground")
    assert ground.prompt_id == "vn.confirm.ground"


def test_fake_api_defaults_to_no_token(tmp_path: Path) -> None:
    open_client = _client(tmp_path)
    created = open_client.post(
        "/v1/captures",
        headers={"Idempotency-Key": "once"},
        json={
            "packetId": "packet-0001",
            "formatVersion": "0.4",
            "tier": "arkit",
            "flow": "guided",
            "device": {"model": "iPhone", "systemVersion": "16.0", "appVersion": "ios-kit"},
        },
    )
    assert created.status_code == 201
    locked = TestClient(create_app(tmp_path / "locked", token="handed-out"))
    missing = locked.post(
        "/v1/captures",
        headers={"Idempotency-Key": "once"},
        json={
            "packetId": "packet-0001",
            "formatVersion": "0.4",
            "tier": "arkit",
            "flow": "guided",
            "device": {"model": "iPhone", "systemVersion": "16.0", "appVersion": "ios-kit"},
        },
    )
    assert missing.status_code == 401
    assert missing.json()["errors"][0]["code"] == "unauthorized"
    accepted = locked.post(
        "/v1/captures",
        headers={"Authorization": "Bearer" + " " + "handed-out", "Idempotency-Key": "once"},
        json={
            "packetId": "packet-0001",
            "formatVersion": "0.4",
            "tier": "arkit",
            "flow": "guided",
            "device": {"model": "iPhone", "systemVersion": "16.0", "appVersion": "ios-kit"},
        },
    )
    assert accepted.status_code == 201


def test_fake_api_rejects_nonconforming_ids_and_paths(tmp_path: Path) -> None:
    client = _client(tmp_path)
    headers = {"Authorization": "Bearer" + " " + "dev"}
    for packet_id in ("../evil-id", "a/b/c/d/e", "short"):
        rejected = _create(client, packet_id=packet_id, key=packet_id)
        assert rejected.status_code == 422
        assert rejected.json()["errors"][0]["code"] == "invalid_pattern"
        assert rejected.json()["errors"][0]["pointer"] == "/packetId"
    created = _create(client, key="conforming")
    assert created.status_code == 201
    capture = created.json()["captureId"]
    escaped = client.post(
        f"/v1/captures/{capture}/files",
        headers=headers,
        json={"files": [{"path": "../../escape.txt", "role": "other", "bytes": 1, "sha256": "a" * 64}]},
    )
    assert escaped.status_code == 422
    assert escaped.json()["errors"][0]["pointer"] == "/files/0/path"
    assert list(tmp_path.rglob("escape.txt")) == []
    amended = client.post(
        f"/v1/captures/{capture}/amendments",
        headers=headers,
        json={"amendmentId": "../amend", "answers": ["meter_close"]},
    )
    assert amended.status_code == 422
    assert amended.json()["errors"][0]["pointer"] == "/amendmentId"


def test_prompt_parity_with_server_copy() -> None:
    copy = pytest.importorskip("server.result.copy")
    kit = load_catalogue()
    server = copy.load_catalogue(kit.path)
    queries = (
        {"outcome": "eligible"},
        {"outcome": "needs_more_photos"},
        {"outcome": "manual_review"},
        {"outcome": "not_eligible"},
        {"outcome": "failed"},
        {"criterion": "ground_surface"},
        {"criterion": "meter_number"},
        {"kind": "band", "band": "wall", "cause": "grazing"},
        {"kind": "band", "band": "ground"},
        {"kind": "past_end"},
        {"kind": "close_up", "target_class": "meter"},
        {"kind": "close_up", "target_class": "panel"},
        {"kind": "close_up", "target_class": "window"},
        {"kind": "confirm_attribute", "target_class": "window"},
        {"kind": "region"},
        {"capture_check_code": "ground_downward_left"},
        {"capture_check_code": "ground_downward_right"},
        {"hint_code": "ground_downward_left"},
        {"hint_code": "ground_downward_right"},
        {"hint_code": "meter_number_confirm"},
        {"member_action": "retake_meter_closeups"},
    )
    offset = {
        "side": "to the left of your meter",
        "s_ft": "7",
        "out_ft": "12",
        "what": "window",
        "threshold_ft": "3",
    }
    centred = {**offset, "side": "at your meter", "s_ft": "0"}
    assert len(queries) >= 20
    for query in queries:
        kit_entry = select_prompt(kit, **query)
        server_entry = copy.select_prompt(server, **query)
        assert kit_entry.prompt_id == server_entry.prompt_id
        kit_offset = render(kit_entry, **offset)
        server_offset = copy.render(server_entry, **offset)
        assert (kit_offset.promptId, kit_offset.title, kit_offset.body) == (
            server_offset.promptId,
            server_offset.title,
            server_offset.body,
        )
        kit_zero = render(kit_entry, **centred)
        server_zero = copy.render(server_entry, **centred)
        assert ", 0 ft along" not in kit_zero.title
        assert ", 0 ft along" not in kit_zero.body
        assert kit_zero.title == _drop_zero_offset(server_zero.title)
        assert kit_zero.body == _drop_zero_offset(server_zero.body)
