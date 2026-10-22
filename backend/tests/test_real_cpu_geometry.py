"""Real CPU geometry journey on the committed mini-arkit-lidar fixture.

Runs the four stage entries (dense, coverage, scene, objects_3d) over
``ios-kit/fixtures/mini-arkit-lidar`` on CPU, each stage in a child process
with two BLAS/OpenMP threads, pinned to two cores where the OS supports CPU
affinity (Linux; macOS runs unpinned). Per stage, wall time and peak RSS are
asserted against fixed budgets and every artifact is matched against
``server/orchestrator/graph.py``. The scene must contain an observed wall and
a ground plane, and the dense LiDAR cloud, moved into the scene frame, must lie
on that wall's plane. A single injected 2D ``main_panel`` detection with two
views must lift onto that wall through ``run_3d`` in the same budgeted child.

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

# Every dense run of this fixture on WSL (Linux) wrote 109,252 points. The floor
# sits near half of that, so a large loss fails while small method drift passes.
MIN_DENSE_POINTS = 50_000

# Wall fit statistics, all in the scene frame. A wall's support is every dense
# point over its baseline span, between WALL_FLOOR_CLEAR_M and its height, and
# within WALL_SUPPORT_SLAB_M of its plane. The slab only keeps other walls out
# (this fixture has one about 8 m behind the meter wall); it is far wider than
# the inlier band, so a misfit shows up in the median and the inlier share.
WALL_INLIER_M = 0.15
WALL_SUPPORT_SLAB_M = 1.0
WALL_FLOOR_CLEAR_M = 0.1
# Measured on WSL over 20 runs of the 0da4677d journey: inlier share 0.66-0.78
# (median 0.045-0.095 m) on the 15 runs where the meter wall landed on the LiDAR
# surface, 0.12-0.21 (median 0.40-0.49 m) on the 5 where it did not. Support p95 is
# 0.36-0.58 m even on good fits, because the meter equipment stands off the wall,
# so p95 is reported, not asserted.
MIN_WALL_INLIER_SHARE = 0.5

LIFT_CLASS = "main_panel"  # WALL_MOUNTED detector class; SCENE_CLASS maps it to "panel"

# Stage entry environment. Thread caps hold the two-core budget honest;
# LIBGL/EGL keep Open3D usable on a headless runner. HSS_COVERAGE_METER_FRAMES
# must stay unset. The LiDAR method is pinned because the child inherits the
# parent environment, and HSS_DENSE_LIDAR_METHOD=points would skip Open3D TSDF
# while still reporting lidar_fused.
_CHILD_ENV = {
    "HSS_DENSE_GEOMETRY": "lidar",
    "HSS_DENSE_LIDAR_METHOD": "tsdf",
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
        "maxrss_kb": _peak_rss_kb(),
        "artifacts": result.artifacts,
        "warnings": list(result.warnings),
    }


def _peak_rss_kb() -> int:
    """Peak RSS of this process in KiB. ``ru_maxrss`` is KiB on Linux but bytes on macOS."""
    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return peak // 1024 if sys.platform == "darwin" else peak


def _pin_two_cpus() -> list[int] | None:
    """Pin this process to its first two allowed CPUs and return them.

    macOS has no ``sched_getaffinity``/``sched_setaffinity``. There the child runs
    unpinned, under the thread caps in ``_CHILD_ENV`` only, and this returns None so
    the stage result records that no pinning happened.
    """
    if not (hasattr(os, "sched_getaffinity") and hasattr(os, "sched_setaffinity")):
        return None
    allowed = sorted(os.sched_getaffinity(0))[:2]
    os.sched_setaffinity(0, set(allowed))
    return allowed


if __name__ == "__main__":  # child entry: python tests/test_real_cpu_geometry.py <stage>
    stage = sys.argv[1]
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # bare python children lack pytest's pythonpath
    os.environ.update(_CHILD_ENV)
    pinned = _pin_two_cpus()
    print(json.dumps({**_run_stage(stage), "cpus_pinned": pinned}))
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
    if hasattr(os, "sched_setaffinity"):
        pinned = result["cpus_pinned"]
        assert pinned and len(pinned) <= 2, f"{result['stage']} pinned to {pinned}"


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
    import numpy as np

    cloud = np.load(work / "dense" / "cloud.npz")["points"]
    assert len(cloud) == report["counts"]["points"], (len(cloud), report["counts"])
    assert len(cloud) > MIN_DENSE_POINTS, len(cloud)
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
    residual = _wall_plane_residual(_scene_cloud(work), observed[0])
    summary()["wall_residual"] = residual
    summary()["wall"] = _wall_summary(observed[0])
    assert residual["points_near"] > 100, residual
    assert residual["median_m"] < WALL_INLIER_M, residual
    assert residual["inlier_share"] >= MIN_WALL_INLIER_SHARE, residual
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
        # lift_wall_cast casts the box centre, so the box must not be clipped:
        # clipping would move the centre off the pixel _casts_wall checked.
        u, v, z = project_arkit(pose_matrix(cam["pose"]), cam["intrinsics"], point)
        u, v = float(u), float(v)
        assert z > 0 and _box_fits(cam, u, v), (frame, u, v, z)
        return [u - BOX_HALF_PX, v - BOX_HALF_PX, u + BOX_HALF_PX, v + BOX_HALF_PX]

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

    lift = _spawn("objects_3d", lift_work)
    _assert_artifacts_match_graph("objects_3d", lift_work, lift["artifacts"])
    _assert_budget(lift)

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
        "wall_s": lift["wall_s"],
        "maxrss_kb": lift["maxrss_kb"],
    }
    requests = json.loads((lift_work / "objects" / "view_requests.json").read_text())
    assert isinstance(requests, list)


def _box_fits(cam: dict, u: float, v: float) -> bool:
    """True when a BOX_HALF_PX box centred on (u, v) lies inside the image unclipped."""
    return BOX_HALF_PX <= u <= cam["w"] - 1.0 - BOX_HALF_PX and BOX_HALF_PX <= v <= cam["h"] - 1.0 - BOX_HALF_PX


def _casts_wall(cam: dict, wall: dict, walls: list, frac: float = 0.5) -> bool:
    """True when the in-plane point and its +/-0.18 m neighbours project far enough
    inside the image for an unclipped box AND cast onto this wall (other fitted
    walls can occlude part of it)."""
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
        if not (z > 0 and _box_fits(cam, float(u), float(v))):
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


def _scene_cloud(work: Path):
    """The dense cloud in the scene frame, where scene.json's walls live.

    ``dense/cloud.npz`` holds points in frame W. Coverage moves them into the
    meter-anchored scene frame with ``coverage/state.npz["t"]`` (written by
    server/coverage/run.py, applied with ``frame.apply`` in server/scene/build.py),
    and the scene stage keeps that frame.
    """
    import numpy as np

    from server.eval.frame import apply

    cloud = np.load(work / "dense" / "cloud.npz")["points"]
    transform = np.load(work / "coverage" / "state.npz")["t"]
    return apply(np.asarray(transform, float), cloud)


def _quantile(values, q: float) -> float | None:
    import numpy as np

    return round(float(np.percentile(values, q)), 4) if len(values) else None


def _wall_plane_residual(points, wall: dict) -> dict:
    """Distances from scene-frame points to a wall's plane, over the wall's support.

    Top-level statistics cover every support point (see WALL_SUPPORT_SLAB_M).
    ``near`` repeats them for points within WALL_INLIER_M only: that describes
    plane noise, but on its own it cannot show a misfit, because every point a
    misfit pushes away is dropped from it.
    """
    import numpy as np

    a, b = np.asarray(wall["baseline_m"], float)
    direction = b - a
    length = float(np.hypot(direction[0], direction[1]))
    along = direction / length
    normal = np.array([-direction[1], direction[0]]) / length
    pts = np.asarray(points, float)
    xz = pts[:, [0, 2]]
    s = (xz - a) @ along
    dist = np.abs((xz - a) @ normal)
    y = pts[:, 1]
    support = (
        (s >= 0.0)
        & (s <= length)
        & (y >= WALL_FLOOR_CLEAR_M)
        & (y <= float(wall["height_m"]))
        & (dist < WALL_SUPPORT_SLAB_M)
    )
    d = dist[support]
    near = d[d < WALL_INLIER_M]
    return {
        "points_support": len(d),
        "points_near": len(near),
        "inlier_share": round(len(near) / len(d), 4) if len(d) else 0.0,
        "median_m": _quantile(d, 50),
        "p95_m": _quantile(d, 95),
        "max_m": round(float(d.max()), 4) if len(d) else None,
        "near": {"median_m": _quantile(near, 50), "p95_m": _quantile(near, 95)},
    }


_SUMMARY: dict = {}


def summary() -> dict:
    """Measurements the journey tests collect; ``_emit_summary`` prints them after the module."""
    return _SUMMARY


@pytest.fixture(scope="module", autouse=True)
def _emit_summary(request):
    yield
    if not _SUMMARY:
        return
    line = "real CPU geometry summary: " + json.dumps(_SUMMARY, sort_keys=True)
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(line)


# --- helper checks with known answers (no journey) ---

# Baseline along +x at z = 0, so a point's z is its signed distance to the plane.
_TEST_WALL = {"id": "w1", "baseline_m": [[0.0, 0.0], [4.0, 0.0]], "height_m": 2.5}


def _wall_points(offsets):
    """One point per offset, spread over the test wall's span and height, offset along its normal."""
    import numpy as np

    offsets = np.asarray(offsets, float)
    n = len(offsets)
    return np.stack([np.linspace(0.2, 3.8, n), np.linspace(0.3, 2.2, n), offsets], axis=1)


