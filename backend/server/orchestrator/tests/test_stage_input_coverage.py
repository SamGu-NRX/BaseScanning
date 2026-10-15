"""Each stage's declared inputs must cover every artifact path its body reads."""

from __future__ import annotations

import ast
from pathlib import Path

from server.orchestrator.graph import STAGE_INPUTS, STAGES, _input_match

_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_ROOTS = frozenset(
    {
        "packet",
        "poses",
        "objects",
        "reads",
        "early",
        "scale",
        "dense",
        "coverage",
        "scene",
        "placement",
        "verdicts",
        "result",
    }
)
_ENTRY = {
    "validate": "packet/__init__.py",
    "poses": "poses/__init__.py",
    "objects": "objects/__init__.py",
    "reads": "reads/__init__.py",
    "scale": "scale/run.py",
    "dense": "dense/run.py",
    "coverage": "coverage/run.py",
    "scene": "scene/run.py",
    "objects_3d": "objects/run.py",
    "placement": "placement/adapter.py",
    "criteria": "criteria/__init__.py",
    "result": "result/compose.py",
}


def _const(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _from_work_dir(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute) and node.attr == "work_dir":
        return True
    if isinstance(node, ast.Name) and node.id == "work":
        return True
    if isinstance(node, ast.Call) and _call_name(node.func) == "Path" and node.args:
        return _from_work_dir(node.args[0])
    return False


def _join(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.BinOp) and isinstance(current.op, ast.Div):
        text = _const(current.right)
        if text is None:
            return None
        parts.append(text)
        current = current.left
    if not _from_work_dir(current):
        return None
    return "/".join(reversed(parts))


def _bound_join(node: ast.AST, bound: dict[str, str]) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.BinOp) and isinstance(current.op, ast.Div):
        text = _const(current.right)
        if text is None:
            return None
        parts.append(text)
        current = current.left
    if isinstance(current, ast.Name) and current.id in bound:
        prefix = bound[current.id].rstrip("/")
        return "/".join([prefix, *reversed(parts)]) if parts else prefix
    return None


def _note(node: ast.AST, bound: dict[str, str], found: set[str]) -> None:
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        root = _join(node.value)
        extra = _bound_join(node.value, bound)
        if root:
            bound[node.targets[0].id] = root
        elif extra:
            bound[node.targets[0].id] = extra
    joined = _join(node) or _bound_join(node, bound)
    if joined and "/" in joined and not joined.startswith(("packet", "captures")):
        found.add(joined)
    if isinstance(node, ast.Call):
        name = _call_name(node.func)
        if name in {"_artifact", "_work_dir_input"}:
            for arg in node.args:
                text = _const(arg)
                if text and "/" in text:
                    found.add(text)
        if name == "get" and node.args:
            text = _const(node.args[0])
            if text and "/" in text and not text.startswith(("packet", "captures/")):
                found.add(text)
    if isinstance(node, ast.JoinedStr):
        parts = [_const(value) or "" for value in node.values]
        text = "".join(parts)
        if text.startswith(("coverage/", "scene/", "dense/")):
            found.add(text.split("{")[0])
    for child in ast.iter_child_nodes(node):
        _note(child, bound, found)


def _reads(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for fn in [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]:
        _note(fn, {}, found)
    return found


def test_declared_inputs_cover_every_body_read() -> None:
    missing: list[str] = []
    for stage, spec in STAGES.items():
        reads = _reads(_ROOT / _ENTRY[stage])
        outputs = set(spec.artifacts)
        for key in sorted(reads):
            if key.split("/", 1)[0] not in _ARTIFACT_ROOTS:
                continue
            if key in outputs or key.rstrip("/") + "/" in outputs:
                continue
            if _input_match(key, STAGE_INPUTS[stage]):
                continue
            if key.endswith("/") and any(item.startswith(key) for item in STAGE_INPUTS[stage]):
                continue
            missing.append(f"{stage} reads {key}")
    assert missing == []
