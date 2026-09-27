"""Score a pose run against a GT scene.

``--gt`` wraps ``server.eval.geomscore.score_cloud`` on ``poses/sparse.ply``.
``--truth`` rewrites image stems to the GT keyframe ids (packet ``k00001`` ->
``DSC_0324``) so the scorer can match cameras. ``--metric`` aligns rigidly.
``--print-meter-frames`` prints the two packet ids the validator passes to
``--meter-frames``.
"""

from __future__ import annotations

import argparse
import inspect
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pycolmap  # noqa: E402

import server.eval.geomscore as geomscore  # noqa: E402
from server.common.geometry import arkit_to_colmap_pose, column_major, from_column_major, umeyama  # noqa: E402
from server.eval.frame import mat4_from_list  # noqa: E402
from server.poses.params import PoseParams  # noqa: E402
from server.poses.report import write_ply  # noqa: E402
from server.poses.sfm import lock_intrinsics, triangulate_fixed  # noqa: E402

# server/eval commit named in the diagnosis, plus A7's one-line camera patch.
# The patch lives in this script so server/eval stays untouched.
HARNESS = "5525a2d80bfc0f41633f66d08c919faaf8436c06+A7"
_A7_STOCK = (
    'gt_cams = [Camera(n, mat4_from_list(gkf[n]["pose"]), np.array(gkf[n]["intrinsics"]), '
    'gkf[n]["w"], gkf[n]["h"]) for n in names]'
)
_A7_PATCH = (
    'gt_cams = [Camera(n, mat4_from_list(gkf[n]["pose"]), np.asarray(cams_src[n]["K"], float), '
    'cams_src[n]["w"], cams_src[n]["h"]) for n in names]'
)
_PATCHED_SCORE = None


def _load_centers(path: Path) -> dict[str, np.ndarray]:
    raw = json.loads(path.read_text())
    if isinstance(raw, dict) and "centers" in raw:
        raw = raw["centers"]
    return {str(k): np.asarray(v, float) for k, v in raw.items()}


def _stats(errors: list[float]) -> dict:
    if not errors:
        return {"n": 0, "median_in": None, "p90_in": None}
    values = np.abs(np.asarray(errors, float))
    return {
        "n": int(len(values)),
        "median_in": round(float(np.median(values)), 2),
        "p90_in": round(float(np.percentile(values, 90)), 2),
    }


def _element_id(row_id: str) -> str | None:
    if "[" not in row_id:
        return None
    return row_id.split("[", 1)[1].split(":", 1)[0].rstrip("]")


def _row_element(row_id: str) -> str:
    """Opening rows are keyed by opening id. ``meter.height`` has no brackets."""
    if row_id.startswith("meter"):
        return "meter"
    found = _element_id(row_id)
    return found if found is not None else row_id


def _is_height_row(row_id: str) -> bool:
    return "bottom" in row_id or "top" in row_id or "meter.height" in row_id


def _wall_by_element(gt_path: Path) -> dict[str, str]:
    scene = json.loads(gt_path.read_text())
    index: dict[str, str] = {}
    for obj in scene.get("objects") or []:
        geometry = obj.get("geometry") or {}
        wall = obj.get("wall_id") or geometry.get("wall_id")
        if wall:
            index[str(obj.get("id"))] = str(wall)
    return index


def patched_score_cloud():
    """``score_cloud`` with A7's reconstruction intrinsics on GT clicks.

    Older ``server.eval`` builds those clicks from the GT camera. The local
    copy below rewrites that one line. Current ``geomscore`` already uses
    ``cams_src`` K and size, so the stock function is the patch.
    """
    global _PATCHED_SCORE
    if _PATCHED_SCORE is not None:
        return _PATCHED_SCORE
    source = inspect.getsource(geomscore.score_cloud)
    if _A7_STOCK in source:
        source = source.replace("def score_cloud(", "def score_cloud_a7(", 1)
        source = source.replace(_A7_STOCK, _A7_PATCH, 1)
        namespace = dict(geomscore.__dict__)
        exec(compile(source, "score_cloud_a7", "exec"), namespace)
        _PATCHED_SCORE = namespace["score_cloud_a7"]
        return _PATCHED_SCORE
    if 'np.asarray(cams_src[n]["K"], float)' in source:
        _PATCHED_SCORE = geomscore.score_cloud
        return _PATCHED_SCORE
    raise RuntimeError("A7 patch anchor is missing from server.eval.geomscore.score_cloud")


