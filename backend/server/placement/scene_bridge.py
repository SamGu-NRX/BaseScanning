"""Pure bridge from the backend scene contract to the placement wire scene.

`convert_scene(document: dict) -> dict` takes a `base-scene/v0` document — metres, the
`meter_anchored/v0` frame, validated by `server.contracts.scene.Scene` — and returns the
placement wire scene the unchanged top-level `POST /v1/placements` server accepts
(`server/schemas/scene.schema.json`, feet). This is the pinned consumer interface for the
placement stage's `placement/scene.json` input, which nothing publishes yet
(`backend/server/placement/adapter.py` falls back to a committed reference fixture until
a stage hydrates it through this bridge).

Purity contract: `convert_scene` reads its argument and nothing else — it loads no
fixture, reads no environment setting, and calls no solver — never mutates its argument,
and returns an equal dict for an equal document on every call.

Coordinate mapping. Both contracts measure `s` along the wall chain from the meter,
positive to the right seen from outside (scene-and-verdict-format.md §1: "on the meter
wall s = x"), and both order wall baselines left-end-first seen from outside with the
outward normal the baseline direction turned 90° clockwise seen from above. Only the
units differ, so every length converts with exactly 1 ft = 0.3048 m and no s rebasing:
`wire_s_ft = source_s_m / 0.3048`. The s coordinates are nonetheless cross-checked
against the plan geometry (see the refusals below), so a document whose s values do not
match its own baselines is refused, never silently accepted.

What converts (the initial supported geometry is one straight observed house wall, an
observed meter anchor and level ground, in the declared meter-anchored frame):

- `meter` — the anchor object (point or wall_rect geometry), with position and error in
  feet. A point meter keeps its measured `pos_m`; a wall_rect meter sits on the wall at
  its span centre at mid-height, `(bottom_m + top_m) / 2`, which the frame definition
  (origin on the ground directly below the meter) and the s=0-centre check below pin to
  the same place the source frame does.
- `walls` — the single chain wall: scaled baseline, `height_ft`, `plus_minus_ft`. The
  wire `source` field is omitted (absent means tap): the source contract records how the
  wall's *points* were supported (`support`), not how the line was found, and guessing a
  provenance would be worse than the schema default. `polygon_m`, `s_range_m`
  (after validation), `observed_fraction` and `support` have no wire field and are
  dropped; `observed_fraction` is redundant with the converted coverage bands.
- `objects` — observed wall_rect hazards with the exact class map (below) and the
  observed `source.kind` map (`vlm`→`vlm`, `tap`→`tap`, `tape`→`tape`). The class map
  follows the wire enum and its own prose ("Disconnects, sub-panels, EV chargers and
  solar equipment are elec_box"; the footprint note names gas regulators and pipe runs):
  `window/door/garage_door/gas_meter/vent/downspout/pool/battery` map to themselves,
  `ac_unit`→`ac`, `gas_regulator/gas_pipe`→`gas_meter`,
  `disconnect/panel/wall_box/meter_can`→`elec_box` (the c1 converter's precedent).
  `conf` carries the source confidence unchanged (it is recognition confidence in both
  contracts and never a distance error); `plus_minus_ft` converts with the lengths;
  `attrs` carries `operable` and `well` when present — a missing attribute stays missing,
  which both contracts read as unknown. `ground_level`, `movable` and `text` have no
  wire field and are dropped; the object's `source.views`/`model` and `label` likewise.
- `ground` — the plane surface must be exactly level at frame height 0; observed regions
  with a wire material (`concrete/gravel/lawn/mulch/deck/drive` map to themselves) become
  wire ground patches. The surface's `plus_minus_m`, region `source` and region
  `confidence` have no wire field and are dropped.
- `overheads` and `facing` — observed entries on the wall, converted with the lengths.
  `object_id` cross-references have no wire field and are dropped.
- `coverage` — the chain ends (`limit`/`unexplored` with their notes) and the explicit
  primary bands from `coverage.bands`: band name, `span_ft` and `out_ft` convert with
  the lengths; `views` and `best_incidence_deg` have no wire field and are dropped.
  Sensitivity bands (`coverage.sensitivity[].bands`) are validated the same way but are
  not observations — they are what-if variants, not the ground-truth coverage the
  device saw. A coverage without bands converts to ends only, which
  the wire parser reads as nothing observed — an absent band stays unobserved.

Refusals — every refusal raises `SceneBridgeError`, a `ValueError` carrying a `code` and
a JSON-pointer `pointer` string that names the offending input:

- code `placement_scene_unsupported` (valid scene, outside the bounded scope): more than
  one wall (`/walls`); a wall whose role is not `house` (`/walls/0/role`) or whose
  evidence is not `observed` (`/walls/0/evidence`) or whose base sits above the ground
  (`/walls/0/base_y_m`); any object that is not observed evidence (`/objects/<i>/evidence`
  — generated or inferred evidence is never promoted to observed); an unmapped class
  (`/objects/<i>/class`); unsupported geometry (`/objects/<i>/geometry/kind` — only the
  meter may be a point, only hazards on the wall convert); a wall_rect `standoff_m`
  (`/objects/<i>/geometry/standoff_m` — a stand-off hazard needs a footprint this bridge
  does not synthesise); an unmapped source kind (`/objects/<i>/source/kind`); a
  heightfield or non-level ground surface (`/ground/surface`); a sloped region
  (`/ground/regions/<i>/slope_deg`); an unmapped material (`/ground/regions/<i>/material`);
  non-observed ground regions (`/ground/regions/<i>/evidence`); non-observed overheads or
  facing (`/overheads/<i>/evidence`, `/facing/<i>/evidence`); a facing `lower_bound`
  (`/facing/<i>/lower_bound` — the wire cannot express it); grid-based coverage
  (`/coverage/grid` — only explicit bands convert); keyframes (`/keyframes` — the wire
  requires per-frame image files the backend contract does not carry) and generated
  regions (`/generated_regions`).
- code `placement_scene_invalid` (the source contradicts itself or the schema): anything
  `Scene` itself rejects, pointer from the pydantic error location; the cross-reference
  failures of `scene_consistency_errors`; a chain that is not exactly the one wall
  (`/chain/wall_ids`) or has corners (`/chain/corners`); a meter that projects past its
  wall (`/frame/anchor`) or a wall_rect meter whose span is not centred on s=0
  (`/objects/<i>/geometry/s_m`); s coordinates that disagree with the plan geometry — a
  wall `s_range_m` that is not its baseline span relative to the meter
  (`/walls/0/s_range_m`), a chain end not at the wall's end (`/chain/ends/<side>/s_m`), a
  hazard span running past its wall (`/objects/<i>/geometry/s_m`), a reversed span
  (`.../s_m`) — compared at `_S_NOISE_M`, an arithmetic float-noise bound, not a
  measurement tolerance; non-finite numbers, pointer at the offending field; negative or
  inverted wall_rect heights.
"""

