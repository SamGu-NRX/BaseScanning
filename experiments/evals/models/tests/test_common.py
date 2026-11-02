"""The input <-> network image geometry has one right answer, so it is tested on synthetic cameras."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from models.common import (
    cover_crop_resample,
    depth_to_input,
    identity_resample,
    intrinsics_to_input,
    intrinsics_to_network,
    longest_side_resample,
    patch_aligned_size,
    read_intrinsics,
    stretch_resample,
    to_network_image,
)

K = np.array([1082.1, 1081.1, 640.79, 359.41])  # OpenCV pixels of a 1280x720 image


def project(k: np.ndarray, points: np.ndarray) -> np.ndarray:
    return np.stack(
        [k[0] * points[:, 0] / points[:, 2] + k[2], k[1] * points[:, 1] / points[:, 2] + k[3]],
        axis=1,
    )


@pytest.mark.parametrize(
    "r",
    [
        cover_crop_resample(1280, 720, 518, 294),
        stretch_resample(1280, 720, 504, 280),
        longest_side_resample(1280, 720, 1000),
    ],
)
def test_network_intrinsics_project_like_the_resampled_image(r):
    rng = np.random.default_rng(0)
    points = np.column_stack(
        [rng.uniform(-3, 3, 50), rng.uniform(-2, 2, 50), rng.uniform(2, 20, 50)]
    )
    uv_in = project(K, points)
    uv_net_expected = np.column_stack(
        [r.sx * (uv_in[:, 0] + 0.5) - r.x0 - 0.5, r.sy * (uv_in[:, 1] + 0.5) - r.y0 - 0.5]
    )
    np.testing.assert_allclose(
        project(intrinsics_to_network(K, r), points), uv_net_expected, atol=1e-9
    )
    np.testing.assert_allclose(intrinsics_to_input(intrinsics_to_network(K, r), r), K, atol=1e-9)


def test_cover_crop_for_16_9_into_518x294():
    r = cover_crop_resample(1280, 720, 518, 294)
    assert (r.resized_w, r.resized_h, r.x0, r.y0) == (523, 294, 2, 0)
    image = np.zeros((720, 1280, 3), np.uint8)
    assert to_network_image(image, r).shape == (294, 518, 3)


def test_patch_aligned_size():
    assert patch_aligned_size(6048, 4032, 518, 14) == (518, 350)


def test_constant_depth_survives_resampling_and_crop_is_invalid():
    r = cover_crop_resample(1280, 720, 518, 294)
    depth, valid = depth_to_input(
        np.full((294, 518), 4.0, np.float32), np.ones((294, 518), bool), r
    )
    assert depth.shape == (720, 1280) and depth.dtype == np.float32
    np.testing.assert_allclose(depth[valid], 4.0, rtol=1e-6)
    # The crop removed 2 resized columns on the left and 3 on the right. Input column c lies at
    # 523/1280 * (c + 0.5) - 2.5 in the network image, inside [-0.5, 517.5] for c = 5 ... 1272.
    assert not valid[:, :5].any()
    assert valid[:, 5:1273].all()
    assert not valid[:, 1273:].any()
    assert np.isnan(depth[~valid]).all()


def test_depth_is_interpolated_linearly_and_invalid_neighbours_propagate():
    r = stretch_resample(1280, 720, 640, 360)
    u = np.arange(640, dtype=np.float32)
    ramp = np.broadcast_to(1.0 + 0.01 * u, (360, 640)).astype(np.float32)
    valid_net = np.ones((360, 640), bool)
    valid_net[100, 300] = False
    depth, valid = depth_to_input(ramp, valid_net, r)
    # Input column c sits at network column (c + 0.5) / 2 - 0.5.
    cols = np.arange(10, 1270)
    expected = 1.0 + 0.01 * ((cols + 0.5) / 2 - 0.5)
    np.testing.assert_allclose(depth[400, cols], expected, atol=1e-3)
    # Every input pixel that interpolates from network pixel (row 100, col 300) is invalid.
    assert not valid[199:203, 599:603].any()
    assert valid[195, 590] and valid[210, 610]


def test_identity_resample_keeps_values():
    r = identity_resample(8, 4)
    d = np.arange(32, dtype=np.float32).reshape(4, 8) + 1
    depth, valid = depth_to_input(d, np.ones((4, 8), bool), r)
    np.testing.assert_array_equal(depth, d)
    assert valid.all()


def test_intrinsics_json_must_cover_every_image(tmp_path):
    images = []
    for name in ("a.jpg", "b.jpg"):
        (tmp_path / name).write_bytes(b"")
        images.append(tmp_path / name)
    path = tmp_path / "k.json"
    path.write_text(json.dumps({"a": [1, 1, 0, 0]}))
    with pytest.raises(ValueError, match=r"no intrinsics keyed by path or stem for images \['b'\]"):
        read_intrinsics(path, images)
    path.write_text(json.dumps({str(images[0]): [1, 1, 0, 0], "b": [2, 2, 0, 0]}))
    assert [k[0] for k in read_intrinsics(path, images)] == [1, 2]
    path.write_text(json.dumps([[1, 1, 0, 0]]))
    with pytest.raises(ValueError, match="1 intrinsics entries for 2 images"):
        read_intrinsics(path, images)


def test_fingerprint_changes_with_every_input(tmp_path):
    from models.common import fingerprint

    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    pose = np.eye(4)
    k = [np.array([500.0, 500, 320, 240])] * 2

    def key(members=("a", "b"), intrinsics=k, poses=None, max_side=392, ckpt="c1"):
        images = [tmp_path / f"{m}.jpg" for m in members]
        return fingerprint(list(members), images, intrinsics, poses, max_side, ckpt)

    base = key()
    assert base == key()
    assert base != key(poses=[pose, pose])  # poses added
    assert base != key(members=("a",), intrinsics=k[:1])  # members changed
    assert base != key(max_side=518)  # resolution changed
    assert base != key(ckpt="c2")  # checkpoint changed
    focal = [np.array([510.0, 500, 320, 240]), k[1]]
    assert base != key(intrinsics=focal)  # only one focal length changed
    b.write_bytes(b"B")
    assert base != key()  # an image changed


def test_free_space_is_measured_where_the_download_lands(tmp_path, monkeypatch):
    import shutil
    from collections import namedtuple

    from models import common

    Usage = namedtuple("Usage", "total used free")
    measured = []

    def fake_usage(path):
        measured.append(Path(path))
        free = 100 * 1024**3 if Path(path) == tmp_path / "big" else 1024**3
        return Usage(0, 0, free)

    monkeypatch.setattr(shutil, "disk_usage", fake_usage)
    (tmp_path / "big").mkdir()
    # The cache folder does not exist yet: its nearest existing parent is on the roomy volume.
    monkeypatch.setenv("HF_HOME", str(tmp_path / "big" / "hf-cache"))
    common.require_free_space(common.hf_cache_dir(), "ckpt")
    assert measured == [tmp_path / "big"]
    monkeypatch.setenv("HF_HOME", str(tmp_path / "small"))
    with pytest.raises(RuntimeError, match=r"only 1\.0 GB is free"):
        common.require_free_space(common.hf_cache_dir(), "ckpt")


def test_checkpoint_record_rejects_a_replaced_checkpoint(tmp_path, monkeypatch):
    import hashlib
    import sys
    import types

    from models.common import checkpoint_record

    weights = tmp_path / "model.safetensors"
    weights.write_bytes(b"pinned weights")
    hub = types.ModuleType("huggingface_hub")
    hub.try_to_load_from_cache = lambda repo, name, revision: str(weights)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    pinned = hashlib.sha256(b"pinned weights").hexdigest()
    record = checkpoint_record("org/model", "model.safetensors", "rev", pinned)
    assert record["sha256"] == pinned and record["bytes"] == 14
    weights.write_bytes(b"other weights")
    with pytest.raises(RuntimeError, match="is not the pinned"):
        checkpoint_record("org/model", "model.safetensors", "rev", pinned)


def test_a_replaced_checkpoint_stops_the_run_before_any_output(tmp_path, monkeypatch):
    import sys
    import types

    from models import run

    weights = tmp_path / "model.safetensors"
    weights.write_bytes(b"some other loadable file")
    hub = types.ModuleType("huggingface_hub")
    hub.try_to_load_from_cache = lambda repo, name, revision: str(weights)
    torch = types.ModuleType("torch")
    torch.__version__ = "fake"
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    torch.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False))
    model = types.ModuleType("fake_model")
    model.REPO, model.FILENAME, model.REVISION = "org/model", "model.safetensors", "rev"
    model.SHA256 = "0" * 64
    model.load = lambda device: object()

    def never_run(*args):
        raise AssertionError("inference ran with an unverified checkpoint")

    model.run = never_run
    for name, module in (("huggingface_hub", hub), ("torch", torch), ("fake_model", model)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setitem(run.MODULES, "moge2", "fake_model")
    image = tmp_path / "a.jpg"
    image.write_bytes(b"")
    (tmp_path / "images.txt").write_text(f"{image}\n")
    out = tmp_path / "out"
    with pytest.raises(RuntimeError, match="is not the pinned"):
        run.main(
            [
                "--model",
                "moge2",
                "--images",
                str(tmp_path / "images.txt"),
                "--device",
                "cpu",
                "--out",
                str(out),
            ]
        )
    assert not list(out.glob("*.npz"))


def _fake_model_run(tmp_path, monkeypatch, fail_on: str | None):
    """models.run.main on two images with a stand-in model whose checkpoint verifies; it raises
    on the image named `fail_on` after the images before it were written."""
    import hashlib
    import sys
    import types

    from models import common, run

    weights = tmp_path / "model.safetensors"
    weights.write_bytes(b"pinned")
    hub = types.ModuleType("huggingface_hub")
    hub.try_to_load_from_cache = lambda repo, name, revision: str(weights)
    torch = types.ModuleType("torch")
    torch.__version__ = "fake"
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    torch.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False))
    model = types.ModuleType("fake_model")
    model.REPO, model.FILENAME, model.REVISION = "org/model", "model.safetensors", "rev"
    model.SHA256 = hashlib.sha256(b"pinned").hexdigest()
    model.LICENSE, model.CODE = "test", "test"
    model.load = lambda device: object()

    def infer(_, inputs):
        for path in inputs.images:
            if path.stem == fail_on:
                raise MemoryError(f"out of memory on {path.name}")
            depth = np.full((2, 2), 9.0, np.float32)
            yield types.SimpleNamespace(
                path=path,
                depth=depth,
                valid=np.ones((2, 2), bool),
                intrinsics=np.array([1.0, 1, 0.5, 0.5]),
                cam_to_world=None,
                arrays={},
                seconds=0.0,
                network_wh=(2, 2),
                resample=common.Resample(2, 2, 2, 2, 0, 0, 2, 2),
                extra={},
            )

    model.run = infer
    for name, module in (("huggingface_hub", hub), ("torch", torch), ("fake_model", model)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setitem(run.MODULES, "moge2", "fake_model")
    listing = tmp_path / "images.txt"
    listing.write_text("".join(f"{tmp_path / n}.jpg\n" for n in ("a", "b")))
    for n in ("a", "b"):
        (tmp_path / f"{n}.jpg").write_bytes(b"")
    out = tmp_path / "out"
    return lambda: run.main(
        ["--model", "moge2", "--images", str(listing), "--device", "cpu", "--out", str(out)]
    ), out


def _snapshot(folder):
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())}


def test_a_run_that_fails_midway_leaves_the_previous_run_intact(tmp_path, monkeypatch):
    main, out = _fake_model_run(tmp_path, monkeypatch, fail_on="b")
    out.mkdir()
    for name in ("a.npz", "b.npz", "run.json"):
        (out / name).write_bytes(f"previous {name}".encode())
    before = _snapshot(out)
    with pytest.raises(MemoryError):
        main()  # image a is predicted, then b fails
    assert _snapshot(out) == before
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("out")) == ["out"]


def test_a_completed_run_replaces_the_previous_one_whole(tmp_path, monkeypatch):
    main, out = _fake_model_run(tmp_path, monkeypatch, fail_on=None)
    out.mkdir()
    (out / "stale.npz").write_bytes(b"from an older image list")
    (out / "run.json").write_text("{}")
    main()
    assert sorted(p.name for p in out.iterdir()) == ["a.npz", "b.npz", "run.json"]
    assert json.loads((out / "run.json").read_text())["images"][1]["stem"] == "b"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("out")) == ["out"]


def _complete_run(folder, tag):
    folder.mkdir()
    (folder / "a.npz").write_bytes(f"{tag} a".encode())
    (folder / "run.json").write_text(json.dumps({"run": tag}))


def test_a_publication_that_fails_twice_keeps_the_previous_run(tmp_path, monkeypatch):
    from models import run

    out = tmp_path / "out"
    _complete_run(out, "previous")
    before = _snapshot(out)
    real_rename = Path.rename

    def failing_rename(self, target):
        if self.name == "out.staging":
            raise OSError("injected: the new run could not be moved into place")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", failing_rename)
    for attempt in range(2):
        stage = tmp_path / "out.staging"
        _complete_run(stage, f"new {attempt}")
        with pytest.raises(OSError, match="injected"):
            run.publish(stage, out)
        assert _snapshot(out) == before
        assert not (tmp_path / "out.previous").exists()
        shutil.rmtree(stage)


def test_a_run_left_aside_by_a_killed_publication_is_restored_not_deleted(tmp_path, monkeypatch):
    from models import run

    # A kill between setting the old run aside and moving the new one in leaves only .previous.
    _complete_run(tmp_path / "out.previous", "previous")
    before = _snapshot(tmp_path / "out.previous")
    stage = tmp_path / "out.staging"
    _complete_run(stage, "new")
    run.publish(stage, tmp_path / "out")
    assert json.loads((tmp_path / "out" / "run.json").read_text()) == {"run": "new"}
    assert not (tmp_path / "out.previous").exists()
    # Restored first, then superseded by a complete publication: never deleted while alone.
    _complete_run(tmp_path / "out.previous", "previous")
    shutil.rmtree(tmp_path / "out")
    stage = tmp_path / "out.staging"
    _complete_run(stage, "new again")

    real = Path.rename

    def refuse_staging(self, target):
        if self.name == "out.staging":
            raise OSError("injected")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", refuse_staging)
    with pytest.raises(OSError, match="injected"):
        run.publish(stage, tmp_path / "out")
    assert _snapshot(tmp_path / "out") == before


def test_a_group_write_that_fails_midway_keeps_the_previous_outputs(tmp_path, monkeypatch):
    import types

    from models import run_groups

    out = tmp_path / "n2-a"
    _complete_run(out, "previous")
    (out / "b.npz").write_bytes(b"previous b")
    before = _snapshot(out)
    real_write = run_groups.write_npz

    def fail_on_b(folder, stem, *args):
        if stem == "b":
            raise OSError("injected: disk full while writing b")
        return real_write(folder, stem, *args)

    monkeypatch.setattr(run_groups, "write_npz", fail_on_b)
    results = [
        types.SimpleNamespace(
            path=tmp_path / f"{n}.jpg",
            depth=np.ones((2, 2), np.float32),
            valid=np.ones((2, 2), bool),
            intrinsics=np.array([1.0, 1, 0.5, 0.5]),
            cam_to_world=np.eye(4),
            arrays={},
        )
        for n in ("a", "b")
    ]
    with pytest.raises(OSError, match="injected"):
        run_groups.write_group(out, results, {"run": "new"})  # a is written, then b fails
    assert _snapshot(out) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["n2-a"]
    monkeypatch.setattr(run_groups, "write_npz", real_write)
    run_groups.write_group(out, results, {"run": "new"})
    assert sorted(p.name for p in out.iterdir()) == ["a.npz", "b.npz", "run.json"]
    doc = json.loads((out / "run.json").read_text())
    # run.json now also records each published NPZ's digest, what reuse validates.
    assert doc["run"] == "new" and set(doc["outputs"]) == {"a", "b"}
