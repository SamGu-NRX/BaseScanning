"""Tests for server.placement.scene_bridge.

Expected values are computed here from first principles — metres divided by 0.3048
written out in this file, plan geometry with math.hypot — never from the bridge's own
constants, and the converted output is validated by the real root scene parser
(`server/scene.py`, the code behind POST /v1/placements) and `scene.schema.json`.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from server.placement.adapter import load_solver
from server.placement.scene_bridge import (
    INVALID,
    UNSUPPORTED,
    SceneBridgeError,
    convert_scene,
)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_WIRE_SCHEMA = Draft202012Validator(json.loads((_REPO_ROOT / "server" / "schemas" / "scene.schema.json").read_text()))


def base_scene() -> dict[str, Any]:
    """One straight observed house wall from s = -3 m to s = 2 m (the meter projects at
    arc 3.0 m, a nonzero wall s origin), an observed meter anchor, level ground, mapped
    hazards, ground region, coverage band, overhead and facing."""
    return {
        "format": "base-scene/v0",
        "scene_id": "bridge-test",
        "provenance": {
            "data": "synthetic",
            "site": "bridge-test",
            "producer": {"path": "scene-bridge-test"},
            "scale_source": "native_metric",
            "gravity_source": "ground_truth",
        },
        "frame": {
            "units": "m",
            "convention": "meter_anchored/v0",
            "anchor": {"element_id": "meter", "plus_minus_m": 0.01},
        },
        "walls": [
            {
                "id": "w",
                "role": "house",
                "baseline_m": [[-3.0, 0.0], [5.0, 0.0]],
                "height_m": 3.0,
                "plus_minus_m": 0.05,
                "evidence": "observed",
            }
        ],
        "chain": {
            "wall_ids": ["w"],
            "ends": {
                "left": {"kind": "unexplored"},
                "right": {"kind": "limit", "note": "fence line"},
            },
        },
        "ground": {
            "surface": {"kind": "plane", "normal": [0, 1, 0], "offset_m": 0},
            "plus_minus_m": 0.05,
            "regions": [
                {
                    "id": "pad",
                    "material": "concrete",
                    "polygon_m": [[0.2, 0.5], [2.0, 0.5], [2.0, 3.0], [0.2, 3.0]],
                    "evidence": "observed",
                    "plus_minus_m": 0.1,
                }
            ],
        },
        "objects": [
            {
                "id": "meter",
                "class": "meter",
                "geometry": {"kind": "point", "pos_m": [0.0, 1.5, 0.02]},
                "confidence": 1,
                "plus_minus_m": 0.02,
                "source": {"kind": "reconstruction"},
                "evidence": "observed",
            },
            {
                "id": "win",
                "class": "window",
                "geometry": {
                    "kind": "wall_rect",
                    "wall_id": "w",
                    "s_m": [1.0, 2.0],
                    "bottom_m": 1.0,
                    "top_m": 2.2,
                },
                "confidence": 0.9,
                "plus_minus_m": 0.08,
                "source": {"kind": "vlm"},
                "evidence": "observed",
            },
            {
                "id": "gas",
                "class": "gas_meter",
                "geometry": {
                    "kind": "wall_rect",
                    "wall_id": "w",
                    "s_m": [-0.6, -0.3],
                    "bottom_m": 0.3,
                    "top_m": 0.9,
                },
                "confidence": 0.95,
                "plus_minus_m": 0.05,
                "source": {"kind": "tap"},
                "evidence": "observed",
                "attrs": {"operable": True},
            },
        ],
        "coverage": {
            "method": "scene-bridge-test",
            "sensitivity": [
                {
                    "surface": "wall",
                    "max_incidence_deg": 75,
                    "bands": [{"band": "wall", "s_m": [-2.0, 4.0], "out_m": 2.13}],
                }
            ],
        },
        "overheads": [
            {
                "wall_id": "w",
                "s_m": [1.0, 2.0],
                "clearance_m": 2.1,
                "plus_minus_m": 0.1,
                "evidence": "observed",
            }
        ],
        "facing": [
            {
                "wall_id": "w",
                "s_m": [-1.0, 1.0],
                "depth_m": 8.0,
                "plus_minus_m": 0.3,
                "evidence": "observed",
            }
        ],
    }


def by_id(document: dict[str, Any], object_id: str) -> dict[str, Any]:
    return next(obj for obj in document["objects"] if obj["id"] == object_id)


def _parse_with_root_parser(converted: dict[str, Any]) -> Any:
    """The real root scene parser (schema + geometry) behind POST /v1/placements."""
    modules = load_solver()
    loaded = modules["rules"].load_rules()
    return modules["scene"].parse_scene(converted, loaded.rules)


def test_happy_path_fields_in_independently_computed_feet() -> None:
    converted = convert_scene(base_scene())

    assert converted["meter"] == {
        "pos": [0.0 / 0.3048, 1.5 / 0.3048, 0.02 / 0.3048],
        "wall_id": "w",
        "plus_minus_ft": 0.02 / 0.3048,
    }
    wall = converted["walls"][0]
    assert wall["id"] == "w"
    assert wall["baseline"] == [[-3.0 / 0.3048, 0.0], [5.0 / 0.3048, 0.0]]
    assert wall["height_ft"] == 3.0 / 0.3048
    assert wall["plus_minus_ft"] == 0.05 / 0.3048
    assert "source" not in wall

    window, gas = converted["objects"]
    assert window["type"] == "window"
    assert window["span_ft"] == [1.0 / 0.3048, 2.0 / 0.3048]
    assert window["bottom_ft"] == 1.0 / 0.3048
    assert window["top_ft"] == 2.2 / 0.3048
    assert window["source"] == "vlm"
    assert window["conf"] == 0.9
    assert window["plus_minus_ft"] == 0.08 / 0.3048
    assert "attrs" not in window  # no attrs stays unknown, never false

    assert gas["type"] == "gas_meter"
    assert gas["span_ft"] == [-0.6 / 0.3048, -0.3 / 0.3048]
    assert gas["source"] == "tap"
    assert gas["attrs"] == {"operable": True}

    assert converted["ground"] == [
        {
            "type": "concrete",
            "polygon": [[x / 0.3048, z / 0.3048] for x, z in [(0.2, 0.5), (2.0, 0.5), (2.0, 3.0), (0.2, 3.0)]],
            "plus_minus_ft": 0.1 / 0.3048,
        }
    ]
    assert converted["overheads"] == [
        {
            "wall_id": "w",
            "span_ft": [1.0 / 0.3048, 2.0 / 0.3048],
            "clearance_ft": 2.1 / 0.3048,
            "plus_minus_ft": 0.1 / 0.3048,
        }
    ]
    assert converted["facing"] == [
        {
            "wall_id": "w",
            "span_ft": [-1.0 / 0.3048, 1.0 / 0.3048],
            "depth_ft": 8.0 / 0.3048,
            "plus_minus_ft": 0.3 / 0.3048,
        }
    ]
    assert converted["coverage"]["ends"] == {
        "left": {"kind": "unexplored"},
        "right": {"kind": "limit", "note": "fence line"},
    }
    assert converted["coverage"]["observed"] == [
        {"band": "wall", "span_ft": [-2.0 / 0.3048, 4.0 / 0.3048], "out_ft": 2.13 / 0.3048}
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda doc: None, id="point-meter"),
        pytest.param(
            lambda doc: by_id(doc, "meter").update(
                {
                    "geometry": {
                        "kind": "wall_rect",
                        "wall_id": "w",
                        "s_m": [-0.1, 0.1],
                        "bottom_m": 1.4,
                        "top_m": 1.8,
                    }
                }
            ),
            id="wall-rect-meter",
        ),
        pytest.param(
            lambda doc: doc["walls"][0].update({"baseline_m": [[3.0, 1.0], [9.0, 5.0]]}),
            id="rotated-wall",
        ),
    ],
)
def test_output_is_valid_for_the_real_root_scene_parser_and_schema(mutate) -> None:
    document = base_scene()
    mutate(document)
    if document["walls"][0]["baseline_m"] == [[3.0, 1.0], [9.0, 5.0]]:
        # The meter stays on the rotated wall line (3, 1) + 2.5 m along it.
        length = math.hypot(6.0, 4.0)
        by_id(document, "meter")["geometry"] = {
            "kind": "point",
            "pos_m": [
                3.0 + (6.0 / length) * 2.5,
                1.5,
                1.0 + (4.0 / length) * 2.5,
            ],
        }

    converted = convert_scene(document)

    _WIRE_SCHEMA.validate(converted)
    parsed = _parse_with_root_parser(converted)
    assert json.dumps(converted, sort_keys=True, allow_nan=False)  # JSON-compatible

    # The parser's unrolled s=0 is the meter's projection onto the wall, in feet: the
    # same plan x as the meter, and within the meter's plan offset of the meter itself
    # (0.02 m off-wall for the point meter, on-wall otherwise).
    meter_plan_ft = (converted["meter"]["pos"][0], converted["meter"]["pos"][2])
    s0_point = parsed.point_at(0.0)
    assert s0_point[0] == pytest.approx(meter_plan_ft[0], abs=1e-6)
    assert math.hypot(s0_point[0] - meter_plan_ft[0], s0_point[1] - meter_plan_ft[1]) == pytest.approx(
        0.0, abs=0.02 / 0.3048 + 1e-6
    )


def test_rotated_translated_wall_and_nonzero_s_origin_place_spans_independently() -> None:
    """The wall is translated off the origin and rotated; the hazard span is checked
    against plan geometry computed here, through the root parser's own unrolling."""
    document = base_scene()
    document["walls"][0]["baseline_m"] = [[3.0, 1.0], [9.0, 5.0]]
    length = math.hypot(9.0 - 3.0, 5.0 - 1.0)
    along = ((9.0 - 3.0) / length, (5.0 - 1.0) / length)
    t_meter = 2.5
    meter = by_id(document, "meter")
    meter["geometry"] = {
        "kind": "point",
        "pos_m": [3.0 + along[0] * t_meter, 1.5, 1.0 + along[1] * t_meter],
    }

    converted = convert_scene(document)

    # Feet computed independently: the scaled baseline and the span from source s.
    assert converted["walls"][0]["baseline"] == [
        [3.0 / 0.3048, 1.0 / 0.3048],
        [9.0 / 0.3048, 5.0 / 0.3048],
    ]
    window = converted["objects"][0]
    assert window["span_ft"] == [1.0 / 0.3048, 2.0 / 0.3048]

    # Independent placement: the hazard at source s=1.0 m sits at arc t_meter + 1.0 m
    # along the wall, i.e. plan (3, 1) + 3.5 m * along — in feet. The root parser must
    # find exactly that point at the converted span start, re-deriving s from geometry.
    parsed = _parse_with_root_parser(converted)
    expected = (
        (3.0 + along[0] * (t_meter + 1.0)) / 0.3048,
        (1.0 + along[1] * (t_meter + 1.0)) / 0.3048,
    )
    point = parsed.point_at(window["span_ft"][0])
    assert point[0] == pytest.approx(expected[0], rel=1e-9)
    assert point[1] == pytest.approx(expected[1], rel=1e-9)


