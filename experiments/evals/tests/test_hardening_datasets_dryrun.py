"""Hand-computed cases for the dataset downloader and the synthetic field dry-run.

datasets.py is exercised through a fake urlopen serving real zips, so checksums, partial
downloads, member filtering, the free-space reserve and the completion record are all tested
without a network. field_dryrun.py is checked against its documented scene: the sheet's tap
order, the AR-scale shrink, the tape readings, and the wall/ground rendering the taps project
into. Pinned archive sizes and sha256 values come from METHODS.md's Datasets table.
"""

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from evals import datasets, field_dryrun
from evals.field import load_session, parse_tape
from evals.paths import ADVIO_DIR, ETH3D_DIR

# --- datasets.py: fakes -----------------------------------------------------------------------


class _FakeResponse:
    """A urlopen result over fixed bytes."""

    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)

    def __enter__(self):
        return self._buf

    def __exit__(self, *exc):
        return False


class _DyingResponse:
    """A urlopen result that dies partway through the body."""

    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)
        self._reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        self._reads += 1
        if self._reads > 1:
            raise OSError("connection reset mid-download")
        return self._buf.read(n)


MEMBERS = (
    "advio-99/iphone/arkit.csv",
    "advio-99/iphone/frames.csv",
    "advio-99/iphone/platform-locations.csv",
    "advio-99/ground-truth/",
    "advio-99/pixel/arcore.csv",
)