from __future__ import annotations

import math
from typing import Any, NoReturn

from pydantic import ValidationError

from server.contracts.scene import Scene, scene_consistency_errors

UNSUPPORTED = "placement_scene_unsupported"
INVALID = "placement_scene_invalid"

# scene.schema.json: coverage.observed allows at most 500 band entries.
_WIRE_MAX_OBSERVED_BANDS = 500

# 1 ft = 0.3048 m exactly, so lengths convert with one exact factor in one direction:
# dividing by 0.3048, the correctly rounded conversion, never multiplying by a rounded
# reciprocal.

# Arithmetic float-noise bound for cross-checking declared s coordinates against plan
# geometry. Several orders below the smallest measurement error either contract
# represents (the tape default is 0.05 ft); it exists so `4.0 + 1e-16` style noise in a
# producer's own arithmetic does not refuse an otherwise consistent scene. It is not a
# measurement tolerance: nothing is rounded, absorbed or guessed at this scale.
_S_NOISE_M = 1e-6

_CLASS: dict[str, str] = {
    "window": "window",
    "door": "door",
    "garage_door": "garage_door",
    "ac_unit": "ac",
    "gas_meter": "gas_meter",
    "gas_regulator": "gas_meter",
    "gas_pipe": "gas_meter",
    "vent": "vent",
    "downspout": "downspout",
    "pool": "pool",
    "battery": "battery",
    "disconnect": "elec_box",
    "panel": "elec_box",
    "wall_box": "elec_box",
    "meter_can": "elec_box",
}