def test_meter_wall_rect_geometry_places_the_meter_at_its_span_centre_mid_height() -> None:
    document = base_scene()
    by_id(document, "meter")["geometry"] = {
        "kind": "wall_rect",
        "wall_id": "w",
        "s_m": [-0.1, 0.1],
        "bottom_m": 1.4,
        "top_m": 1.8,
    }

    converted = convert_scene(document)

    # Plan: the frame origin (directly below the meter) projected on the wall;
    # height: mid-span of the meter's own wall_rect.
    assert converted["meter"]["pos"] == [0.0 / 0.3048, (1.4 + 1.8) / 2 / 0.3048, 0.0]
    parsed = _parse_with_root_parser(converted)
    assert parsed.point_at(0.0)[0] == pytest.approx(0.0, abs=1e-6)


def test_absent_coverage_band_stays_unobserved_and_unexplored_ends_are_kept() -> None:
    document = base_scene()
    document["coverage"] = {"method": "scene-bridge-test"}

    converted = convert_scene(document)

    assert converted["coverage"] == {
        "ends": {
            "left": {"kind": "unexplored"},
            "right": {"kind": "limit", "note": "fence line"},
        }
    }
    assert "observed" not in converted["coverage"]


def test_unknown_attributes_stay_unknown() -> None:
    document = base_scene()
    by_id(document, "win")["attrs"] = {
        "operable": None,
        "well": None,
        "ground_level": True,
        "movable": False,
        "text": "sticker",
    }

    converted = convert_scene(document)

    assert "attrs" not in converted["objects"][0]


