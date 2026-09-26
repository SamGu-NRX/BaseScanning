"""Load clean camera poses: a COLMAP model (ETH3D ground truth or any COLMAP output), or run
pycolmap structure-from-motion on a folder of frames."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pycolmap

from .conventions import cam_to_world_cv, qvec_to_rotmat

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG", ".heic", ".HEIC"}


@dataclass
class View:
    name: str
    image_path: Path
    width: int
    height: int
    K: tuple[float, float, float, float]  # continuous pixels of the source image
    T_cv: np.ndarray  # 4x4 OpenCV camera-to-world in the source world
    point_ids: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    point_uv: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))


@dataclass
class Recon:
    views: list[View]  # capture order
    point_ids: np.ndarray
    points: np.ndarray  # (M, 3) source world
    colors: np.ndarray  # (M, 3) uint8
    metric: bool
    source: str
    up_hint: np.ndarray | None = None
    dropped: list[str] = field(default_factory=list)

    def point_lookup(self) -> dict[int, int]:
        return {int(p): i for i, p in enumerate(self.point_ids)}


def list_images(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix in IMAGE_EXTS and p.is_file())


def pinhole_K(camera: pycolmap.Camera) -> tuple[float, float, float, float]:
    """Pinhole [fx, fy, cx, cy] of a COLMAP camera (continuous pixels, COLMAP's convention).
    Distortion terms of non-pinhole models are dropped; run on undistorted images for exact K."""
    return (
        float(camera.focal_length_x),
        float(camera.focal_length_y),
        float(camera.principal_point_x),
        float(camera.principal_point_y),
    )


def from_colmap_model(model_dir: Path, image_root: Path, metric: bool, source: str,
                      up_hint: np.ndarray | None = None, order: str = "name") -> Recon:
    rec = pycolmap.Reconstruction(str(model_dir))
    views = []
    for image_id, img in rec.images.items():
        if not img.has_pose:
            continue
        cam = rec.cameras[img.camera_id]
        cfw = img.cam_from_world() if callable(img.cam_from_world) else img.cam_from_world
        R_cw = np.asarray(cfw.rotation.matrix())
        t_cw = np.asarray(cfw.translation)
        pts = [(p.point3D_id, p.xy) for p in img.points2D if p.has_point3D()]
        ids = np.array([p[0] for p in pts], np.int64)
        uv = np.array([p[1] for p in pts], float).reshape(-1, 2)
        views.append(
            View(
                name=Path(img.name).name,
                image_path=image_root / img.name,
                width=int(cam.width),
                height=int(cam.height),
                K=pinhole_K(cam),
                T_cv=cam_to_world_cv(R_cw, t_cw),
                point_ids=ids,
                point_uv=uv,
            )
        )
    views.sort(key=lambda v: v.name)
    pids = np.array(sorted(rec.points3D.keys()), np.int64)
    pts = np.array([rec.points3D[int(i)].xyz for i in pids], float).reshape(-1, 3)
    cols = np.array([rec.points3D[int(i)].color for i in pids], np.uint8).reshape(-1, 3)
    return Recon(views, pids, pts, cols, metric, source, up_hint)


def run_sfm(image_dir: Path, work_dir: Path, max_image_size: int = 1600, sequential: bool | None = None,
            num_threads: int = 4) -> Path:
    """pycolmap SIFT + matching + incremental mapping. Returns the largest model's folder.

    Sequential matching (with loop detection off) for video-like frame sequences, exhaustive for
    small unordered sets."""
    images = list_images(image_dir)
    if sequential is None:
        sequential = len(images) > 80
    work_dir.mkdir(parents=True, exist_ok=True)
    db = work_dir / "database.db"
    if db.exists():
        db.unlink()
    extraction = pycolmap.FeatureExtractionOptions()
    extraction.max_image_size = max_image_size
    extraction.num_threads = num_threads
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "SIMPLE_RADIAL"
    pycolmap.extract_features(
        str(db), str(image_dir),
        image_names=[str(p.relative_to(image_dir)) for p in images],
        camera_mode=pycolmap.CameraMode.SINGLE,
        reader_options=reader,
        extraction_options=extraction,
    )
    if sequential:
        opts = pycolmap.SequentialPairingOptions()
        opts.overlap = 12
        opts.quadratic_overlap = True
        pycolmap.match_sequential(str(db), pairing_options=opts)
    else:
        pycolmap.match_exhaustive(str(db))
    out = work_dir / "sparse"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    maps = pycolmap.incremental_mapping(str(db), str(image_dir), str(out))
    if not maps:
        raise RuntimeError(f"SfM registered no model for {image_dir}")
    best = max(maps.items(), key=lambda kv: kv[1].num_reg_images())
    return out / str(best[0])


def undistort_model(model_dir: Path, image_dir: Path, out_dir: Path) -> Path:
    """Undistort images to PINHOLE so ARKit's pinhole intrinsics describe them exactly."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    pycolmap.undistort_images(str(out_dir), str(model_dir), str(image_dir), output_type="COLMAP")
    return out_dir


def sim3_align(src: np.ndarray, dst: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """Umeyama: s, R, t minimising |s R src + t - dst|."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    a, b = src - mu_s, dst - mu_d
    U, S, Vt = np.linalg.svd(b.T @ a / len(src))
    D = np.eye(3)
    if np.linalg.det(U @ Vt) < 0:
        D[2, 2] = -1
    R = U @ D @ Vt
    s = np.trace(np.diag(S) @ D) / a.var(0).sum()
    t = mu_d - s * R @ mu_s
    return float(s), R, t


def compare_to_reference(est: Recon, ref: Recon) -> dict:
    """Camera-centre and rotation error of an SfM run against a reference, after Sim(3)."""
    ref_by = {v.name: v for v in ref.views}
    common = [v for v in est.views if v.name in ref_by]
    if len(common) < 3:
        return {"common": len(common)}
    A = np.array([v.T_cv[:3, 3] for v in common])
    B = np.array([ref_by[v.name].T_cv[:3, 3] for v in common])
    s, R, t = sim3_align(A, B)
    pos_err = np.linalg.norm((s * (R @ A.T)).T + t - B, axis=1)
    rot_err = []
    for v in common:
        Rr = ref_by[v.name].T_cv[:3, :3]
        Re = R @ v.T_cv[:3, :3]
        rot_err.append(np.degrees(np.arccos(np.clip((np.trace(Rr.T @ Re) - 1) / 2, -1, 1))))
    return {
        "common": len(common),
        "reference_views": len(ref.views),
        "sim3_scale": s,
        "position_error_m": {"median": float(np.median(pos_err)), "p90": float(np.percentile(pos_err, 90)), "max": float(pos_err.max())},
        "rotation_error_deg": {"median": float(np.median(rot_err)), "p90": float(np.percentile(rot_err, 90))},
    }


__all__ = ["View", "Recon", "from_colmap_model", "run_sfm", "undistort_model", "compare_to_reference", "qvec_to_rotmat", "list_images"]
