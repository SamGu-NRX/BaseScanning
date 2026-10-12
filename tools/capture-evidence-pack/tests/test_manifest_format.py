"""Format validation for capture evidence pack manifests (SPEC.md)."""

import json

import pytest
import validate
from support import EXAMPLE_FIXTURE


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


def test_rejects_missing_captured_at(valid_manifest):
    del valid_manifest["capture"]["captured_at"]
    assert_problems(valid_manifest, "captured_at")


def test_rejects_malformed_captured_at(valid_manifest):
    valid_manifest["capture"]["captured_at"] = "2026-13-45T99:99:99Z"
    assert_problems(valid_manifest, "captured_at")


def test_rejects_calendar_impossible_captured_at(valid_manifest):
    valid_manifest["capture"]["captured_at"] = "2026-02-30T00:00:00Z"
    assert_problems(valid_manifest, "captured_at")


def test_rejects_non_padded_captured_at(valid_manifest):
    valid_manifest["capture"]["captured_at"] = "2026-1-5T00:00:00Z"
    assert_problems(valid_manifest, "captured_at")


def test_rejects_null_missing_fields(valid_manifest):
    valid_manifest["missing_fields"] = None
    assert_problems(valid_manifest, "missing_fields")


def test_missing_fields_entries_need_detail(valid_manifest):
    del valid_manifest["missing_fields"][0]["detail"]
    assert_problems(valid_manifest, "missing_fields[0].detail")


# Present-but-null required values are checked, never skipped as absent.
NULLABLE_REQUIRED = [
    "evidence_pack_format_version",
    "capture_schema_version",
    "result_schema_version",
    "capture",
    "result",
    "reproduction",
    "geometry_correctness",
    "packaged_files",
]


@pytest.mark.parametrize("key", NULLABLE_REQUIRED)
def test_explicit_null_required_field_is_refused(valid_manifest, key):
    valid_manifest[key] = None
    assert_problems(valid_manifest, key)


def test_rejects_missing_result_schema_version(valid_manifest):
    del valid_manifest["result_schema_version"]
    assert_problems(valid_manifest, "result_schema_version")


@pytest.mark.parametrize("value", [None, 5, "", "   "])
def test_rejects_malformed_result_id_claim(valid_manifest, value):
    valid_manifest["result"]["result_id"] = value
    assert_problems(valid_manifest, "result.result_id")


def valid_pack_spec():
    return json.loads((EXAMPLE_FIXTURE / "pack-spec.json").read_text())


def test_example_pack_spec_is_valid():
    assert validate.validate_pack_spec(valid_pack_spec()) == []


@pytest.mark.parametrize("report_path", ["notes/report.md", "report.txt", None, ""])
def test_pack_spec_refuses_a_report_path_other_than_report_md(report_path):
    spec = valid_pack_spec()
    spec["report_path"] = report_path
    problems = validate.validate_pack_spec(spec)
    assert any(problem.startswith("report_path:") for problem in problems), problems


def test_pack_spec_requires_result_schema_version():
    spec = valid_pack_spec()
    del spec["result_schema_version"]
    assert any("result_schema_version" in problem for problem in validate.validate_pack_spec(spec))


def test_pack_spec_refuses_unknown_keys():
    spec = valid_pack_spec()
    spec["result_schema_versoin"] = "typo"
    assert any("result_schema_versoin: unknown" in problem for problem in validate.validate_pack_spec(spec))