_SOURCE_KIND: dict[str, str] = {"vlm": "vlm", "tap": "tap", "tape": "tape"}

_MATERIAL: dict[str, str] = {
    "concrete": "concrete",
    "gravel": "gravel",
    "lawn": "lawn",
    "mulch": "mulch",
    "deck": "deck",
    "drive": "drive",
}


class SceneBridgeError(ValueError):
    """The scene cannot be bridged honestly. `code` is `placement_scene_unsupported`
    (valid scene, outside the bounded scope) or `placement_scene_invalid` (the source
    contradicts itself); `pointer` names the offending field of the input document."""

    def __init__(self, message: str, *, code: str, pointer: str) -> None:
        super().__init__(f"{pointer}: {message}")
        self.code = code
        self.pointer = pointer


def _unsupported(message: str, pointer: str) -> NoReturn:
    raise SceneBridgeError(message, code=UNSUPPORTED, pointer=pointer)


def _invalid(message: str, pointer: str) -> NoReturn:
    raise SceneBridgeError(message, code=INVALID, pointer=pointer)


def _ft(value: Any) -> float:
    return float(value) / 0.3048


def _refuse_nonfinite(document: dict) -> None:
    """No number in a scene may be NaN or infinite. JSON Schema would accept both."""
    stack: list[tuple[Any, str]] = [(document, "")]
    while stack:
        value, path = stack.pop()
        if isinstance(value, dict):
            stack.extend((item, f"{path}/{key}") for key, item in value.items())
        elif isinstance(value, list):
            stack.extend((item, f"{path}/{index}") for index, item in enumerate(value))
        elif isinstance(value, int | float) and not isinstance(value, bool):
            if not math.isfinite(value):
                _invalid(f"{value!r} is not a finite number", path or "/")


def _validated(document: dict) -> Scene:
    try:
        scene = Scene.model_validate(document)
    except ValidationError as exc:
        error = exc.errors()[0]
        pointer = "/" + "/".join(str(part) for part in error["loc"])
        _invalid(error["msg"], pointer)
    errors = scene_consistency_errors(document)
    if errors:
        _invalid(errors[0], "/")
    return scene


def convert_scene(document: dict) -> dict:
    """`base-scene/v0` in metres to the placement wire scene in feet. Pure: see the
    module docstring for the mapping, the drops and every refusal."""
    if not isinstance(document, dict):
        _invalid("the scene must be a JSON object", "/")
    _refuse_nonfinite(document)
    return _convert(_validated(document))


