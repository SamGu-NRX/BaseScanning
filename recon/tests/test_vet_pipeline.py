"""Input preservation and full-depth footprint checks for the reconstruction worker."""

import zipfile

import numpy as np
import pytest
from test_worker import WALL

from recon import capture as cap
from recon import coverage, pipeline


def _bundle(tmp_path):
    path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("scene.json", b"capture input")
        archive.writestr("photos/frame.jpg", b"image bytes")
    work = tmp_path / "work"
    return path, work, cap.unpack(path, work)


@pytest.mark.parametrize("relation", ["same", "ancestor", "descendant"])
def test_zip_output_overlap_is_refused_before_cleanup(tmp_path, monkeypatch, relation):
    bundle, work, root = _bundle(tmp_path)
    out = {"same": root, "ancestor": root.parent, "descendant": root / "outputs"}[relation]
    out.mkdir(exist_ok=True)
    sentinel = out / "model.glb"
    sentinel.write_bytes(b"previous output")

    def load(*_):
        pytest.fail("capture load reached before overlap rejection")

    monkeypatch.setattr(pipeline.cap, "load", load)
    with pytest.raises(ValueError, match="overlaps capture input"):
        pipeline.run(bundle, out, work, "auto", None, False)
    assert (root / "scene.json").read_bytes() == b"capture input"
    assert sentinel.read_bytes() == b"previous output"


@pytest.mark.parametrize("damage", ["missing", "truncated", "changed", "symlink", "extra"])
def test_damaged_extracted_bundle_is_rebuilt(tmp_path, damage):
    bundle, work, root = _bundle(tmp_path)
    member = root / "photos/frame.jpg"
    if damage == "missing":
        member.unlink()
    elif damage == "truncated":
        member.write_bytes(b"image")
    elif damage == "changed":
        member.write_bytes(b"other bytes")
    elif damage == "symlink":
        outside = tmp_path / "outside.jpg"
        outside.write_bytes(b"image bytes")
        member.unlink()
        member.symlink_to(outside)
    else:
        (root / "unexpected.json").write_bytes(b"foreign metadata")
    assert cap.unpack(bundle, work) == root
    assert member.is_file() and not member.is_symlink()
    assert member.read_bytes() == b"image bytes"
    assert not (root / "unexpected.json").exists()
    if damage == "symlink":
        assert outside.read_bytes() == b"image bytes"


def test_intact_extracted_bundle_is_verified_without_reextracting(tmp_path, monkeypatch):
    bundle, work, root = _bundle(tmp_path)
    original = (root / "scene.json").stat().st_mtime_ns
    monkeypatch.setattr(
        zipfile.ZipFile, "open", lambda *_: pytest.fail("re-extracted intact cache")
    )
    assert cap.unpack(bundle, work) == root
    assert (root / "scene.json").stat().st_mtime_ns == original


class _FootprintVolume:
    voxel = 0.05

    def __init__(self, band, obstruction):
        self.band = band
        self.obstruction = obstruction

    def occupied(self, points):
        low, high = self.band
        return (
            self.obstruction
            & (points[:, 2] >= low)
            & (points[:, 2] <= high)
            & (points[:, 1] >= 1.9 - 1e-9)
        )

    def observed(self, points):
        low, high = self.band
        missing = (points[:, 2] >= low) & (points[:, 2] <= high) & (points[:, 1] >= 1.9 - 1e-9)
        return np.ones(len(points), bool) if self.obstruction else ~missing


@pytest.mark.parametrize("band", [(0.05, 0.10), (0.609, 0.611)], ids=["near_wall", "far_edge"])
@pytest.mark.parametrize("obstruction", [True, False], ids=["eave", "unobserved"])
def test_overhead_checks_full_footprint_depth(band, obstruction):
    # The first free step is 0.05 m out, so the 0.56 m footprint ends at z=0.61 m.
    _, _, gap, clear = coverage.free_space(
        WALL, _FootprintVolume(band, obstruction), np.array([0.0])
    )
    assert clear[0] < 1.9
    if obstruction:
        assert gap[0] == pytest.approx(1.85)
    else:
        assert np.isnan(gap[0])