def _zip_bytes(with_arkit: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if with_arkit:
            z.writestr("advio-99/iphone/arkit.csv", "time,x,y,z\n")
        z.writestr("advio-99/iphone/frames.csv", "time\n")
        z.writestr("advio-99/iphone/platform-locations.csv", "time\n")
        z.writestr("advio-99/iphone/video.mov", b"\x00" * 4096)
        z.writestr("advio-99/ground-truth/gt.csv", "time,x,y,z\n")
        z.writestr("advio-99/pixel/arcore.csv", "time,x,y,z\n")
    return buf.getvalue()


def _archive(dest: Path, payload: bytes, **overrides) -> datasets.Archive:
    kwargs = dict(
        url="https://example.test/advio-99.zip",
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        dest=dest,
        marker="advio-99/iphone/arkit.csv",
        members=MEMBERS,
    )
    kwargs.update(overrides)
    return datasets.Archive(**kwargs)


def _serve(monkeypatch: pytest.MonkeyPatch, payload_by_url: dict[str, bytes]) -> list[str]:
    """Serve the given bytes per url; returns the urls asked for."""
    asked = []

    def fake_urlopen(url, timeout=None):
        asked.append(url)
        return _FakeResponse(payload_by_url[url])

    monkeypatch.setattr(datasets.urllib.request, "urlopen", fake_urlopen)
    return asked


def _free(monkeypatch: pytest.MonkeyPatch, free_gb: float) -> None:
    monkeypatch.setattr(
        datasets.shutil, "disk_usage", lambda p: SimpleNamespace(free=free_gb * 1e9)
    )


# --- datasets.py: fetch -----------------------------------------------------------------------


def test_fetch_verifies_and_unpacks_only_requested_members(tmp_path: Path, monkeypatch):
    payload = _zip_bytes()
    archive = _archive(tmp_path, payload)
    asked = _serve(monkeypatch, {archive.url: payload})
    _free(monkeypatch, 100.0)

    datasets.fetch(archive)

    assert asked == [archive.url]
    assert (tmp_path / "advio-99/iphone/arkit.csv").stat().st_size == len("time,x,y,z\n")
    assert (tmp_path / "advio-99/ground-truth/gt.csv").is_file()
    assert (tmp_path / "advio-99/pixel/arcore.csv").is_file()
    # Not a requested member: never lands on disk.
    assert not (tmp_path / "advio-99/iphone/video.mov").exists()
    # The .part file is gone and the completion record is written against the pinned sha256.
    assert not (tmp_path / "advio-99.zip.part").exists()
    record = json.loads(datasets.record_path(archive).read_text())
    assert record["sha256"] == archive.sha256
    assert record["files"]["advio-99/iphone/arkit.csv"] == len("time,x,y,z\n")
    assert datasets.is_complete(archive) is True


def test_fetch_without_member_filter_extracts_everything(tmp_path: Path, monkeypatch):
    payload = _zip_bytes()
    archive = _archive(tmp_path, payload, marker="advio-99/iphone/video.mov", members=())
    _serve(monkeypatch, {archive.url: payload})
    _free(monkeypatch, 100.0)

    datasets.fetch(archive)

    assert (tmp_path / "advio-99/iphone/video.mov").stat().st_size == 4096
    assert datasets.is_complete(archive) is True


def test_fetch_refuses_to_start_without_the_free_space_reserve(tmp_path: Path, monkeypatch):
    payload = _zip_bytes()
    archive = _archive(tmp_path, payload)
    asked = _serve(monkeypatch, {archive.url: payload})
    # The reserve sits on top of the 2.5x unpack margin; 0.001 GB keeps both cases off the
    # exact float boundary.
    need_gb = 2.5 * archive.size / 1e9

    _free(monkeypatch, datasets.MIN_FREE_GB + need_gb + 0.001)
    datasets.fetch(archive)
    assert datasets.is_complete(archive) is True

    other = _archive(tmp_path / "under", payload)
    _free(monkeypatch, datasets.MIN_FREE_GB + need_gb - 0.001)
    with pytest.raises(SystemExit, match="GB free"):
        datasets.fetch(other)
    assert asked == [archive.url]  # the refusal happened before any download
    assert not (other.dest / "advio-99.zip.part").exists()
    assert not datasets.record_path(other).exists()


def test_fetch_rejects_a_wrong_size_or_checksum(tmp_path: Path, monkeypatch):
    payload = _zip_bytes()
    _serve(
        monkeypatch,
        {"https://example.test/a.zip": payload, "https://example.test/b.zip": payload},
    )
    _free(monkeypatch, 100.0)

    wrong_size = _archive(tmp_path / "size", payload, url="https://example.test/a.zip", size=1)
    with pytest.raises(SystemExit, match="bytes"):
        datasets.fetch(wrong_size)

    wrong_sha = _archive(
        tmp_path / "sha", payload, url="https://example.test/b.zip", sha256="0" * 64
    )
    with pytest.raises(SystemExit, match="sha256"):
        datasets.fetch(wrong_sha)

    for dest in (tmp_path / "size", tmp_path / "sha"):
        assert not (dest / "advio-99.zip.part").exists()
        assert not (dest / "advio-99").exists()  # nothing was unpacked
        assert not (dest / ".complete").exists()  # and nothing was recorded complete


def test_fetch_cleans_up_when_the_download_fails_midstream(tmp_path: Path, monkeypatch):
    payload = _zip_bytes()
    archive = _archive(tmp_path, payload)

    def dying_urlopen(url, timeout=None):
        return _DyingResponse(payload)

    monkeypatch.setattr(datasets.urllib.request, "urlopen", dying_urlopen)
    _free(monkeypatch, 100.0)

    with pytest.raises(OSError, match="mid-download"):
        datasets.fetch(archive)

    assert not (tmp_path / "advio-99.zip.part").exists()
    assert not datasets.record_path(archive).exists()
    assert not datasets.is_complete(archive)


def test_fetch_fails_without_a_record_when_a_member_is_missing(tmp_path: Path, monkeypatch):
    payload = _zip_bytes(with_arkit=False)  # the marker and a requested member
    archive = _archive(tmp_path, payload)
    _serve(monkeypatch, {archive.url: payload})
    _free(monkeypatch, 100.0)

    with pytest.raises(SystemExit, match="unpacked without"):
        datasets.fetch(archive)

    assert not datasets.record_path(archive).exists()
    assert not (tmp_path / "advio-99.zip.part").exists()
    assert not datasets.is_complete(archive)


def test_a_failed_fetch_leaves_an_existing_record_alone(tmp_path: Path, monkeypatch):
    payload = _zip_bytes()
    good = _archive(tmp_path, payload)
    _serve(monkeypatch, {good.url: payload})
    _free(monkeypatch, 100.0)
    datasets.fetch(good)
    assert datasets.is_complete(good) is True

    # A redownload whose pinned checksum does not match must not destroy the recorded data.
    bad = _archive(tmp_path, payload, sha256="0" * 64)
    with pytest.raises(SystemExit, match="sha256"):
        datasets.fetch(bad)
    assert datasets.is_complete(good) is True


# --- datasets.py: the completion record -------------------------------------------------------


def test_is_complete_rechecks_the_recorded_files(tmp_path: Path, monkeypatch):
    payload = _zip_bytes()
    archive = _archive(tmp_path, payload)
    _serve(monkeypatch, {archive.url: payload})
    _free(monkeypatch, 100.0)

    assert datasets.is_complete(archive) is False  # no record yet
    datasets.fetch(archive)
    assert datasets.is_complete(archive) is True

    arkit = tmp_path / "advio-99/iphone/arkit.csv"
    gt = tmp_path / "advio-99/ground-truth/gt.csv"

    gt.unlink()
    assert datasets.is_complete(archive) is False  # a recorded file went missing
    gt.write_text("time,x,y,z\n")

    arkit.write_text("time,x,y,z\n\n")  # one byte longer than recorded
    assert datasets.is_complete(archive) is False  # a truncated or edited copy is caught
    arkit.write_text("time,x,y,z\n")
    assert datasets.is_complete(archive) is True

    datasets.record_path(archive).write_text(json.dumps({"sha256": archive.sha256, "files": {}}))
    assert datasets.is_complete(archive) is False  # an empty record is not a complete one


def test_is_complete_treats_a_corrupt_record_as_not_complete(tmp_path: Path):
    """A truncated or non-object record must read as not complete (the next run redownloads),
    not crash every later run on the recovery path."""
    archive = _archive(tmp_path, b"unused")
    record = datasets.record_path(archive)
    record.parent.mkdir(parents=True)

    record.write_text('{"sha256": "abc')  # truncated mid-write
    assert datasets.is_complete(archive) is False
    record.write_text("null")  # valid JSON, but not a record object
    assert datasets.is_complete(archive) is False
    record.write_text('{"no": "files"}')  # an object without the pinned fields
    assert datasets.is_complete(archive) is False


def test_sha256_of_matches_hashlib_and_streams_in_chunks(tmp_path: Path):
    p = tmp_path / "blob.bin"
    p.write_bytes(b"")
    assert datasets.sha256_of(p) == hashlib.sha256(b"").hexdigest()
    blob = bytes(range(256)) * (2 * (1 << 20) // 256 + 1)  # spans several 1 MiB reads
    p.write_bytes(blob)
    assert datasets.sha256_of(p) == hashlib.sha256(blob).hexdigest()


def test_archive_name_is_the_url_tail():
    a = datasets.Archive(
        url="https://example.test/a/b/advio-99.zip",
        size=1,
        sha256="0" * 64,
        dest=Path("."),
        marker="m",
    )
    assert a.name == "advio-99.zip"


def test_main_skips_recorded_complete_and_force_refetches(monkeypatch):
    fetched, checked = [], []
    monkeypatch.setattr(datasets, "fetch", lambda a: fetched.append(a.name))
    monkeypatch.setattr(datasets, "is_complete", lambda a: checked.append(a.name) or True)

    monkeypatch.setattr(sys, "argv", ["datasets", "advio"])
    datasets.main()
    assert fetched == [] and len(checked) == len(datasets.ADVIO) == 4

    monkeypatch.setattr(sys, "argv", ["datasets", "advio", "--force"])
    datasets.main()
    assert fetched == [a.name for a in datasets.ADVIO]

    monkeypatch.setattr(sys, "argv", ["datasets", "eth3d", "--force"])
    fetched.clear()
    datasets.main()
    assert fetched == [a.name for a in datasets.ETH3D]


def test_pinned_datasets_match_the_methods_table():
    """The Datasets table in METHODS.md pins every size and sha256; a typo there would only
    surface after a failed download, so the values are regression-tested against it."""
    advio = [
        (195538352, "be21154394df09d6ecebe1894062f290bb53d74a5c6ccbca4b3a69f91ee2c8a2"),
        (209791503, "fb8a1cf3f645bbd9ea848e66924e83ae75b5cd18d632986e5629af1eecb58570"),
        (254664089, "96c7212fc9cb610a88ba9fa62ac2581f0cdae49e80007888698ee2cf5ebd56f4"),
        (134340011, "726d9b80d30036a8585f6cecaae385236b689cc4b8b19be6362c6df223825dc1"),
    ]
    for a, (size, sha) in zip(datasets.ADVIO, advio, strict=True):
        assert (a.size, a.sha256) == (size, sha)
        assert a.dest == ADVIO_DIR
    eth3d = [
        (
            "facade_dslr_undistorted.7z",
            1252088400,
            "046e577388db0633eeb2d8d72da6a2de53857434b4a7a98e4c97485795f9ce82",
        ),
        (
            "facade_dslr_scan_eval.7z",
            176517224,
            "d686462d417fbea010d0021f918bec8b881444929ec06e3bd4e8a5230ebf59a8",
        ),
        (
            "facade_dslr_occlusion.7z",
            144292883,
            "26a9d2076ba16b7f8e24311579c47b0b7b826b180906ced20fa1cba8f3dd130d",
        ),
        (
            "electro_dslr_undistorted.7z",
            514345623,
            "0d2bc31fec0032b8fb20703abba8e45ef7a395c13d2d3476adc1f4dd4ffd7d8b",
        ),
        (
            "electro_dslr_scan_eval.7z",
            250703286,
            "5ca10a73e7da0e3c511e255bba5656cca4c372dc00c1417c97b75228a5bb3bac",
        ),
        (
            "electro_dslr_occlusion.7z",
            47214879,
            "cc25a22de9fc27251a5178454c24785671a2b2c199756a0d03297413290830b3",
        ),
    ]
    for a, (name, size, sha) in zip(datasets.ETH3D, eth3d, strict=True):
        assert (a.name, a.size, a.sha256) == (name, size, sha)
        assert a.url == f"https://www.eth3d.net/data/{name}"
        assert a.dest == ETH3D_DIR
        assert a.members == ()  # 7z archives unpack everything

    for a, n in zip(datasets.ADVIO, range(20, 24), strict=True):
        base = f"advio-{n:02d}/"
        assert a.url == f"https://zenodo.org/record/1476931/files/advio-{n:02d}.zip"
        assert a.marker == base + "iphone/arkit.csv"
        assert all(m.startswith(base) for m in a.members)
        assert base + "ground-truth/" in a.members and base + "pixel/arcore.csv" in a.members
    # Sequence 20 keeps the whole iphone/ folder (its video is cut into the replay session);
    # the others take three csv files and drop the video.
    assert "advio-20/iphone/" in datasets.ADVIO[0].members
    for a, n in zip(datasets.ADVIO[1:], range(21, 24), strict=True):
        base = f"advio-{n:02d}/"
        assert set(a.members) == {
            base + "iphone/arkit.csv",
            base + "iphone/frames.csv",
            base + "iphone/platform-locations.csv",
            base + "ground-truth/",
            base + "pixel/arcore.csv",
        }
    assert datasets.MIN_FREE_GB == 6.0
    assert datasets.DOWNLOAD_TIMEOUT_S == 60


# --- field_dryrun.py: quantities and tape text -------------------------------------------------


def test_tape_text_reads_to_the_nearest_sixteenth():
    tt = field_dryrun.tape_text
    assert tt(0.0) == "0 0"
    assert tt(field_dryrun.FT) == "1 0"  # exactly one foot
    assert tt(0.0254) == "0 1"  # exactly one inch
    assert tt(30.0) == "98 5 1/8"  # 18898 sixteenths
    assert tt(4.0) == "13 1 1/2"  # mark F's distance from mark A
    assert tt(1.5) == "4 11 1/16"  # the scale reference


def test_tape_readings_round_trip_through_the_survey_tape_format():
    """Every tape reading must survive field.py's parse_tape within half a sixteenth."""
    P = {pid: X for pid, (X, _) in field_dryrun.POINTS.items()}
    for _, a, b, key, sid in field_dryrun.MEASUREMENTS:
        true = field_dryrun.value(P[a], P.get(b), key)
        back = parse_tape(field_dryrun.tape_text(true)) * field_dryrun.FT
        assert abs(back - true) <= 0.5 / 16 / 12 * field_dryrun.FT + 1e-12, sid


def test_value_measures_each_quantity_in_the_scene_convention():
    P = {pid: X for pid, (X, _) in field_dryrun.POINTS.items()}
    v = field_dryrun.value
    assert v(P["P2"], P["P3"], "alongWall") == 9.144  # the 30 ft span along +x
    assert v(P["P3"], P["P2"], "alongWall") == 9.144  # direction-independent
    assert v(P["P5"], P["P6"], "straight") == 1.5
    np.testing.assert_allclose(v(P["P1"], P["P13"], "straight"), np.hypot(0.02, 0.01))
    assert v(P["P9"], None, "heightAboveGround") == 0.9  # a wall target leaves b unused
    assert v(P["P11"], None, "gapToWall") == 1.2  # the z offset stands in for the gap
    with pytest.raises(ValueError, match="nonsense"):
        v(P["P2"], P["P3"], "nonsense")


# --- field_dryrun.py: projection and rendering -------------------------------------------------


def test_pixel_projects_hand_computed_scene_points():
    pixel = field_dryrun.pixel
    uv, depth = pixel(np.array([4.0, 0.0, 2.0]), field_dryrun.CAMERAS[5])  # P1, 4 m ahead
    np.testing.assert_allclose(uv, [320.0, 427.5])
    assert depth == 4.0
    uv, depth = pixel(field_dryrun.POINTS["P2"][0], field_dryrun.CAMERAS[1])  # the wall corner
    np.testing.assert_allclose(uv, [320.0, 365.0])
    assert depth == 6.0
    uv, _ = pixel(field_dryrun.POINTS["P5"][0], field_dryrun.CAMERAS[3])  # 1 m right, 0.1 down
    np.testing.assert_allclose(uv, [320 + 500 / 6, 240 + 50.0 / 6])


def test_render_wall_and_ground_follow_the_projection():
    """Camera (2, 1.5, 6) looks along -z. P5's tap (wall, y = 1.398 at the sampled pixel centre)
    maps to texture[127, 160]; P11's (ground, hit at z = 1.208) to texture[524, 179]; the wall
    base crosses the image between rows 364 (y = +0.006) and 365 (y = -0.006)."""
    cam = field_dryrun.CAMERAS[3]
    texture = np.full((600, 600), 100.0, dtype=np.float32)
    texture[127, 160] = 200.0
    texture[524, 179] = 200.0

    img = field_dryrun.render(cam, texture)

    assert img.shape == (field_dryrun.H, field_dryrun.W)
    assert img.dtype == np.uint8
    assert img[248, 403] == 200  # P5 (wall, pixel [403.3, 248.3]): undimmed
    assert img[396, 528] == 140  # P11 (ground, pixel [528.3, 396.2]): dimmed to 200 * 0.7
    assert img[364, 320] == 100  # just above the wall base line
    assert img[365, 320] == 70  # just below it: ground, dimmed to 100 * 0.7
    assert np.array_equal(img, field_dryrun.render(cam, texture))  # deterministic


# --- field_dryrun.py: the synthetic session ----------------------------------------------------


def test_session_scene_and_counts_follow_the_sheet():
    doc = field_dryrun.session()
    assert doc["format"] == "measure-lab-session" and doc["formatVersion"] == 2
    assert doc["units"] == {"length": "meters"}
    assert doc["session"]["id"] == "dryrun-session"
    assert doc["refusals"] == []
    assert [kf["id"] for kf in doc["keyframes"]] == [f"k{k:05d}" for k in range(1, 13)]
    stamps = [kf["timestamp"] for kf in doc["keyframes"]]
    assert stamps == sorted(stamps) and stamps[0] > doc["session"]["startedAtUptime"]
    assert len(doc["points"]) == len(field_dryrun.POINTS) == 17
    assert len(doc["taps"]) == 18  # every point one tap, P12 from two views
    assert [m["id"] for m in doc["measurements"]] == [f"M{n}" for n in range(1, 11)]

    tap_kfs = {t["id"]: t["keyframe"] for t in doc["taps"]}
    tap_ids = set(tap_kfs)
    assert all(set(p["taps"]) <= tap_ids for p in doc["points"])
    two_view = next(p for p in doc["points"] if p["id"] == "P12")
    assert [tap_kfs[tid] for tid in two_view["taps"]] == ["k00010", "k00012"]  # 2 m sideways
    assert all(len(p["taps"]) == 1 for p in doc["points"] if p["id"] != "P12")

    wall = doc["walls"][0]
    assert wall["id"] == "W1" and wall["contacts"] == ["P2", "P3"]
    assert wall["validations"] == [{"point": "P4", "passes": True}]
    assert np.allclose(wall["cameraPosition"], field_dryrun.CAMERAS[5] * field_dryrun.AR_SCALE)


def test_every_tap_lands_inside_the_frame():
    for t in field_dryrun.session()["taps"]:
        u, v = t["pixel"]
        assert 0 <= u <= field_dryrun.W and 0 <= v <= field_dryrun.H, t["id"]


def test_every_session_value_is_the_true_scene_shrunk_by_ar_scale():
    s = field_dryrun.AR_SCALE
    assert s == 0.985  # the planted 1.5% short reading
    doc = field_dryrun.session()

    for p in doc["points"]:
        true, _ = field_dryrun.POINTS[p["id"]]
        np.testing.assert_allclose(p["position"], np.asarray(true) * s)

    for kf, cam in zip(doc["keyframes"], field_dryrun.CAMERAS, strict=True):
        pose = np.asarray(kf["pose"], dtype=np.float64).reshape(4, 4).T  # stored column-major
        np.testing.assert_allclose(pose[:3, :3], np.eye(3))  # upright, looking along -z
        np.testing.assert_allclose(pose[:3, 3], cam * s)
        assert (kf["w"], kf["h"]) == (field_dryrun.W, field_dryrun.H)
        assert kf["intrinsics"] == [
            field_dryrun.F,
            field_dryrun.F,
            field_dryrun.W / 2,
            field_dryrun.H / 2,
        ]

    P = {pid: X for pid, (X, _) in field_dryrun.POINTS.items()}
    for m, (mid, a, b, key, _) in zip(doc["measurements"], field_dryrun.MEASUREMENTS, strict=True):
        assert m["id"] == mid and m["from"] == a and m["to"] == b
        assert m["values"] == {key: field_dryrun.value(P[a], P.get(b), key) * s}
        assert m["compared"] == key
        assert m["referenceWall"] == ("W1" if key == "alongWall" else None)


def test_session_is_deterministic():
    assert json.dumps(field_dryrun.session()) == json.dumps(field_dryrun.session())


def test_build_writes_the_zip_survey_and_map(tmp_path: Path):
    field_dryrun.build(tmp_path)

    folder = tmp_path / "dryrun-session"
    assert sorted(p.name for p in (folder / "keyframes").iterdir()) == [
        f"k{k:05d}.jpg" for k in range(1, 13)
    ]
    doc = json.loads((folder / "session.json").read_text())
    assert doc["session"]["id"] == "dryrun-session"

    archive = tmp_path / "dryrun-session.zip"
    with zipfile.ZipFile(archive) as z:
        assert "dryrun-session/session.json" in z.namelist()
        assert z.read("dryrun-session/session.json") == (folder / "session.json").read_bytes()
        assert sum(1 for n in z.namelist() if n.endswith(".jpg")) == 12

    survey = json.loads((tmp_path / "survey.json").read_text())
    values = {m["id"]: m["value_ft"] for m in survey["measurements"]}
    assert not any(isinstance(v, str) and "FILL" in v for v in values.values())
    assert values["span-30-ab"] == "30 0"  # the 30 ft span, taped to the nearest sixteenth
    assert values["scale-ref"] == "4 11 1/16"  # 1.5 m
    assert values["sill-height"] == "2 11 7/16"  # 0.9 m
    assert values["return-gap"] == 0  # the true gap is 0, so the template's 0 stays
    assert (tmp_path / "map.json").read_text() == (
        field_dryrun.FIELD_KIT / "map.template.json"
    ).read_text()


def test_the_dryrun_session_passes_the_field_kit_reader(tmp_path: Path):
    field_dryrun.build(tmp_path)
    doc = load_session(tmp_path / "dryrun-session")
    assert doc["format"] == "measure-lab-session" and doc["formatVersion"] == 2
