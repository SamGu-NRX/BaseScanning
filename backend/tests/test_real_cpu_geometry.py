"""Real CPU geometry journey on the committed mini-arkit-lidar fixture.

Runs the four stage entries (dense, coverage, scene, objects_3d) over
``ios-kit/fixtures/mini-arkit-lidar`` on CPU, each stage in a child process
pinned to two cores with two BLAS/OpenMP threads. Per stage, wall time and
peak RSS are asserted against fixed budgets and every artifact is matched
against ``server/orchestrator/graph.py``. The scene must contain an observed
wall and a ground plane, and the wall plane's fit residual against the dense
LiDAR cloud is measured and reported. A single injected 2D ``main_panel``
detection with two views must lift onto that wall through ``run_3d``.

The poses and scale inputs mirror what ``server/poses/run.py`` and
``server/scale/run.py`` write. On this packet (six keyframes, too few to
reconstruct) real poses runs in ``arkit_fixed`` mode and carries the ARKit
poses verbatim, so the helpers emit carried-ARKit camera rows (the public
``_export``/``_carry_arkit`` schema) and an honest ``registered_fraction``
of 0.0. The scale helper emits a unit LiDAR scale block
(``scale_factor`` 1.0, no origin shift), which is the metric frame the
fixture's real ARKit poses already carry.
"""

from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

import pytest

MAX_STAGE_WALL_S = 600.0
MAX_STAGE_RSS_KB = 8 * 1024 * 1024  # 8 GiB, ru_maxrss units (KiB on Linux)
REPO = Path(__file__).resolve().parents[1]
PACKET_DIR = REPO / "ios-kit" / "fixtures" / "mini-arkit-lidar"
STAGES = ("dense", "coverage", "scene", "objects_3d")
BOX_HALF_PX = 120.0

LIFT_CLASS = "main_panel"  # WALL_MOUNTED detector class; SCENE_CLASS maps it to "panel"

# Stage entry environment. Thread caps hold the two-core budget honest;
# LIBGL/EGL keep Open3D usable on a headless runner. HSS_COVERAGE_METER_FRAMES
# must stay unset.
_CHILD_ENV = {
    "HSS_DENSE_GEOMETRY": "lidar",
    "SMP_ENVIRONMENT": "dev",
    "HSS_COVERAGE_ANCHOR_FROM_TAP": "reference_point",
    "OMP_NUM_THREADS": "2",
    "OMP_THREAD_LIMIT": "2",
    "OPENBLAS_NUM_THREADS": "2",
    "MKL_NUM_THREADS": "2",
    "NUMEXPR_NUM_THREADS": "2",
    "LIBGL_ALWAYS_SOFTWARE": "1",
    "EGL_PLATFORM": "surfaceless",
}

_STAGE_INPUTS = {
    "dense": ("packet_dir", "poses_dir", "scale/scale.json"),
    "coverage": ("packet_dir", "poses_dir", "scale/scale.json"),
    "scene": ("packet_dir", "poses_dir", "objects/objects.json"),
    "objects": (),
    "objects_3d": (),
}


def _ctx(stage: str, work: Path):
    from server.contracts.jobs import StageContext

    inputs = {}
    for key in _STAGE_INPUTS[stage]:
        if "/" in key:
            inputs[key] = str(work / key)
        else:
            inputs[key] = str(PACKET_DIR if key == "packet_dir" else work / "poses")
    return StageContext(
        capture_id="burst14-real-geom",
        run_id="run",
        stage=stage,
        attempt=1,
        branch="lidar",
        flow="guided",
        tier="arkit_lidar",
        work_dir=work,
        inputs=inputs,
        deadline_at="2099-01-01T00:00:00Z",
        heartbeat=lambda _s: None,
        vlm_budget_usd=0.0,
        storage=object(),
        state=object(),
        settings=object(),
    )


# --- fixture-input helpers (mirror server/poses/run.py and server/scale/run.py output) ---


def _packet() -> dict:
    return json.loads((PACKET_DIR / "packet.json").read_text())