def _stem_map(truth_path: Path) -> dict[str, str]:
    truth = json.loads(truth_path.read_text())
    mapped: dict[str, str] = {}
    for image in truth.get("images") or []:
        source = image.get("source")
        if source:
            mapped[str(image["id"])] = Path(source).stem
    return mapped


def write_cameras_scored(cameras_path: Path, truth_path: Path | None, dest: Path) -> Path:
    """Drop ARKit-carried poses and rename stems onto GT keyframe ids."""
    rows = json.loads(cameras_path.read_text())
    stems = _stem_map(truth_path) if truth_path else {}
    # Carried phone poses are not the model. A fixed-ARKit fallback marks every
    # row arkit, and those poses are the model, so they stay.
    refined = any(row.get("pose_source") not in (None, "arkit") for row in rows)
    scored = []
    for row in rows:
        if refined and row.get("pose_source") == "arkit":
            continue
        image = str(row["image"])
        renamed = stems.get(image, image)
        scored.append({**row, "image": renamed})
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(scored, indent=1) + "\n")
    return dest


def _false_observed(result: dict) -> dict:
    by_region: dict[str, float] = {}
    for name, entry in (result.get("completeness") or {}).items():
        value = (entry.get("coverage") or {}).get("false_observed_by_rule")
        if value is not None:
            by_region[name] = float(value)
    worst = max(by_region.values()) if by_region else None
    return {"max": worst, "by_region": by_region}


def _observed_errors(result: dict, pred) -> dict:
    rows = (result.get("key_distances") or {}).get("rows") or []
    observed = [
        float(row["error"])
        for row in rows
        if row.get("status") == "scored" and row.get("coverage_truth") == "observed" and pred(str(row.get("id", "")))
    ]
    return _stats(observed)


def _kd_slice(result: dict, wall_id: str, walls: dict[str, str] | None = None) -> dict:
    """Observed KDs whose element maps to ``wall_id``.

    Opening rows are ``opening.edge_s[win1:left]``, not ``w1``. ``walls`` maps
    those element ids through the GT scene. Without it, only a literal wall id
    in the brackets matches, which is why ``w1`` used to come back empty.
    """

    def pred(row_id: str) -> bool:
        if walls is None:
            return _element_id(row_id) == wall_id
        return walls.get(_row_element(row_id)) == wall_id

    return _observed_errors(result, pred)


def _summarize_score(result: dict, walls: dict[str, str]) -> dict:
    kd = result.get("key_distances") or {}
    align = (result.get("scale") or {}).get("alignment") or {}
    return {
        "harness": HARNESS,
        "observed_kd": kd.get("on_truly_observed_elements"),
        "overall_kd": kd.get("overall"),
        "in_plane": _observed_errors(result, lambda row_id: not _is_height_row(row_id)),
        "heights": _observed_errors(result, _is_height_row),
        "meter_wall_w1": _kd_slice(result, "w1", walls),
        "false_observed_by_rule": _false_observed(result),
        "false_observed_claims": kd.get("false_observed_claims"),
        "cloud_scale_error_pct": align.get("cloud_scale_error_pct"),
        "camera_rms_m": align.get("camera_rms_m"),
    }


def _p90(block: dict | None) -> float | None:
    if not block:
        return None
    value = block.get("p90_in")
    return None if value is None else float(value)


def score_cloud_run(
    run: Path,
    gt: Path,
    truth: Path | None,
    out: Path,
    metric: bool,
    *,
    floor: bool = False,
) -> dict:
    poses = run / "poses"
    cameras = write_cameras_scored(poses / "cameras.json", truth, poses / "cameras_scored.json")
    out.mkdir(parents=True, exist_ok=True)
    walls = _wall_by_element(gt)
    result = patched_score_cloud()(
        gt,
        poses / "sparse.ply",
        cameras,
        out,
        metric,
        producer="ws04",
        align_mode="auto",
    )
    report = json.loads((poses / "registration_report.json").read_text())
    summary = {
        "registered": report.get("frames_registered"),
        "frames_given": report.get("frames_given"),
        "mode": report.get("mode"),
        "status": report.get("status"),
        "warnings": report.get("warnings"),
        "metric": metric,
        "pycolmap": (report.get("params") or {}).get("pycolmap"),
        **_summarize_score(result, walls),
    }
    if floor:
        if truth is None:
            summary["floor"] = {"error": "--floor needs --truth"}
        else:
            summary["floor"] = score_floor(run, gt, truth, out / "floor", metric, walls)
            summary["pose_minus_floor_p90_in"] = _delta(
                _p90(summary.get("observed_kd")), _p90(summary["floor"].get("observed_kd"))
            )
            summary["in_plane_minus_floor_p90_in"] = _delta(
                _p90(summary.get("in_plane")), _p90(summary["floor"].get("in_plane"))
            )
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    return summary