def test_wall_residual_keeps_outliers_in_p95():
    residual = _wall_plane_residual(_wall_points([0.01, -0.01] * 450 + [0.6] * 100), _TEST_WALL)
    assert residual["p95_m"] == pytest.approx(0.6, abs=1e-3), residual
    assert residual["max_m"] == pytest.approx(0.6, abs=1e-3), residual
    assert residual["points_support"] == 1000, residual
    assert residual["points_near"] == 900, residual
    assert residual["inlier_share"] == pytest.approx(0.9), residual
    assert residual["near"]["p95_m"] == pytest.approx(0.01, abs=1e-3), residual


def test_wall_residual_shows_partial_misfit():
    # 300 points on the plane would pass a band-only check (>100 near, tiny median);
    # the other 700 sit 0.4 m off and must dominate the statistics.
    residual = _wall_plane_residual(_wall_points([0.01, -0.01] * 150 + [0.4, -0.4] * 350), _TEST_WALL)
    assert residual["median_m"] == pytest.approx(0.4, abs=1e-3), residual
    assert residual["inlier_share"] == pytest.approx(0.3), residual
    assert residual["median_m"] >= WALL_INLIER_M or residual["inlier_share"] < MIN_WALL_INLIER_SHARE


def test_wall_residual_support_excludes_other_surfaces():
    import numpy as np

    wall = _wall_points([0.01, -0.01] * 500)
    far_wall = _wall_points([8.0] * 200)  # another wall behind this one
    ground = _wall_points([0.5] * 200)
    ground[:, 1] = 0.0
    past_end = _wall_points([0.3] * 200)
    past_end[:, 0] += 5.0
    above = _wall_points([0.3] * 200)
    above[:, 1] += 3.0
    residual = _wall_plane_residual(np.vstack([wall, far_wall, ground, past_end, above]), _TEST_WALL)
    assert residual["points_support"] == 1000, residual
    assert residual["inlier_share"] == 1.0, residual