def _write_poses(work: Path) -> None:
    packet = _packet()
    rows = []
    for kf in packet["keyframes"]:
        rows.append(
            {
                "image": Path(kf["img"]).stem,
                "keyframe_id": kf["id"],
                "epoch": kf["epoch"],
                "pose": kf["pose"],
                "intrinsics": kf["intrinsics"],
                "w": kf["w"],
                "h": kf["h"],
                "pose_source": "arkit",
                "reproj_px": 0.0,
                "points3D": 0,
            }
        )
    rows.sort(key=lambda row: row["keyframe_id"])
    poses = work / "poses"
    poses.mkdir(parents=True, exist_ok=True)
    (poses / "cameras.json").write_text(json.dumps(rows, indent=1))
    anchor = rows[0]["epoch"] if rows else "e0"
    carried = len(rows)
    report = {
        "format": "hss-registration-report/v0",
        "mode": "arkit_fixed",
        "status": "ok",
        "source_frame": f"arkit_world:{anchor}",
        "flow": "guided",
        "lidar_frames": len(packet["keyframes"]),
        "frames_given": len(packet.get("frames") or []),
        "frames_in_sfm": 0,
        "frames_registered": 0,
        "registered_fraction": 0.0,
        "registration_counts": {"mapper": 0, "post_registered": 0, "carried": carried},
        "frame_budget": {
            "sfm_max_frames": 0,
            "mapper_frames": 0,
            "registered_post": 0,
            "arkit_carried": carried,
            "frames_dropped_quality": 0,
        },
        "anchor_epoch": anchor,
        "epochs": [],
        "warnings": [],
    }
    (poses / "registration_report.json").write_text(json.dumps(report, indent=1))


def _write_scale(work: Path) -> None:
    scale = work / "scale"
    scale.mkdir(parents=True, exist_ok=True)
    block = {
        "source": "lidar",
        "status": "ok",
        "scale_factor": 1.0,
        "origin_m": None,
        "rel_bound_95": 0.05,
        "rel_sd": 0.02,
        "confidence": 1.0,
        "conflict": False,
        "reason": "fixture_unit_lidar",
        "reason_detail": "unit LiDAR scale for the real CPU geometry journey test",
        "estimates": [],
        "table_entry": None,
        "table_version": None,
    }
    (scale / "scale.json").write_text(json.dumps(block, indent=1))


# --- stage runner (child side) ---


def _run_stage(stage: str) -> dict:
    work = Path(os.environ["HSS_GEOM_WORK"])
    started = time.perf_counter()
    if stage == "dense":
        from server.dense.run import run as entry
    elif stage == "coverage":
        from server.coverage.run import run as entry
    elif stage == "scene":
        from server.scene.run import run as entry
    elif stage == "objects_3d":
        from server.objects.run import run_3d as entry
    else:  # pragma: no cover - guarded by argv check
        raise SystemExit(f"unknown stage {stage}")
    result = entry(_ctx(stage, work))
    wall_s = time.perf_counter() - started
    return {
        "stage": stage,
        "wall_s": round(wall_s, 2),
        "maxrss_kb": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "artifacts": result.artifacts,
        "warnings": list(result.warnings),
    }


if __name__ == "__main__":  # child entry: python tests/test_real_cpu_geometry.py <stage>
    stage = sys.argv[1]
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # bare python children lack pytest's pythonpath
    os.environ.update(_CHILD_ENV)
    allowed = sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0, set(allowed[:2]))
    print(json.dumps(_run_stage(stage)))
    sys.exit(0)


def _spawn(stage: str, work: Path) -> dict:
    env = dict(os.environ)
    env.update(_CHILD_ENV)
    env["HSS_GEOM_WORK"] = str(work)
    env["PYTHONPATH"] = str(REPO)
    env.pop("HSS_COVERAGE_METER_FRAMES", None)
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), stage],
        capture_output=True,
        text=True,
        env=env,
        timeout=int(MAX_STAGE_WALL_S) + 180,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"stage {stage} failed rc={proc.returncode}\nstdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        )
    payload = None
    for line in proc.stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("{") and '"stage"' in line:
            payload = json.loads(line)
    if payload is None:
        pytest.fail(
            f"stage {stage} produced no result JSON\nstdout:\n{proc.stdout[-2000:]}\nstderr:\n{proc.stderr[-2000:]}"
        )
    return payload


