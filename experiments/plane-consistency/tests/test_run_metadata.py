import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_peak_rss_units():
    path = Path(__file__).resolve().parents[1] / "run.py"
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "peak_rss_mb")
    for platform, rss in [("darwin", 900 * 2**20), ("linux", 900 * 1024)]:
        namespace = {"sys": SimpleNamespace(platform=platform), "resource": SimpleNamespace(RUSAGE_SELF=0, getrusage=lambda _: SimpleNamespace(ru_maxrss=rss))}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
        assert namespace["peak_rss_mb"]() == pytest.approx(900)
