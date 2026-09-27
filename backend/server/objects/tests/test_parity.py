"""§8 parity. Text towers and OWLv2 weights download from Hugging Face; embeddings are committed."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import numpy as np
import pytest

from server.objects.config import Detection
from server.objects.proposals import nms_same_class

ROOT = Path(__file__).resolve().parents[3]
FIX = ROOT / "data" / "fixtures" / "objects"


def _load_emb(model: str) -> dict[str, np.ndarray]:
    doc = json.loads((FIX / "b3" / "emb20.json").read_text())
    out = {}
    for fid, payload in doc[model].items():
        out[fid] = np.frombuffer(base64.b64decode(payload), dtype=np.float32).copy()
    return out


def _breadth_root() -> Path | None:
    """Mac agent stores keep files under `files/`. Cloud stores are the directory itself."""
    roots = [
        Path.home() / "Library/Application Support/Cursor/AgentStores/cursor_agent_stores",
        Path("/cursor/stores"),
    ]
    for stores in roots:
        if not stores.is_dir():
            continue
        for child in stores.iterdir():
            for base in (child / "files", child):
                if (base / "internal/data/houses").is_dir():
                    return base
    return None


def test_class_scores_match_cached_embeddings(monkeypatch):
    """20 committed B3 image embeddings, scored with the text towers, within 1e-4."""
    monkeypatch.setenv("HSS_OBJECTS_DEVICE", "cpu")
    pytest.importorskip("torch")
    pytest.importorskip("open_clip")
    pytest.importorskip("transformers")
    from server.objects.gate import class_scores

    ref = json.loads((FIX / "b3" / "gate_scores_all.json").read_text())
    worst = 0.0
    for model in ("clip", "siglip"):
        emb = _load_emb(model)
        got = class_scores(emb, model)
        assert len(got) == 20
        for fid, row in got.items():
            for cls, score in row.items():
                worst = max(worst, abs(score - ref[model][fid][cls]))
    assert worst <= 1e-4


def test_owlv2_boxes_match_b1_on_ten_images(monkeypatch):
    """10 B1 house images: same labels, boxes within 2 px, scores within 0.01."""
    monkeypatch.setenv("HSS_OBJECTS_DEVICE", "cpu")
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    from server.objects.config import FrameRef
    from server.objects.prompts import B1_CLASSES
    from server.objects.proposals import run_owlv2

    manifest = json.loads((FIX / "b1" / "owl_parity_images.json").read_text())
    ref = {}
    for line in (FIX / "b1" / "owlv2_parity.jsonl").read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            ref[row["id"]] = row
    root = _breadth_root()
    if root is None:
        pytest.skip("B1 eval images are not on this machine")
    frames = []
    for row in manifest:
        path = root / row["rel"]
        if not path.is_file():
            pytest.skip(f"missing eval image {row['id']}")
        frames.append(FrameRef(row["id"], path, 0, 0, "keyframe"))
    got = run_owlv2(frames, queries=B1_CLASSES, device="cpu")
    worst_px = 0.0
    worst_score = 0.0
    for row in manifest:
        fid = row["id"]
        width, height = ref[fid]["size"]
        expected = nms_same_class(
            [Detection(det["label"], det["score"], det["box"]) for det in ref[fid]["dets"] if det["score"] >= 0.08]
        )
        found = got[fid]
        assert len(found) == len(expected)
        expected = sorted(expected, key=lambda det: -det.score)
        found = sorted(found, key=lambda det: -det.score)
        for left, right in zip(expected, found, strict=True):
            assert left.label == right.label
            worst_score = max(worst_score, abs(left.score - right.score))
            for index, side in enumerate((width, height, width, height)):
                worst_px = max(worst_px, abs(left.box[index] - right.box[index]) * side)
    assert worst_px <= 2.0
    assert worst_score <= 0.01