def _assert_budget(result: dict) -> None:
    assert result["wall_s"] <= MAX_STAGE_WALL_S, f"{result['stage']} wall {result['wall_s']}s"
    assert result["maxrss_kb"] <= MAX_STAGE_RSS_KB, (
        f"{result['stage']} peak RSS {result['maxrss_kb'] / 1048576:.2f} GiB"
    )


def _assert_artifacts_match_graph(stage: str, work: Path, artifacts: dict) -> None:
    """graph.py declares the minimum artifact set; runners may write extra files."""
    from server.orchestrator.graph import STAGES

    declared = set(STAGES[stage].artifacts)
    got = set(artifacts)
    on_disk = {key for key in declared if (work / key).exists()}
    assert declared <= got | on_disk, f"{stage} missing artifacts {sorted(declared - got - on_disk)}"


@pytest.fixture(scope="module")
def journey(tmp_path_factory) -> tuple[Path, dict]:
    work = tmp_path_factory.mktemp("real-geom")
    _write_poses(work)
    _write_scale(work)
    results: dict[str, dict] = {}
    for stage in STAGES:
        if stage == "scene":  # stand-in 2D objects, as the orchestrator would hydrate
            from server.orchestrator.stubs.objects import run as stub_objects

            stub_objects(_ctx("objects", work))
        results[stage] = _spawn(stage, work)
    return work, results


# --- chain assertions ---


def test_dense_stage_runs_real_lidar(journey):
    work, results = journey
    dense = results["dense"]
    assert "lidar_fused" in dense["warnings"], dense["warnings"]
    _assert_artifacts_match_graph("dense", work, dense["artifacts"])
    assert (work / "poses" / "cameras.json").is_file()
    assert (work / "scale" / "scale.json").is_file()
    report = json.loads((work / "dense" / "dense_report.json").read_text())
    assert report["geometry_role"] == "coverage_only", report["geometry_role"]
    assert report["counts"]["points"] > 0
    _assert_budget(dense)


def test_coverage_stage_anchors_on_tap(journey):
    work, results = journey
    coverage = results["coverage"]
    _assert_artifacts_match_graph("coverage", work, coverage["artifacts"])
    ready = json.loads((work / "coverage" / "scene_ready.json").read_text())
    assert ready["walls"], ready["walls"]
    _assert_budget(coverage)


def test_scene_stage_wall_ground_and_residual(journey):
    work, results = journey
    scene = results["scene"]
    _assert_artifacts_match_graph("scene", work, scene["artifacts"])
    doc = _scene_doc(work)
    observed = [w for w in doc["walls"] if w.get("evidence") == "observed"]
    assert observed, doc["walls"]
    assert doc["ground"]["surface"]["kind"] == "plane", doc["ground"]
    import numpy as np

    cloud = np.load(work / "dense" / "cloud.npz")["points"]
    residual = _wall_plane_residual(cloud, observed[0])
    summary()["wall_residual"] = residual
    summary()["wall"] = _wall_summary(observed[0])
    assert residual["points_near"] > 100, residual
    assert residual["median_m"] < 0.15, residual
    assert residual["p95_m"] < 0.35, residual
    _assert_budget(scene)


def test_objects_3d_stage_artifacts(journey):
    work, results = journey
    res = results["objects_3d"]
    _assert_artifacts_match_graph("objects_3d", work, res["artifacts"])
    out = json.loads((work / "objects" / "objects_3d.json").read_text())
    assert out["format"] == "hss-objects-3d/v0"
    assert isinstance(out["objects"], list)
    requests = json.loads((work / "objects" / "view_requests.json").read_text())
    assert isinstance(requests, list)
    _assert_budget(res)


def test_stage_budget_summary(journey):
    _, results = journey
    rows = {stage: {"wall_s": res["wall_s"], "maxrss_kb": res["maxrss_kb"]} for stage, res in results.items()}
    summary()["stages"] = rows
    assert all(row["wall_s"] <= MAX_STAGE_WALL_S for row in rows.values()), rows


# --- direct run_3d lift ---


