"""The vendored extractor is not patched, so GT builders stay byte-identical."""

from __future__ import annotations

import hashlib
from pathlib import Path

import evalharness.extract as harness_extract

import server.eval.extract as vendored_extract

FACADE_GT = Path("eval/results/eth3d-facade/gt-laser/scene.json")
# sha256 of the committed facade GT scene. WS-06 does not edit evalharness/extract.py.
FACADE_SHA = "5c245024114e8385528cedeffc690fb43dfe661cc071ac716e186752b5a6e39c"


def test_facade_gt_scene_unchanged():
    digest = hashlib.sha256(FACADE_GT.read_bytes()).hexdigest()
    assert digest == FACADE_SHA


def test_extract_scene_is_the_harness():
    assert harness_extract.extract_scene.__module__ == "evalharness.extract"


def test_server_eval_extract_is_vendored():
    """WS-00 vendors the harness. WS-06 does not edit ``server/eval/extract.py``."""
    assert Path("server/eval/extract.py").is_file()
    assert Path("server/eval/eth3d/build_gt.py").is_file()
    assert Path("server/eval/eth3d/build_gt_c2.py").is_file()
    assert vendored_extract.extract_scene.__module__ == "server.eval.extract"
