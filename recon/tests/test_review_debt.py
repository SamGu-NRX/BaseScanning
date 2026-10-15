"""Regressions for review findings that survived the reconstruction handoff."""

import fcntl
import importlib.util
import io
import json
import struct
import zipfile
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest
from test_worker import (
    NO_COVERAGE,
    WALL,
    _all_visible_setup,
    _capture_named_scan,
    _fake_moge,
    _lidar_frames,
    _slab_volume,
)

from recon import capture as cap
from recon import coverage, depth, geometry, pipeline, scene
from recon.coverage import CELL_M


def test_replacing_wall_drops_its_old_measurements_and_keeps_other_walls():
    old_facing = {"wall_id": "w", "span_ft": [-1, 1], "depth_ft": 20}
    old_overhead = {"wall_id": "w", "span_ft": [-1, 1], "clearance_ft": 10}
    other = {"wall_id": "v", "span_ft": [1, 2], "depth_ft": 3}
    prior = {
        "meter": {"wall_id": "w", "pos": [0, 5, 0]},
        "walls": [{"id": "w", "baseline": [[-1, 0], [1, 0]]}],
        "facing": [old_facing, other],
        "overheads": [old_overhead],
    }
    capture = cap.Capture("scan-bundle", Path("b"), [], 0.0, WALL.meter, None, prior)
    rotated = replace(WALL, along=np.array([np.cos(0.1), 0, np.sin(0.1)]))
    doc = scene.build(capture, rotated, NO_COVERAGE, "mesh", 0.5)
    assert doc["facing"] == [other] and doc["overheads"] == []
    assert prior["facing"] == [old_facing, other]


@pytest.mark.parametrize("n", [500, 501])
def test_export_observations_refuses_contract_overflow_without_slicing(monkeypatch, n):
    entries = [{"band": "wall", "span_ft": [i, i + 0.5]} for i in range(n)]
    monkeypatch.setattr(scene, "observed", lambda _: entries)
    capture = cap.Capture("measure-lab", Path("b"), [], 0.0, None, None)
    if n == 501:
        with pytest.raises(ValueError, match=r"501 entries.*limit is 500"):
            scene.build(capture, WALL, NO_COVERAGE, "mesh", 0.5)
    else:
        assert (
            scene.build(capture, WALL, NO_COVERAGE, "mesh", 0.5)["coverage"]["observed"] == entries
        )


def test_coverage_exports_only_cells_inside_fitted_extent(monkeypatch):
    wall, frames, depths, vol, surface, _ = _all_visible_setup()
    monkeypatch.setattr(coverage, "seen_by", lambda points, *_: np.ones((len(points), 2), bool))
    # This test isolates cell bounds; the two-view wrapper has separate real-depth regressions.
    monkeypatch.setattr(coverage, "SeenFromTwo", lambda vol, frames, depths: vol)
    monkeypatch.setattr(
        coverage, "free_space", lambda wall, vol, cells, ground=None: (np.zeros(len(cells)),) * 4
    )
    wall = replace(wall, s_range=(0.9 * CELL_M, 2.1 * CELL_M))
    cov = coverage.compute(wall, frames, depths, vol, surface)
    np.testing.assert_allclose(cov.cells, [CELL_M])
    entries = coverage.observed(cov)
    assert all(0.5 <= e["span_ft"][0] < e["span_ft"][1] <= 1.0 for e in entries)
    cov = coverage.compute(replace(wall, s_range=(0.01, 0.02)), frames, depths, vol, surface)
    assert cov.cells.size == 0 and coverage.observed(cov) == []


