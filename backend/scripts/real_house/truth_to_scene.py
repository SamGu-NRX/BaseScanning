"""Turn a real-house-truth/v0 file into a base-scene/v0 tape scene.

The meter sits at s = 0. A wall's baseline runs from x = 0 to x = length_m
on z = 0, which is correct when the truth measures the wall from the meter
toward the right. Corner and opening s values are copied onto objects; they
do not move the baseline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from redact import contains_live_meter_number, redact


class TruthError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def truth_to_scene(truth: dict, site: str, producer_version: str = "0") -> dict:
    if truth.get("format") != "real-house-truth/v0":
        raise TruthError(2, "format must be real-house-truth/v0")
    if truth.get("house_id") != site:
        raise TruthError(2, "house_id must match --site")
    if contains_live_meter_number(truth):
        raise TruthError(4, "meter_number must be REDACTED before a scene is written")
    cleaned = redact(truth)
    walls_in = cleaned.get("walls") or []
    if not walls_in:
        raise TruthError(2, "walls is required")
    measurements = cleaned.get("measurements") or []
    for measurement in measurements:
        if "photo" not in measurement or "value_m" not in measurement:
            raise TruthError(2, f"measurement {measurement.get('id')} needs value_m and photo")

    walls = []
    for wall in walls_in:
        length = float(wall["length_m"])
        if "height_m" not in wall:
            raise TruthError(2, f"wall {wall.get('id')} needs height_m")
        height_m = float(wall["height_m"])
        if height_m <= 0:
            raise TruthError(2, f"wall {wall.get('id')} height_m must be positive")
        record = {
            "id": wall["id"],
            "role": wall.get("role", "house"),
            "baseline_m": [[0.0, 0.0], [length, 0.0]],
            "base_y_m": 0.0,
            "height_m": height_m,
            "s_range_m": [0.0, length],
            "plus_minus_m": wall.get("plus_minus_m", 0.01),
            "evidence": "observed",
        }
        walls.append(record)
    wall_ids = [wall["id"] for wall in walls]
    meter_wall = wall_ids[0]
    height = _measurement(measurements, "meter.height")
    objects = [
        {
            "id": "meter",
            "class": "meter",
            "label": "meter",
            "geometry": {"kind": "point", "pos_m": [0.0, height if height is not None else 0.0, 0.0]},
            "wall_id": meter_wall,
            "confidence": 1.0,
            "plus_minus_m": 0.01,
            "source": {"kind": "tape"},
            "attrs": {},
            "evidence": "observed",
        }
    ]
    for item in cleaned.get("objects") or []:
        s_pair = item.get("s_m")
        if not s_pair:
            continue
        objects.append(
            {
                "id": item["id"],
                "class": item.get("class", "other"),
                "label": item.get("class", "other"),
                "geometry": {
                    "kind": "wall_rect",
                    "wall_id": item.get("wall_id", meter_wall),
                    "s_m": list(s_pair),
                    "bottom_m": item.get("bottom_m", 0.0),
                    "top_m": item.get("top_m", 0.0),
                    "standoff_m": 0.0,
                },
                "wall_id": item.get("wall_id", meter_wall),
                "confidence": 1.0,
                "plus_minus_m": 0.01,
                "source": {"kind": "tape"},
                "attrs": item.get("attrs") or {},
                "evidence": "observed",
            }
        )
    regions = []
    for region in cleaned.get("ground_regions") or []:
        length = walls[0]["s_range_m"][1]
        far = float(region.get("to_wall_m", 0.0))
        regions.append(
            {
                "id": region["id"],
                "material": region.get("material", "unknown"),
                "polygon_m": [[0.0, 0.0], [length, 0.0], [length, far], [0.0, far]],
                **({"slope_deg": region["slope_deg"]} if region.get("slope_deg") is not None else {}),
                "confidence": 1.0,
                "plus_minus_m": 0.01,
                "evidence": "observed",
                "source": {"kind": "tape"},
            }
        )
    overheads = []
    facing = []
    for measurement in measurements:
        measure_id = str(measurement.get("id", ""))
        if measure_id.startswith("overhead.clearance"):
            overheads.append(
                {
                    "wall_id": measurement.get("wall_id", meter_wall),
                    "s_m": _span(measurement, measure_id),
                    "clearance_m": measurement["value_m"],
                    "plus_minus_m": measurement.get("plus_minus_m", 0.01),
                    "evidence": "observed",
                }
            )
        if measure_id.startswith("facing.depth"):
            facing.append(
                {
                    "wall_id": measurement.get("wall_id", meter_wall),
                    "s_m": _span(measurement, measure_id),
                    "depth_m": measurement["value_m"],
                    "lower_bound": False,
                    "plus_minus_m": measurement.get("plus_minus_m", 0.01),
                    "evidence": "observed",
                }
            )
    ends = {
        "left": {
            "kind": _end_kind(walls_in[0].get("left_end", "limit")),
            "point_m": [0.0, 0.0],
            "s_m": 0.0,
            "note": f"tape left_end {walls_in[0].get('left_end', 'limit')}",
        },
        "right": {
            "kind": _end_kind(walls_in[0].get("right_end", "limit")),
            "point_m": [walls[0]["s_range_m"][1], 0.0],
            "s_m": walls[0]["s_range_m"][1],
            "note": f"tape right_end {walls_in[0].get('right_end', 'limit')}",
        },
    }
    return {
        "format": "base-scene/v0",
        "scene_id": site,
        "provenance": {
            "data": "real",
            "site": site,
            "producer": {
                "path": "gt-tape",
                "version": producer_version,
                "params": {"key_distances": [item.get("id") for item in measurements]},
            },
            "inputs": {"images_given": 0, "images_used": 0, "sensors_used": []},
            "scale_source": "ground_truth",
            "gravity_source": "ground_truth",
            "notes": ["tape ground truth, no cameras", "meter wall baseline starts at the meter (s = 0)"],
        },
        "frame": {
            "units": "m",
            "convention": "meter_anchored/v0",
            "anchor": {"element_id": "meter", "plus_minus_m": 0.01, "heading_plus_minus_deg": 1.0},
            "from_source": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        },
        "walls": walls,
        "chain": {"wall_ids": wall_ids, "corners": [], "ends": ends},
        "ground": {
            "surface": {"kind": "plane", "normal": [0.0, 1.0, 0.0], "offset_m": 0.0},
            "plus_minus_m": 0.01,
            "regions": regions,
        },
        "objects": objects,
        "overheads": overheads,
        "facing": facing,
        "coverage": {"method": "none: tape ground truth, no cameras"},
    }


def _span(measurement: dict, measure_id: str) -> list[float]:
    raw = measurement.get("s_m")
    if isinstance(raw, list) and len(raw) == 2:
        start, end = float(raw[0]), float(raw[1])
        if start <= end:
            return [start, end]
    raise TruthError(2, f"{measure_id} needs s_m as a low-to-high pair")


def _end_kind(value: str) -> str:
    if value in {"limit", "unexplored"}:
        return value
    return "limit"


def _measurement(measurements: list[dict], measure_id: str) -> float | None:
    for measurement in measurements:
        if measurement.get("id") == measure_id:
            return float(measurement["value_m"])
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert real-house truth to a tape scene")
    parser.add_argument("--truth", required=True, type=Path)
    parser.add_argument("--site", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--producer-version", default="0")
    args = parser.parse_args(argv)
    if not args.truth.is_file():
        print(f"missing input: {args.truth}", file=sys.stderr)
        return 3
    try:
        scene = truth_to_scene(json.loads(args.truth.read_text()), args.site, args.producer_version)
    except TruthError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except (KeyError, TypeError, ValueError) as exc:
        print(f"schema invalid: {exc}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(scene, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
