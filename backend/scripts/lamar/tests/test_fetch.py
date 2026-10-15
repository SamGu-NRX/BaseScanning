import json
from pathlib import Path

import fetch_frames
import fetch_meta


def test_urls_and_licence_and_members():
    assert fetch_meta.session_zip_url("ios_2022-06-17_10.43.29").endswith(
        "/raw/LIN/sessions/ios_2022-06-17_10.43.29.zip"
    )
    assert fetch_meta.colmap_zip_url().endswith("/colmap/LIN.zip")
    assert fetch_meta.licence_ok("Licence: CC BY-SA 4.0\n")
    assert not fetch_meta.licence_ok("all rights reserved")
    names = [
        "ios_a/raw_data/images/1.jpg",
        "ios_a/trajectories.txt",
        "other/trajectories.txt",
        "ios_a/sensors.txt",
        "ios_a/images.txt",
    ]
    chosen = fetch_meta.pick_meta_members(names, "ios_a")
    assert chosen["trajectories.txt"] == "ios_a/trajectories.txt"
    assert set(chosen) == {"trajectories.txt", "images.txt", "sensors.txt"}
    images = fetch_frames.image_members(names, "ios_a")
    assert images == ["ios_a/raw_data/images/1.jpg"]


class _Rotation:
    def matrix(self):
        return [[1, 0, 0], [0, 1, 0], [0, 0, 1]]


class _Rigid:
    rotation = _Rotation()
    translation = (0.0, 1.0, 2.0)


class _Image:
    def __init__(self, name: str) -> None:
        self.name = name
        self.camera_id = 1

    def cam_from_world(self):
        return _Rigid()


class _Camera:
    model_name = "PINHOLE"
    width = 1920
    height = 1440
    params = [100.0, 100.0, 960.0, 720.0]


def test_split_gt_keeps_ios_and_navvis():
    images = {1: _Image("ios_2022-06-17_10.43.29/1.jpg"), 2: _Image("navvis_1/2.jpg"), 3: _Image("other.jpg")}
    ios, navvis = fetch_meta.split_gt(images, {1: _Camera()})
    assert set(ios) == {"ios_2022-06-17_10.43.29/1.jpg"}
    assert set(navvis) == {"navvis_1/2.jpg"}
    assert ios["ios_2022-06-17_10.43.29/1.jpg"]["t"] == [0.0, 1.0, 2.0]


def test_gate_sessions_ignore_other_ios_keys():
    document = {
        "ios_2022-06-17_10.43.29": {},
        "ios_2022-02-10_14.47.24": {},
        "ios_not_in_the_gate": {},
        "navvis_1": {},
    }
    present, missing = fetch_meta.gate_sessions(document)
    assert present == ["ios_2022-06-17_10.43.29", "ios_2022-02-10_14.47.24"]
    assert missing == ["ios_2021-06-02_11.12.32"]
    assert fetch_frames.SESSIONS == fetch_meta.SESSIONS


def test_manifest_fingerprint(tmp_path: Path):
    data = tmp_path / "lamar"
    target = data / "metadata_phone.json"
    target.parent.mkdir()
    target.write_text("{}\n")
    manifest = tmp_path / "manifest.json"
    fetch_meta.update_fixture_manifest(manifest, data, fetch_meta.lamar_manifest_items())
    document = json.loads(manifest.read_text())
    assert document["format"] == "hss-fixtures/v0"
    meta = next(item for item in document["items"] if item["id"] == "lamar:lin-metadata-phone")
    assert meta["sha256"]
    assert meta["bytes"] == target.stat().st_size
    assert meta["committed"] is False
    assert "data_relative" not in meta
