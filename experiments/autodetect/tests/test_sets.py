import json

import pytest

from autodetect import sets

DET = {"label": "window", "score": 0.9, "box": [0.1, 0.1, 0.3, 0.3]}


def write_gt(path, gt):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(gt))


def test_pred_path_lays_out_model_directory_then_set(tmp_path, monkeypatch):
    monkeypatch.setattr(sets, "PREDS", tmp_path / "preds")
    assert sets.pred_path("owlv2", "oi_eval") == tmp_path / "preds" / "owlv2" / "oi_eval.json"


def test_save_preds_then_load_preds_round_trips_images(tmp_path, monkeypatch):
    monkeypatch.setattr(sets, "PREDS", tmp_path / "preds")
    images = {"img1": {"elapsed_ms": 12.5, "dets": [DET]}}
    sets.save_preds("test", "oi_tune", {"model": "test"}, images)
    loaded = sets.load_preds("test", "oi_tune")
    assert loaded["images"] == images
    assert loaded["meta"]["model"] == "test"
    assert (tmp_path / "preds" / "test" / "oi_tune.json").exists()


def test_save_preds_adds_load_average_and_leaves_the_callers_meta_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(sets, "PREDS", tmp_path / "preds")
    meta = {"model": "test"}
    sets.save_preds("test", "oi_tune", meta, {})
    assert "load_avg_at_save" not in meta
    saved = sets.load_preds("test", "oi_tune")["meta"]["load_avg_at_save"]
    assert len(saved) == 3  # the 1, 5 and 15 minute load averages
    assert all(isinstance(x, float) for x in saved)


def test_image_path_mapping_for_all_five_accepted_names(tmp_path, monkeypatch):
    monkeypatch.setattr(sets, "OI", tmp_path / "oi")
    monkeypatch.setattr(sets, "CMP", tmp_path / "cmp")
    monkeypatch.setattr(sets, "ELECTRO_1024", tmp_path / "electro_1024")
    assert sets.image_path("oi_tune", "i") == tmp_path / "oi" / "tune" / "i.jpg"
    assert sets.image_path("oi_eval", "i") == tmp_path / "oi" / "eval" / "i.jpg"
    assert sets.image_path("oi_train", "i") == tmp_path / "oi" / "train" / "i.jpg"
    assert sets.image_path("cmp", "i") == tmp_path / "cmp" / "base" / "i.jpg"
    assert sets.image_path("electro", "i") == tmp_path / "electro_1024" / "i.jpg"


def test_ground_truth_reads_the_cmp_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sets, "CMP", tmp_path / "cmp")
    write_gt(tmp_path / "cmp" / "gt.json", {"i1": {"verified": {}, "boxes": []}})
    assert sets.ground_truth("cmp") == {"i1": {"verified": {}, "boxes": []}}


@pytest.mark.parametrize("name,split", [("oi_tune", "tune"), ("oi_eval", "eval"), ("oi_train", "train")])
def test_ground_truth_reads_the_oi_split_file(name, split, tmp_path, monkeypatch):
    monkeypatch.setattr(sets, "OI", tmp_path / "oi")
    write_gt(tmp_path / "oi" / f"gt_{split}.json", {"i1": {"verified": {}, "boxes": []}})
    assert sets.ground_truth(name) == {"i1": {"verified": {}, "boxes": []}}


def test_ground_truth_electro_is_prediction_only(tmp_path, monkeypatch):
    electro = tmp_path / "electro"
    electro.mkdir()
    (electro / "manifest.json").write_text(json.dumps({"photos": [{"id": "p1"}, {"id": "p2"}]}))
    monkeypatch.setattr(sets, "ELECTRO", electro)
    assert sets.ground_truth("electro") == {
        "p1": {"verified": {}, "boxes": []},
        "p2": {"verified": {}, "boxes": []},
    }


def test_ground_truth_unknown_set_error_names_every_accepted_name():
    with pytest.raises(KeyError, match="unknown set 'nope'") as exc_info:
        sets.ground_truth("nope")
    message = str(exc_info.value)
    for accepted in ("oi_tune", "oi_eval", "cmp", "oi_train", "electro"):
        assert accepted in message