def test_class_and_source_map_exactly() -> None:
    mapping = {
        "ac_unit": "ac",
        "gas_regulator": "gas_meter",
        "gas_pipe": "gas_meter",
        "disconnect": "elec_box",
        "panel": "elec_box",
        "wall_box": "elec_box",
        "meter_can": "elec_box",
        "vent": "vent",
        "downspout": "downspout",
        "pool": "pool",
        "battery": "battery",
        "door": "door",
        "garage_door": "garage_door",
    }
    for source_class, wire_type in mapping.items():
        document = base_scene()
        by_id(document, "gas")["class"] = source_class
        converted = convert_scene(document)
        assert converted["objects"][-1]["type"] == wire_type
        for kind in ("vlm", "tap", "tape"):
            document["objects"][-1]["source"] = {"kind": kind}
            assert convert_scene(document)["objects"][-1]["source"] == kind


def _expect_bridge_error(document: Any, *, code: str, pointer: str) -> SceneBridgeError:
    with pytest.raises(SceneBridgeError) as exc_info:
        convert_scene(document)
    error = exc_info.value
    assert isinstance(error, ValueError)
    assert error.code == code
    assert error.pointer == pointer
    return error


def test_unsupported_refusals_carry_code_and_pointer() -> None:
    document = base_scene()

    two_walls = copy.deepcopy(document)
    two_walls["walls"].append(
        {
            "id": "w2",
            "role": "house",
            "baseline_m": [[10.0, 0.0], [12.0, 0.0]],
            "height_m": 3.0,
            "plus_minus_m": 0.05,
            "evidence": "observed",
        }
    )
    two_walls["chain"]["wall_ids"] = ["w", "w2"]
    _expect_bridge_error(two_walls, code=UNSUPPORTED, pointer="/walls")

    cases: list[tuple[Any, str, str]] = []
    for evidence in ("inferred", "generated"):
        inferred = copy.deepcopy(document)
        by_id(inferred, "win")["evidence"] = evidence
        cases.append((inferred, UNSUPPORTED, "/objects/1/evidence"))
    inferred_meter = copy.deepcopy(document)
    by_id(inferred_meter, "meter")["evidence"] = "inferred"
    cases.append((inferred_meter, UNSUPPORTED, "/objects/0/evidence"))

    fence = copy.deepcopy(document)
    fence["walls"][0]["role"] = "fence"
    cases.append((fence, UNSUPPORTED, "/walls/0/role"))
    unobserved_wall = copy.deepcopy(document)
    unobserved_wall["walls"][0]["evidence"] = "inferred"
    cases.append((unobserved_wall, UNSUPPORTED, "/walls/0/evidence"))
    raised_wall = copy.deepcopy(document)
    raised_wall["walls"][0]["base_y_m"] = 0.4
    cases.append((raised_wall, UNSUPPORTED, "/walls/0/base_y_m"))

    for unsupported_class in ("tree", "eave", "porch_roof", "stairs", "hose_bib", "other"):
        unmapped = copy.deepcopy(document)
        by_id(unmapped, "win")["class"] = unsupported_class
        cases.append((unmapped, UNSUPPORTED, "/objects/1/class"))

    standoff = copy.deepcopy(document)
    by_id(standoff, "win")["geometry"]["standoff_m"] = 0.3
    cases.append((standoff, UNSUPPORTED, "/objects/1/geometry/standoff_m"))
    standoff_meter = copy.deepcopy(document)
    by_id(standoff_meter, "meter")["geometry"] = {
        "kind": "wall_rect",
        "wall_id": "w",
        "s_m": [-0.1, 0.1],
        "bottom_m": 1.4,
        "top_m": 1.8,
        "standoff_m": 0.05,
    }
    cases.append((standoff_meter, UNSUPPORTED, "/objects/0/geometry/standoff_m"))

    for source_kind in ("detector", "annotation", "laser_scan", "lidar", "reconstruction", "generated"):
        unmapped_source = copy.deepcopy(document)
        by_id(unmapped_source, "win")["source"] = {"kind": source_kind}
        cases.append((unmapped_source, UNSUPPORTED, "/objects/1/source/kind"))

    heightfield = copy.deepcopy(document)
    heightfield["ground"]["surface"] = {"kind": "heightfield", "asset": "h"}
    cases.append((heightfield, UNSUPPORTED, "/ground/surface"))
    tilted = copy.deepcopy(document)
    tilted["ground"]["surface"] = {"kind": "plane", "normal": [0.05, 0.998, 0], "offset_m": 0}
    cases.append((tilted, UNSUPPORTED, "/ground/surface"))
    lifted = copy.deepcopy(document)
    lifted["ground"]["surface"] = {"kind": "plane", "normal": [0, 1, 0], "offset_m": 0.3}
    cases.append((lifted, UNSUPPORTED, "/ground/surface"))
    sloped = copy.deepcopy(document)
    sloped["ground"]["regions"][0]["slope_deg"] = 4.0
    cases.append((sloped, UNSUPPORTED, "/ground/regions/0/slope_deg"))
    for material in ("dirt", "pavers", "walkway", "pool", "water", "unknown"):
        unmapped_material = copy.deepcopy(document)
        unmapped_material["ground"]["regions"][0]["material"] = material
        cases.append((unmapped_material, UNSUPPORTED, "/ground/regions/0/material"))
    generated_region = copy.deepcopy(document)
    generated_region["ground"]["regions"][0]["evidence"] = "generated"
    cases.append((generated_region, UNSUPPORTED, "/ground/regions/0/evidence"))
    inferred_region = copy.deepcopy(document)
    inferred_region["ground"]["regions"][0]["evidence"] = "inferred"
    cases.append((inferred_region, UNSUPPORTED, "/ground/regions/0/evidence"))

    inferred_overhead = copy.deepcopy(document)
    inferred_overhead["overheads"][0]["evidence"] = "inferred"
    cases.append((inferred_overhead, UNSUPPORTED, "/overheads/0/evidence"))
    lower_bound_facing = copy.deepcopy(document)
    lower_bound_facing["facing"][0]["lower_bound"] = True
    cases.append((lower_bound_facing, UNSUPPORTED, "/facing/0/lower_bound"))

    grid = copy.deepcopy(document)
    grid["coverage"]["grid"] = {
        "asset": "a",
        "voxel_m": 0.1,
        "origin_m": [0, 0, 0],
        "shape": [2, 2, 2],
    }
    grid["assets"] = [{"id": "a", "kind": "coverage_grid", "path": "coverage.npz"}]
    cases.append((grid, UNSUPPORTED, "/coverage/grid"))

    keyframes = copy.deepcopy(document)
    keyframes["keyframes"] = [
        {
            "id": "k1",
            "pose": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
            "intrinsics": [1000.0, 1000.0, 640.0, 480.0],
            "w": 1280,
            "h": 960,
        }
    ]
    cases.append((keyframes, UNSUPPORTED, "/keyframes"))

    generated_regions = copy.deepcopy(document)
    generated_regions["generated_regions"] = [
        {
            "id": "gen",
            "geometry": {"kind": "asset_mask", "asset": "a"},
            "method": "inpaint",
        }
    ]
    cases.append((generated_regions, UNSUPPORTED, "/generated_regions"))

    for bad_document, code, pointer in cases:
        error = _expect_bridge_error(bad_document, code=code, pointer=pointer)
        assert str(error).startswith(pointer)