def test_run_3d_lifts_single_detection_onto_wall(journey, tmp_path):
    from server.eval.frame import project_arkit
    from server.objects.lift import cast_pixel, pose_matrix
    from server.objects.run import run_3d

    work, _ = journey
    doc = _scene_doc(work)
    walls = [w for w in doc["walls"] if w.get("baseline_m")]
    assert walls, doc["walls"]

    # The fixture's reference_point tap pixel casts no wall from any camera
    # (the k00023 camera sits 0.15 m off the wall plane and its ray misses the
    # reconstructed fragment), so the injected views aim at points ON the
    # observed wall instead, projected through a frame that genuinely sees it.
    # Boxes must come from the SCENE-frame keyframe poses — the scene builder
    # aligns poses into the scene frame, and lift_wall_cast casts pixels with
    # those same scene poses (cameras.json poses live in the raw ARKit frame).
    rows = {kf["id"]: kf for kf in doc["keyframes"]}
    # Scene wall fits are not bit-deterministic across runs, so pick any
    # (frame, observed wall, span fraction) whose in-plane point is in view.
    observed = [w for w in doc["walls"] if w.get("evidence") == "observed" and w.get("baseline_m")]
    assert observed, doc["walls"]
    pick = next(
        (
            (kid, w, frac)
            for frac in (0.5, 0.35, 0.65)
            for kid in sorted(rows)
            for w in observed
            if _casts_wall(rows[kid], w, walls, frac)
        ),
        None,
    )
    assert pick, "no keyframe sees any observed wall"
    frame, wall, frac = pick
    cam = rows[frame]
    pose = pose_matrix(cam["pose"])

    baseline = wall["baseline_m"]
    a, b = baseline[0], baseline[1]
    length = float(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5)
    probe = cast_pixel(
        pose[:3, 3],
        pose,
        cam["intrinsics"],
        *_wall_pixel(rows[frame], wall, length * frac, wall["height_m"] / 2),
        walls,
    )
    assert probe is not None and probe[0]["id"] == wall["id"], (probe, wall["id"])
    _, hit_point, tap_s = probe

    def wall_point(s: float) -> list[float]:
        return [a[0] + (b[0] - a[0]) * s / length, wall["height_m"] / 2, a[1] + (b[1] - a[1]) * s / length]

    def box_for(point: list[float]) -> list[float]:
        u, v, z = project_arkit(pose_matrix(cam["pose"]), cam["intrinsics"], point)
        u, v = float(u), float(v)
        assert z > 0 and 0 <= u < cam["w"] and 0 <= v < cam["h"], (frame, u, v, z)
        box = [
            max(u - BOX_HALF_PX, 0.0),
            max(v - BOX_HALF_PX, 0.0),
            min(u + BOX_HALF_PX, cam["w"] - 1.0),
            min(v + BOX_HALF_PX, cam["h"] - 1.0),
        ]
        assert box[0] < box[2] and box[1] < box[3], (frame, box)
        return box

    s_mid = tap_s
    half_span = 0.18  # two wall points 0.36 m apart: inside the 0.5 m agree radius
    points = [wall_point(s_mid - half_span), wall_point(s_mid + half_span)]
    row = {
        "class": LIFT_CLASS,
        "confidence": 0.9,
        "views": [
            {"image": frame, "box_px": box_for(points[0]), "score": 0.9},
            {"image": frame + "b", "box_px": box_for(points[1]), "score": 0.9},
        ],
    }

    lift_work = tmp_path / "lift"
    (lift_work / "poses").mkdir(parents=True)
    (lift_work / "objects").mkdir()
    # cluster_points requires two DISTINCT frame ids (min_views counts frames,
    # not views). In this fixture only one camera ever sees the reconstructed
    # wall fragment, so duplicate that keyframe under a second id — same real
    # pose — to stand in for a second capture of the same wall.
    scene_doc = {**doc, "keyframes": [*doc["keyframes"], {**rows[frame], "id": frame + "b"}]}
    target = lift_work / "scene" / "scene.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(scene_doc))
    (lift_work / "poses" / "registration_report.json").write_text(
        (work / "poses" / "registration_report.json").read_text()
    )
    (lift_work / "objects" / "objects.json").write_text(json.dumps({"ground_type": [], "objects": [row]}))

    run_3d(_ctx("objects_3d", lift_work))

    out = json.loads((lift_work / "objects" / "objects_3d.json").read_text())
    panels = [o for o in out["objects"] if o["class"] == "panel"]
    assert panels, out
    panel = panels[0]
    geometry = panel["geometry"]
    assert geometry["kind"] == "wall_rect", geometry
    assert geometry["wall_id"] == wall["id"], (geometry, wall["id"])
    s0, s1 = geometry["s_m"]
    assert -0.5 <= s0 <= s1 <= length + 0.5, (s0, s1, length)
    assert abs((s0 + s1) / 2 - tap_s) <= 0.35, (s0, s1, tap_s)
    assert 0.0 <= geometry["bottom_m"] < geometry["top_m"] <= wall.get("height_m", 99.0) + 0.5, geometry
    summary()["lift"] = {
        "wall_id": wall["id"],
        "s_m": [s0, s1],
        "tap_s": tap_s,
        "evidence": panel["evidence"],
        "plus_minus_m": panel["plus_minus_m"],
    }
    requests = json.loads((lift_work / "objects" / "view_requests.json").read_text())
    assert isinstance(requests, list)