def test_ground_coverage_uses_local_slope_height_and_normal(monkeypatch):
    setup = _all_visible_setup()
    ground = geometry.Ground(np.zeros(3), np.array([0.0, 1, -0.2]), 0.0, 10)
    sampled = []

    def see(points, normal, *_):
        if normal is ground.normal:
            sampled.append(points.copy())
            seen = np.abs(points[:, 1] - 0.2 * points[:, 2]) < 1e-9
        else:
            seen = np.ones(len(points), bool)
        return np.repeat(seen[:, None], 2, axis=1)

    monkeypatch.setattr(coverage, "seen_by", see)
    _, reach, _ = coverage.wall_and_ground(*setup, ground)
    assert sampled and np.all(sampled[0][:, 1] > 0)
    np.testing.assert_allclose(reach, coverage.GROUND_MAX_M)


def test_depth_test_sees_sloped_ground_and_rejects_horizontal_datum():
    # Two cameras look vertically down at y = 0.2*z. Pixel-ray intersections supply metric depth.
    from test_worker import DOWN, GROUND_FRAMES, H, K, W

    frames = GROUND_FRAMES
    v, _u = np.mgrid[:H, :W]
    dz_per_depth = (v + 0.5 - K[3]) / K[1]
    d = 1.5 / (1 + 0.2 * dz_per_depth)
    depths = {
        f.id: depth.Depth(d.astype(np.float32), K, np.zeros((H, W, 3), np.uint8), "lidar")
        for f in frames
    }
    assert np.array_equal(frames[0].cam_to_world[:3, :3], DOWN)

    class EmptyRays:
        voxel = 0.05

        def blocked(self, start, ends, stop_short):
            return np.zeros(len(ends), bool)

    points = np.array([[0.0, 0.12, 0.6], [0.0, -0.12, -0.6]])
    normal = np.array([0.0, 1, -0.2]) / np.sqrt(1.04)
    assert coverage.seen_by(points, normal, frames, depths, EmptyRays()).all()
    horizontal = points.copy()
    horizontal[:, 1] = 0.0
    assert not coverage.seen_by(horizontal, normal, frames, depths, EmptyRays()).any()


def test_reconstruction_passes_fitted_ground_to_coverage(monkeypatch):
    wall, frames, depths, _, surface, _ = _all_visible_setup()
    capture = cap.Capture("measure-lab", Path("b"), frames, 0.0, wall.meter, None)
    ground = geometry.Ground(np.zeros(3), np.array([0.0, 1, -0.2]), 0.0, 10)
    volume = _slab_volume([], 7)
    monkeypatch.setattr(pipeline.fusion, "integrate", lambda *_: volume)
    monkeypatch.setattr(pipeline.fusion, "mesh", lambda _: surface)
    monkeypatch.setattr(pipeline.geometry, "fit_ground", lambda *_: ground)
    monkeypatch.setattr(pipeline.geometry, "wall_lines", lambda *_: ["line"])
    monkeypatch.setattr(pipeline.geometry, "choose_wall", lambda *_: "line")
    monkeypatch.setattr(pipeline.geometry, "wall_frame", lambda *_: wall)

    def compute(*args):
        assert args[-1] is ground
        return NO_COVERAGE

    monkeypatch.setattr(pipeline.coverage, "compute", compute)
    assert pipeline.reconstruct(capture, depths, {"source": "lidar"})["coverage"] is NO_COVERAGE


@pytest.mark.parametrize("band", ["facing", "overhead"])
def test_one_voxel_obstruction_ends_clearance(band):
    wall, *_ = _all_visible_setup()
    vol = _slab_volume([(-0.3, 0.0)], 7.0)
    if band == "facing":
        # One 5 cm row at y=1 m, one step at z=1 m: under 20% of the facing slab.
        vol.tsdf[:, 20, 30] = -1
    else:
        # One outward voxel at z=0.3 m, one height step at y=1.5 m.
        vol.tsdf[:, 30, 16] = -1
    fg, fc, og, oc = coverage.free_space(wall, vol, np.array([0.0]))
    if band == "facing":
        assert np.isfinite(fg[0]) and 0.8 < fg[0] < 1.0 and fc[0] < 1.0
    else:
        assert np.isfinite(og[0]) and 1.3 < og[0] < 1.5 and oc[0] < 1.5