def test_invalid_refusals_carry_code_and_pointer() -> None:
    document = base_scene()

    cases: list[tuple[Any, str]] = []

    wrong_chain = copy.deepcopy(document)
    wrong_chain["chain"]["wall_ids"] = ["w2"]
    # The source contract's consistency check refuses the unknown wall reference before
    # the bridge's own chain check runs.
    cases.append((wrong_chain, "/"))
    corners = copy.deepcopy(document)
    corners["chain"]["corners"] = [
        {
            "id": "c1",
            "left_wall": "w",
            "right_wall": "w",
            "point_m": [5.0, 0.0],
        }
    ]
    cases.append((corners, "/chain/corners"))

    # A geometry that is not a wall_rect is refused by the source contract itself; the
    # pydantic discriminator emits the union tag in the error location.
    for kind, field in (("box", "pos_m"), ("footprint", "bottom_m"), ("point", "center_m")):
        wrong_geom = copy.deepcopy(document)
        by_id(wrong_geom, "win")["geometry"] = {
            "kind": kind,
            "pos_m": [1.0, 1.5, 0.0],
            "center_m": [1.0, 1.5, 0.0],
            "size_m": [1.0, 1.0, 0.2],
            "polygon_m": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
        }
        cases.append((wrong_geom, f"/objects/1/geometry/{kind}/{field}"))

    off_wall = copy.deepcopy(document)
    # The meter anchors at arc 3.0 m on an 8 m wall, so the wall ends at s = 5.0 m.
    by_id(off_wall, "win")["geometry"]["s_m"] = [4.5, 5.5]
    cases.append((off_wall, "/objects/1/geometry/s_m"))
    off_wall_left = copy.deepcopy(document)
    by_id(off_wall_left, "gas")["geometry"]["s_m"] = [-3.5, -3.2]  # wall starts at -3.0
    cases.append((off_wall_left, "/objects/2/geometry/s_m"))

    s_range = copy.deepcopy(document)
    s_range["walls"][0]["s_range_m"] = [-2.0, 5.0]  # geometry says [-3.0, 2.0]
    cases.append((s_range, "/walls/0/s_range_m"))

    end_s = copy.deepcopy(document)
    end_s["chain"]["ends"]["right"]["s_m"] = 2.5
    cases.append((end_s, "/chain/ends/right/s_m"))

    off_centre_meter = copy.deepcopy(document)
    by_id(off_centre_meter, "meter")["geometry"] = {
        "kind": "wall_rect",
        "wall_id": "w",
        "s_m": [0.2, 0.5],
        "bottom_m": 1.4,
        "top_m": 1.8,
    }
    cases.append((off_centre_meter, "/objects/0/geometry/s_m"))

    past_end_meter = copy.deepcopy(document)
    by_id(past_end_meter, "meter")["geometry"] = {
        "kind": "point",
        "pos_m": [6.0, 1.5, 0.0],  # past the wall's right end at x = 5.0
    }
    cases.append((past_end_meter, "/objects/0/geometry/pos_m"))

    reversed_band = copy.deepcopy(document)
    reversed_band["coverage"]["sensitivity"][0]["bands"][0]["s_m"] = [4.0, -2.0]
    cases.append((reversed_band, "/coverage/sensitivity/0/bands/0/s_m"))
    reversed_hazard = copy.deepcopy(document)
    by_id(reversed_hazard, "win")["geometry"]["s_m"] = [2.0, 1.0]
    cases.append((reversed_hazard, "/objects/1/geometry/s_m"))

    inverted_heights = copy.deepcopy(document)
    by_id(inverted_heights, "win")["geometry"]["bottom_m"] = 2.4
    cases.append((inverted_heights, "/objects/1/geometry"))
    negative_height = copy.deepcopy(document)
    by_id(negative_height, "win")["geometry"]["bottom_m"] = -0.2
    cases.append((negative_height, "/objects/1/geometry"))

    for value in (float("nan"), float("inf"), float("-inf")):
        nonfinite = copy.deepcopy(document)
        by_id(nonfinite, "win")["geometry"]["s_m"] = [value, 2.0]
        cases.append((nonfinite, "/objects/1/geometry/s_m/0"))
        nonfinite_meter = copy.deepcopy(document)
        by_id(nonfinite_meter, "meter")["geometry"]["pos_m"] = [0.0, value, 0.02]
        cases.append((nonfinite_meter, "/objects/0/geometry/pos_m/1"))

    missing_field = copy.deepcopy(document)
    del by_id(missing_field, "win")["evidence"]
    cases.append((missing_field, "/objects/1/evidence"))
    extra_field = copy.deepcopy(document)
    extra_field["walls"][0]["baseline_ft"] = [[-10.0, 0.0], [16.0, 0.0]]
    cases.append((extra_field, "/walls/0/baseline_ft"))
    not_a_meter_anchor = copy.deepcopy(document)
    not_a_meter_anchor["frame"]["anchor"]["element_id"] = "win"
    cases.append((not_a_meter_anchor, "/"))

    for bad_document, pointer in cases:
        _expect_bridge_error(bad_document, code=INVALID, pointer=pointer)


def test_non_object_input_is_invalid() -> None:
    for bad in ([], "scene", None, 3):
        with pytest.raises(SceneBridgeError) as exc_info:
            convert_scene(bad)  # type: ignore[arg-type]
        assert exc_info.value.code == INVALID
        assert exc_info.value.pointer == "/"


def test_conversion_is_deterministic_and_never_mutates_its_argument() -> None:
    document = base_scene()
    frozen = copy.deepcopy(document)

    first = convert_scene(document)
    second = convert_scene(document)

    assert document == frozen  # no mutation, at any depth
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert convert_scene(copy.deepcopy(document)) == first


def test_the_committed_orchestrator_stub_scene_converts_and_parses() -> None:
    from server.orchestrator.stubs._common import scene_document

    converted = convert_scene(scene_document("stub-bridge"))

    _WIRE_SCHEMA.validate(converted)
    parsed = _parse_with_root_parser(converted)
    assert converted["meter"]["pos"][1] == pytest.approx((1.4 + 1.8) / 2 / 0.3048)
    assert parsed.point_at(0.0)[0] == pytest.approx(0.0, abs=1e-6)
    assert converted["coverage"]["ends"] == {
        "left": {"kind": "limit"},
        "right": {"kind": "limit"},
    }
