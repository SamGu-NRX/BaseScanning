"""Command line for the object stage. Cached scores and answers reproduce the acceptance without API keys."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

from server.objects.config import Cluster, FrameRef, ObjectsConfig
from server.objects.depth import read_depth
from server.objects.emit import to_objects, validate_object
from server.objects.lift import (
    FACADE_LASER_MAX_M,
    alignment_to_source,
    lift_depth,
    lift_triangulate,
    map_cluster_positions,
    pose_matrix,
    scan_wall_segments,
    score_openings,
)
from server.objects.prompts import GATE_EQUIP
from server.objects.proposals import run_gdino
from server.objects.run import _clusters_from_2d, run_2d
from server.objects.select import select_house

# Eval-only drift profiles live with the packet generator. Production stages do not import it.
_PACKET_ROOT = Path(__file__).resolve().parents[2] / "packet"
if _PACKET_ROOT.is_dir() and str(_PACKET_ROOT) not in sys.path:
    sys.path.append(str(_PACKET_ROOT))
from packetgen.noise import apply_drift, load_profiles  # noqa: E402

# SD-43. Callers pass this path. The stage never globs packet.*.json.
MINI_ARKIT_PACKET = Path("data/fixtures/packets/mini-arkit/packet.json")


def offline_zero_scores(frames: list[FrameRef]) -> dict:
    """Scores for every keyframe and still when no cache file is beside the packet.

    WS-08 stills stay in `frame_scores.json` so the file covers the packet, and
    `select_frames` keeps them out of the VLM set by purpose.
    """
    scores: dict = {"clip": {}, "siglip": {}}
    for frame in frames:
        row = {c: 0.0 for c in GATE_EQUIP}
        scores["clip"][frame.id] = row
        scores["siglip"][frame.id] = dict(row)
    return scores


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="server.objects.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    gate = sub.add_parser("gate-eval")
    gate.add_argument("--images", type=Path, required=True)
    gate.add_argument("--gt", type=Path, required=True)
    gate.add_argument("--out", type=Path, required=True)
    gate.add_argument("--scores", type=Path, default=None)
    casc = sub.add_parser("cascade-eval")
    casc.add_argument("--presence-cache", type=Path, required=True)
    casc.add_argument("--scores", type=Path, required=True)
    casc.add_argument("--out", type=Path, required=True)
    casc.add_argument("--images", type=Path, default=None)
    casc.add_argument("--gt", type=Path, default=None)
    lift = sub.add_parser("lift-eval")
    lift.add_argument("--packet", type=Path, required=True)
    lift.add_argument("--scene", type=Path, required=True)
    lift.add_argument("--gt", type=Path, required=True)
    lift.add_argument("--detector", default="gdino-tiny")
    lift.add_argument("--thr", type=float, default=0.35)
    lift.add_argument("--out", type=Path, required=True)
    lift.add_argument("--noise", default="")
    lift.add_argument("--seeds", type=int, default=5)
    lift.add_argument("--dets", type=Path, default=None)
    run_p = sub.add_parser("run")
    run_p.add_argument("--packet", type=Path, required=True)
    run_p.add_argument("--scene", type=Path, default=None)
    run_p.add_argument("--cameras", type=Path, default=None)
    run_p.add_argument("--out", type=Path, required=True)
    run_p.add_argument("--stage", default="both", choices=["objects", "objects_3d", "both"])
    run_p.add_argument("--no-external", action="store_true")
    run_p.add_argument("--tier-override", default="")
    check = sub.add_parser("check")
    check.add_argument("objects_3d", type=Path)
    check.add_argument("--scene", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.cmd == "gate-eval":
        gate_eval(args)
    elif args.cmd == "cascade-eval":
        cascade_eval(args)
    elif args.cmd == "lift-eval":
        lift_eval(args)
    elif args.cmd == "run":
        run_cmd(args)
    elif args.cmd == "check":
        check_cmd(args)


def gate_eval(args) -> None:
    images = json.loads(args.images.read_text())
    gt = json.loads(args.gt.read_text())["frames"]
    scores_path = args.scores or args.images.parent / "gate_scores_all.json"
    if not scores_path.is_file():
        raise SystemExit(f"inputs_missing:scores ({scores_path})")
    scores = json.loads(scores_path.read_text())
    # Images are optional: the committed class scores are the cached model output.
    report = {}
    for name, margin in (("clip", 0.8), ("siglip", 0.5)):
        if name not in scores:
            continue
        report[name] = cross_fit(scores[name], gt, images, margin)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "gate_scores.json").write_text(json.dumps(scores))
    (args.out / "gate_eval.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


def cross_fit(scores: dict, gt: dict, images: list, margin: float) -> dict:
    """B3 gate_eval.evaluate. Thresholds are cross-fitted; they are not used to filter frames."""
    must = ["meter", "panel", "gas_meter", "ac_condenser", "pool"]
    lab = sorted(i for i in scores if gt.get(i, {}).get("labelled"))
    folds = [lab[0::2], lab[1::2]]

    def thresholds(pool):
        th = {}
        for cls in must:
            pos = [scores[i][cls] for i in pool if cls in gt[i].get("classes", [])]
            th[cls] = min(pos) * margin if pos else float("inf")
        return th

    def keep(i, th):
        return any(scores[i][cls] >= th[cls] for cls in must)

    kept_pos = tot_pos = 0
    ths = []
    for k in (0, 1):
        th = thresholds(folds[1 - k])
        ths.append(th)
        for i in folds[k]:
            if gt[i].get("must_keep"):
                tot_pos += 1
                kept_pos += keep(i, th)
    walk = [r["id"] for r in images if r.get("set") == "walk" and r["id"] in scores]
    th_avg = {c: float(np.mean([t[c] for t in ths])) for c in must}
    return {
        "recall": f"{kept_pos}/{tot_pos}",
        "recall_frac": kept_pos / max(1, tot_pos),
        "thresholds": th_avg,
        "walk_frames": len(walk),
    }


def cascade_eval(args) -> None:
    images_path = args.images or args.scores.parent.parent / "images.json"
    if not images_path.is_file():
        # --presence-cache data/fixtures/objects/b3/raw/presence → images.json is in b3/, one directory up from raw/.
        images_path = args.presence_cache.parents[1] / "images.json"
    gt_path = args.gt or (images_path.parent / "gt.json")
    images = json.loads(Path(images_path).read_text())
    gt = json.loads(Path(gt_path).read_text())
    scores = json.loads(args.scores.read_text())
    mini = _load_jsonl(args.presence_cache / "gpt-5.4-mini.jsonl")
    big = _load_jsonl(args.presence_cache / "gpt-5.5.jsonl")
    walk = {}
    for row in images:
        if row.get("set") == "walk" and row["id"] in mini and row["id"] in big:
            walk.setdefault(row.get("house") or row["id"].split("/")[0], []).append(row["id"])
    houses = gt["houses"]
    summary = {}
    for strict in (True, False):
        summary["strict" if strict else "lenient"] = _select_cascade(walk, scores, mini, big, houses, strict=strict)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "cascade.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


def _select_cascade(walk, scores, mini, big, houses, *, strict: bool) -> dict:
    selected = {}
    hazards = {}
    for house, frames in walk.items():
        union, haz = set(), set()
        for model in ("clip", "siglip"):
            got, hz = select_house(frames, scores[model], k=2, min_gap=2)
            union.update(got)
            haz.update(hz)
        selected[house] = union
        hazards[house] = haz
    equip = {
        "meter": "electric_meter",
        "panel": "main_panel",
        "gas_meter": "gas_meter",
        "ac_condenser": "ac_condenser",
        "pool": "pool",
    }
    found = {}
    n_sel = n_conf = n_safe = 0
    n_frames = sum(len(v) for v in walk.values())
    for house, frames in selected.items():
        n_sel += len(frames)
        hz = hazards[house]
        asked = set()
        for fid in frames:
            if any(mini[fid]["present"].get(c) in {"yes", "unsure"} for c in equip.values()):
                asked.add(fid)
        n_conf += len(asked)
        n_safe += len(asked | hz)
        for cls in equip.values():
            found[(house, cls)] = any(big[i]["present"].get(cls) == "yes" for i in (asked | hz))
    tp = fn = fp = tn = 0
    for house, visible in houses.items():
        if house not in selected:
            continue
        for gate_cls, cls in equip.items():
            state = visible[gate_cls]["state"]
            if state == "unknown" or (strict and state == "partial"):
                continue
            truth = state in {"yes", "partial"}
            got = found.get((house, cls), False)
            tp += truth and got
            fn += truth and not got
            fp += got and not truth
            tn += (not truth) and (not got)
    c_mini = 0.0011
    c_big = 0.0167
    planned = (n_sel * c_mini + n_safe * c_big) / max(1, len(selected))
    return {
        "house_recall": f"{tp}/{tp + fn}",
        "false_present": f"{fp}/{fp + tn}",
        "frames_sent": f"{n_sel}/{n_frames}",
        "filtered_frac": 1 - n_sel / max(1, n_frames),
        "planned_usd_per_house": planned,
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "tn": tn,
    }


def lift_eval(args) -> None:
    scene = json.loads(args.scene.read_text())
    gt = json.loads(args.gt.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    if not args.packet.is_file() and args.dets is None:
        report = {"status": "inputs_missing:packet", "packet": str(args.packet), "recovered": None}
        (args.out / "lift.json").write_text(json.dumps(report, indent=1))
        print(json.dumps(report))
        raise SystemExit(2)
    if not args.packet.is_file():
        report = {"status": "inputs_missing:packet", "packet": str(args.packet), "recovered": None}
        (args.out / "lift.json").write_text(json.dumps(report, indent=1))
        print(json.dumps(report))
        raise SystemExit(2)
    packet = json.loads(args.packet.read_text())
    root = args.packet.parent
    truth_path = root / "_truth" / "truth.json"
    if not truth_path.is_file():
        report = {"status": "inputs_missing:truth", "truth": str(truth_path), "recovered": None}
        (args.out / "lift.json").write_text(json.dumps(report, indent=1))
        print(json.dumps(report))
        raise SystemExit(2)
    truth = json.loads(truth_path.read_text())
    dets = _packet_dets(args, packet, root)
    _write_opening_dets(args.out / "dets.jsonl", dets)
    if args.noise:
        report = _noise_evidence(packet, truth, dets, scene, args.noise, args.seeds, args.detector, args.thr)
    else:
        report = _score_clean_depth(packet, truth, dets, scene, gt, root, args.detector, args.thr)
    (args.out / "lift.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report))


def _packet_dets(args, packet: dict, root: Path) -> dict:
    if args.dets is not None:
        return _load_opening_dets(args.dets, args.thr)
    if args.detector != "gdino-tiny":
        raise SystemExit(f"unsupported detector {args.detector}")
    frames = []
    for kf in packet.get("keyframes") or []:
        img = root / kf["img"] if not Path(kf["img"]).is_absolute() else Path(kf["img"])
        frames.append(FrameRef(kf["id"], img, int(kf.get("w") or 0), int(kf.get("h") or 0), "keyframe"))
    raw = run_gdino(frames, thr=args.thr)
    return {
        fid: [{"label": det.label, "score": det.score, "box": det.box} for det in rows] for fid, rows in raw.items()
    }


def _write_opening_dets(path: Path, dets: dict) -> None:
    lines = []
    for fid, rows in dets.items():
        lines.append(json.dumps({"id": fid, "dets": rows}))
    path.write_text("\n".join(lines) + ("\n" if lines else ""))


def _load_opening_dets(path: Path, thr: float) -> dict:
    out: dict[str, list] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "error" in row:
            continue
        out[row["id"]] = [
            det
            for det in row.get("dets") or []
            if det.get("label") in {"window", "door"} and det.get("score", 0) >= thr
        ]
    return out


def _clean_capture(packet: dict, truth: dict, root: Path) -> tuple[dict, dict]:
    """Clean ARKit poses the laser depth was rendered from. They register to the GT scene cameras."""
    variant = truth["variants"]["arkit-lidar"]["frames"]
    by_id = {frame["id"]: frame for frame in variant}
    cams: dict = {}
    depths: dict = {}
    for kf in packet.get("keyframes") or []:
        src = by_id.get(kf["id"])
        if src is None:
            continue
        cams[kf["id"]] = {
            "pose": src["pose_true"],
            "intrinsics": src["intrinsics_true"],
            "w": int(kf.get("w") or 0),
            "h": int(kf.get("h") or 0),
        }
        spec = kf.get("depth") or {}
        if spec.get("file") and spec.get("confidenceFile"):
            depths[kf["id"]] = read_depth(
                {
                    "file": str(root / spec["file"]),
                    "confidence": str(root / spec["confidenceFile"]),
                    "w": int(spec["w"]),
                    "h": int(spec["h"]),
                }
            )
    return cams, depths


def _score_clean_depth(packet, truth, dets, scene, gt, root, detector: str, thr: float) -> dict:
    cams, depths = _clean_capture(packet, truth, root)
    # The confidence mask is the phone LiDAR model and drops the facade. The depth channel is the laser.
    clusters = lift_depth(dets, cams, depths, min_confidence=0, max_m=FACADE_LASER_MAX_M)
    clusters = map_cluster_positions(clusters, lambda point: alignment_to_source(point, truth["alignment"]))
    walls = scan_wall_segments(scene)
    scored = score_openings(clusters, gt, walls)
    return {
        "status": "scored",
        "poses": "clean_truth",
        "detector": detector,
        "thr": thr,
        "recovered": f"{scored['recovered']}/{scored['n_gt']}",
        "recovered_n": scored["recovered"],
        "n_gt": scored["n_gt"],
        "false_3d": scored["false_3d"],
        "centre_err_m_median": scored["centre_err_m_median"],
        "found": scored["found"],
        "n_clusters": len(clusters),
        "match_m": 0.8,
    }


def _noise_evidence(packet, truth, dets, scene, profile_name: str, seeds: int, detector: str, thr: float) -> dict:
    """Drifted non-lidar poses. Depth is not an observation, so nothing is `observed`."""
    profiles = load_profiles()
    if profile_name not in profiles:
        raise SystemExit(f"unknown noise profile {profile_name}")
    profile = profiles[profile_name]
    by_id = {frame["id"]: frame for frame in truth["variants"]["arkit-lidar"]["frames"]}
    kf = {row["id"]: row for row in packet.get("keyframes") or []}
    ids = [fid for fid in kf if fid in by_id]
    poses = [pose_matrix(by_id[fid]["pose_true"]) for fid in ids]
    rotations = np.stack([pose[:3, :3] for pose in poses])
    centres = np.stack([pose[:3, 3] for pose in poses])
    classes = sorted({det["label"] for rows in dets.values() for det in rows})
    runs = []
    for seed in range(seeds):
        rng = np.random.default_rng(1000 + seed)
        rot_obs, centre_obs, _, _ = apply_drift(rotations, centres, profile, rng, reset_index=None)
        cams = {}
        for index, fid in enumerate(ids):
            transform = np.eye(4)
            transform[:3, :3] = rot_obs[index]
            transform[:3, 3] = centre_obs[index]
            row = kf[fid]
            cams[fid] = {
                "pose": transform,
                "intrinsics": by_id[fid]["intrinsics_true"],
                "w": int(row.get("w") or 0),
                "h": int(row.get("h") or 0),
            }
        placed = lift_triangulate(dets, cams)
        if not placed:
            placed = [
                Cluster(cls, [0.0, 0.0, 0.0], [{"frame": ids[0], "score": 0.0}], 0.0, "location_unsure")
                for cls in classes
            ]
        objects, _requests = to_objects(placed, {}, "arkit", scene, ObjectsConfig(external_vision=False))
        observed = sum(1 for obj in objects if obj["evidence"] == "observed")
        unsure = sum(1 for obj in objects if obj["plus_minus_m"] >= 10)
        inferred = sum(1 for obj in objects if obj["evidence"] == "inferred" and obj["plus_minus_m"] < 10)
        runs.append(
            {
                "seed": seed,
                "observed": observed,
                "inferred": inferred,
                "location_unsure": unsure,
                "objects": len(objects),
            }
        )
    return {
        "status": "noise",
        "noise": profile_name,
        "detector": detector,
        "thr": thr,
        "seeds": runs,
        "observed": sum(run["observed"] for run in runs),
    }


def run_cmd(args) -> None:
    if args.no_external:
        os.environ["VISION_EXTERNAL_CALLS"] = "off"
    cfg = ObjectsConfig.from_env()
    packet = json.loads(args.packet.read_text())
    root = args.packet.parent
    frames = []
    for kf in packet.get("keyframes") or []:
        img = root / kf["img"]
        frames.append(FrameRef(kf["id"], img, int(kf.get("w") or 1), int(kf.get("h") or 1), "keyframe"))
    for still in packet.get("stills") or []:
        img = root / still["img"]
        frames.append(
            FrameRef(
                still["id"],
                img,
                int(still.get("w") or 1),
                int(still.get("h") or 1),
                "still",
                purpose=still.get("purpose"),
            )
        )
    # Offline path: a scores file next to the packet, else empty scores (still routing still records every frame).
    scores_path = args.packet.parent / "frame_scores.json"
    if scores_path.is_file():
        raw = json.loads(scores_path.read_text())
        scores = {}
        for fid, models in raw.items():
            for model, row in models.items():
                scores.setdefault(model, {})[fid] = row
    else:
        scores = offline_zero_scores(frames)
    if args.stage in {"objects", "both"}:
        run_2d(frames, packet, args.out, cfg, vlm_budget_usd=None, scores=scores, detections=None)
    if args.stage in {"objects_3d", "both"}:
        if args.scene is None:
            raise SystemExit("inputs_missing:scene")
        scene = json.loads(args.scene.read_text())
        doc = (
            json.loads((args.out / "objects.json").read_text())
            if (args.out / "objects.json").is_file()
            else {"objects": []}
        )
        tier = args.tier_override or (packet.get("session") or {}).get("tier") or "arkit"
        clusters = _clusters_from_2d(doc, scene, tier)
        objects, requests = to_objects(clusters, {}, tier, scene, cfg)
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "objects_3d.json").write_text(
            json.dumps({"format": "hss-objects-3d/v0", "objects": objects}, indent=1)
        )
        (args.out / "view_requests.json").write_text(json.dumps(requests, indent=1))


def check_cmd(args) -> None:
    doc = json.loads(args.objects_3d.read_text())
    scene = json.loads(args.scene.read_text())
    ids = [obj.get("id") for obj in scene.get("objects") or []]
    for obj in doc["objects"]:
        validate_object(obj)
        if obj["id"] == "meter":
            raise SystemExit("object id meter is owned by the scene anchor")
        if obj["id"] in ids:
            raise SystemExit(f"id collision {obj['id']}")
        ids.append(obj["id"])
        for view in obj["source"]["views"]:
            if "box_px" in view:
                pass
    print(json.dumps({"ok": True, "objects": len(doc["objects"])}))


def _load_jsonl(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "error" not in row:
            out[row["id"]] = row
    return out


if __name__ == "__main__":
    main()
