import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

import geom


def function(name, namespace):
    path = Path(__file__).resolve().parents[1] / "run.py"
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


def test_harness_must_match_full_pinned_commit():
    expected = "190ed339f41924ae0244e0b88c5ebe647ddd9eb4"
    run = Mock(return_value=SimpleNamespace(stdout=expected + "\n", returncode=0))
    check = function("evals_commit", {"subprocess": SimpleNamespace(run=run), "EVALS_DIR": Path("harness"), "EVALS_COMMIT": expected})
    assert check() == expected
    for head, code in [("190ed33different", 0), ("", 128)]:
        run.return_value = SimpleNamespace(stdout=head, returncode=code)
        with pytest.raises(SystemExit, match="expected"):
            check()


def test_unpaired_points_reduce_p4_match_rate():
    grade = function("grade", {"np": np, "geom": geom, "p90_in": lambda x: 1.0, "NCC_MIN": .8})
    single = {"angle": np.array([5., 40.]), "m0": np.zeros((2, 3)), "m1": np.zeros((2, 3)), "in_plane": np.ones(2, bool)}
    ma = {"m2_span": np.zeros((4, 3)), "in_plane": np.ones(4, bool), "m3_pair": np.array([True, True, False, False]), "m3_ncc": np.ones(4), "m3_frame": np.zeros((4, 3)), "m3_span": np.zeros((4, 3))}
    result = grade(single, {"modern_assumed": ma})["P4"]
    assert result["match_rate"] == .5
    assert result["pass"] is False