def test_scene_cloud_applies_coverage_transform(tmp_path):
    import numpy as np

    from server.eval.frame import apply

    angle = np.deg2rad(35.0)
    transform = np.eye(4)
    transform[:3, :3] = [[np.cos(angle), 0, np.sin(angle)], [0, 1, 0], [-np.sin(angle), 0, np.cos(angle)]]
    transform[:3, 3] = [2.0, 0.4, -3.0]
    scene_points = _wall_points([0.01, -0.01] * 500)
    cloud_w = apply(np.linalg.inv(transform), scene_points)
    (tmp_path / "dense").mkdir()
    (tmp_path / "coverage").mkdir()
    np.savez_compressed(tmp_path / "dense" / "cloud.npz", points=cloud_w.astype(np.float32))
    np.savez_compressed(tmp_path / "coverage" / "state.npz", t=transform)

    # In frame W the points miss the wall, so only the transform can make this pass.
    assert _wall_plane_residual(cloud_w, _TEST_WALL)["points_near"] == 0
    residual = _wall_plane_residual(_scene_cloud(tmp_path), _TEST_WALL)
    assert residual["points_near"] == 1000, residual
    assert residual["points_support"] == 1000, residual
    assert residual["median_m"] == pytest.approx(0.01, abs=1e-3), residual


def test_pin_two_cpus_skips_without_affinity(monkeypatch):
    monkeypatch.delattr(os, "sched_getaffinity", raising=False)
    monkeypatch.delattr(os, "sched_setaffinity", raising=False)
    assert _pin_two_cpus() is None


def test_pin_two_cpus_pins_first_two_allowed(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: {7, 1, 3}, raising=False)
    monkeypatch.setattr(os, "sched_setaffinity", lambda pid, cpus: calls.append((pid, cpus)), raising=False)
    assert _pin_two_cpus() == [1, 3]
    assert calls == [(0, {1, 3})]


@pytest.mark.parametrize(
    ("platform", "raw", "expected_kb"), [("darwin", 2 * 1024**3, 2 * 1024**2), ("linux", 2 * 1024**2, 2 * 1024**2)]
)
def test_peak_rss_kb_units(monkeypatch, platform, raw, expected_kb):
    from types import SimpleNamespace

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(resource, "getrusage", lambda _who: SimpleNamespace(ru_maxrss=raw))
    assert _peak_rss_kb() == expected_kb
