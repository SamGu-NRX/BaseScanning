"""Hand witnesses on the depth cache's key path, the guard the experiment stands on.

These run the actual `moge_key` / `moge_cache` / `moge` code with the model stubbed (bundles.py):
no weights, no network, no depth model.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from recon import capture as cap
from recon import depth

import bundles

K2 = (104.0, 104.0, 162.0, 122.0)  # the sensitivity shift: fx/fy +4 px, cx/cy +2 px


def _set_intrinsics(bundle, k):
    doc = json.loads((bundle / "scene.json").read_text())
    for kf in doc["keyframes"]:
        kf["intrinsics"] = [round(float(v), 4) for v in k]
    (bundle / "scene.json").write_text(json.dumps(doc, indent=1))
    return bundle


def _set_meter(bundle, pos_ft):
    doc = json.loads((bundle / "scene.json").read_text())
    doc["meter"]["pos"] = pos_ft
    (bundle / "scene.json").write_text(json.dumps(doc, indent=1))
    return bundle


def test_duplicate_frame_id_refused(tmp_path):
    """One depth view must not count from two camera positions (it fakes two-view coverage)."""
    bundle = bundles.write_bundle(tmp_path / "bundle", duplicate=True)
    with pytest.raises(cap.DuplicateFrameId):
        cap.load(bundle, tmp_path)


def test_key_covers_image_pose_intrinsics_but_not_marks(tmp_path):
    """moge_key moves when image bytes, a pose or the intrinsics move; the phone's marks (the
    meter's position) do not touch depth and must not move it."""
    base_key = depth.moge_key(cap.load(bundles.write_bundle(tmp_path / "b0"), tmp_path))

    marked = _set_meter(bundles.write_bundle(tmp_path / "b1"), [2.0, 4.0, 0.1])
    assert depth.moge_key(cap.load(marked, tmp_path)) == base_key
    assert (
        depth.moge_key(cap.load(bundles.write_bundle(tmp_path / "b2", reverse=True), tmp_path))
        != base_key
    )
    shifted = _set_intrinsics(bundles.write_bundle(tmp_path / "b3"), K2)
    assert depth.moge_key(cap.load(shifted, tmp_path)) != base_key
    assert (
        depth.moge_key(cap.load(bundles.write_bundle(tmp_path / "b4", xs_shift=0.25), tmp_path))
        != base_key
    )
    moved = bundles.edit_pose(bundles.write_bundle(tmp_path / "b5"), "k1", 0.02)
    assert depth.moge_key(cap.load(moved, tmp_path)) != base_key
    reencoded = bundles.write_bundle(tmp_path / "b6")
    bundles.reencode_jpegs(reencoded)
    assert depth.moge_key(cap.load(reencoded, tmp_path)) != base_key


def test_cache_folder_keyed_by_content(tmp_path):
    """Same capture, same cache folder; a different capture (moved cameras), a different folder."""
    work = tmp_path / "w"
    same = cap.load(bundles.write_bundle(tmp_path / "b1"), tmp_path)
    other = cap.load(bundles.write_bundle(tmp_path / "b2", xs_shift=0.25), tmp_path)
    assert depth.moge_cache(same, work) == depth.moge_cache(same, work)
    assert depth.moge_cache(same, work) != depth.moge_cache(other, work)


def test_stale_maps_deleted_on_key_mismatch(tmp_path):
    """A folder that claims this key but holds other photos' maps loses them: the guard deletes
    before recompute, so a hash collision or a key-less write cannot serve foreign depth."""
    capture = cap.load(bundles.write_bundle(tmp_path / "b"), tmp_path)
    work = tmp_path / "w"
    folder = depth.moge_cache(capture, work)
    other = cap.load(bundles.write_bundle(tmp_path / "other", xs_shift=0.25), tmp_path)
    (folder / "key.json").write_text(json.dumps(depth.moge_key(other)))
    np.savez(folder / "k0.moge2.npz", depth=np.full((bundles.H, bundles.W), 7.0, np.float32))

    depths = depth.moge(capture, work)

    expected = bundles.synthetic_depth("k0", bundles.K[0])
    assert np.array_equal(depths["k0"].depth, expected)
    assert json.loads((folder / "key.json").read_text()) == depth.moge_key(capture)


def test_same_size_cache_edit_consumed_silently(tmp_path):
    """The guard's gap, as a hand witness: a cached map overwritten with a shifted copy of the
    same shape and dtype, key.json untouched, is served as if it were the model's own output."""
    capture = cap.load(bundles.write_bundle(tmp_path / "b"), tmp_path)
    work = tmp_path / "w"
    clean = depth.moge(capture, work)["k1"].depth.copy()

    bundles.poison_cache(work, "k1")

    poisoned = depth.moge(capture, work)["k1"].depth
    assert not np.array_equal(clean, poisoned)
    assert np.allclose(poisoned, clean + bundles.POISON_M)
