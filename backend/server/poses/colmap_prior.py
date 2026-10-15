# vendored from internal/modal-pipeline/housepipe/colmap_prior.py (store, 2026-09-26)
"""GPU SIFT + spatial matching + ARKit position-prior mapping.

Feature extraction and matching go through the CUDA COLMAP binary.
Intrinsics stay fixed and each epoch is one reconstruction. The position
prior defaults to the real-phone recipe from A6: sigma 0.03 m and no robust
loss. Sigma stays a caller argument. The ETH3D recipe (0.3 m, robust loss)
bent on real walks, so robust loss is off unless the caller asks for it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np

from server.common.geometry import arkit_center

COLMAP = os.environ.get("COLMAP_BIN", "colmap")


def _run(args: list[str]) -> str:
    proc = subprocess.run(args, check=False, text=True, capture_output=True)
    log = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        tail = log[-4000:]
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(args[:6])}\n{tail}")
    return log


def _help(command: str) -> str:
    proc = subprocess.run([COLMAP, command, "-h"], check=False, text=True, capture_output=True)
    return (proc.stdout or "") + (proc.stderr or "")


def _flag(help_text: str, candidates: list[str]) -> str | None:
    for name in candidates:
        if name in help_text:
            return name
    return None


def spatial_pairs(
    names: list[str],
    centers: np.ndarray,
    *,
    max_neighbors: int = 20,
    radius_m: float = 12.0,
    sequential: int = 2,
) -> list[tuple[str, str]]:
    """Pairs for spatial matching: sequential overlap plus nearest cameras."""
    n = len(names)
    pairs: set[tuple[str, str]] = set()
    if n < 2:
        return []
    if centers is None or not np.isfinite(centers).all():
        # Photo-only: connect a small window so exhaustive matching stays bounded.
        for i in range(n):
            for j in range(i + 1, min(n, i + 1 + max(sequential, 3))):
                pairs.add((names[i], names[j]))
        return sorted(pairs)
    for i in range(n):
        for j in range(i + 1, min(n, i + 1 + sequential)):
            pairs.add((names[i], names[j]))
        dist = np.linalg.norm(centers - centers[i], axis=1)
        order = np.argsort(dist)
        kept = 0
        for j in order:
            if int(j) == i:
                continue
            if dist[j] > radius_m and kept >= 4:
                continue
            a, b = (names[i], names[int(j)]) if i < int(j) else (names[int(j)], names[i])
            pairs.add((a, b))
            kept += 1
            if kept >= max_neighbors:
                break
    return sorted(pairs)


def _write_list(path: Path, names: list[str]) -> None:
    path.write_text("".join(f"{name}\n" for name in names))


def extract_and_match(
    image_dir: Path,
    db_path: Path,
    names: list[str],
    pairs: list[tuple[str, str]],
    *,
    max_image_size: int,
    max_features: int,
    work: Path,
) -> dict:
    if db_path.exists():
        db_path.unlink()
    extract_help = _help("feature_extractor")
    gpu_extract = _flag(extract_help, ["--FeatureExtraction.use_gpu", "--SiftExtraction.use_gpu"])
    list_path = work / "image_list.txt"
    _write_list(list_path, names)
    cmd = [
        COLMAP, "feature_extractor",
        "--database_path", str(db_path),
        "--image_path", str(image_dir),
        "--image_list_path", str(list_path),
        "--ImageReader.camera_model", "PINHOLE",
        "--ImageReader.single_camera", "0",
        "--SiftExtraction.max_image_size", str(max_image_size),
        "--SiftExtraction.max_num_features", str(max_features),
    ]
    if gpu_extract:
        cmd.extend([gpu_extract, "1"])
    t0 = time.perf_counter()
    _run(cmd)
    extract_s = time.perf_counter() - t0

    match_help = _help("matches_importer")
    gpu_match = _flag(match_help, ["--FeatureMatching.use_gpu", "--SiftMatching.use_gpu"])
    pair_path = work / "pairs.txt"
    pair_path.write_text("".join(f"{a} {b}\n" for a, b in pairs))
    cmd = [
        COLMAP, "matches_importer",
        "--database_path", str(db_path),
        "--match_list_path", str(pair_path),
        "--match_type", "pairs",
    ]
    if gpu_match:
        cmd.extend([gpu_match, "1"])
    # Guided matching needs two-view geometry; raw SIFT matching is enough here.
    if "--SiftMatching.guided_matching" in match_help:
        cmd.extend(["--SiftMatching.guided_matching", "0"])
    t1 = time.perf_counter()
    _run(cmd)
    match_s = time.perf_counter() - t1
    return {
        "extract_s": round(extract_s, 3),
        "match_s": round(match_s, 3),
        "pairs": len(pairs),
        "gpu_extract_flag": gpu_extract,
        "gpu_match_flag": gpu_match,
    }


def _open_db(db_path: Path):
    """pycolmap 3.11 binds Database.open as an instance method, not a classmethod."""
    import pycolmap

    db = pycolmap.Database()
    db.open(str(db_path))
    return db


def _lock_intrinsics(db_path: Path, intrinsics: dict[str, tuple]) -> None:
    db = _open_db(db_path)
    try:
        for img in db.read_all_images():
            known = intrinsics.get(img.name)
            if known is None:
                continue
            fx, fy, cx, cy, w, h = known
            cam = db.read_camera(img.camera_id)
            cam.width = int(w)
            cam.height = int(h)
            cam.params = np.array([fx, fy, cx, cy], dtype=float)
            cam.has_prior_focal_length = True
            db.update_camera(cam)
    finally:
        db.close()


def _write_priors(db_path: Path, centers: dict[str, np.ndarray], sigma_m: float) -> int:
    """Same PosePrior rows as a1-classical/sfm.py arkit()."""
    import pycolmap

    db = _open_db(db_path)
    written = 0
    try:
        db.clear_pose_priors()
        cov = np.eye(3) * (sigma_m ** 2)
        for img in db.read_all_images():
            if img.name not in centers:
                continue
            # pycolmap 3.11.1: PosePrior has no corr_data_id; the image id is the second argument.
            prior = pycolmap.PosePrior()
            prior.position = np.asarray(centers[img.name], dtype=float)
            prior.position_covariance = cov
            prior.coordinate_system = pycolmap.PosePriorCoordinateSystem.CARTESIAN
            db.write_pose_prior(int(img.image_id), prior)
            written += 1
    finally:
        db.close()
    return written


_PRIOR_LIB = None

# pycolmap 3.11.1's wheel omits these fields. The COLMAP 3.11.1 headers still
# have them, and incremental_mapping reads the C++ object, so a matching
# pointer write turns the priors on without rebuilding the image.
_PRIOR_SRC = r"""
#include <setjmp.h>
#include <signal.h>
#include <stddef.h>
#include "colmap/controllers/incremental_pipeline.h"
static jmp_buf prior_jmp;
static void prior_on_fault(int) { longjmp(prior_jmp, 1); }
static int prior_guard(int (*fn)(void*), void* p) {
  struct sigaction sa;
  struct sigaction old_segv;
  struct sigaction old_bus;
  sa.sa_handler = prior_on_fault;
  sigemptyset(&sa.sa_mask);
  sa.sa_flags = 0;
  sigaction(SIGSEGV, &sa, &old_segv);
  sigaction(SIGBUS, &sa, &old_bus);
  if (setjmp(prior_jmp) != 0) {
    sigaction(SIGSEGV, &old_segv, 0);
    sigaction(SIGBUS, &old_bus, 0);
    return 0;
  }
  int result = fn(p);
  sigaction(SIGSEGV, &old_segv, 0);
  sigaction(SIGBUS, &old_bus, 0);
  return result;
}
static int prior_is_opts_raw(void* p) {
  colmap::IncrementalPipelineOptions* opts = reinterpret_cast<colmap::IncrementalPipelineOptions*>(p);
  return opts->min_num_matches == 15 ? 1 : 0;
}
extern "C" {
int prior_is_opts(void* p) { return p ? prior_guard(prior_is_opts_raw, p) : 0; }
void* prior_find(void** words, int nwords) {
  for (int i = 0; i < nwords; ++i) {
    void* p = words[i];
    if (prior_is_opts(p)) return p;
  }
  for (int i = 0; i < nwords; ++i) {
    void* p = words[i];
    if (!p) continue;
    struct sigaction sa;
    struct sigaction old_segv;
    struct sigaction old_bus;
    sa.sa_handler = prior_on_fault;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = 0;
    sigaction(SIGSEGV, &sa, &old_segv);
    sigaction(SIGBUS, &sa, &old_bus);
    if (setjmp(prior_jmp) != 0) {
      sigaction(SIGSEGV, &old_segv, 0);
      sigaction(SIGBUS, &old_bus, 0);
      continue;
    }
    void* inner = *reinterpret_cast<void**>(p);
    sigaction(SIGSEGV, &old_segv, 0);
    sigaction(SIGBUS, &old_bus, 0);
    if (prior_is_opts(inner)) return inner;
  }
  return 0;
}
static int prior_set_raw(void* p, int robust) {
  colmap::IncrementalPipelineOptions* opts = reinterpret_cast<colmap::IncrementalPipelineOptions*>(p);
  opts->use_prior_position = true;
  opts->use_robust_loss_on_prior_position = robust != 0;
  return opts->use_prior_position ? 1 : 0;
}
int prior_set(void* p, int robust) {
  // prior_guard only passes one pointer, so stash the flag.
  return prior_set_raw(p, robust);
}
int prior_read(void* p) {
  colmap::IncrementalPipelineOptions* opts = reinterpret_cast<colmap::IncrementalPipelineOptions*>(p);
  return opts->use_prior_position ? 1 : 0;
}
}
"""


def _prior_lib():
    global _PRIOR_LIB
    if _PRIOR_LIB is not None:
        return _PRIOR_LIB
    import ctypes

    src = Path("/tmp/prior_setter.cpp")
    so = Path("/tmp/libprior_setter.so")
    src.write_text(_PRIOR_SRC)
    cmd = [
        "g++", "-shared", "-fPIC", "-std=c++17", "-O2",
        "-I/usr/local/include", "-I/usr/include/eigen3",
        str(src), "-o", str(so),
    ]
    proc = subprocess.run(cmd, check=False, text=True, capture_output=True)
    if proc.returncode != 0:
        tail = ((proc.stdout or "") + (proc.stderr or ""))[-1500:]
        raise RuntimeError(f"prior setter compile failed: {tail}")
    _PRIOR_LIB = ctypes.CDLL(str(so))
    _PRIOR_LIB.prior_find.restype = ctypes.c_void_p
    _PRIOR_LIB.prior_find.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]
    _PRIOR_LIB.prior_set.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _PRIOR_LIB.prior_set.restype = ctypes.c_int
    _PRIOR_LIB.prior_read.argtypes = [ctypes.c_void_p]
    _PRIOR_LIB.prior_read.restype = ctypes.c_int
    return _PRIOR_LIB


def _enable_position_priors(options, robust_prior: bool) -> str:
    """Turn on ARKit position priors. Returns how the flag was set."""
    if hasattr(options, "use_prior_position"):
        options.use_prior_position = True
        options.use_robust_loss_on_prior_position = bool(robust_prior)
        return "attribute"
    import ctypes

    lib = _prior_lib()
    words = (ctypes.c_void_p * 48).from_address(id(options))
    found = lib.prior_find(words, 48)
    if not found:
        raise RuntimeError("pycolmap 3.11.1 options object has no use_prior_position field and the C++ object was not found")
    if not lib.prior_set(found, 1 if robust_prior else 0):
        raise RuntimeError("failed to set use_prior_position on the COLMAP options object")
    if not lib.prior_read(found):
        raise RuntimeError("use_prior_position did not stick")
    return "cpp_field"


def prior_binding_ok() -> dict:
    """Compile the setter and prove a live options object accepts the prior flag."""
    import pycolmap

    options = pycolmap.IncrementalPipelineOptions()
    try:
        how = _enable_position_priors(options, False)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "how": how}


def _mapper_options(use_prior: bool, *, robust_prior: bool = False):
    import pycolmap

    options = pycolmap.IncrementalPipelineOptions()
    options.num_threads = max(1, os.cpu_count() or 4)
    options.ba_refine_focal_length = False
    options.ba_refine_principal_point = False
    options.ba_refine_extra_params = False
    if use_prior:
        # A6: robust loss with a loose sigma lets SfM bend away from ARKit.
        _enable_position_priors(options, robust_prior)
    return options


def map_scene(
    db_path: Path,
    image_dir: Path,
    out_dir: Path,
    *,
    use_prior: bool,
    robust_prior: bool = False,
) -> dict:
    import pycolmap

    if out_dir.exists():
        shutil.rmtree(out_dir)
    tmp = out_dir.parent / (out_dir.name + "_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    t0 = time.perf_counter()
    maps = pycolmap.incremental_mapping(
        str(db_path), str(image_dir), str(tmp),
        options=_mapper_options(use_prior, robust_prior=robust_prior),
    )
    mapper_s = time.perf_counter() - t0
    if not maps:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError("COLMAP produced no model")
    def _nreg(rec) -> int:
        value = rec.num_reg_images
        return int(value() if callable(value) else value)

    def _npoints(rec) -> int:
        value = rec.num_points3D
        return int(value() if callable(value) else value)

    best = max(maps.values(), key=_nreg)
    out_dir.mkdir(parents=True, exist_ok=True)
    best.write_text(str(out_dir))
    shutil.rmtree(tmp, ignore_errors=True)
    parsed = _read_text_reconstruction(out_dir)
    return {
        "mapper_s": round(mapper_s, 3),
        "models": len(maps),
        "registered": _nreg(best),
        "points3D": _npoints(best),
        "mean_reproj_px": float(best.compute_mean_reprojection_error()),
        **parsed,
    }


def _read_text_reconstruction(model_dir: Path) -> dict:
    """COLMAP text model -> cameras, xyz, colors. Stable across pycolmap versions."""
    from server.common.geometry import qvec_to_rotmat

    cams = {}
    for line in (model_dir / "cameras.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        cams[int(parts[0])] = {
            "model": parts[1],
            "w": int(parts[2]),
            "h": int(parts[3]),
            "params": [float(v) for v in parts[4:]],
        }
    cameras = []
    lines = [ln for ln in (model_dir / "images.txt").read_text().splitlines() if not ln.startswith("#")]
    i = 0
    while i < len(lines):
        head = lines[i]
        if not head.strip():
            i += 1
            continue
        parts = head.split()
        R = qvec_to_rotmat(np.array(parts[1:5], float))
        t = np.array(parts[5:8], float)
        cam = cams[int(parts[8])]
        cameras.append({
            "name": parts[9],
            "R_cw": R,
            "t_cw": t,
            "center": -R.T @ t,
            "K": cam["params"][:4],
            "w": cam["w"],
            "h": cam["h"],
            "image_id": int(parts[0]),
        })
        i += 2
    points, colors = [], []
    point_file = model_dir / "points3D.txt"
    if point_file.exists():
        for line in point_file.read_text().splitlines():
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            points.append([float(parts[1]), float(parts[2]), float(parts[3])])
            colors.append([int(float(parts[4])), int(float(parts[5])), int(float(parts[6]))])
    xyz = np.asarray(points, float) if points else np.zeros((0, 3))
    rgb = np.asarray(colors, np.uint8) if colors else None
    return {"cameras": cameras, "points": xyz, "colors": rgb}


def reconstruct_epoch(
    work: Path,
    frames: list[dict],
    *,
    max_image_size: int,
    max_features: int,
    prior_sigma_m: float,
    max_neighbors: int,
    spatial_radius_m: float,
    robust_prior: bool = False,
) -> dict:
    """frames: dicts with name, image path already in work/images, K, size, T_ar or None."""
    image_dir = work / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    for frame in frames:
        dest = image_dir / frame["name"]
        src = Path(frame["path"])
        if not src.is_file():
            raise FileNotFoundError(f"missing frame image {src}")
        if dest.exists() or dest.is_symlink():
            if dest.resolve() == src.resolve():
                continue
            dest.unlink()
        dest.symlink_to(src.resolve())
    names = [f["name"] for f in frames]
    has_pose = all(f.get("T_ar") is not None for f in frames)
    centers = None
    center_by_name: dict[str, np.ndarray] = {}
    if has_pose:
        centers = np.stack([arkit_center(f["T_ar"]) for f in frames])
        center_by_name = {f["name"]: c for f, c in zip(frames, centers)}
    pairs = spatial_pairs(
        names, centers, max_neighbors=max_neighbors, radius_m=spatial_radius_m
    )
    db_path = work / "database.db"
    timings = extract_and_match(
        image_dir, db_path, names, pairs,
        max_image_size=max_image_size, max_features=max_features, work=work,
    )
    intrinsics = {
        f["name"]: (*f["K"], f["w"], f["h"]) for f in frames
    }
    _lock_intrinsics(db_path, intrinsics)
    priors = 0
    if has_pose:
        priors = _write_priors(db_path, center_by_name, prior_sigma_m)
    mapped = map_scene(
        db_path, image_dir, work / "sparse",
        use_prior=has_pose and priors > 0,
        robust_prior=robust_prior,
    )
    timings.update({k: v for k, v in mapped.items() if k not in ("cameras", "points", "colors")})
    timings["pose_priors"] = priors
    timings["prior_sigma_m"] = prior_sigma_m if has_pose else None
    timings["robust_prior"] = bool(robust_prior) if has_pose else None
    return {
        "timings": timings,
        "cameras": mapped["cameras"],
        "points": mapped["points"],
        "colors": mapped["colors"],
        "posed": has_pose,
    }
