"""Comparing two pipeline runs: byte equality, the largest numeric diff, the wall metric."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

TEXT_FILES = ("geometry.json", "scene.json", "coverage.json", "report.md")
JSON_FILES = ("geometry.json", "scene.json", "coverage.json")
GLB = "model.glb"


def collect_outputs(out: Path) -> dict:
    """What the caller ships, reduced for comparison: four texts and the mesh's digest."""
    outputs = {name: (out / name).read_text() for name in TEXT_FILES}
    outputs[GLB] = hashlib.sha256((out / GLB).read_bytes()).hexdigest()
    geo = json.loads(outputs["geometry.json"])
    outputs["wall"] = {
        "baseline_ft": geo["walls"][0]["baseline"],
        "s_range_ft": geo["walls"][0]["s_range_ft"],
        "ground_height_ft": geo["ground"]["height_ft"],
    }
    return outputs


def outputs_equal(a: dict, b: dict) -> bool:
    return all(a[name] == b[name] for name in (*TEXT_FILES, GLB))


def max_numeric_diff(a, b) -> float:
    """Largest absolute difference between two JSON outputs' numbers; inf on any structural
    change (a key, a list length, a string). Accepts JSON text or a parsed document."""
    da = json.loads(a) if isinstance(a, str) else a
    db = json.loads(b) if isinstance(b, str) else b

    def walk(x, y):
        if isinstance(x, bool) or isinstance(y, bool):
            return 0.0 if x is y else float("inf")
        if isinstance(x, dict):
            if not isinstance(y, dict) or x.keys() != y.keys():
                return float("inf")
            return max((walk(x[k], y[k]) for k in x), default=0.0)
        if isinstance(x, list):
            if not isinstance(y, list) or len(x) != len(y):
                return float("inf")
            return max((walk(i, j) for i, j in zip(x, y, strict=False)), default=0.0)
        if x is None or y is None or isinstance(x, str) or isinstance(y, str):
            return 0.0 if x == y else float("inf")
        return abs(x - y)

    return walk(da, db)


def _canonical(doc, name):
    """scene.json echoes the capture's keyframes in input order; a frame reorder legitimately
    permutes that list, so compare it id-sorted."""
    if name == "scene.json" and isinstance(doc, dict) and isinstance(doc.get("keyframes"), list):
        return {**doc, "keyframes": sorted(doc["keyframes"], key=lambda k: str(k.get("id", "")))}
    return doc


def json_diff_ft(a: dict, b: dict) -> float:
    """Largest numeric difference across the two runs' JSON outputs, in the outputs' feet."""
    return max(
        max_numeric_diff(_canonical(json.loads(a[n]), n), _canonical(json.loads(b[n]), n))
        for n in JSON_FILES
    )