def _convert(scene: Scene) -> dict:
    if len(scene.walls) != 1:
        _unsupported(
            f"{len(scene.walls)} walls; the bridge starts with one straight house wall",
            "/walls",
        )
    if scene.keyframes:
        _unsupported(
            "keyframes do not convert (the wire format requires a per-frame image file "
            "the backend scene does not carry)",
            "/keyframes",
        )
    if scene.generated_regions:
        _unsupported(
            "generated regions are unknown space, not observed geometry; the wire format cannot carry them",
            "/generated_regions",
        )
    if scene.coverage.grid is not None:
        _unsupported(
            "grid coverage does not convert; only explicit coverage bands do",
            "/coverage/grid",
        )

    wall = scene.walls[0]
    _wall_scope(wall)

    chain = scene.chain
    if chain.wall_ids != [wall.id]:
        _invalid(
            f"chain {chain.wall_ids!r} must be exactly the one wall {wall.id!r}",
            "/chain/wall_ids",
        )
    if chain.corners:
        _invalid("a one-wall chain has no corners", "/chain/corners")

    meter_index, meter_pos, u_meter = _meter(scene, wall)
    lo_s, hi_s = _wall_s_bounds(wall, u_meter)

    span_checks = _SpanChecks(wall.id, lo_s, hi_s)

    meter = scene.objects[meter_index]
    out: dict[str, Any] = {
        "meter": {
            "pos": [_ft(meter_pos[0]), _ft(meter_pos[1]), _ft(meter_pos[2])],
            "wall_id": wall.id,
            "plus_minus_ft": _ft(meter.plus_minus_m),
        },
        "walls": [
            {
                "id": wall.id,
                "baseline": [
                    [_ft(wall.baseline_m[0][0]), _ft(wall.baseline_m[0][1])],
                    [_ft(wall.baseline_m[1][0]), _ft(wall.baseline_m[1][1])],
                ],
                "height_ft": _ft(wall.height_m),
                "plus_minus_ft": _ft(wall.plus_minus_m),
            }
        ],
    }

    objects_out = _objects(scene, meter_index, span_checks)
    if objects_out:
        out["objects"] = objects_out

    ground_out = _ground(scene, wall.id)
    if ground_out:
        out["ground"] = ground_out

    overheads_out = _measured(scene.overheads or [], wall.id, span_checks, "overheads")
    if overheads_out:
        out["overheads"] = overheads_out

    facing_out = _measured(scene.facing or [], wall.id, span_checks, "facing")
    if facing_out:
        out["facing"] = facing_out

    out["coverage"] = _coverage(scene, wall.id, span_checks)
    return out


def _wall_scope(wall) -> None:
    """The single wall must be an observed house wall sitting on the ground."""
    if wall.role != "house":
        _unsupported(f"wall role {wall.role!r}; only a house wall backs a battery", "/walls/0/role")
    if wall.evidence != "observed":
        _unsupported(
            f"wall evidence {wall.evidence!r}; only an observed wall converts",
            "/walls/0/evidence",
        )
    if wall.base_y_m is not None and float(wall.base_y_m) != 0.0:
        _unsupported(
            "the wall base sits above the ground; the bridge starts with level ground",
            "/walls/0/base_y_m",
        )


def _wall_s_bounds(wall, u_meter: float) -> tuple[float, float]:
    """The wall's ends in chain s (metres from the meter), plus its declared range check."""
    length = _baseline_length(wall)
    lo_s, hi_s = -u_meter, length - u_meter
    if wall.s_range_m is not None:
        declared_lo, declared_hi = float(wall.s_range_m[0]), float(wall.s_range_m[1])
        if abs(declared_lo - lo_s) > _S_NOISE_M or abs(declared_hi - hi_s) > _S_NOISE_M or declared_lo > declared_hi:
            _invalid(
                f"s_range_m [{declared_lo}, {declared_hi}] disagrees with the baseline "
                f"and the meter anchor, which put the wall at s in [{lo_s}, {hi_s}]",
                "/walls/0/s_range_m",
            )
    return lo_s, hi_s


def _baseline_length(wall) -> float:
    (x0, z0), (x1, z1) = (
        (float(wall.baseline_m[0][0]), float(wall.baseline_m[0][1])),
        (float(wall.baseline_m[1][0]), float(wall.baseline_m[1][1])),
    )
    length = math.hypot(x1 - x0, z1 - z0)
    if length <= 0.0:
        _invalid("the baseline repeats its first point", "/walls/0/baseline")
    return length