def _delta(pose_p90: float | None, floor_p90: float | None) -> float | None:
    if pose_p90 is None or floor_p90 is None:
        return None
    return round(pose_p90 - floor_p90, 2)


def score_floor(
    run: Path,
    gt: Path,
    truth: Path,
    out: Path,
    metric: bool,
    walls: dict[str, str],
) -> dict:
    """Triangulate the kept database on GT poses and GT-derived intrinsics.

    The run must have been made with ``HSS_POSES_KEEP_DB=1`` so ``database.db``
    and ``_images`` are still on disk. ``_images`` is a scorer input, not a
    published artifact. Intrinsics are the scene keyframe's
    full-frame values cropped into the packet image:
    ``fx·sx, fy·sy, (cx−x0)·sx, (cy−y0)·sy``.
    """
    db_src = run / "poses" / "database.db"
    if not db_src.exists():
        db_src = run / "poses" / "_database.db"
    image_dir = run / "poses" / "_images"
    if not db_src.exists() or not image_dir.exists():
        return {"error": "poses/database.db and poses/_images are required (HSS_POSES_KEEP_DB=1)"}
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    db_path = out / "database.db"
    shutil.copy(db_src, db_path)
    truth_doc = json.loads(truth.read_text())
    scene = json.loads(gt.read_text())
    gkf = {str(frame["id"]): frame for frame in scene.get("keyframes") or []}
    by_packet = {str(image["id"]): image for image in truth_doc.get("images") or []}
    database = pycolmap.Database.open(db_path)
    try:
        images = list(database.read_all_images())
        sizes = {
            image.name: (
                int(database.read_camera(image.camera_id).width),
                int(database.read_camera(image.camera_id).height),
            )
            for image in images
        }
    finally:
        database.close()
    locked: dict[str, tuple[str, list[float]]] = {}
    poses: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    scored_rows = []
    for name, (width, height) in sizes.items():
        meta = by_packet.get(Path(name).stem)
        if meta is None:
            continue
        stem = Path(str(meta.get("source") or "")).stem
        keyframe = gkf.get(stem)
        if keyframe is None:
            continue
        box = meta.get("crop_box") or [0, 0, 0, 0]
        scale = meta.get("scale") or [1.0, 1.0]
        x0, y0 = float(box[0]), float(box[1])
        sx, sy = float(scale[0]), float(scale[1])
        fx, fy, cx, cy = [float(value) for value in keyframe["intrinsics"][:4]]
        intrinsics = [fx * sx, fy * sy, (cx - x0) * sx, (cy - y0) * sy]
        locked[name] = ("PINHOLE", intrinsics)
        pose = mat4_from_list(keyframe["pose"])
        poses[name] = arkit_to_colmap_pose(pose)
        scored_rows.append(
            {
                "image": stem,
                "pose": column_major(pose),
                "intrinsics": intrinsics,
                "w": width,
                "h": height,
                "pose_source": "gt",
            }
        )
    if len(poses) < 3:
        return {"error": f"fewer than 3 GT-matched frames in the database ({len(poses)})"}
    lock_intrinsics(db_path, locked)
    params = PoseParams(gpu=False, num_threads=4, seed=0)
    reconstruction = triangulate_fixed(
        db_path,
        image_dir,
        out / "_tmp",
        poses,
        params,
        image_order=sorted(poses),
    )
    ply_path = out / "sparse.ply"
    xyz, rgb, nviews = [], [], []
    for point in reconstruction.points3D.values():
        xyz.append(np.asarray(point.xyz, float))
        color = np.asarray(getattr(point, "color", [128, 128, 128]), int)
        rgb.append(color[:3])
        nviews.append(len(point.track.elements))
    if xyz:
        write_ply(ply_path, np.stack(xyz), np.stack(rgb), np.asarray(nviews, np.int32))
    else:
        write_ply(ply_path, np.zeros((0, 3)), np.zeros((0, 3), int), np.zeros((0,), np.int32))
    cameras_path = out / "cameras.json"
    cameras_path.write_text(json.dumps(scored_rows, indent=1) + "\n")
    result = patched_score_cloud()(
        gt,
        ply_path,
        cameras_path,
        out / "score",
        metric,
        producer="ws04-floor",
        align_mode="auto",
    )
    summary = _summarize_score(result, walls)
    summary["registered"] = reconstruction.num_reg_images()
    summary["points3D"] = reconstruction.num_points3D()
    return summary


