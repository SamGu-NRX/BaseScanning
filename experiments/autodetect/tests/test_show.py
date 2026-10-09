"""Offline tests for autodetect.show.draw: fake image, ground truth and prediction cache."""

from pathlib import Path

from PIL import Image, ImageChops

import autodetect.show as show

NAME, IID = "oi_tune", "abc123"


def flat_image(path):
    Image.new("RGB", (128, 96), (10, 20, 30)).save(path)
    return path


def install_fakes(monkeypatch, tmp_path, boxes, dets):
    """Point draw's three data sources at a flat test image and caller-supplied boxes."""
    src = flat_image(tmp_path / "src.jpg")
    monkeypatch.setattr(show, "image_path", lambda name, image_id: src)
    monkeypatch.setattr(show, "ground_truth", lambda name: {IID: {"verified": {}, "boxes": boxes}})
    monkeypatch.setattr(
        show, "load_preds", lambda model, name: {"meta": {}, "images": {IID: {"elapsed_ms": 1.0, "dets": dets}}}
    )
    monkeypatch.setattr(show, "DATA", tmp_path / "data")


def max_diff(a, b):
    """Largest per-pixel channel difference between two same-size images."""
    return max(band[1] for band in ImageChops.difference(a, b).getextrema())


def mean_changed(original, saved, floor=50):
    """Mean RGB over pixels that moved more than floor, or None when nothing moved."""
    diff = ImageChops.difference(original, saved).load()
    px = saved.load()
    w, h = original.size
    totals, n = [0, 0, 0], 0
    for y in range(h):
        for x in range(w):
            if max(diff[x, y]) > floor:
                n += 1
                for i, c in enumerate(px[x, y]):
                    totals[i] += c
    return tuple(c / n for c in totals) if n else None


def test_draw_ground_truth_writes_preview(tmp_path, monkeypatch):
    install_fakes(
        monkeypatch,
        tmp_path,
        boxes=[
            {"label": "window", "box": [0.1, 0.1, 0.5, 0.5], "group": False},
            {"label": "door", "box": [0.55, 0.55, 0.95, 0.95], "group": True},
        ],
        dets=[],
    )
    out = Path(show.draw(NAME, IID))
    assert out.name == f"{NAME}_{IID}_gt.jpg"
    assert out.parent == tmp_path / "data" / "preview"
    saved = Image.open(out)
    assert saved.size == (128, 96)
    assert max_diff(Image.open(tmp_path / "src.jpg"), saved) > 20


def test_draw_colors_follow_labels(tmp_path, monkeypatch):
    install_fakes(
        monkeypatch,
        tmp_path,
        boxes=[
            {"label": "window", "box": [0.1, 0.1, 0.4, 0.9], "group": False},
            {"label": "door", "box": [0.6, 0.1, 0.9, 0.9], "group": False},
        ],
        dets=[],
    )
    src = Image.open(tmp_path / "src.jpg")
    saved = Image.open(Path(show.draw(NAME, IID)))
    window = mean_changed(src.crop((0, 0, 64, 96)), saved.crop((0, 0, 64, 96)))
    door = mean_changed(src.crop((64, 0, 128, 96)), saved.crop((64, 0, 128, 96)))
    assert window[1] > window[0] and window[1] > window[2]  # green
    assert door[2] > door[0] and door[2] > door[1]  # blue


def test_draw_model_threshold_filters_detections(tmp_path, monkeypatch):
    install_fakes(
        monkeypatch,
        tmp_path,
        boxes=[],
        dets=[
            {"label": "door", "score": 0.9, "box": [0.05, 0.05, 0.25, 0.45]},
            {"label": "door", "score": 0.5, "box": [0.05, 0.55, 0.25, 0.95]},
            {"label": "door", "score": 0.1, "box": [0.75, 0.05, 0.95, 0.45]},
        ],
    )
    out = Path(show.draw(NAME, IID, model="owl", threshold=0.5))
    assert out.name == f"{NAME}_{IID}_owl.jpg"
    src = Image.open(tmp_path / "src.jpg")
    saved = Image.open(out)
    top_left = (src.crop((0, 0, 40, 48)), saved.crop((0, 0, 40, 48)))
    bottom_left = (src.crop((0, 48, 40, 96)), saved.crop((0, 48, 40, 96)))
    top_right = (src.crop((88, 0, 128, 48)), saved.crop((88, 0, 128, 48)))
    assert max_diff(*top_left) > 50  # score 0.9 clears the threshold
    assert max_diff(*bottom_left) > 50  # a score exactly at the threshold draws
    assert max_diff(*top_right) < 10  # score 0.1 stays off
    for region in (top_left, bottom_left):
        r, g, b = mean_changed(*region)
        assert r > g and r > b  # model boxes draw in red


def test_draw_with_no_boxes_writes_plain_preview(tmp_path, monkeypatch):
    install_fakes(monkeypatch, tmp_path, boxes=[], dets=[])
    out = Path(show.draw(NAME, IID))
    assert out.exists()
    assert max_diff(Image.open(tmp_path / "src.jpg"), Image.open(out)) < 10  # JPEG noise only
