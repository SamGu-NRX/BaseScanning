import ast
from pathlib import Path
from types import CodeType, FunctionType, SimpleNamespace

import pytest


def test_peak_rss_units():
    path = Path(__file__).resolve().parents[1] / "run.py"
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "peak_rss_mb")
    for platform, rss in [("darwin", 900 * 2**20), ("linux", 900 * 1024)]:
        namespace = {
            "sys": SimpleNamespace(platform=platform),
            "resource": SimpleNamespace(
                RUSAGE_SELF=0, getrusage=lambda _, rss=rss: SimpleNamespace(ru_maxrss=rss)
            ),
        }
        module = compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec")
        code = next(value for value in module.co_consts if isinstance(value, CodeType))
        assert FunctionType(code, namespace)() == pytest.approx(900)
