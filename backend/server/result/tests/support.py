"""Shared builders for the result tests and the committed synthetic fixtures."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from server.result.ar import look_pose
from server.result.copy import FALLBACK, catalogue_document
from server.result.harness import frame as frame_mod
from server.result.harness import sceneio

DATA = Path(__file__).resolve().parent / "data"
WHEN = "2026-09-26T00:00:00Z"


def similarity() -> np.ndarray:
    yaw = math.radians(8.0)
    c, s = math.cos(yaw), math.sin(yaw)
    rotation = np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])
    pose = np.eye(4)
    pose[:3, :3] = 1.05 * rotation
    pose[:3, 3] = (0.3, -0.1, 0.4)
    return pose


def _coverage() -> dict:
    return {
        "region": {"wall_id": "w1", "s_m": [-1.0, 1.0], "height_m": [0.0, 2.13]},
        "status": "observed",
        "observed_share": 1.0,
        "views_median": 4,
        "best_incidence_deg": 8,
        "rule": {"min_views": 2, "max_incidence_deg": 75, "surface": "wall", "observed_share": 0.9},
    }


def _object(obj_id: str, klass: str, geometry: dict) -> dict:
    return {
        "id": obj_id,
        "class": klass,
        "geometry": geometry,
        "confidence": 1.0,
        "plus_minus_m": 0.05,
        "source": {"kind": "annotation"},
        "evidence": "observed",
    }


def scene_dict(*, source_frame: str, with_keyframe: bool) -> dict:
    pose = similarity() if source_frame.startswith("arkit_world") else np.eye(4)
    keyframes = []
    if with_keyframe:
        cam = look_pose(np.array([0.0, 1.4, 4.0]), np.array([0.0, 1.0, 0.0]))
        keyframes.append(
            {
                "id": "k01",
                "image": "k01.jpg",
                "pose": frame_mod.mat4_to_list(cam),
                "intrinsics": [1200.0, 1200.0, 1000.0, 750.0],
                "w": 2000,
                "h": 1500,
                "pose_source": "model",
            }
        )
    return {
        "format": "base-scene/v0",
        "scene_id": "synthetic-flatwall",
        "provenance": {
            "data": "synthetic",
            "site": "synthetic-flatwall",
            "producer": {"path": "server/result/tests"},
            "scale_source": "native_metric",
            "gravity_source": "ground_plane_fit",
        },
        "frame": {
            "units": "m",
            "convention": "meter_anchored/v0",
            "anchor": {"element_id": "meter", "plus_minus_m": 0.0},
            "source_frame": source_frame,
            "from_source": frame_mod.mat4_to_list(pose),
        },
        "walls": [
            {
                "id": "w1",
                "role": "house",
                "baseline_m": [[-4.0, 0.0], [4.0, 0.0]],
                "height_m": 3.0,
                "s_range_m": [-4.0, 4.0],
                "plus_minus_m": 0.01,
                "evidence": "observed",
            }
        ],
        "chain": {
            "wall_ids": ["w1"],
            "ends": {
                "left": {"kind": "limit", "point_m": [-4.0, 0.0], "s_m": -4.0},
                "right": {"kind": "limit", "point_m": [4.0, 0.0], "s_m": 4.0},
            },
        },
        "ground": {"surface": {"kind": "plane", "normal": [0.0, 1.0, 0.0], "offset_m": 0.0}, "plus_minus_m": 0.01},
        "objects": [
            _object("meter", "meter", {"kind": "point", "pos_m": [0.0, 1.5, 0.0]}),
            _object(
                "win1",
                "window",
                {"kind": "wall_rect", "wall_id": "w1", "s_m": [1.0, 1.6], "bottom_m": 0.8, "top_m": 2.0},
            ),
        ],
        "coverage": {"method": "synthetic"},
        "assets": [
            {
                "id": "cloud",
                "kind": "point_cloud",
                "path": "points.ply",
                "format": "ply",
                "in_scene_frame": True,
                "count": 4,
                "fields": ["x", "y", "z", "generated"],
                "generated_field": "generated",
            }
        ],
        "generated_regions": [
            {
                "id": "g1",
                "method": "box",
                "geometry": {"kind": "box", "center_m": [1.0, 0.5, 0.2], "size_m": [0.3, 0.3, 0.3]},
            }
        ],
        "keyframes": keyframes,
    }


def _shell(decision: str, reasons: list[str], criteria: list[dict], views: list[dict], chosen: str | None) -> dict:
    return {
        "format": "base-verdict/v0",
        "verdict_id": f"synthetic/{decision}",
        "scene": {"scene_id": "synthetic-flatwall", "site": "synthetic-flatwall", "data": "synthetic"},
        "evaluator": {"path": "server/result/tests"},
        "site": {"decision": decision, "reasons": reasons, "chosen_candidate": chosen},
        "criteria": criteria,
        "candidates": [
            {
                "id": "spot",
                "wall_id": "w1",
                "start_s_m": [-1.0, 1.0],
                "outcome": "pass" if decision == "pass" else "fail",
            }
        ]
        if chosen
        else [],
        "views_needed": views,
    }


def pass_criterion() -> dict:
    return {
        "id": "wall_backing",
        "scope": "candidate",
        "candidate_id": "spot",
        "outcome": "pass",
        "reason": "The footprint sits on one straight wall.",
        "measured": {"value_ft": 8.0, "plus_minus_ft": 0.2, "coverage": [_coverage()]},
        "threshold": {"value_ft": 3.0, "comparison": "at_least"},
        "margin_over_uncertainty": 3.0,
    }


def eligible_verdict() -> dict:
    return _shell("pass", [], [pass_criterion()], [], "spot")


def views_verdict() -> dict:
    unsure = {
        "id": "ground_surface",
        "scope": "site",
        "outcome": "unsure",
        "unsure_cause": "unobserved",
        "reason": "The ground in front of the wall still needs a photo.",
        "measured": None,
        "threshold": None,
    }
    views = [
        {
            "id": "vn-wall",
            "kind": "band",
            "band": "wall",
            "wall_id": "w1",
            "s_m": [-1.0, 1.0],
            "target": "head-on photo of this wall section",
            "criteria": ["wall_backing"],
            "why": "A head-on photo of this stretch would settle it.",
        },
        {
            "id": "vn-ground",
            "kind": "band",
            "band": "ground",
            "wall_id": "w1",
            "s_m": [-0.5, 0.5],
            "out_m": 1.0,
            "target": "downward photo of the ground in front of this wall, from chest height",
            "criteria": ["ground_surface"],
            "why": "A downward photo of the ground would settle it.",
        },
    ]
    return _shell("manual_review", ["coverage_gap"], [unsure, pass_criterion()], views, "spot")


def reject_verdict() -> dict:
    failed = {
        "id": "drive_clearance",
        "scope": "candidate",
        "candidate_id": "spot",
        "outcome": "fail",
        "reason": "The spot is closer to the driveway than the rule allows.",
        "measured": {"value_ft": 0.0, "plus_minus_ft": 0.3, "coverage": [_coverage()]},
        "threshold": {"value_ft": 3.0, "comparison": "at_least"},
        "margin_ft": -3.0,
        "margin_over_uncertainty": -10.0,
        "evidence": {"views": ["k01"], "coverage": "observed"},
    }
    return _shell("reject", ["drive_clearance"], [failed], [], "spot")


def capture_dict() -> dict:
    return {
        "capture_id": "cap_test",
        "packet_id": "pkt_test",
        "status": "processing",
        "tier": "arkit",
        "flow": "guided",
        "device_model": "iPhone16,1",
        "order_ref": "base-order-0",
        "owner_principal": "member:demo",
        "created_at": WHEN,
        "finalize_by": "2026-09-27T00:00:00Z",
        "epochs": ["e1"],
    }


def run_dict(*, degraded: list[str] | None = None, failure_code: str | None = None, run_id: str = "run_ok") -> dict:
    doc = {
        "run_id": run_id,
        "capture_id": "cap_test",
        "packet_sha256": "abc",
        "status": "done",
        "branch": "lidar",
        "flow": "guided",
        "tier": "arkit",
        "profile_default": "C",
        "created_at": WHEN,
        "deadline_at": "2026-09-27T00:00:00Z",
        "artifacts": {},
    }
    if degraded:
        doc["degraded"] = degraded
    if failure_code:
        doc["failure_code"] = failure_code
        doc["run_id"] = "run_failed"
    return doc


def reads_dict(status: str, value: str | None, *, agree: bool = False, candidates: list[str] | None = None) -> dict:
    return {
        "meter": {
            "status": status,
            "value": value,
            "agree": agree,
            "barcode_match": status == "auto_fill",
            "candidates": [{"value": item, "rank": index + 1} for index, item in enumerate(candidates or [])],
        },
        "confirm": {"still_id": "s01"},
        "facts": [{"id": "meter_number", "clean": status != "unreadable"}],
    }


def _dump(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1) + "\n")


def _check_scene(doc: dict) -> None:
    sceneio.validate(doc)


def _check_verdict(doc: dict) -> None:
    sceneio.validate(doc)


def write_fixtures(root: Path = DATA) -> None:
    arkit = scene_dict(source_frame="arkit_world/e1", with_keyframe=False)
    photo = scene_dict(source_frame="eth3d_world", with_keyframe=True)
    _check_scene(arkit)
    _check_scene(photo)
    eligible = eligible_verdict()
    views = views_verdict()
    reject = reject_verdict()
    _check_verdict(eligible)
    _check_verdict(views)
    _check_verdict(reject)
    _dump(root / "synthetic" / "scene.json", arkit)
    _dump(root / "photo" / "scene.json", photo)
    _dump(root / "synthetic" / "verdicts" / "verdict.C.json", eligible)
    _dump(root / "photo" / "verdicts" / "verdict.C.json", views)
    _dump(root / "reject" / "verdicts" / "verdict.C.json", reject)
    xyz = np.array([[0.0, 0.5, 1.0], [1.0, 0.5, 0.2], [3.0, 0.5, 1.0], [5.0, 0.2, 2.0]], float)
    gen = np.zeros(4, np.uint8)
    sceneio.write_ply(root / "synthetic" / "points.ply", xyz, {"generated": gen})
    sceneio.write_ply(root / "photo" / "points.ply", xyz, {"generated": gen})
    _dump(root / "capture.sample.json", capture_dict())
    _dump(root / "run.ok.json", run_dict())
    _dump(root / "run.degraded.json", run_dict(degraded=["objects"], run_id="run_degraded"))
    _dump(root / "run.failed.json", run_dict(failure_code="hard_stop"))
    _dump(root / "reads.auto_fill.json", reads_dict("auto_fill", "000000000"))
    _dump(root / "reads.prefill.json", reads_dict("prefill", "111111111", candidates=["111111111", "000000000"]))
    _dump(root / "reads.choose.json", reads_dict("choose", "111111111", agree=True, candidates=["111111111"]))
    _dump(root / "reads.unreadable.json", reads_dict("unreadable", None))
    _dump(root / "reads.disabled.json", reads_dict("disabled", None))
    _dump(root / "prompts.en-US.json", catalogue_document(FALLBACK))


if __name__ == "__main__":
    write_fixtures()
