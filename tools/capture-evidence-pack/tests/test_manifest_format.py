"""Format validation for capture evidence pack manifests (SPEC.md)."""

import validate


def assert_problems(manifest, needle):
    problems = validate.validate_manifest(manifest)
    assert problems, "expected the manifest to be refused"
    assert any(needle in problem for problem in problems), problems


def test_valid_manifest_passes(valid_manifest):
    assert validate.validate_manifest(valid_manifest) == []


def test_rejects_missing_capture_schema_version(valid_manifest):
    del valid_manifest["capture_schema_version"]
    assert_problems(valid_manifest, "capture_schema_version")


def test_rejects_missing_pack_format_version(valid_manifest):
    del valid_manifest["evidence_pack_format_version"]
    assert_problems(valid_manifest, "evidence_pack_format_version")


def test_rejects_unknown_pack_format_version(valid_manifest):
    valid_manifest["evidence_pack_format_version"] = "2.0"
    assert_problems(valid_manifest, "evidence_pack_format_version")


def test_rejects_unknown_top_level_key(valid_manifest):
    valid_manifest["geometry_correct"] = True
    assert_problems(valid_manifest, "geometry_correct")


def test_rejects_empty_source_refs(valid_manifest):
    valid_manifest["capture"]["source_refs"] = []
    assert_problems(valid_manifest, "source_refs")


def test_rejects_bad_sha256_in_source_ref(valid_manifest):
    valid_manifest["capture"]["source_refs"][0]["sha256"] = "NOTAHASH"
    assert_problems(valid_manifest, "sha256")


def test_rejects_escaping_source_path(valid_manifest):
    valid_manifest["capture"]["source_refs"][0]["path"] = "../../etc/passwd"
    assert_problems(valid_manifest, "path")


def test_rejects_result_without_id(valid_manifest):
    valid_manifest["result"]["result_id"] = ""
    assert_problems(valid_manifest, "result_id")


def test_missing_fields_key_is_required(valid_manifest):
    del valid_manifest["missing_fields"]
    assert_problems(valid_manifest, "missing_fields")


def test_missing_fields_entries_need_field_names(valid_manifest):
    valid_manifest["missing_fields"][0]["field"] = "  "
    assert_problems(valid_manifest, "missing_fields[0]")


def test_rejects_empty_reproduction_command(valid_manifest):
    valid_manifest["reproduction"]["command"] = ""
    assert_problems(valid_manifest, "reproduction.command")


def test_rejects_non_object_manifest():
    assert validate.validate_manifest(["not", "an", "object"]) == ["manifest: expected a JSON object"]