def _meter(scene: Scene, wall) -> tuple[int, tuple[float, float, float], float]:
    """The anchor meter's index, its 3D position in metres, and the arc distance of its
    projection from the wall's left end (u_meter). s = arc - u_meter on the wall."""
    anchor_id = scene.frame.anchor.element_id
    meter_index = next((index for index, obj in enumerate(scene.objects) if obj.id == anchor_id), None)
    if meter_index is None:
        _invalid(f"the anchor {anchor_id!r} names no object", "/frame/anchor")
    meter_path = f"/objects/{meter_index}"
    meter = scene.objects[meter_index]
    if meter.evidence != "observed":
        _unsupported(
            f"meter anchor evidence {meter.evidence!r}; only an observed meter converts",
            f"{meter_path}/evidence",
        )

    (x0, z0) = (float(wall.baseline_m[0][0]), float(wall.baseline_m[0][1]))
    (x1, z1) = (float(wall.baseline_m[1][0]), float(wall.baseline_m[1][1]))
    length = _baseline_length(wall)
    along_x, along_z = (x1 - x0) / length, (z1 - z0) / length
    geometry = meter.geometry

    if geometry.kind == "point":
        pos_x, pos_y, pos_z = (float(value) for value in geometry.pos_m)
        if pos_y < 0.0:
            _invalid("the meter sits below the ground", f"{meter_path}/geometry/pos_m")
        plan_x, plan_z = pos_x, pos_z
        failure_pointer = f"{meter_path}/geometry/pos_m"
    elif geometry.kind == "wall_rect":
        _wall_rect_on_wall(geometry, meter_path, wall.id)
        if geometry.standoff_m is not None and float(geometry.standoff_m) != 0.0:
            _unsupported(
                "a stand-off meter anchor needs a footprint the bridge does not synthesise",
                f"{meter_path}/geometry/standoff_m",
            )
        s0, s1 = float(geometry.s_m[0]), float(geometry.s_m[1])
        if s0 > s1:
            _invalid("reversed s_m span", f"{meter_path}/geometry/s_m")
        if abs((s0 + s1) / 2) > _S_NOISE_M:
            _invalid(
                f"the meter anchor's span is centred on s={(s0 + s1) / 2}, not s=0 "
                "(the frame origin is on the ground directly below the meter)",
                f"{meter_path}/geometry/s_m",
            )
        if float(geometry.bottom_m) < 0.0 or float(geometry.top_m) < 0.0:
            _invalid("a negative height is below the ground", f"{meter_path}/geometry")
        if float(geometry.bottom_m) > float(geometry.top_m):
            _invalid("bottom_m is above top_m", f"{meter_path}/geometry")
        # The frame origin is defined on the ground directly below the meter, so the
        # meter's plan position is the origin's projection onto its wall.
        plan_x, plan_z = 0.0, 0.0
        pos_y = (float(geometry.bottom_m) + float(geometry.top_m)) / 2
        failure_pointer = "/frame/anchor"
    else:
        _unsupported(
            f"meter geometry {geometry.kind!r}; only point and wall_rect convert",
            f"{meter_path}/geometry/kind",
        )

    arc = (plan_x - x0) * along_x + (plan_z - z0) * along_z
    if arc < -_S_NOISE_M or arc > length + _S_NOISE_M:
        _invalid(
            f"the meter anchor projects {arc:.3f} m from the wall's left end, past the "
            f"{length:.3f} m wall; the meter must sit on its wall",
            failure_pointer,
        )
    u_meter = min(max(arc, 0.0), length)
    return meter_index, (plan_x, pos_y, plan_z), u_meter


def _wall_rect_on_wall(geometry, object_path: str, wall_id: str) -> None:
    if geometry.wall_id != wall_id:
        _invalid(
            f"the object sits on unknown wall {geometry.wall_id!r}",
            f"{object_path}/geometry/wall_id",
        )


class _SpanChecks:
    """Converts and bounds-checks declared s spans against the wall's geometry."""

    def __init__(self, wall_id: str, lo_s: float, hi_s: float) -> None:
        self.wall_id = wall_id
        self.lo_s = lo_s
        self.hi_s = hi_s

    def span_ft(self, s_m: list[Any], pointer: str, *, bounded: bool) -> list[float]:
        s0, s1 = float(s_m[0]), float(s_m[1])
        if s0 > s1:
            _invalid(f"reversed span [{s0}, {s1}]", pointer)
        if bounded and (s0 < self.lo_s - _S_NOISE_M or s1 > self.hi_s + _S_NOISE_M):
            _invalid(
                f"span [{s0}, {s1}] runs past its wall at s in [{self.lo_s}, {self.hi_s}]",
                pointer,
            )
        return [_ft(s0), _ft(s1)]


