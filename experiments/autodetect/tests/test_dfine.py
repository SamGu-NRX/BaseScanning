import random

import numpy as np
import pytest
from PIL import Image

torch = pytest.importorskip("torch")
from transformers import DFineConfig  # noqa: E402
from transformers.models.d_fine.modeling_d_fine import DFineIntegral  # noqa: E402

import autodetect.dfine as dfine  # noqa: E402
from autodetect.dfine import SIZE, _batches, _integral_forward, pixels  # noqa: E402


def _synthetic_image(path):
    """Left half red, right half blue: a horizontal flip swaps the two edges."""
    im = Image.new("RGB", (64, 32), (255, 0, 0))
    im.paste((0, 0, 255), (32, 0, 64, 32))
    im.save(path)


def test_patched_integral_matches_transformers():
    cfg = DFineConfig()
    layer = DFineIntegral(cfg)
    g = torch.Generator().manual_seed(0)
    corners = torch.randn(2, 5, 4 * (cfg.max_num_bins + 1), generator=g)
    project = torch.randn(cfg.max_num_bins + 1, generator=g)
    expected = DFineIntegral.forward(layer, corners, project)
    assert torch.allclose(_integral_forward(layer, corners, project), expected, atol=1e-6)


def test_pixels_synthetic_image():
    p = pixels(Image.new("RGB", (100, 50), (255, 0, 0)))
    assert p.shape == (3, SIZE, SIZE)
    assert p.dtype == np.float32
    assert float(p.min()) >= 0.0
    assert float(p.max()) <= 1.0
    # A solid red image survives the stretch: red channel full, the others empty.
    assert float(p[0].mean()) == 1.0
    assert float(p[1].mean()) == 0.0
    assert float(p[2].mean()) == 0.0


def test_batches_flip_geometry(tmp_path, monkeypatch):
    image = tmp_path / "img0.jpg"
    _synthetic_image(image)
    monkeypatch.setattr(dfine, "image_path", lambda name, i: image)
    gt = {
        "img0": {
            "boxes": [
                {"label": "window", "box": [0.0, 0.25, 0.5, 0.75]},
                {"label": "door", "box": [0.5, 0.0, 1.0, 0.5]},
            ]
        }
    }
    (x, ys), = _batches(gt, 1, random.Random(1))
    (c, cxcywh), = ys
    assert x.shape == (1, 3, SIZE, SIZE)
    assert c.tolist() == [0, 1]  # window maps to 0, door to 1 in LABELS order
    left_red = float(x[0, 0, :, 0].mean()) == 1.0
    original = np.array([[0.25, 0.5, 0.5, 0.5], [0.75, 0.25, 0.5, 0.5]], dtype=np.float32)
    mirrored = np.array([[0.75, 0.5, 0.5, 0.5], [0.25, 0.25, 0.5, 0.5]], dtype=np.float32)
    if np.allclose(cxcywh, mirrored):
        assert not left_red  # mirrored boxes mean the image flipped too
    else:
        assert np.allclose(cxcywh, original)
        assert left_red


def test_batches_drops_partial_tail(tmp_path, monkeypatch):
    paths = {}
    for i in range(3):
        p = tmp_path / f"img{i}.jpg"
        _synthetic_image(p)
        paths[f"img{i}"] = p
    monkeypatch.setattr(dfine, "image_path", lambda name, i: paths[i])
    gt = {i: {"boxes": [{"label": "window", "box": [0.0, 0.0, 1.0, 1.0]}]} for i in paths}
    batches = list(_batches(gt, 2, random.Random(0)))
    assert len(batches) == 1  # the third image is dropped, not padded
    assert batches[0][0].shape == (2, 3, SIZE, SIZE)
