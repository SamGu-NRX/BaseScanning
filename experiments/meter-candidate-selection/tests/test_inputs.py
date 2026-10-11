"""The frozen inputs are what they claim: reproducible, complete, and gold-free."""

import ast
import json
import re

from meter_candidates import generate, gold
from meter_candidates.paths import (
    EXPERIMENT_DIR,
    GOLD_PATH,
    IMAGES_DIR,
    OBSERVATIONS_DIR,
)

SRC = EXPERIMENT_DIR / "src" / "meter_candidates"


def _bytes(paths) -> dict:
    return {p.name: p.read_bytes() for p in paths}


def test_regeneration_reproduces_every_frozen_byte(tmp_path):
    """The committed inputs are a pure function of generate.py: same bytes back."""
    written = generate.write_inputs(tmp_path / "inputs", tmp_path / "gold" / "labels.json")
    regenerated = {p.name: p.read_bytes() for p in written}
    frozen = _bytes(IMAGES_DIR.glob("*.png"))
    frozen |= _bytes(OBSERVATIONS_DIR.glob("*.json"))
    frozen |= {GOLD_PATH.name: GOLD_PATH.read_bytes()}
    assert regenerated == frozen


def test_case_counts():
    """18 generated plates (15 with a serial, 3 without), 12 hand-authored sets
    (9 with, 3 without): the generator and ranking case counts the README commits."""
    assert len(generate.PLATES) == 18
    assert sum(g["present"] for g in generate.gold().values()) == 24
    assert len(generate.OBSERVATIONS) == 12
    labels = gold.load()
    assert len(labels) == 30
    present = [g for g in labels.values() if g["present"]]
    assert len(present) == 24 and len(labels) - len(present) == 6


def test_serials_are_synthetic_eight_digits():
    for case_id, g in gold.load().items():
        if g["present"]:
            assert re.fullmatch(r"[1-9]\d{7}", g["serial"]), case_id


def test_reader_inputs_carry_no_gold():
    """Observation files are reader output only: no label fields anywhere in them."""
    for path in sorted(OBSERVATIONS_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        assert set(data) == {"lines", "barcodes"}, path.name
        for line in data["lines"]:
            assert set(line) == {"text", "box"} and len(line["box"]) == 4


def _imports(path) -> set:
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


def test_candidate_and_ranking_code_never_reads_gold():
    """Extraction, ranking and the reader arm join gold nowhere: only scoring does,
    by case id, after ranking."""
    for name in ("candidates", "ranking", "tesseract"):
        modules = _imports(SRC / f"{name}.py")
        assert not any("gold" in m for m in modules), name


def test_manifest_matches_gold():
    manifest = json.loads((EXPERIMENT_DIR / "manifest.json").read_text())
    labels = gold.load()
    cases = {case["id"]: case for case in manifest["cases"]}
    assert set(cases) == set(labels)
    for case_id, case in cases.items():
        assert case["present"] == labels[case_id]["present"], case_id
