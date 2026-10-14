"""Regenerating the e2e cases replaces only the files the generator owns."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "e2e" / "cases" / "generate.py"
spec = importlib.util.spec_from_file_location("generate_cases", SCRIPT)
generate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = generate  # dataclasses look their module up there
spec.loader.exec_module(generate)


def test_hand_written_cases_survive_and_stale_generated_ones_go(tmp_path):
    hand = tmp_path / "manually-added-regression.json"
    hand.write_text(json.dumps({"id": "manually-added-regression", "scene": {}}))
    stale = tmp_path / "renamed-away.json"
    stale.write_text(json.dumps({"generated_by": generate.GENERATED_BY, "id": "renamed-away"}))
    generate.main(tmp_path)
    assert hand.exists()
    assert not stale.exists()
    written = {p.stem for p in tmp_path.glob("*.json")} - {hand.stem}
    assert written == {c["id"] for c in generate.CASES}


def test_a_hand_written_file_with_a_generated_id_is_refused(tmp_path):
    taken = generate.CASES[0]["id"]
    (tmp_path / f"{taken}.json").write_text(json.dumps({"id": taken}))
    with pytest.raises(SystemExit, match="written by hand"):
        generate.main(tmp_path)
    assert json.loads((tmp_path / f"{taken}.json").read_text()) == {"id": taken}


def test_the_committed_cases_are_what_the_generator_writes(tmp_path):
    generate.main(tmp_path)
    committed = SCRIPT.parent
    for f in tmp_path.glob("*.json"):
        assert f.read_text() == (committed / f.name).read_text(), f.name


def test_fixture_geometric_baselines_match_public_rules():
    import yaml

    rules = yaml.safe_load((SCRIPT.parents[3] / "server" / "rules.yaml").read_text())
    assert pytest.approx(rules["battery"]["width_ft"]["value"]) == generate.W
    assert rules["route"]["corner_allowance_ft"]["value"] == generate.CORNER_ALLOWANCE
    for case in generate.CASES:
        assert case["rules_assumed"]["battery_width"] == generate.W
    for cid in [
        "g03-negative-s-near-edge",
        "g05-inside-corner-gas",
        "g11b-both-ends-real-all-fail",
        "g12b-exact-fit-last-start",
    ]:
        case = next(c for c in generate.CASES if c["id"] == cid)
        assert case["rules_assumed"]["corner_allowance"] == generate.CORNER_ALLOWANCE


@pytest.mark.parametrize("origin", ["committed", "generated"])
@pytest.mark.parametrize("override", ["width", "corner"])
def test_real_case_rule_override_skips_stale_expectation(tmp_path, monkeypatch, origin, override):
    from dataclasses import replace

    from hsverify import e2e
    from hsverify.resultcheck import RuleSet, expectation_problems

    if origin == "generated":
        generate.main(tmp_path)
        folder = tmp_path
    else:
        folder = SCRIPT.parent
    fixture = e2e.load_input(folder / "g12b-exact-fit-last-start.json")
    public = (SCRIPT.parents[3] / "server" / "rules.yaml").read_text()
    baseline = RuleSet.from_yaml(public)
    rules = (
        replace(baseline, width_ft=4.0)
        if override == "width"
        else replace(baseline, corner_allowance_ft=10.0)
    )
    # A wider battery cannot fit the exact-width segment; a high corner allowance loses reach.
    answer = {"decision": "reject", "spot": None, "checks": [], "sweep": []}
    assert expectation_problems(fixture.expect, answer)  # stale fit assertion would fail
    monkeypatch.setattr(
        e2e, "post", lambda *args, **kwargs: (200, json.dumps(answer).encode(), 1.0)
    )
    monkeypatch.setattr(e2e, "schema_errors", lambda *args: [])
    monkeypatch.setattr(e2e, "invariant_problems", lambda *args, **kwargs: [])
    monkeypatch.setattr(e2e, "property_problems", lambda *args: [])

    def forbidden(*args):
        pytest.fail("a mismatched case must not apply its stale expectations")

    monkeypatch.setattr(e2e, "expectation_problems", forbidden)
    record = e2e.judge(
        "unused",
        e2e.Endpoint("/place", "application/json"),
        fixture,
        {"scene": {}, "result": {}},
        rules,
    )
    assert record["status"] == "assumptions unverified" and record["assumption_warnings"]
    assert "assumed public rules; server may override" in record["assumption_warnings"][0]
    assert record["rule_checks_unverified"]
    assert record["contract_problems"] == []
    assert record["expectation_problems"] == []
    meta = {
        "peak_memory": {},
        "server_ref": "public",
        "server_sha": "a" * 40,
        "started_at": "test",
        "endpoint": "mock",
        "load_average_1_5_15": [],
    }
    probes = [
        {"name": name, "hostile": True, "hostile_probe_executed": True, "status": "pass"}
        for name in e2e.HOSTILE_PROBE_NAMES
    ]
    report = e2e.write_report(tmp_path, meta, [record, *probes])
    assert not report["all_passed"] and not report["full_contract_ok"]
