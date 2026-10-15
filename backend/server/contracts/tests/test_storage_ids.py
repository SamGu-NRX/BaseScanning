"""Client ids and paths that become storage or state keys reject traversal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from server.contracts._base import RelPath, SegmentId, StorageId
from server.contracts.api import AmendmentRequest, CreateCaptureRequest
from server.contracts.packet import FileEntry, Keyframe, Mesh, Packet, Still

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "data/fixtures/packets/examples/example-packet.json"

_STORAGE = TypeAdapter(StorageId)
_SEGMENT = TypeAdapter(SegmentId)
_PATH = TypeAdapter(RelPath)


def test_storage_id_pattern() -> None:
    _STORAGE.validate_python("mini-arkit")
    _STORAGE.validate_python("5b0c1e9e-7a8e-4f0e-9d8c-2f1b7a3c9e41")
    _STORAGE.validate_python("01ARZ3NDEKTSV4RRFFQ69G5FAV")
    for bad in (
        "..",
        "../etc/passwd",
        "abcd/efgh",
        "........",
        "1234567",
        "a" * 65,
        "abcd\nefgh",
        "abcd\x00efgh",
        "abcd\u2028efgh",
        "<script>1",
    ):
        with pytest.raises(ValidationError):
            _STORAGE.validate_python(bad)


def test_segment_id_pattern() -> None:
    _SEGMENT.validate_python("k00001")
    _SEGMENT.validate_python("s1")
    for bad in ("..", "../k", "k/1", "-hidden", ".hidden", "k\n1", "a" * 129):
        with pytest.raises(ValidationError):
            _SEGMENT.validate_python(bad)


def test_rel_path_rejects_traversal() -> None:
    _PATH.validate_python("keyframes/k00001.jpg")
    _PATH.validate_python("streams/arkit-nonlidar-2018/arkit_poses.csv.gz")
    for bad in (
        "..",
        "../packet.json",
        "a/..",
        "foo/../../x",
        "foo..bar",
        "/etc/passwd",
        "a//b",
        "C:/x",
        "a\\b",
        "-secret",
        ".hidden",
        "dir/./file",
        "has space.jpg",
        "line\nbreak",
    ):
        with pytest.raises(ValidationError):
            _PATH.validate_python(bad)


def test_packet_and_api_ids_use_the_pattern() -> None:
    document = json.loads(EXAMPLE.read_text())
    document["packetId"] = "a/b/cdef"
    with pytest.raises(ValidationError):
        Packet.model_validate(document)
    document["packetId"] = "mini-arkit"
    document["parentPacketId"] = "../parent"
    with pytest.raises(ValidationError):
        Packet.model_validate(document)
    document["parentPacketId"] = "5b0c1e9e-7a8e-4f0e-9d8c-2f1b7a3c9e41"
    Packet.model_validate(document)

    with pytest.raises(ValidationError):
        CreateCaptureRequest.model_validate(
            {
                "packetId": "../packet",
                "formatVersion": "0.4",
                "tier": "arkit",
                "flow": "guided",
                "device": {"model": "iPhone", "systemVersion": "18", "appVersion": "0.4"},
            }
        )
    with pytest.raises(ValidationError):
        AmendmentRequest.model_validate({"amendmentId": "..", "answers": []})
    parsed = AmendmentRequest.model_validate({"amendmentId": "amend-0001", "answers": ["door"]})
    assert parsed.amendmentId == "amend-0001"

    with pytest.raises(ValidationError) as keyframe_error:
        Keyframe.model_validate({"id": "../k", "img": "k.jpg"})
    assert any(err["loc"] == ("id",) for err in keyframe_error.value.errors())
    with pytest.raises(ValidationError) as still_error:
        Still.model_validate({"id": "s/1", "img": "s.jpg"})
    assert any(err["loc"] == ("id",) for err in still_error.value.errors())
    with pytest.raises(ValidationError):
        Mesh.model_validate({"file": "../mesh.ply", "epoch": "e1"})
    Mesh.model_validate({"file": "mesh/scan.ply", "epoch": "e1"})
    with pytest.raises(ValidationError):
        FileEntry.model_validate({"path": "a/..", "bytes": 1, "sha256": "a" * 64, "role": "still"})
