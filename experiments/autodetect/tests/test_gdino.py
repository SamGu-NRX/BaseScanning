"""Offline tests for autodetect.gdino: the missing-model failure, preprocess geometry, and the
run() bookkeeping. Nothing here downloads weights, reaches Hugging Face, or runs a model."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from autodetect import config, gdino
from autodetect.gdino import GDino, run


def test_init_raises_when_model_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(gdino, "MODEL", tmp_path / "missing" / "model_fp16.onnx")
    with pytest.raises(FileNotFoundError, match="model_fp16.onnx"):
        GDino()


def test_preprocess_landscape_fills_top_left_and_applies_mean_std():
    im = Image.new("RGB", (200, 100), (127, 127, 127))
    pixels, mask = GDino.preprocess(im)
    assert pixels.shape == (1, 3, 800, 800)
    assert pixels.dtype == np.float32
    assert mask.dtype == np.int64
    expected = np.zeros((1, 800, 800), dtype=np.int64)
    expected[0, :400, :800] = 1
    assert np.array_equal(mask, expected)
    expected_pixel = (127 / 255 - 0.485) / 0.229
    assert pixels[0, 0, 0, 0] == pytest.approx(expected_pixel, abs=1e-4)
    # Padding is exactly 0, which no normalized real pixel value is.
    assert not pixels[0, :, 400:, :].any()


def test_preprocess_portrait_pads_right():
    im = Image.new("RGB", (100, 200), (0, 255, 0))
    pixels, mask = GDino.preprocess(im)
    expected = np.zeros((1, 800, 800), dtype=np.int64)
    expected[0, :800, :400] = 1
    assert np.array_equal(mask, expected)
    assert not pixels[0, :, :, 400:].any()
    assert pixels[0, 1, 0, 0] == pytest.approx((255 / 255 - 0.456) / 0.224, abs=1e-4)


def test_run_saves_one_record_per_image_in_sorted_order(monkeypatch, tmp_path):
    model_file = tmp_path / "model_fp16.onnx"
    model_file.write_bytes(b"")  # meta() reads this file's size
    monkeypatch.setattr(gdino, "MODEL", model_file)
    monkeypatch.setattr(gdino, "ground_truth", lambda name: {"b": {}, "a": {}})
    paths = {"a": tmp_path / "a.jpg", "b": tmp_path / "b.jpg"}
    monkeypatch.setattr(gdino, "image_path", lambda name, image_id: paths[image_id])
    saved = {}

    def fake_save(model_name, set_name, meta, images):
        saved.update(model_name=model_name, set_name=set_name, meta=meta, images=images)

    monkeypatch.setattr(gdino, "save_preds", fake_save)

    class FakeModel:
        def __init__(self):
            self.seen = []

        def detect(self, path):
            self.seen.append(path)
            dets = [{"label": "window", "score": 0.9, "box": [0.0, 0.0, 0.5, 0.5]}]
            return dets, 1.5, 2.5

    model = FakeModel()
    run(model, "oi_eval")
    assert model.seen == [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    assert saved["model_name"] == "gdino"
    assert saved["set_name"] == "oi_eval"
    assert saved["meta"]["phrases"] == config.GDINO_PHRASES
    assert saved["meta"]["size"] == "0 MB (fp16 ONNX)"
    dets = [{"label": "window", "score": 0.9, "box": [0.0, 0.0, 0.5, 0.5]}]
    assert saved["images"] == {
        "a": {"elapsed_ms": 2.5, "preprocess_ms": 1.5, "dets": dets},
        "b": {"elapsed_ms": 2.5, "preprocess_ms": 1.5, "dets": dets},
    }
