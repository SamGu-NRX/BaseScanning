"""Shared paths and small helpers for the A1 classical-reconstruction breadth run."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

STORE = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2")
CODE = STORE / "internal/breadth/a1-classical"
WORK = Path(os.environ.get("A1_WORK", Path.home() / "a1/work"))
ETH3D = Path(os.environ.get("A1_ETH3D", Path.home() / "a1/eth3d"))
OPENMVS_BIN = Path(os.environ.get("A1_OPENMVS", Path.home() / "a1/openmvs_install/bin/OpenMVS"))
ETH3D_EVAL = Path(os.environ.get("A1_ETH3D_EVAL", Path.home() / "a1/multi-view-evaluation/build/ETH3DMultiViewEvaluation"))
PACKETGEN = STORE / "internal/packet-generator"

M_TO_IN = 39.3700787
M_TO_FT = 3.2808399

if str(PACKETGEN) not in sys.path:
    sys.path.insert(0, str(PACKETGEN))


def qvec_to_rotmat(q) -> np.ndarray:
    w, x, y, z = np.asarray(q, float) / np.linalg.norm(q)
    return np.array(
        [
            [1 - 2 * y * y - 2 * z * z, 2 * x * y - 2 * w * z, 2 * x * z + 2 * w * y],
            [2 * x * y + 2 * w * z, 1 - 2 * x * x - 2 * z * z, 2 * y * z - 2 * w * x],
            [2 * x * z - 2 * w * y, 2 * y * z + 2 * w * x, 1 - 2 * x * x - 2 * y * y],
        ]
    )


def rotmat_to_qvec(R: np.ndarray) -> np.ndarray:
    """Rotation matrix to COLMAP (w, x, y, z)."""
    m = R
    tr = np.trace(m)
    if tr > 0:
        s = np.sqrt(tr + 1.0) * 2
        q = [0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [(m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s]
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s]
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s]
    q = np.array(q)
    return q if q[0] >= 0 else -q


def read_text_model(model_dir: Path) -> tuple[dict, dict]:
    """Minimal COLMAP text reader: cameras {id: (model, w, h, params)}, images {name: dict}."""
    cams = {}
    for ln in (model_dir / "cameras.txt").read_text().splitlines():
        if ln.startswith("#") or not ln.strip():
            continue
        p = ln.split()
        cams[int(p[0])] = (p[1], int(p[2]), int(p[3]), np.array(p[4:], float))
    images = {}
    lines = [ln for ln in (model_dir / "images.txt").read_text().splitlines() if not ln.startswith("#")]
    i = 0
    while i < len(lines):
        head = lines[i]
        if not head.strip():
            i += 1
            continue
        p = head.split()
        q = np.array(p[1:5], float)
        t = np.array(p[5:8], float)
        R = qvec_to_rotmat(q)
        images[Path(p[9]).name] = {
            "image_id": int(p[0]),
            "camera_id": int(p[8]),
            "R_cw": R,
            "t_cw": t,
            "center": -R.T @ t,
        }
        i += 2
    return cams, images


def write_text_model(out_dir: Path, cams: dict, images: dict) -> None:
    """Write a COLMAP text model without 2D/3D points. images: name -> dict(image_id, camera_id, R_cw, t_cw)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "cameras.txt", "w") as f:
        f.write("# CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n")
        for cid, (model, w, h, params) in sorted(cams.items()):
            f.write(f"{cid} {model} {w} {h} " + " ".join(f"{v:.10g}" for v in params) + "\n")
    with open(out_dir / "images.txt", "w") as f:
        f.write("# IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME\n")
        for name, im in sorted(images.items(), key=lambda kv: kv[1]["image_id"]):
            q = rotmat_to_qvec(im["R_cw"])
            t = im["t_cw"]
            f.write(
                f"{im['image_id']} " + " ".join(f"{v:.12g}" for v in q) + " " + " ".join(f"{v:.12g}" for v in t)
                + f" {im['camera_id']} {name}\n\n"
            )
    (out_dir / "points3D.txt").write_text("# POINT3D_ID, X, Y, Z, R, G, B, ERROR, TRACK[]\n")


def umeyama(src: np.ndarray, dst: np.ndarray, with_scale: bool = True) -> tuple[float, np.ndarray, np.ndarray]:
    """s, R, t minimising |s R src + t - dst|^2."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    a, b = src - mu_s, dst - mu_d
    U, S, Vt = np.linalg.svd(b.T @ a / len(src))
    D = np.eye(3)
    if np.linalg.det(U @ Vt) < 0:
        D[2, 2] = -1
    R = U @ D @ Vt
    s = float(np.trace(np.diag(S) @ D) / a.var(0).sum()) if with_scale else 1.0
    t = mu_d - s * R @ mu_s
    return s, R, t


def dump_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def default(o):
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        raise TypeError(type(o))

    path.write_text(json.dumps(obj, indent=2, default=default))
