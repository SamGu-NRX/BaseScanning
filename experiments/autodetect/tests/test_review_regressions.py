import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from PIL import Image
import pytest

from autodetect import sets

BRAND_DIR = Path(__file__).resolve().parents[1] / "meter_brand"
spec = importlib.util.spec_from_file_location("brands", BRAND_DIR / "brands.py")
brands = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brands)
sys.modules["brands"] = brands
spec = importlib.util.spec_from_file_location("brand_score", BRAND_DIR / "score.py")
score = importlib.util.module_from_spec(spec)
spec.loader.exec_module(score)


def test_brand_matches_are_tokens_not_substrings():
    for text in ["VOLTAGE", "GENERAL ELECTRICITY", "NOTITRONWORD"]:
        assert brands.match_brand(text) is None
        assert not score.names_brand(text, "GE")
    assert score.names_brand("GE 120 VOLT", "GE")
    assert score.names_brand("GENERAL ELECTRIC", "GE")
    assert brands.match_brand("LANDIS GYR") == "LANDIS+GYR"
    assert score.names_brand("OXFORO D4S", "OXFORO")
    assert not score.names_brand("OTHER OXFOROISH", "OXFORO")


def test_electro_preparation_uses_packet_image_reference(tmp_path, monkeypatch):
    packet = tmp_path / "packet"
    (packet / "photos").mkdir(parents=True)
    Image.new("RGB", (2048, 1024)).save(packet / "photos" / "p1.jpg")
    (packet / "manifest.json").write_text(json.dumps({"photos": [
        {"id": "p1", "image": {"path": "photos/p1.jpg"}}
    ]}))
    monkeypatch.setattr(sets, "ELECTRO", packet)
    monkeypatch.setattr(sets, "ELECTRO_1024", tmp_path / "prepared")
    sets.prepare_electro()
    with Image.open(sets.image_path("electro", "p1")) as image:
        assert image.size == (1024, 512)


def isolated_function(filename, name, namespace):
    tree = ast.parse((Path(__file__).resolve().parents[1] / "autodetect" / filename).read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), filename, "exec"), namespace)
    return namespace[name]


def test_elevation_render_creates_output_directory(tmp_path):
    render = isolated_function("extent_gt.py", "render", {
        "raster": lambda door: (np.zeros((2, 2)), (0, .02, 0, .02), None),
        "np": np, "Image": Image, "ImageDraw": __import__("PIL.ImageDraw", fromlist=["Draw"]),
        "RES": .01, "DATA": tmp_path,
    })
    assert Path(render("test")).is_file()


def test_dfine_train_creates_log_without_student_prepare(tmp_path):
    torch = SimpleNamespace(manual_seed=Mock(), optim=SimpleNamespace(AdamW=lambda *a, **k: SimpleNamespace(param_groups=[])))
    model = Mock()
    model.train.return_value = model
    model.named_parameters.return_value = []
    train = isolated_function("dfine.py", "train", {
        "SEED": 1, "_device": lambda: "cpu", "_model": lambda *a: model,
        "BASE": "base", "LR": 1, "BACKBONE_LR_SCALE": 1, "WEIGHT_DECAY": 1,
        "ground_truth": lambda name: {}, "random": __import__("random"),
        "time": __import__("time"), "DATA": tmp_path,
        "OUT": tmp_path / "student" / "dfine_small", "json": json,
    })
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(sys.modules, "torch", torch)
        train(0)
    assert (tmp_path / "student" / "dfine_small_train.log").is_file()