def _objects(scene: Scene, meter_index: int, checks: _SpanChecks) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, obj in enumerate(scene.objects):
        if index == meter_index:
            continue
        path = f"/objects/{index}"
        if obj.evidence != "observed":
            _unsupported(
                f"evidence {obj.evidence!r}; hazards convert only when observed, never as generated or inferred",
                f"{path}/evidence",
            )
        wire_class = _CLASS.get(obj.class_)
        if wire_class is None:
            _unsupported(f"class {obj.class_!r} has no wire type", f"{path}/class")
        if obj.wall_id is not None and obj.wall_id != checks.wall_id:
            _invalid(
                f"the object names wall {obj.wall_id!r}, not {checks.wall_id!r}",
                f"{path}/wall_id",
            )
        geometry = obj.geometry
        if geometry.kind != "wall_rect":
            _unsupported(
                f"geometry {geometry.kind!r}; the bridge starts with wall-relative rectangular hazards",
                f"{path}/geometry/kind",
            )
        _wall_rect_on_wall(geometry, path, checks.wall_id)
        if geometry.standoff_m is not None and float(geometry.standoff_m) != 0.0:
            _unsupported(
                "a stand-off hazard needs a wire footprint the bridge does not synthesise",
                f"{path}/geometry/standoff_m",
            )
        if float(geometry.bottom_m) < 0.0 or float(geometry.top_m) < 0.0:
            _invalid("a negative height is below the ground", f"{path}/geometry")
        if float(geometry.bottom_m) > float(geometry.top_m):
            _invalid("bottom_m is above top_m", f"{path}/geometry")
        wire_source = _SOURCE_KIND.get(obj.source.kind)
        if wire_source is None:
            _unsupported(
                f"source kind {obj.source.kind!r}; only vlm, tap and tape map",
                f"{path}/source/kind",
            )
        entry: dict[str, Any] = {
            "type": wire_class,
            "wall_id": checks.wall_id,
            "span_ft": checks.span_ft(geometry.s_m, f"{path}/geometry/s_m", bounded=True),
            "bottom_ft": _ft(geometry.bottom_m),
            "top_ft": _ft(geometry.top_m),
            "source": wire_source,
            "conf": float(obj.confidence),
            "plus_minus_ft": _ft(obj.plus_minus_m),
        }
        attrs: dict[str, Any] = {}
        if obj.attrs is not None:
            if obj.attrs.operable is not None:
                attrs["operable"] = obj.attrs.operable
            if obj.attrs.well is not None:
                attrs["well"] = obj.attrs.well
        if attrs:
            entry["attrs"] = attrs
        out.append(entry)
    return out


def _ground(scene: Scene, wall_id: str) -> list[dict[str, Any]]:
    surface = scene.ground.surface
    if surface.kind != "plane":
        _unsupported(
            f"ground surface {surface.kind!r}; the bridge starts with a plane ground",
            "/ground/surface",
        )
    normal = [float(value) for value in surface.normal]
    if normal != [0.0, 1.0, 0.0]:
        _unsupported(
            f"ground normal {normal!r}; the bridge starts with level ground",
            "/ground/surface",
        )
    if float(surface.offset_m) != 0.0:
        _unsupported(
            f"ground at height {float(surface.offset_m)}; the bridge starts with the ground at frame height 0",
            "/ground/surface",
        )
    out: list[dict[str, Any]] = []
    for index, region in enumerate(scene.ground.regions or []):
        path = f"/ground/regions/{index}"
        if region.evidence != "observed":
            _unsupported(
                f"region evidence {region.evidence!r}; ground converts only when observed",
                f"{path}/evidence",
            )
        if region.slope_deg is not None and float(region.slope_deg) != 0.0:
            _unsupported(
                f"sloped region ({float(region.slope_deg)} deg); the bridge starts with level ground",
                f"{path}/slope_deg",
            )
        wire_type = _MATERIAL.get(region.material)
        if wire_type is None:
            _unsupported(f"material {region.material!r} has no wire type", f"{path}/material")
        entry: dict[str, Any] = {
            "type": wire_type,
            "polygon": [[_ft(point[0]), _ft(point[1])] for point in region.polygon_m],
        }
        if region.plus_minus_m is not None:
            entry["plus_minus_ft"] = _ft(region.plus_minus_m)
        out.append(entry)
    return out


