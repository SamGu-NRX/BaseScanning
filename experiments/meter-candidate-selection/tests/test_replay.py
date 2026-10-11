"""Replay: the committed presentation files recompute from results/raw alone."""

import json

import pytest

from meter_candidates import gold, scoring, tables
from meter_candidates.paths import EXPERIMENT_DIR, RESULTS_DIR

PRESENTATION = [RESULTS_DIR / "cases.csv", RESULTS_DIR / "summary.json", RESULTS_DIR / "tables.md"]

pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in PRESENTATION), reason="results not committed yet"
)


def test_replay_is_byte_identical_to_the_committed_tables():
    manifest = json.loads((EXPERIMENT_DIR / "manifest.json").read_text())
    cases = {case["id"]: case for case in manifest["cases"]}
    labels = gold.load()
    records = []
    for path in sorted((RESULTS_DIR / "raw").glob("*/*.json")):
        result = json.loads(path.read_text())
        records.append(
            scoring.evaluate_case(cases[result["case_id"]], result, labels[result["case_id"]])
        )
    summary = tables.build_summary(records, manifest["arms"])
    assert tables.cases_csv(records) == PRESENTATION[0].read_text()
    assert tables.dumps(summary) == PRESENTATION[1].read_text()
    assert tables.tables_md(summary, records) == PRESENTATION[2].read_text()
