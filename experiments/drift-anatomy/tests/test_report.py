"""The cached run must distinguish path straightness from wall orientation."""

import json
from pathlib import Path

from run import markdown


def test_straight_path_report_qualifies_the_filter():
    def numeric_keys(pairs):
        def key(value):
            try:
                return int(value) if value.isdigit() else float(value)
            except ValueError:
                return value

        return {
            key(k): ({f"{m:.2f}": z for m, z in v.items()} if k == "straight_path" else v)
            for k, v in pairs
        }

    report = json.loads(
        (Path(__file__).parents[1] / "results/drift_anatomy.json").read_text(),
        object_pairs_hook=numeric_keys,
    )
    rendered = markdown(*(report[k] for k in ("meta", "a1", "a2", "a3", "a4", "a5")))
    assert "### Straight-path check" in rendered
    assert "Max chord deviation" in rendered
    assert "cannot identify wall-parallel travel" in rendered
    assert "measure the benefit of a wall-plane constraint" in rendered
    assert "stand in for a walk along a wall" not in rendered
    assert rendered == (Path(__file__).parents[1] / "results/drift_anatomy.md").read_text()