def _measured(entries, wall_id: str, checks: _SpanChecks, key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        path = f"/{key}/{index}"
        if entry.evidence != "observed":
            _unsupported(
                f"evidence {entry.evidence!r}; the measurement converts only when observed",
                f"{path}/evidence",
            )
        if entry.wall_id != wall_id:
            _invalid(f"the entry names unknown wall {entry.wall_id!r}", f"{path}/wall_id")
        record: dict[str, Any] = {
            "wall_id": wall_id,
            "span_ft": checks.span_ft(entry.s_m, f"{path}/s_m", bounded=True),
        }
        if key == "overheads":
            record["clearance_ft"] = _ft(entry.clearance_m)
        else:
            if entry.lower_bound:
                _unsupported(
                    "a lower-bound facing depth cannot be written as a wire depth_ft",
                    f"{path}/lower_bound",
                )
            record["depth_ft"] = _ft(entry.depth_m)
        record["plus_minus_ft"] = _ft(entry.plus_minus_m)
        out.append(record)
    return out


def _coverage(scene: Scene, wall_id: str, checks: _SpanChecks) -> dict[str, Any]:
    def end_out(end, side: str) -> dict[str, Any]:
        record: dict[str, Any] = {"kind": end.kind}
        if end.s_m is not None:
            expected = checks.lo_s if side == "left" else checks.hi_s
            if abs(float(end.s_m) - expected) > _S_NOISE_M:
                _invalid(
                    f"{side} chain end s_m {float(end.s_m)} disagrees with the wall's {side} end at s {expected}",
                    f"/chain/ends/{side}/s_m",
                )
        if end.note is not None:
            record["note"] = end.note
        return record

    out: dict[str, Any] = {
        "ends": {
            "left": end_out(scene.chain.ends.left, "left"),
            "right": end_out(scene.chain.ends.right, "right"),
        }
    }

    observed: list[dict[str, Any]] = []

    def band_out(band, pointer: str) -> dict[str, Any]:
        if band.wall_id is not None and band.wall_id != wall_id:
            _invalid(f"the band names unknown wall {band.wall_id!r}", f"{pointer}/wall_id")
        if band.band == "ground" and band.out_m is None:
            # scene.schema.json requires out_ft on every ground band ("the ground in
            # front of the wall out to out_ft"), while the source contract leaves
            # out_m optional — this band cannot be represented, so refuse rather
            # than guess a depth the capture never measured.
            _unsupported(
                "a ground band without a measured depth (out_m) has no wire out_ft; "
                "the ground's depth was never captured",
                f"{pointer}/out_m",
            )
        record: dict[str, Any] = {
            "band": band.band,
            "span_ft": checks.span_ft(band.s_m, f"{pointer}/s_m", bounded=False),
        }
        if band.out_m is not None:
            record["out_ft"] = _ft(band.out_m)
        return record

    for index, band in enumerate(scene.coverage.bands or []):
        observed.append(band_out(band, f"/coverage/bands/{index}"))
    for sensitivity_index, sensitivity in enumerate(scene.coverage.sensitivity or []):
        for band_index, band in enumerate(sensitivity.bands):
            # Sensitivity variants are validated but are not observations: the wire's
            # observed list is the ground-truth coverage the device saw, and a what-if
            # sensitivity band is not that. A sensitivity-only scene still converts to
            # ends only — no observation is invented.
            band_out(band, f"/coverage/sensitivity/{sensitivity_index}/bands/{band_index}")

    if len(observed) > _WIRE_MAX_OBSERVED_BANDS:
        _unsupported(
            f"{len(observed)} observed bands exceed the wire scene's limit of "
            f"{_WIRE_MAX_OBSERVED_BANDS} (scene.schema.json coverage.observed maxItems); "
            "thin or merge the capture's bands before conversion",
            "/coverage/bands",
        )
    if observed:
        out["observed"] = observed
    return out
