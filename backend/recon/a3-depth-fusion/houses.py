"""A3 on online houses (no laser truth except tt-barn's scale): SfM poses + per-frame metric depth
+ TSDF fusion. Robustness and coverage only.

    python houses.py sfm <house>                 # pycolmap SIFT + exhaustive matching + incremental mapper
    python houses.py infer <house> <model>       # per-frame depth, given the SfM focal
    python houses.py fuse <house> <model>        # metric scale from depth, TSDF, stats, renders

Frames come from internal/data/houses/<house>/frames (images <= 1024 px wide, one shared
SIMPLE_RADIAL camera estimated by SfM; depth is run on the undistorted frames). SfM is up to scale:
the metric scale is the median over frames of median(mono depth / SfM depth) at the frame's
triangulated points, then each frame's depth is rescaled to the SfM points (the `tri` mode of the
ETH3D runs, with SfM instead of AR poses). For tt-barn the SfM is also aligned (Sim3, camera
centres) to Tanks and Temples' laser-registered poses (Barn_COLMAP_SfM.log x Barn_trans.txt), which
measures the depth-derived scale against the laser's.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import open3d as o3d
import open3d.core as o3c
import pycolmap

from common import WORK  # noqa: I001
from evals.triangulate import sample_depth
from fuse import flying_pixel_mask

HOUSES = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/data/houses")
HW = WORK / "houses"
MAX_W = 1024
THREADS = 4


def paths(house: str) -> dict[str, Path]:
    d = HW / house
    return {"dir": d, "images": d / "images", "undist": d / "undistorted", "db": d / "database.db",
            "sparse": d / "sparse", "dense": d / "undistorted" / "sparse"}


def prepare_images(house: str) -> list[str]:
    p = paths(house)
    p["images"].mkdir(parents=True, exist_ok=True)
    names = sorted(x.name for x in (HOUSES / house / "frames").glob("*.jpg"))
    for n in names:
        out = p["images"] / n
        if not out.exists():
            im = cv2.imread(str(HOUSES / house / "frames" / n))
            h, w = im.shape[:2]
            if w > MAX_W:
                im = cv2.resize(im, (MAX_W, round(h * MAX_W / w)), interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(out), im, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return names


def sfm(house: str) -> dict:
    p = paths(house)
    names = prepare_images(house)
    if p["db"].exists():
        p["db"].unlink()
    t0 = time.time()
    ext = pycolmap.FeatureExtractionOptions()
    ext.num_threads = THREADS
    ext.sift.max_num_features = 8192
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "SIMPLE_RADIAL"
    pycolmap.extract_features(str(p["db"]), str(p["images"]), image_names=names,
                              camera_mode=pycolmap.CameraMode.SINGLE, reader_options=reader,
                              extraction_options=ext, device=pycolmap.Device.cpu)
    t1 = time.time()
    mopt = pycolmap.FeatureMatchingOptions()
    mopt.num_threads = THREADS
    pycolmap.match_exhaustive(str(p["db"]), matching_options=mopt, device=pycolmap.Device.cpu)
    t2 = time.time()
    tmp = p["sparse"] / "_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    o = pycolmap.IncrementalPipelineOptions()
    o.num_threads = THREADS
    o.random_seed = 0
    maps = pycolmap.incremental_mapping(str(p["db"]), str(p["images"]), str(tmp), options=o)
    t3 = time.time()
    best = max(maps.values(), key=lambda r: r.num_reg_images())
    out = p["sparse"] / "best"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    best.write(str(out))
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(p["undist"], ignore_errors=True)
    pycolmap.undistort_images(str(p["undist"]), str(out), str(p["images"]))
    rep = {"house": house, "frames": len(names), "models": len(maps),
           "model_sizes": sorted((r.num_reg_images() for r in maps.values()), reverse=True),
           "registered": best.num_reg_images(), "points3D": best.num_points3D(),
           "mean_reproj_px": best.compute_mean_reprojection_error(),
           "seconds": {"extract": t1 - t0, "match": t2 - t1, "map": t3 - t2}}
    (p["dir"] / "sfm.json").write_text(json.dumps(rep, indent=1))
    return rep


def load_model(house: str):
    """Undistorted model: {name: (K 3x3, cam-to-world 4x4, (uv, xyz) of its triangulated points)}."""
    rec = pycolmap.Reconstruction(str(paths(house)["dense"]))
    out = {}
    for img in rec.images.values():
        if not img.has_pose:
            continue
        cam = rec.cameras[img.camera_id]
        K = cam.calibration_matrix().copy()
        K[0, 2] -= 0.5  # COLMAP's first pixel centre is (0.5, 0.5); OpenCV's is (0, 0)
        K[1, 2] -= 0.5
        Rt = img.cam_from_world().matrix()
        T = np.eye(4)
        T[:3, :3] = Rt[:3, :3].T
        T[:3, 3] = -Rt[:3, :3].T @ Rt[:3, 3]
        uv, xyz = [], []
        for p2 in img.points2D:
            if p2.has_point3D():
                uv.append(np.asarray(p2.xy) - 0.5)
                xyz.append(rec.points3D[p2.point3D_id].xyz)
        out[img.name] = (K, T, (np.array(uv).reshape(-1, 2), np.array(xyz).reshape(-1, 3)))
    return out


def infer(house: str, model_name: str) -> None:
    import torch

    from infer import build

    torch.set_num_threads(4)
    model = load_model(house)
    out = paths(house)["dir"] / "pred" / model_name
    out.mkdir(parents=True, exist_ok=True)
    net = build(model_name)
    times = []
    for n, (K, _, _) in sorted(model.items()):
        f = out / f"{Path(n).stem}.npz"
        if f.exists():
            continue
        rgb = cv2.cvtColor(cv2.imread(str(paths(house)["undist"] / "images" / n)), cv2.COLOR_BGR2RGB)
        t = time.perf_counter()
        with torch.no_grad():
            d, valid = net(rgb, K)
        times.append(time.perf_counter() - t)
        np.savez_compressed(f, depth=d.astype(np.float32), valid=valid, K=K, seconds=times[-1])
    print(house, model_name, f"{len(times)} frames, {np.mean(times) if times else 0:.1f} s/frame")


def barn_reference() -> dict[str, np.ndarray]:
    """Tanks and Temples camera centres in the laser frame, by frame name."""
    cal = HOUSES / "tt-barn" / "calibration"
    trans = np.loadtxt(cal / "Barn_trans.txt")
    lines = (cal / "Barn_COLMAP_SfM.log").read_text().split("\n")
    out = {}
    for i in range(0, len(lines) - 4, 5):
        head = lines[i].split()
        if len(head) < 3:
            continue
        T = np.array([list(map(float, lines[i + k].split())) for k in range(1, 5)])
        out[f"{int(head[0]) + 1:06d}.jpg"] = (trans @ T)[:3, 3]
    return out


def umeyama_sim3(src: np.ndarray, dst: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    U, S, Vt = np.linalg.svd(xd.T @ xs / len(src))
    D = np.diag([1, 1, np.sign(np.linalg.det(U @ Vt))])
    R = U @ D @ Vt
    s = np.trace(np.diag(S) @ D) / xs.var(0).sum()
    return float(s), R, mu_d - s * R @ mu_s


def shade_render(mesh, eye, target, up, K, w, h) -> np.ndarray:
    rc = o3d.t.geometry.RaycastingScene()
    rc.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(mesh))
    f = target - eye
    f /= np.linalg.norm(f)
    r = np.cross(f, up)
    r /= np.linalg.norm(r)
    d = np.cross(f, r)
    u, v = np.meshgrid(np.arange(w), np.arange(h))
    dirs = ((u - K[0, 2]) / K[0, 0])[..., None] * r + ((v - K[1, 2]) / K[1, 1])[..., None] * d + f
    dirs = dirs.reshape(-1, 3)
    rays = o3c.Tensor(np.c_[np.broadcast_to(eye, dirs.shape), dirs].astype(np.float32))
    ans = rc.cast_rays(rays)
    hit = np.isfinite(ans["t_hit"].numpy())
    prim = ans["primitive_ids"].numpy()
    cols = np.asarray(mesh.vertex_colors)
    tris = np.asarray(mesh.triangles)
    img = np.full((h * w, 3), 255, np.float64)
    n = ans["primitive_normals"].numpy()
    lam = np.abs((n * (-dirs / np.linalg.norm(dirs, axis=1, keepdims=True))).sum(1))
    c = cols[tris[prim[hit]]].mean(1) if len(cols) else np.full((hit.sum(), 3), 0.7)
    img[hit] = 255 * np.clip(c * (0.35 + 0.65 * lam[hit, None]), 0, 1)
    return img.reshape(h, w, 3).astype(np.uint8)


def fuse_house(house: str, model_name: str, voxel: float = 0.04, depth_trunc: float = 10.0) -> dict:
    p = paths(house)
    model = load_model(house)
    pred = p["dir"] / "pred" / model_name
    depths, ratios = {}, {}
    for n, (K, T, (uv, xyz)) in model.items():
        z = np.load(pred / f"{Path(n).stem}.npz")
        d = np.where(z["valid"], z["depth"], np.nan).astype(np.float32)
        d = np.where(flying_pixel_mask(d), d, np.nan)
        depths[n] = d
        if len(xyz) >= 20:
            z_sfm = (xyz - T[:3, 3]) @ T[:3, 2]
            z_mono = sample_depth(d, uv)
            ok = np.isfinite(z_mono) & (z_sfm > 0)
            if ok.sum() >= 20:
                ratios[n] = z_mono[ok] / z_sfm[ok]
    per_frame = {n: float(np.median(r)) for n, r in ratios.items()}
    scale = float(np.median(list(per_frame.values())))  # metres per SfM unit, from mono depth
    spread = np.array(list(per_frame.values())) / scale
    stats = {"house": house, "model": model_name, "registered": len(model), "frames_scaled": len(per_frame),
             "metric_scale_m_per_unit": scale,
             "per_frame_mono_scale_spread_p10_p90_pct": [float((np.percentile(spread, 10) - 1) * 100),
                                                          float((np.percentile(spread, 90) - 1) * 100)]}
    if house == "tt-barn":
        ref = barn_reference()
        common = [n for n in model if n in ref]
        s, R, t = umeyama_sim3(np.stack([model[n][1][:3, 3] for n in common]), np.stack([ref[n] for n in common]))
        res = np.stack([s * R @ model[n][1][:3, 3] + t for n in common]) - np.stack([ref[n] for n in common])
        stats["barn_laser_scale_m_per_unit"] = s
        stats["barn_depth_scale_error_pct"] = (scale / s - 1) * 100
        stats["barn_sim3_centre_rms_m"] = float(np.sqrt((res ** 2).sum(1).mean()))
        stats["barn_frames_aligned"] = len(common)
    cpu = o3c.Device("CPU:0")
    vbg = o3d.t.geometry.VoxelBlockGrid(("tsdf", "weight", "color"), (o3c.float32, o3c.float32, o3c.float32),
                                        ((1), (1), (3)), voxel, 8, 60_000, cpu)
    t0 = time.perf_counter()
    for n, (K, T, _) in sorted(model.items()):
        if n not in per_frame:
            continue
        Tm = T.copy()
        Tm[:3, 3] *= scale
        d = depths[n] * (scale / per_frame[n])  # this frame's depth on the (metric) SfM scale
        rgb = cv2.cvtColor(cv2.imread(str(p["undist"] / "images" / n)), cv2.COLOR_BGR2RGB)
        dt = o3d.t.geometry.Image(o3c.Tensor(np.nan_to_num(d, nan=0.0).astype(np.float32)))
        ct = o3d.t.geometry.Image(o3c.Tensor(rgb.astype(np.float32) / 255))
        Kt, E = o3c.Tensor(K, o3c.float64), o3c.Tensor(np.linalg.inv(Tm), o3c.float64)
        blocks = vbg.compute_unique_block_coordinates(dt, Kt, E, 1.0, depth_trunc, trunc_voxel_multiplier=4.0)
        vbg.integrate(blocks, dt, ct, Kt, Kt, E, 1.0, depth_trunc, trunc_voxel_multiplier=4.0)
    mesh = vbg.extract_triangle_mesh(weight_threshold=1.0).to_legacy()
    mesh.compute_vertex_normals()
    stats["fuse_s"] = time.perf_counter() - t0
    stats["vertices"] = len(mesh.vertices)
    # Self-consistency: per frame, the fused surface rendered back vs the frame's own scaled depth.
    rc = o3d.t.geometry.RaycastingScene()
    rc.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(mesh))
    agree, has = [], []
    for n, (K, T, _) in sorted(model.items()):
        if n not in per_frame:
            continue
        d = depths[n] * (scale / per_frame[n])
        h, w = d.shape
        u, v = np.meshgrid(np.arange(0, w, 4), np.arange(0, h, 4))
        dc = np.stack([(u - K[0, 2]) / K[0, 0], (v - K[1, 2]) / K[1, 1], np.ones(u.shape)], -1).reshape(-1, 3)
        dw = dc @ T[:3, :3].T
        rays = o3c.Tensor(np.c_[np.broadcast_to(T[:3, 3] * scale, dw.shape), dw].astype(np.float32))
        th = rc.cast_rays(rays)["t_hit"].numpy()
        own = d[v.ravel(), u.ravel()]
        m = np.isfinite(own) & (own < depth_trunc)
        has.append(np.isfinite(th[m]).mean() if m.any() else np.nan)
        ok = m & np.isfinite(th)
        agree.append((np.abs(th[ok] - own[ok]) <= 0.05 * own[ok]).mean() if ok.any() else np.nan)
    stats["fused_surface_behind_own_pixels_share"] = float(np.nanmean(has))
    stats["fused_agrees_own_depth_5pct_share"] = float(np.nanmean(agree))
    out = p["dir"] / "fused"
    out.mkdir(exist_ok=True)
    o3d.io.write_triangle_mesh(str(out / f"{model_name}.ply"), mesh)
    # Renders: top-down and a raised oblique view from outside the camera ring.
    V = np.asarray(mesh.vertices)
    C = np.stack([T[:3, 3] * scale for _, T, _ in model.values()])
    up = -np.mean([T[:3, 1] for _, T, _ in model.values()], axis=0)
    up /= np.linalg.norm(up)
    centre = np.median(V, axis=0)
    radius = np.percentile(np.linalg.norm(C - centre, axis=1), 90)
    Kr = np.array([[600.0, 0, 512], [0, 600.0, 384], [0, 0, 1]])
    top = shade_render(mesh, centre + up * radius * 2.2, centre, np.cross(up, [1, 0, 0]) if abs(up[0]) < 0.9 else np.cross(up, [0, 0, 1]), Kr, 1024, 768)
    side_dir = C.mean(0) - centre
    side_dir -= up * (side_dir @ up)
    side_dir = side_dir / (np.linalg.norm(side_dir) + 1e-9) if np.linalg.norm(side_dir) > 1e-6 else np.cross(up, [1, 0, 0])
    oblique = shade_render(mesh, centre + side_dir * radius * 1.6 + up * radius * 0.8, centre, up, Kr, 1024, 768)
    cv2.imwrite(str(out / f"{model_name}_top.jpg"), cv2.cvtColor(top, cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(out / f"{model_name}_oblique.jpg"), cv2.cvtColor(oblique, cv2.COLOR_RGB2BGR))
    (out / f"{model_name}.json").write_text(json.dumps(stats, indent=1))
    return stats


if __name__ == "__main__":
    step, house = sys.argv[1], sys.argv[2]
    if step == "sfm":
        print(json.dumps(sfm(house), indent=1))
    elif step == "infer":
        infer(house, sys.argv[3])
    elif step == "fuse":
        print(json.dumps(fuse_house(house, sys.argv[3]), indent=1))
