"""Loader for the meter dimension table. The table file itself belongs to WS-13."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import jsonschema

from server.scale.reads_view import MeterIdentity

_METRE_FIELDS = ("cover_od_m", "ring_od_m", "base_od_m", "rim_offset_m", "depth_overall_m")
_PROV_OK = {"datasheet", "standard", "caliper", "derived", "assumed", "missing"}
_SCHEMA_RELATIVE = Path("data/meter-models/meter_models.schema.json")


def _schema_path() -> Path | None:
    """WS-13's schema, found from this package so a test's cwd cannot hide it."""

    candidates = [_SCHEMA_RELATIVE]
    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / _SCHEMA_RELATIVE)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


class TableError(ValueError):
    pass


@dataclass
class TableMatch:
    model_id: str
    match: str
    cover_hypotheses: list[tuple[float, float]]
    rim_offset_m: float | None
    ring_od_m: float | None
    base_od_m: float | None
    cover_od_prov: str


@dataclass
class TableMiss:
    code: str


class MeterTable:
    def __init__(self, table_version: str, entries: list[dict]) -> None:
        self.table_version = table_version
        self.entries = entries

    @classmethod
    def load(cls, path: Path) -> MeterTable:
        payload = json.loads(Path(path).read_text())
        schema = _schema_path()
        if schema is not None:
            try:
                jsonschema.validate(payload, json.loads(schema.read_text()))
            except jsonschema.ValidationError as exc:
                raise TableError(exc.message) from exc
        problems = validate_table(payload)
        if problems:
            raise TableError("; ".join(problems))
        return cls(str(payload["table_version"]), list(payload["entries"]))

    def lookup(self, ident: MeterIdentity, *, allow_assumed: bool = False) -> TableMatch | TableMiss:
        groups = _family_groups(self.entries)
        code = _matched_type_code(ident.model_family, self.entries)
        chosen: list[dict] | None = None
        how = ""
        if code:
            chosen = [entry for entry in self.entries if code in (entry.get("type_codes") or [])]
            if chosen:
                how = "type_code"
        if not chosen:
            chosen = _family_hits(ident, groups)
            if chosen:
                how = "family_material"
        if not chosen:
            fuzzy = _fuzzy_hit(ident, groups)
            if fuzzy is not None:
                chosen = fuzzy
                how = "fuzzy"
        if not chosen:
            return TableMiss("meter_model_not_in_table")
        return _to_match(ident, chosen, how, allow_assumed=allow_assumed)


def validate_table(payload: dict) -> list[str]:
    """WS-13's checks, applied in the loader until that validator is on main."""

    problems: list[str] = []
    if payload.get("format") != "meter-models/v1":
        problems.append("format")
    if not payload.get("table_version"):
        problems.append("table_version")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return problems + ["entries"]
    assumed_ids = []
    for entry in entries:
        model_id = str(entry.get("model_id") or "")
        for field in _METRE_FIELDS:
            if field not in entry:
                continue
            prov_name = field.replace("_m", "_prov")
            if prov_name not in entry:
                problems.append(f"{model_id}:{field}_without_prov")
                continue
            prov = entry.get(prov_name)
            if prov not in _PROV_OK:
                problems.append(f"{model_id}:{prov_name}")
            value = entry.get(field)
            if prov == "assumed" and isinstance(value, (int, float)):
                assumed_ids.append(model_id)
            if prov in {"datasheet", "standard", "derived", "assumed"} and not _source_ok(entry):
                problems.append(f"{model_id}:source")
        cover = entry.get("cover_od_m")
        prov = entry.get("cover_od_prov")
        usable = bool(entry.get("usable_for_scale"))
        if usable and not (prov in {"datasheet", "caliper"} and isinstance(cover, (int, float))):
            problems.append(f"{model_id}:usable_for_scale")
        if isinstance(cover, (int, float)) and not 0.150 <= float(cover) <= 0.185:
            problems.append(f"{model_id}:cover_range")
        ring = entry.get("ring_od_m")
        if isinstance(cover, (int, float)) and isinstance(ring, (int, float)) and not float(ring) > float(cover):
            problems.append(f"{model_id}:ring_gt_cover")
        base = entry.get("base_od_m")
        if isinstance(base, (int, float)) and abs(float(base) - 0.1765) > 0.002:
            problems.append(f"{model_id}:base_range")
        if prov == "caliper" and not all(
            entry.get(key) is not None for key in ("measured_by", "n_units", "measured_on", "readings_m")
        ):
            problems.append(f"{model_id}:caliper")
    extra_assumed = {item for item in assumed_ids if item != "generic-prior"}
    if extra_assumed:
        problems.append("assumed_outside_generic_prior")
    return problems