def test_lidar_without_confidence_is_refused_before_depth_read(tmp_path):
    frames = _lidar_frames(tmp_path, {"a": True})
    f = replace(frames[0], lidar=replace(frames[0].lidar, confidence=None))
    f.lidar.file.unlink()
    with pytest.raises(ValueError, match="requires its confidence map"):
        depth.lidar(f)


@pytest.mark.parametrize("orientation", [3, 6])
def test_keyframe_jpeg_exif_does_not_rotate_sensor_pixels(tmp_path, orientation):
    image = np.zeros((16, 24, 3), np.uint8)
    image[:8, :12] = 255
    ok, jpeg = cv2.imencode(".jpg", image)
    assert ok
    tiff = struct.pack("<2sHIH", b"II", 42, 8, 1)
    tiff += struct.pack("<HHIHHI", 274, 3, 1, orientation, 0, 0)
    exif = b"Exif\0\0" + tiff
    raw = jpeg.tobytes()
    tagged = raw[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + raw[2:]
    path = tmp_path / "k.jpg"
    path.write_bytes(tagged)
    frame = cap.Frame("k", path, 24, 16, np.array([24.0, 24, 12, 8]), np.eye(4))
    sensor = cv2.imdecode(jpeg, cv2.IMREAD_COLOR)
    np.testing.assert_array_equal(depth._image(frame), sensor)
    default = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert default.shape != sensor.shape or not np.array_equal(default, sensor)


def corrupt_compressed_npz() -> bytes:
    archive = io.BytesIO()
    np.savez_compressed(archive, depth=np.ones((2, 2), np.float32))
    raw = bytearray(archive.getvalue())
    name_bytes, extra_bytes = struct.unpack_from("<HH", raw, 26)
    # The reserved deflate block type fails during decompression rather than a ZIP CRC check.
    raw[30 + name_bytes + extra_bytes] = 7
    return bytes(raw)


def unsupported_compression_npz() -> bytes:
    archive = io.BytesIO()
    np.savez(archive, depth=np.ones((2, 2), np.float32))
    raw = bytearray(archive.getvalue())
    central = raw.index(b"PK\x01\x02")
    # Both ZIP headers agree on an unsupported method, so the failure occurs on member read.
    struct.pack_into("<H", raw, 8, 99)
    struct.pack_into("<H", raw, central + 10, 99)
    return bytes(raw)


def encrypted_flag_npz() -> bytes:
    archive = io.BytesIO()
    np.savez(archive, depth=np.ones((2, 2), np.float32))
    raw = bytearray(archive.getvalue())
    central = raw.index(b"PK\x01\x02")
    # Corrupt only the directory's encryption bit. zipfile uses that bit when
    # NumPy reads depth.npy, so a normal cache read now asks for a password.
    flags = struct.unpack_from("<H", raw, central + 8)[0]
    struct.pack_into("<H", raw, central + 8, flags | 1)
    return bytes(raw)


@pytest.mark.parametrize(
    "bad",
    [
        b"PK\x03\x04truncated",
        b"not an npz",
        corrupt_compressed_npz(),
        unsupported_compression_npz(),
        encrypted_flag_npz(),
    ],
    ids=[
        "truncated_zip",
        "not_npz",
        "damaged_deflate",
        "unsupported_compression",
        "encrypted_flag",
    ],
)
def test_corrupt_moge_cache_is_recomputed(tmp_path, monkeypatch, bad):
    capture = _capture_named_scan(tmp_path, 10)
    work = tmp_path / "work"
    cache = depth.moge_cache(capture, work)
    (cache / "k.moge2.npz").write_bytes(bad)
    calls = []
    monkeypatch.setattr(depth.subprocess, "run", _fake_moge(calls))
    assert np.allclose(depth.moge(capture, work)["k"].depth, 10)
    assert calls == [1]
    assert np.allclose(depth.moge(capture, work)["k"].depth, 10)
    assert calls == [1]


def test_moge_holds_exclusive_cache_lock_through_inference(tmp_path, monkeypatch):
    capture = _capture_named_scan(tmp_path, 10)
    work = tmp_path / "work"
    fake = _fake_moge([])

    def run(*args, **kwargs):
        with (work / ".moge.lock").open("a") as contender, pytest.raises(BlockingIOError):
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fake(*args, **kwargs)

    monkeypatch.setattr(depth.subprocess, "run", run)
    depth.moge(capture, work)
    with (work / ".moge.lock").open("a") as contender:
        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_failed_rerun_clears_all_old_generated_outputs(tmp_path, monkeypatch):
    names = [
        "model.glb",
        "geometry.json",
        "scene.json",
        "coverage.json",
        "report.md",
        "result.json",
        "site-plan.svg",
    ]
    for name in names:
        (tmp_path / name).write_bytes(b"old output")
    (tmp_path / "keep.txt").write_text("user file")

    def refuse(*_):
        raise ValueError("invalid capture")

    monkeypatch.setattr(pipeline.cap, "load", refuse)
    with pytest.raises(ValueError, match="invalid capture"):
        pipeline.run(Path("bad"), tmp_path, tmp_path / "work", "auto", None, False)
    assert not any((tmp_path / name).exists() for name in names)
    assert (tmp_path / "keep.txt").read_text() == "user file"


@pytest.mark.parametrize("kind", ["folder", "file"])
def test_output_cleanup_refuses_input_overlap_before_deleting(tmp_path, kind):
    name = "scene.json" if kind == "folder" else "model.glb"
    source = tmp_path / name
    source.write_bytes(b"capture input")
    bundle = tmp_path if kind == "folder" else source
    with pytest.raises(ValueError, match="overlaps capture input"):
        pipeline.run(bundle, tmp_path, tmp_path / "work", "auto", None, False)
    assert source.read_bytes() == b"capture input"


def test_atomic_model_depth_write_keeps_old_cache_on_failed_write(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("moge_depth", depth.MOGE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    monkeypatch.setenv("TORCH_HOME", str(tmp_path))
    spec.loader.exec_module(module)
    target = tmp_path / "k.moge2.npz"
    np.savez(target, depth=np.ones((2, 2), np.float32))
    original = target.read_bytes()
    save = np.savez

    def interrupted(file, **_):
        file.write(b"partial archive")
        raise OSError("interrupted write")

    monkeypatch.setattr(np, "savez", interrupted)
    with pytest.raises(OSError, match="interrupted write"):
        module.write_depth(target, np.full((2, 2), 2.0))
    assert target.read_bytes() == original and list(tmp_path.iterdir()) == [target]
    monkeypatch.setattr(np, "savez", save)
    module.write_depth(target, np.full((2, 2), 2.0))
    with np.load(target) as archive:
        np.testing.assert_array_equal(archive["depth"], np.full((2, 2), 2.0))


@pytest.mark.parametrize("kind", ["file", "expanded", "entries"])
def test_zip_resource_limits_refuse_before_extraction(tmp_path, monkeypatch, kind):
    path = tmp_path / "bundle.zip"
    if kind == "file":
        path.write_bytes(b"x" * 401)
        monkeypatch.setattr(cap, "MAX_BUNDLE_BYTES", 400)
    else:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for i in range(3 if kind == "entries" else 1):
                z.writestr(str(i), b"x" * 1000)
        if kind == "entries":
            monkeypatch.setattr(cap, "MAX_BUNDLE_ENTRIES", 2)
        else:
            assert path.stat().st_size < 400
            monkeypatch.setattr(cap, "MAX_BUNDLE_BYTES", 400)
    with pytest.raises(ValueError, match=r"exceeds|entries, limit"):
        cap.unpack(path, tmp_path / "work")
    assert not (tmp_path / "work").exists()


def test_zip_digest_streams_and_completed_bundle_is_reused(tmp_path, monkeypatch):
    path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("scan/scene.json", json.dumps({"schema_version": "1.0"}))
    monkeypatch.setattr(Path, "read_bytes", lambda _: pytest.fail("whole ZIP read into memory"))
    out = cap.unpack(path, tmp_path / "work")
    assert json.loads((out / "scan/scene.json").read_text()) == {"schema_version": "1.0"}
    assert cap.unpack(path, tmp_path / "work") == out


def test_zip_entry_count_is_checked_before_metadata_allocation(tmp_path, monkeypatch):
    path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(path, "w") as z:
        for i in range(3):
            z.writestr(str(i), b"x")
    # A false EOCD count must not bypass the streamed central-record count.
    data = bytearray(path.read_bytes())
    pos = data.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", data, pos + 8, 1, 1)
    path.write_bytes(data)
    monkeypatch.setattr(cap, "MAX_BUNDLE_ENTRIES", 2)
    monkeypatch.setattr(zipfile, "ZipFile", lambda *_: pytest.fail("allocated ZIP metadata"))
    with pytest.raises(ValueError, match="3 entries, limit 2"):
        cap.unpack(path, tmp_path / "work")


@pytest.mark.parametrize("truncated", [False, True])
def test_zip_comment_cannot_select_a_different_footer(tmp_path, monkeypatch, truncated):
    path = tmp_path / "bundle.zip"
    fake = struct.pack("<4s4H2IH", b"PK\x05\x06", 0, 0, 4, 4, 184, 0, 1)
    comment = b"PK\x05\x06x" if truncated else fake + b"tail"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("one", b"one")
        z.comment = comment
    # The private stdlib parser reads only the bounded footer, without constructing ZipInfo
    # objects. It chooses the later signature despite its claimed one-byte comment ending early.
    if not truncated:
        with path.open("rb") as f:
            parsed = zipfile._EndRecData(f)
        assert parsed[zipfile._ECD_ENTRIES_TOTAL] == 4
        assert parsed[zipfile._ECD_LOCATION] == path.stat().st_size - len(comment)
    monkeypatch.setattr(zipfile, "ZipFile", lambda *_: pytest.fail("allocated ZIP metadata"))
    with pytest.raises(ValueError, match=r"ambiguous ZIP end record|truncated ZIP end record"):
        cap.unpack(path, tmp_path / "work")
    assert not (tmp_path / "work").exists()


def test_zip_with_an_ordinary_comment_extracts(tmp_path):
    path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("scan/scene.json", '{"schema_version": "1.0"}')
        z.comment = b"ordinary scan bundle comment"
    out = cap.unpack(path, tmp_path / "work")
    assert json.loads((out / "scan/scene.json").read_text()) == {"schema_version": "1.0"}


def test_zip64_locator_cannot_override_a_checked_ordinary_footer(tmp_path, monkeypatch):
    path = tmp_path / "bundle.zip"
    info = zipfile.ZipInfo("one")
    # A locator in the final central record's file comment is immediately before the footer,
    # where stdlib probes for ZIP64 even without sentinel values in the ordinary footer.
    info.comment = struct.pack("<4sIQI", b"PK\x06\x07", 0, 0, 1)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(info, b"one")
    monkeypatch.setattr(zipfile, "ZipFile", lambda *_: pytest.fail("allocated ZIP metadata"))
    with pytest.raises(ValueError, match="ZIP64 archive metadata is not supported"):
        cap.unpack(path, tmp_path / "work")


def test_failed_zip_extraction_does_not_publish_partial_bundle(tmp_path, monkeypatch):
    path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("one", b"one")
        z.writestr("two", b"two")
    real = zipfile.ZipFile.open

    def open_member(z, info, *args, **kwargs):
        if info.filename == "two":
            raise zipfile.BadZipFile("CRC failure")
        return real(z, info, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", open_member)
    work = tmp_path / "work"
    with pytest.raises(zipfile.BadZipFile, match="CRC failure"):
        cap.unpack(path, work)
    assert list((work / "bundles").iterdir()) == []
