"""Ids the kit mints match the server patterns in spec doc revision 0.4.2."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from ios_kit.fake_api import create_app

# Same expressions as server.contracts._base (WS-00 security contracts) and the spec.
STORAGE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
SEGMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
REL_PATH = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9][A-Za-z0-9_-]*)*(/[A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9][A-Za-z0-9_-]*)*)*$"
)

KIT = Path(__file__).resolve().parents[1]
MINIS = (
    KIT / "fixtures" / "mini-arkit-lidar" / "packet.json",
    KIT / "fixtures" / "mini-arkit" / "packet.json",
    KIT / "fixtures" / "mini-photo-only" / "packet.json",
)
PATH_KEYS = {"path", "file", "img"}


def _rel_paths(node: object) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in PATH_KEYS and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_rel_paths(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_rel_paths(item))
    return found


def test_uuid4_with_hyphens_fits_storage_id() -> None:
    minted = str(uuid.uuid4())
    assert len(minted) == 36
    assert STORAGE_ID.fullmatch(minted)


def test_minis_use_conforming_ids() -> None:
    for path in MINIS:
        document = json.loads(path.read_text())
        packet_id = document["packetId"]
        assert STORAGE_ID.fullmatch(packet_id)
        assert len(packet_id) == 36
        for name in ("parentPacketId", "amendmentId"):
            value = document.get(name)
            if value:
                assert STORAGE_ID.fullmatch(value)
        for frame in document.get("keyframes") or []:
            assert SEGMENT_ID.fullmatch(frame["id"])
        for still in document.get("stills") or []:
            assert SEGMENT_ID.fullmatch(still["id"])
        mesh_file = (document.get("mesh") or {}).get("file")
        if mesh_file:
            assert REL_PATH.fullmatch(mesh_file)
        for rel in _rel_paths(document):
            assert REL_PATH.fullmatch(rel), rel


def test_fake_api_mints_conforming_ids(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            tmp_path,
            verdict_delay_s=0,
            stage_interval_s=0,
            hint_delay_s=0,
            capture_check_delay_s=0,
        )
    )
    packet_id = str(uuid.uuid4())
    headers = {"Authorization": "Bearer" + " " + "dev", "Idempotency-Key": packet_id}
    created = client.post(
        "/v1/captures",
        headers=headers,
        json={
            "packetId": packet_id,
            "formatVersion": "0.4",
            "tier": "arkit",
            "flow": "guided",
            "device": {"model": "iPhone", "systemVersion": "16.0", "appVersion": "ios-kit"},
        },
    )
    assert created.status_code == 201
    capture_id = created.json()["captureId"]
    assert STORAGE_ID.fullmatch(capture_id)
    amendment_id = str(uuid.uuid4())
    amended = client.post(
        f"/v1/captures/{capture_id}/amendments",
        headers=headers,
        json={"amendmentId": amendment_id, "answers": ["meter_close"]},
    )
    assert amended.status_code == 201
    assert amended.json()["amendmentId"] == amendment_id
    assert STORAGE_ID.fullmatch(amendment_id)
    digest = "a" * 64
    registered = client.post(
        f"/v1/captures/{capture_id}/files",
        headers=headers,
        json={
            "files": [
                {
                    "path": "geometry/mesh.ply",
                    "role": "mesh",
                    "bytes": 1,
                    "sha256": digest,
                    "contentType": "application/octet-stream",
                }
            ]
        },
    )
    assert registered.status_code == 200
    upload_url = registered.json()["files"][0]["upload"]["url"]
    token = upload_url.rsplit("/", 1)[-1]
    assert STORAGE_ID.fullmatch(token)
    assert REL_PATH.fullmatch("geometry/mesh.ply")
