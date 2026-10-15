"""Validate ``data/meter-models/meter_models.v1.json``.

Exit 0 when the table passes. Exit 1 when a check fails. Exit 2 when the
path is missing or the JSON does not parse. Assumed numbers are refused,
including the generic-prior row the brief once allowed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema

PROVENANCE = ("datasheet", "standard", "caliper", "derived", "assumed", "missing")
DIMS = ("cover_od", "ring_od", "base_od", "rim_offset", "depth_overall")
SBASE_M = 0.1765
SBASE_TOL_M = 0.002
COVER_MIN_M = 0.150
COVER_MAX_M = 0.185


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate(doc: dict, *, schema_path: Path | None = None) -> list[str]:
    errors: list[str] = []
    if schema_path is not None:
        schema = json.loads(schema_path.read_text())
        validator = jsonschema.Draft202012Validator(schema)
        for err in sorted(validator.iter_errors(doc), key=lambda item: list(item.path)):
            errors.append(f"schema: {err.json_path}: {err.message}")
    if doc.get("format") != "meter-models/v1":
        errors.append("format must be meter-models/v1")
    entries = doc.get("entries")
    if not isinstance(entries, list):
        return [*errors, "entries must be a list"]
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"entries[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{where} must be an object")
            continue
        model_id = entry.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            errors.append(f"{where}: model_id is required")
        elif model_id in seen:
            errors.append(f"{model_id}: duplicate model_id")
        else:
            seen.add(str(model_id))
            where = str(model_id)
        provs: list[str] = []
        for dim in DIMS:
            value = entry.get(f"{dim}_m")
            prov = entry.get(f"{dim}_prov")
            if prov not in PROVENANCE:
                errors.append(f"{where}: {dim}_prov is missing or unknown")
                continue
            provs.append(prov)
            if prov == "assumed":
                errors.append(f"{where}: {dim}_prov assumed is refused")
            elif prov == "missing":
                if value is not None:
                    errors.append(f"{where}: {dim}_m must be null when provenance is missing")
            elif not _number(value):
                errors.append(f"{where}: {dim}_m must be a number when provenance is {prov}")
        cover = entry.get("cover_od_m")
        ring = entry.get("ring_od_m")
        base = entry.get("base_od_m")
        if _number(cover) and not (COVER_MIN_M <= float(cover) <= COVER_MAX_M):
            errors.append(f"{where}: cover_od_m {cover} is outside {COVER_MIN_M}..{COVER_MAX_M}")
        if _number(cover) and _number(ring) and not float(ring) > float(cover):
            errors.append(f"{where}: ring_od_m must be greater than cover_od_m")
        if _number(base) and abs(float(base) - SBASE_M) > SBASE_TOL_M:
            errors.append(f"{where}: base_od_m {base} is outside the S-base band {SBASE_M} ± {SBASE_TOL_M}")
        if entry.get("usable_for_scale") is True and entry.get("cover_od_prov") not in {"datasheet", "caliper"}:
            errors.append(f"{where}: usable_for_scale requires cover_od_prov datasheet or caliper")
        if "caliper" in provs:
            for key in ("measured_by", "n_units", "measured_on", "readings_m"):
                if key not in entry:
                    errors.append(f"{where}: caliper row missing {key}")
            readings = entry.get("readings_m")
            if not isinstance(readings, list) or not readings:
                errors.append(f"{where}: caliper readings_m must be a non-empty list")
        needs_source = any(prov in {"datasheet", "standard", "derived"} for prov in provs)
        if needs_source:
            source = entry.get("source")
            if not isinstance(source, dict) or not source.get("url") or not source.get("accessed"):
                errors.append(f"{where}: source.url and source.accessed are required")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: validate_table.py <meter_models.v1.json>", file=sys.stderr)
        return 2
    path = Path(args[0])
    if not path.is_file():
        print(f"missing input: {path}", file=sys.stderr)
        return 2
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        print(f"invalid json: {exc}", file=sys.stderr)
        return 2
    schema = path.with_name("meter_models.schema.json")
    errors = validate(doc, schema_path=schema if schema.is_file() else None)
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        return 1
    usable = sum(1 for entry in doc["entries"] if entry.get("usable_for_scale") is True)
    print(f"ok entries={len(doc['entries'])} usable_for_scale={usable} table_version={doc.get('table_version')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
