"""SPEC.md and the code must stay aligned: the spec names every required
manifest field, every refusal kind, and states the identity/correctness split."""

from pathlib import Path

import validate

SPEC = Path(__file__).resolve().parents[1] / "SPEC.md"


def test_spec_names_every_required_manifest_field():
    text = SPEC.read_text(encoding="utf-8")
    for key in validate.REQUIRED_TOP_LEVEL:
        assert f"`{key}`" in text, f"SPEC.md never mentions {key}"


def test_spec_names_every_refusal_kind():
    text = SPEC.read_text(encoding="utf-8")
    for kind in ("missing-source", "mismatched-result", "invalid-manifest", "wrong-pack-format"):
        assert f"`{kind}`" in text, f"SPEC.md never mentions the {kind} refusal"


def test_spec_states_identity_is_not_correctness():
    text = SPEC.read_text(encoding="utf-8")
    assert "## Identity is not correctness" in text