def _source_ok(entry: dict) -> bool:
    source = entry.get("source") or {}
    return bool(source.get("url")) and bool(source.get("accessed"))


def normalize_text(value: str | None) -> str:
    text = (value or "").lower().replace("polycarbonate", "poly")
    cleaned = "".join(ch if ch.isalnum() or ch in "+- " else " " for ch in text)
    return " ".join(cleaned.split())


def normalize_material(value: str | None) -> str | None:
    text = normalize_text(value)
    if not text or text in {"unknown", "none"}:
        return None
    tokens = set(text.split())
    if "poly" in tokens:
        return "poly"
    if "glass" in tokens:
        return "glass"
    return None


def _family_key(entry: dict) -> tuple[str, str]:
    return normalize_text(entry.get("manufacturer")), normalize_text(entry.get("model_family"))


def _family_groups(entries: list[dict]) -> dict[tuple[str, str], list[dict]]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for entry in entries:
        groups.setdefault(_family_key(entry), []).append(entry)
    return groups


def _matched_type_code(model_family: str | None, entries: list[dict]) -> str | None:
    tokens: set[str] = set()
    for entry in entries:
        tokens.update(entry.get("type_codes") or [])
    family = model_family or ""
    for token in sorted(tokens, key=len, reverse=True):
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", family, re.IGNORECASE):
            return token
    return None


def _family_hits(ident: MeterIdentity, groups: dict[tuple[str, str], list[dict]]) -> list[dict] | None:
    manufacturer = normalize_text(ident.manufacturer)
    identity_tokens = set(normalize_text(ident.model_family).split())
    hits: list[tuple[int, list[dict]]] = []
    for (maker, family), rows in groups.items():
        if maker != manufacturer or maker == "":
            continue
        needed = set(family.split())
        if needed and needed <= identity_tokens:
            hits.append((len(needed), rows))
    if not hits:
        return None
    hits.sort(key=lambda item: item[0], reverse=True)
    return hits[0][1]


def _fuzzy_hit(ident: MeterIdentity, groups: dict[tuple[str, str], list[dict]]) -> list[dict] | None:
    query = normalize_text(f"{ident.manufacturer or ''} {ident.model_family or ''}")
    if not query:
        return None
    best_ratio = 0.0
    best: list[dict] | None = None
    for (maker, family), rows in groups.items():
        if rows and rows[0].get("model_id") == "generic-prior":
            continue
        ratio = SequenceMatcher(None, query, f"{maker} {family}".strip()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best = rows
    if best is not None and best_ratio >= 0.85:
        return best
    return None


def _to_match(ident: MeterIdentity, rows: list[dict], how: str, *, allow_assumed: bool) -> TableMatch | TableMiss:
    usable = [row for row in rows if row.get("usable_for_scale") and isinstance(row.get("cover_od_m"), (int, float))]
    if not usable:
        provs = {row.get("cover_od_prov") for row in rows}
        if provs & {"assumed", "derived"}:
            if not allow_assumed:
                return TableMiss("meter_dimension_assumed")
            usable = [row for row in rows if isinstance(row.get("cover_od_m"), (int, float))]
        if not usable:
            return TableMiss("meter_model_not_in_table")
    material = normalize_material(ident.cover_material)
    by_material: dict[str | None, dict] = {}
    for row in usable:
        by_material[normalize_material(row.get("cover_material"))] = row
    if material in by_material and len(by_material) > 1:
        primary = by_material[material]
        other = next(row for key, row in by_material.items() if key != material)
        hypotheses = [(float(primary["cover_od_m"]), 0.9), (float(other["cover_od_m"]), 0.1)]
        dominant = primary
    elif material is None and len(by_material) > 1:
        ordered = list(by_material.values())
        hypotheses = [(float(row["cover_od_m"]), 1.0 / len(ordered)) for row in ordered]
        dominant = ordered[0]
    else:
        dominant = by_material.get(material) or usable[0]
        hypotheses = [(float(dominant["cover_od_m"]), 1.0)]
    return TableMatch(
        model_id=str(dominant["model_id"]),
        match=how,
        cover_hypotheses=hypotheses,
        rim_offset_m=_optional_float(dominant.get("rim_offset_m")),
        ring_od_m=_optional_float(dominant.get("ring_od_m")),
        base_od_m=_optional_float(dominant.get("base_od_m")),
        cover_od_prov=str(dominant.get("cover_od_prov")),
    )


def _optional_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None
