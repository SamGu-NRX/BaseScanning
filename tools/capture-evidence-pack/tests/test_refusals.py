"""Refusal cases: identity that cannot be satisfied, and correctness claims.

Each test here represents a pack or manifest the tools must refuse with an
explicit reason naming what mismatched.
"""

import hashlib
import json

import validate


def write_input(root, relative_path, content: bytes) -> str:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def satisfiable_manifest(valid_manifest, root):
    """A manifest whose source and result records describe real files under root."""
    manifest = valid_manifest
    manifest["capture"]["source_refs"][0] = {
        "role": "frame",
        "path": "capture/frame_0001.json",
        "sha256": write_input(root, "capture/frame_0001.json", b'{"frame": 1}'),
        "bytes": len(b'{"frame": 1}'),
    }
    manifest["result"] = {
        "result_id": "result-synth-0001",
        "path": "result/result.json",
        "sha256": write_input(root, "result/result.json", b'{"status": "synthetic"}'),
        "bytes": len(b'{"status": "synthetic"}'),
    }
    return manifest


def test_refuse_non_null_correctness_assertion(valid_manifest):
    """The correctness slot exists but must stay empty in a conforming pack."""
    valid_manifest["geometry_correctness"]["assertion"] = "checked: meter clearance holds"
    problems = validate.validate_manifest(valid_manifest)
    assert any("geometry_correctness.assertion" in problem and "must be null" in problem for problem in problems)


def test_refuse_missing_source_file(valid_manifest, tmp_path):
    """A source ref whose file is absent under the capture root is refused."""
    manifest = satisfiable_manifest(valid_manifest, tmp_path)
    (tmp_path / "capture/frame_0001.json").unlink()
    problems = validate.check_sources(manifest, tmp_path)
    assert any("missing-source" in problem and "frame_0001.json" in problem for problem in problems)


def test_refuse_source_hash_mismatch(valid_manifest, tmp_path):
    """A source ref whose bytes do not match the recorded hash is refused."""
    manifest = satisfiable_manifest(valid_manifest, tmp_path)
    (tmp_path / "capture/frame_0001.json").write_bytes(b'{"frame": tampered}')
    problems = validate.check_sources(manifest, tmp_path)
    assert any("does not satisfy its recorded identity" in problem and "hash" in problem for problem in problems)


def test_refuse_result_hash_mismatch(valid_manifest, tmp_path):
    """Content that differs from the recorded result identity is refused."""
    manifest = satisfiable_manifest(valid_manifest, tmp_path)
    (tmp_path / "result/result.json").write_bytes(b'{"status": "different result"}')
    problems = validate.check_sources(manifest, tmp_path)
    assert any(problem.startswith("result:") and "does not satisfy" in problem for problem in problems)


def test_refuse_source_schema_drift(valid_manifest, tmp_path):
    """A capture input declaring a different schema_version than the manifest records is refused."""
    manifest = satisfiable_manifest(valid_manifest, tmp_path)
    manifest["capture_schema_version"] = "synthetic-capture/2026.10"
    frame = tmp_path / "capture/frame_0001.json"
    data = json.loads(frame.read_text())
    data["schema_version"] = "synthetic-capture/2025.01"
    content = json.dumps(data)
    frame.write_text(content)
    # The pack records the drifted file's true hash: only the schema disagrees.
    ref = manifest["capture"]["source_refs"][0]
    ref["sha256"] = hashlib.sha256(content.encode()).hexdigest()
    ref["bytes"] = len(content)
    problems = validate.check_sources(manifest, tmp_path)
    assert any("schema-drift" in problem and "frame_0001.json" in problem for problem in problems)
