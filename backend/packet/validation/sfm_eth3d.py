"""Run the SfM route on ETH3D facade's prepared keyframes and score it against ground truth."""

import json
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/packet-generator")
from packetgen.conventions import from_column_major  # noqa: E402
from packetgen.sources import from_colmap_model, run_sfm, sim3_align  # noqa: E402

src = Path("/tmp/pgtest/eth3d-facade")
work = Path("/home/ubuntu/work/packetgen-work/eth3d-facade-sfm")
imgs = work / "images"
if imgs.exists():
    shutil.rmtree(imgs)
imgs.mkdir(parents=True)
for p in sorted((src / "keyframes").glob("k*.jpg")):
    shutil.copy2(p, imgs / p.name)
model = run_sfm(imgs, work, sequential=False)
rec = from_colmap_model(model, imgs, metric=False, source="pycolmap_sfm")
truth = json.loads((src / "_truth" / "truth.json").read_text())
frames = {f["id"]: f for f in truth["variants"]["arkit-lidar"]["frames"]}
common = [v for v in rec.views if v.name[:-4] in frames]
A = np.array([v.T_cv[:3, 3] for v in common])
B = np.array([from_column_major(frames[v.name[:-4]]["pose_true"])[:3, 3] for v in common])
s, R, t = sim3_align(A, B)
pos = np.linalg.norm((s * (R @ A.T)).T + t - B, axis=1)
rot = []
for v in common:
    Rt = from_column_major(frames[v.name[:-4]]["pose_true"])[:3, :3] @ np.diag([1, -1, -1])
    Re = R @ v.T_cv[:3, :3]
    rot.append(np.degrees(np.arccos(np.clip((np.trace(Rt.T @ Re) - 1) / 2, -1, 1))))
fx_true = np.array([frames[v.name[:-4]]["intrinsics_true"][0] for v in common])
fx_sfm = np.array([v.K[0] for v in common])
out = {
    "registered": len(rec.views), "frames": len(frames), "points": int(len(rec.points)),
    "sim3_scale_to_metric": s,
    "position_error_m": {"median": float(np.median(pos)), "p90": float(np.percentile(pos, 90)), "max": float(pos.max())},
    "rotation_error_deg": {"median": float(np.median(rot)), "p90": float(np.percentile(rot, 90))},
    "focal_rel_error": {"median": float(np.median(fx_sfm / fx_true - 1)), "range": [float((fx_sfm / fx_true - 1).min()), float((fx_sfm / fx_true - 1).max())]},
    "model": str(model),
}
print(json.dumps(out, indent=1))
(work / "sfm_vs_truth.json").write_text(json.dumps(out, indent=1))
