import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import validate_table

ROOT = Path(__file__).resolve().parents[3]
TABLE = ROOT / "data/meter-models/meter_models.v1.json"
SCHEMA = ROOT / "data/meter-models/meter_models.schema.json"


def _inches(value: str) -> float:
    metres = Decimal(value) * Decimal("0.0254")
    return float(metres.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def test_shipped_table_passes():
    assert validate_table.main([str(TABLE)]) == 0
    document = json.loads(TABLE.read_text())
    assert validate_table.validate(document, schema_path=SCHEMA) == []
    assert len(document["entries"]) >= 20
    usable = [entry for entry in document["entries"] if entry["usable_for_scale"]]
    assert len(usable) == 5
    assert {entry["model_id"] for entry in usable} == {
        "itron-centron-c1s-poly",
        "itron-centron-c1s-glass",
        "itron-openway-riva-centron",
        "itron-gen5-riva",
        "elster-a3-alpha",
    }
    poly = next(entry for entry in document["entries"] if entry["model_id"] == "itron-centron-c1s-poly")
    assert poly["cover_od_m"] == _inches("6.29")
    assert poly["base_od_m"] == _inches("6.95")
    assert poly["depth_overall_m"] == _inches("4.53")
    glass = next(entry for entry in document["entries"] if entry["model_id"] == "itron-centron-c1s-glass")
    assert glass["cover_od_m"] == _inches("6.42")
    assert glass["depth_overall_m"] == _inches("4.9")
    riva = next(entry for entry in document["entries"] if entry["model_id"] == "itron-openway-riva-centron")
    gen5 = next(entry for entry in document["entries"] if entry["model_id"] == "itron-gen5-riva")
    for entry in (riva, gen5):
        assert entry["cover_od_m"] == _inches("6.30")
        assert entry["base_od_m"] == _inches("6.95")
        assert entry["depth_overall_m"] == _inches("5.27")
        assert "page 4" in entry["source"]["doc"]
        assert "letter G" in entry["source"]["doc"]
    alpha = next(entry for entry in document["entries"] if entry["model_id"] == "elster-a3-alpha")
    assert alpha["cover_od_m"] == 0.162
    assert alpha["base_od_m"] == 0.177
    assert "figure 2-6" in alpha["source"]["doc"]
    for entry in document["entries"]:
        for dim in validate_table.DIMS:
            assert entry[f"{dim}_prov"] != "assumed"


def test_assumed_number_is_refused():
    document = json.loads(TABLE.read_text())
    document["entries"][0]["cover_od_prov"] = "assumed"
    errors = validate_table.validate(document, schema_path=SCHEMA)
    assert any("assumed is refused" in error for error in errors)


def test_ring_must_exceed_cover_and_url_is_required(tmp_path: Path):
    entry = json.loads(TABLE.read_text())["entries"][0]
    entry["ring_od_m"] = entry["cover_od_m"]
    entry["ring_od_prov"] = "datasheet"
    document = {"format": "meter-models/v1", "entries": [entry]}
    errors = validate_table.validate(document)
    assert any("ring_od_m" in error for error in errors)
    entry["ring_od_m"] = entry["cover_od_m"] + 0.01
    entry["source"] = {"doc": "sheet"}
    errors = validate_table.validate({"format": "meter-models/v1", "entries": [entry]})
    assert any("source.url" in error for error in errors)
    entry["cover_od_m"] = 0.1
    entry["source"] = {"doc": "sheet", "url": "https://example.invalid/sheet", "accessed": "2026-09-26"}
    errors = validate_table.validate({"format": "meter-models/v1", "entries": [entry]})
    assert any("outside" in error for error in errors)
    assert tmp_path is not None
