"""A1/A6 COLMAP recipe on pycolmap 4.2.

Priors are position-only, σ from ``PoseParams``, written with ``corr_data_id``.
There is no 3.11 pointer switch and no GLOMAP call.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import numpy as np
import pycolmap

from server.poses.errors import PosesError
from server.poses.params import PoseParams

# Mapping, post-registration, and fixed triangulation stay on one thread.
# Feature extraction and matching keep ``PoseParams.num_threads``. A busy CPU
# makes multi-threaded bundle adjustment change the reconstruction (SD-93).
MAPPING_THREADS = 1


def device_for(params: PoseParams) -> pycolmap.Device:
    if not params.gpu:
        return pycolmap.Device.cpu
    return pycolmap.Device.auto


def pipeline_options(params: PoseParams, *, refine: bool, use_prior: bool) -> pycolmap.IncrementalPipelineOptions:
    options = pycolmap.IncrementalPipelineOptions()
    # Extraction and matching stay on ``params.num_threads``. This options
    # object is only for incremental mapping and fixed triangulation.
    options.num_threads = MAPPING_THREADS
    options.random_seed = params.seed
    options.ba_refine_focal_length = refine
    options.ba_refine_principal_point = False
    options.ba_refine_extra_params = refine
    options.use_prior_position = use_prior
    options.use_robust_loss_on_prior_position = bool(params.robust_prior) if use_prior else False
    return options


def extract_features(
    db_path: Path,
    image_dir: Path,
    names: list[str],
    *,
    camera_model: str,
    params: PoseParams,
    reset: bool = True,
) -> None:
    if reset and db_path.exists():
        db_path.unlink()
    extraction = pycolmap.FeatureExtractionOptions()
    extraction.max_image_size = params.max_image_size
    extraction.num_threads = params.num_threads
    extraction.use_gpu = False
    extraction.sift.max_num_features = params.max_features
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = camera_model
    pycolmap.extract_features(
        db_path,
        image_dir,
        image_names=names,
        camera_mode=pycolmap.CameraMode.PER_IMAGE,
        reader_options=reader,
        extraction_options=extraction,
        device=device_for(params),
    )
    # Parallel extraction inserts images as each worker finishes, so image ids
    # follow completion order. The mapper walks ids, and a reshuffle changes
    # the initial pair. Put this batch back into ``names`` order before matching.
    _stabilize_image_ids(db_path, names)


def _stabilize_image_ids(db_path: Path, names: list[str]) -> None:
    """Renumber one extraction batch so image ids follow ``names``.

    Only ids belonging to ``names`` move, and they keep the same id slots, so a
    later ``reset=False`` extract cannot renumber images that already have matches.
    """
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        rows = connection.execute("SELECT image_id, name FROM images").fetchall()
        by_name = {name: image_id for image_id, name in rows}
        targets = [name for name in names if name in by_name]
        if len(targets) < 2:
            return
        slots = sorted(by_name[name] for name in targets)
        mapping = {by_name[name]: slot for name, slot in zip(targets, slots, strict=True) if by_name[name] != slot}
        if not mapping:
            return
        offset = 10_000_000
        columns = (
            ("images", "image_id"),
            ("keypoints", "image_id"),
            ("descriptors", "image_id"),
            ("frames", "frame_id"),
            ("frame_data", "frame_id"),
            ("frame_data", "data_id"),
        )
        for old in mapping:
            for table, column in columns:
                connection.execute(
                    f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
                    (old + offset, old),
                )
        for old, new in mapping.items():
            for table, column in columns:
                connection.execute(
                    f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
                    (new, old + offset),
                )
        connection.commit()
    finally:
        connection.close()


def match_imported(
    db_path: Path,
    pairs: list[tuple[str, str]],
    params: PoseParams,
) -> int:
    """Photo-flow pairs, written to a list and matched with ``match_image_pairs``."""
    if not pairs:
        raise PosesError("too_few_frames")
    pair_path = db_path.with_suffix(".pairs.txt")
    pair_path.write_text("".join(f"{a} {b}\n" for a, b in pairs))
    matching = pycolmap.FeatureMatchingOptions()
    matching.num_threads = params.num_threads
    matching.use_gpu = False
    pairing = pycolmap.ImportedPairingOptions()
    pairing.match_list_path = str(pair_path)
    verification = pycolmap.TwoViewGeometryOptions()
    verification.ransac.random_seed = params.seed
    pycolmap.match_image_pairs(
        db_path,
        matching_options=matching,
        pairing_options=pairing,
        verification_options=verification,
        device=device_for(params),
    )
    pair_path.unlink(missing_ok=True)
    return len(pairs)


def lock_intrinsics(db_path: Path, cameras: dict[str, tuple[str, list[float]]]) -> None:
    """``cameras[name] = (model, params)`` with params in COLMAP order."""
    db = pycolmap.Database.open(db_path)
    try:
        for image in db.read_all_images():
            known = cameras.get(image.name)
            if known is None:
                continue
            model, params = known
            cam = db.read_camera(image.camera_id)
            cam.model = _model(model)
            cam.params = np.asarray(params, dtype=float)
            cam.has_prior_focal_length = True
            db.update_camera(cam)
    finally:
        db.close()


def _model(name: str) -> pycolmap.CameraModelId:
    return getattr(pycolmap.CameraModelId, name)


def write_priors(db_path: Path, centers: dict[str, np.ndarray], sigma_m: float) -> int:
    db = pycolmap.Database.open(db_path)
    written = 0
    try:
        db.clear_pose_priors()
        for image in db.read_all_images():
            center = centers.get(image.name)
            if center is None:
                continue
            prior = pycolmap.PosePrior()
            prior.corr_data_id = image.data_id
            prior.position = np.asarray(center, dtype=float)
            prior.position_covariance = np.eye(3) * sigma_m**2
            prior.coordinate_system = pycolmap.PosePriorCoordinateSystem.CARTESIAN
            db.write_pose_prior(prior)
            written += 1
    finally:
        db.close()
    return written


def keypoint_median(db_path: Path) -> float:
    db = pycolmap.Database.open(db_path)
    counts: list[int] = []
    try:
        for image in db.read_all_images():
            keypoints = db.read_keypoints(image.image_id)
            counts.append(int(len(keypoints)))
    finally:
        db.close()
    if not counts:
        return 0.0
    return float(np.median(counts))


def incremental(
    db_path: Path,
    image_dir: Path,
    tmp: Path,
    *,
    image_names: list[str],
    params: PoseParams,
    refine: bool,
    use_prior: bool,
) -> dict[int, pycolmap.Reconstruction]:
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    options = pipeline_options(params, refine=refine, use_prior=use_prior)
    if image_names:
        options.image_names = list(image_names)
    return pycolmap.incremental_mapping(db_path, image_dir, tmp, options=options)


def best_reconstruction(maps: dict[int, pycolmap.Reconstruction]) -> pycolmap.Reconstruction | None:
    if not maps:
        return None
    return max(maps.values(), key=lambda rec: rec.num_reg_images())


def triangulate_fixed(
    db_path: Path,
    image_dir: Path,
    tmp: Path,
    poses: dict[str, tuple[np.ndarray, np.ndarray]],
    params: PoseParams,
    image_order: list[str] | None = None,
) -> pycolmap.Reconstruction:
    """Triangulate with poses held fixed. The bundle step does not move them."""
    db = pycolmap.Database.open(db_path)
    rec = pycolmap.Reconstruction()
    seen: set[int] = set()
    rank = {name: index for index, name in enumerate(image_order or [])}
    try:
        images = [image for image in db.read_all_images() if image.name in poses]
        images.sort(key=lambda image: (rank.get(image.name, len(rank)), image.name))
        for image in images:
            cam = db.read_camera(image.camera_id)
            if image.camera_id not in seen:
                rec.add_camera_with_trivial_rig(cam)
                seen.add(image.camera_id)
            R_cw, t_cw = poses[image.name]
            clone = pycolmap.Image(name=image.name, camera_id=image.camera_id, image_id=image.image_id)
            rigid = pycolmap.Rigid3d(pycolmap.Rotation3d(np.asarray(R_cw, float)), np.asarray(t_cw, float))
            rec.add_image_with_trivial_frame(clone, rigid)
    finally:
        db.close()
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    options = pipeline_options(params, refine=False, use_prior=False)
    return pycolmap.triangulate_points(
        rec,
        db_path,
        image_dir,
        tmp,
        clear_points=True,
        options=options,
        refine_intrinsics=False,
    )


def registration_options(params: PoseParams) -> pycolmap.IncrementalMapperOptions:
    """Post-registration options. One thread, same seed as the mapper."""
    options = pycolmap.IncrementalMapperOptions()
    options.num_threads = MAPPING_THREADS
    options.random_seed = params.seed
    options.abs_pose_max_error = params.register_max_reproj_px
    options.abs_pose_min_num_inliers = params.register_min_points3d
    return options


def register_into(
    db_path: Path,
    rec: pycolmap.Reconstruction,
    names: list[str],
    params: PoseParams,
) -> tuple[int, int]:
    """Register ``names`` into ``rec``. Returns ``(registered, failed)``."""
    if not names:
        return 0, 0
    db = pycolmap.Database.open(db_path)
    cache = pycolmap.DatabaseCache.create(db, pycolmap.DatabaseCacheOptions())
    mapper = pycolmap.IncrementalMapper(cache)
    mapper.begin_reconstruction(rec)
    options = registration_options(params)
    tri = pycolmap.IncrementalTriangulatorOptions()
    tri.random_seed = params.seed
    ok = 0
    failed = 0
    by_name = {image.name: image for image in db.read_all_images()}
    try:
        for name in names:
            image = by_name.get(name)
            if image is None:
                continue
            if image.name in _registered_names(rec):
                continue
            try:
                accepted = mapper.register_next_image(options, image.image_id)
            except (RuntimeError, TypeError, ValueError, IndexError):
                accepted = False
            if accepted:
                try:
                    mapper.triangulate_image(tri, image.image_id)
                except (RuntimeError, TypeError, ValueError, IndexError):
                    accepted = False
            if accepted:
                ok += 1
            else:
                failed += 1
    finally:
        mapper.end_reconstruction(False)
        db.close()
    return ok, failed


def _registered_names(rec: pycolmap.Reconstruction) -> set[str]:
    names: set[str] = set()
    for image in rec.images.values():
        if image.has_pose:
            names.add(image.name)
    return names


def colmap_version() -> str:
    binary = os.environ.get("HSS_POSES_COLMAP_BIN", "colmap")
    try:
        proc = subprocess.run([binary, "-h"], check=False, capture_output=True, text=True)
    except (OSError, FileNotFoundError):
        return f"pycolmap {pycolmap.__version__} (no colmap binary)"
    text = (proc.stdout or "") + (proc.stderr or "")
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line or f"pycolmap {pycolmap.__version__}"
