"""Shared fixtures: the recon package on the path, the MoGe-2 model stubbed for every test."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

EXPERIMENT = Path(__file__).resolve().parents[1]
REPO = EXPERIMENT.parents[1]
for _p in (str(REPO / "recon"), str(EXPERIMENT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from recon import depth, pipeline  # noqa: E402

import bundles  # noqa: E402


def _identity_rescale(capture, depths):
    report = {
        "fitted": 0,
        "frames": len(depths),
        "median_scale": 1.0,
        "scale_range": [1.0, 1.0],
        "per_frame": {},
    }
    return depths, report


@pytest.fixture(autouse=True)
def stub_model():
    """The analytic stub in place of the MoGe-2 subprocess for the whole test."""
    run, calls = bundles.make_stub_model()
    saved = (subprocess.run, depth.rescale)
    subprocess.run = run
    depth.rescale = _identity_rescale
    yield calls
    subprocess.run, depth.rescale = saved


@pytest.fixture
def pipeline_run(stub_model):
    """Run the real pipeline on a bundle; returns the model-call count of the run."""

    def go(bundle: Path, out: Path, work: Path) -> int:
        before = stub_model["n"]
        pipeline.run(bundle, out, work, "moge", None, False)
        return stub_model["n"] - before

    return go