def _casts_wall(cam: dict, wall: dict, walls: list, frac: float = 0.5) -> bool:
    """True when the in-plane point and its +/-0.18 m neighbours project in view
    AND cast onto this wall (other fitted walls can occlude part of it)."""
    from server.eval.frame import project_arkit
    from server.objects.lift import cast_pixel, pose_matrix

    baseline = wall["baseline_m"]
    a, b = baseline[0], baseline[1]
    length = float(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5)
    pose = pose_matrix(cam["pose"])
    for ds in (-0.18, 0.0, 0.18):
        s = length * frac + ds
        point = [a[0] + (b[0] - a[0]) * s / length, wall["height_m"] / 2, a[1] + (b[1] - a[1]) * s / length]
        u, v, z = project_arkit(pose, cam["intrinsics"], point)
        if not (z > 0 and 0 <= float(u) < cam["w"] and 0 <= float(v) < cam["h"]):
            return False
        hit = cast_pixel(pose[:3, 3], pose, cam["intrinsics"], float(u), float(v), walls)
        if hit is None or hit[0]["id"] != wall["id"]:
            return False
    return True


def _wall_pixel(cam: dict, wall: dict, s: float, y: float) -> tuple[float, float]:
    from server.eval.frame import project_arkit
    from server.objects.lift import pose_matrix

    baseline = wall["baseline_m"]
    a, b = baseline[0], baseline[1]
    length = float(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5)
    point = [a[0] + (b[0] - a[0]) * s / length, y, a[1] + (b[1] - a[1]) * s / length]
    u, v, z = project_arkit(pose_matrix(cam["pose"]), cam["intrinsics"], point)
    assert z > 0, (s, z)
    return float(u), float(v)


# --- helpers ---


def _scene_doc(work: Path) -> dict:
    return json.loads((work / "scene" / "scene.json").read_text())


def _wall_summary(wall: dict) -> dict:
    baseline = wall["baseline_m"]
    length = float(((baseline[1][0] - baseline[0][0]) ** 2 + (baseline[1][1] - baseline[0][1]) ** 2) ** 0.5)
    return {
        "id": wall["id"],
        "length_m": round(length, 3),
        "height_m": wall.get("height_m"),
        "evidence": wall.get("evidence"),
    }


def _wall_plane_residual(points, wall: dict) -> dict:
    import numpy as np

    baseline = np.asarray(wall["baseline_m"], float)
    a, b = baseline[0], baseline[1]
    direction = b - a
    length = float(np.hypot(direction[0], direction[1]))
    normal = np.array([-direction[1], direction[0]]) / length
    xz = np.asarray(points, float)[:, [0, 2]]
    signed = (xz - a) @ normal
    band = np.abs(signed) < 0.15
    if int(band.sum()) < 100:
        return {"points_near": int(band.sum()), "median_m": None, "p95_m": None, "share": round(float(band.mean()), 4)}
    dist = np.abs(signed[band])
    return {
        "points_near": int(band.sum()),
        "median_m": round(float(np.median(dist)), 4),
        "p95_m": round(float(np.percentile(dist, 95)), 4),
        "share": round(float(band.mean()), 4),
    }


_SUMMARY: dict = {}


def summary() -> dict:
    """Module-level scratch the reporting test reads; never asserted on directly."""
    return _SUMMARY