def print_meter_frames(gt_path: Path, truth_path: Path | None) -> str:
    """Two nearest frontal GT views of the meter, as packet keyframe ids."""
    from server.eval.frame import mat4_from_list
    from server.eval.sceneio import load_json
    from server.eval.taps import meter_taps
    from server.eval.visibility import Camera

    gt = load_json(gt_path)
    cameras = [
        Camera(
            kf["id"],
            mat4_from_list(kf["pose"]),
            np.asarray(kf["intrinsics"], float),
            int(kf["w"]),
            int(kf["h"]),
        )
        for kf in gt["keyframes"]
    ]
    taps = meter_taps(gt, cameras, {i: cam.w for i, cam in enumerate(cameras)}, n=2)
    stems = {cam_id: packet_id for packet_id, cam_id in (_stem_map(truth_path).items() if truth_path else [])}
    ids = []
    for index, _u, _v in taps:
        stem = cameras[index].id
        ids.append(stems.get(stem, stem))
    return ",".join(ids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score a poses/ directory")
    parser.add_argument("--run", type=Path, default=None)
    parser.add_argument("--gt-centers", type=Path, default=None)
    parser.add_argument("--gt", type=Path, default=None, help="Laser scene.json")
    parser.add_argument("--truth", type=Path, default=None, help="Packet _truth/truth.json stem map")
    parser.add_argument("--metric", action="store_true")
    parser.add_argument(
        "--floor", action="store_true", help="Triangulate kept DB on GT poses and report pose p90 minus floor p90"
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--print-meter-frames", action="store_true")
    args = parser.parse_args(argv)
    if args.print_meter_frames:
        if args.gt is None:
            print("print-meter-frames needs --gt", file=sys.stderr)
            return 2
        print(print_meter_frames(args.gt, args.truth))
        return 0
    if args.run is None:
        print("--run is required", file=sys.stderr)
        return 2
    if args.gt is not None:
        out = args.out or (args.run / "score")
        summary = score_cloud_run(args.run, args.gt, args.truth, out, args.metric, floor=args.floor)
        print(json.dumps(summary, indent=1))
        return 0

    report_path = args.run / "poses" / "registration_report.json"
    cameras_path = args.run / "poses" / "cameras.json"
    report = json.loads(report_path.read_text())
    summary = {
        "registered": report["frames_registered"],
        "frames_given": report["frames_given"],
        "mode": report["mode"],
        "status": report["status"],
        "arkit_fit": report.get("arkit_fit"),
        "warnings": report.get("warnings"),
        "pycolmap": report.get("params", {}).get("pycolmap"),
    }
    if args.gt_centers is not None:
        gt = _load_centers(args.gt_centers)
        rows = json.loads(cameras_path.read_text())
        src, dst = [], []
        for row in rows:
            if row.get("pose_source") == "arkit":
                continue
            key = row["image"]
            if key not in gt and f"{key}.jpg" in gt:
                key = f"{key}.jpg"
            if key not in gt:
                continue
            src.append(from_column_major(row["pose"])[:3, 3])
            dst.append(gt[key])
        if len(src) >= 3:
            src_a, dst_a = np.stack(src), np.stack(dst)
            scale, _, _ = umeyama(src_a, dst_a, with_scale=True)
            _, rotation, translation = umeyama(src_a, dst_a, with_scale=False)
            err = (src_a @ rotation.T) + translation - dst_a
            rms = float(np.sqrt((err**2).sum(1).mean()))
            summary["gt"] = {
                "n": len(src),
                "scale_pct": float((1.0 / scale - 1.0) * 100.0) if scale else None,
                "rigid_rms_m": rms,
            }
        else:
            summary["gt"] = {"n": len(src), "error": "fewer than 3 shared centres"}
    text = json.dumps(summary, indent=1)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
