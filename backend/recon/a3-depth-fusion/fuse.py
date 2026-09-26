"""Scale per-image metric depth and fuse it along camera poses into one TSDF mesh.

    python fuse.py --scene electro --model moge2-vits --poses gt --scale feat

Scale modes are described in `poses.py`. Depth is cleaned of flying pixels (5x5 depth range above
5% of depth) before scaling. The TSDF is built in the pose frame (ETH3D world for `gt`, the drifted
AR-like world otherwise). Mesh -> WORK/fused/<scene>/<tag>.ply.
"""

from __future__ import annotations

import argparse
import json
import time
from functools import lru_cache

import cv2
import numpy as np
import open3d as o3d
import open3d.core as o3c

from common import WORK, eval_image, load_depth, scene_views  # noqa: I001 (puts PR #12 on sys.path)
from evals.recon import Scene
from poses import feature_scale, median_ratio, poses, simulated_lidar, triangulated_scales

FUSED = WORK / "fused"
SCALE_MODES = ("raw", "feat", "tri", "lidar", "oracle")
_SIFT: dict[str, dict] = {}  # per scene: view -> (keypoints, descriptors), shared across runs


def flying_pixel_mask(d: np.ndarray, window: int = 5, rel: float = 0.05) -> np.ndarray:
    z = np.where(np.isfinite(d), d, 0).astype(np.float32)
    k = np.ones((window, window), np.uint8)
    big = np.where(np.isfinite(d), d, -1e6).astype(np.float32)
    small = np.where(np.isfinite(d), d, 1e6).astype(np.float32)
    rng = cv2.dilate(big, k) - cv2.erode(small, k)
    return np.isfinite(d) & (z > 0) & (rng <= rel * np.maximum(z, 1e-6))


@lru_cache(maxsize=2)
def _scene(scene: str) -> Scene:
    return Scene(scene)


def oracle_depth(scene: str, view: str) -> np.ndarray:
    return _scene(scene).oracle_depth(view)[0]


class DepthSource:
    """Scaled, cleaned depth per view for one (scene, model, pose mode, scale mode)."""

    def __init__(self, scene: str, model: str, pose_mode: str, scale_mode: str, seed: int = 0):
        self.scene, self.model, self.scale_mode = scene, model, scale_mode
        self.views = {v.name: v for v in scene_views(scene)}
        self.T, self.pose_info = poses(scene, pose_mode)
        self.session_scale = self.pose_info.get("session_scale", 1.0)
        self.rng = np.random.default_rng(seed)
        self.raw = {}
        for n in self.views:
            if model == "scan":  # the laser scan rendered as depth: the evaluation floor
                d = oracle_depth(scene, n)
            else:
                try:
                    d, _ = load_depth(scene, model, n)
                except FileNotFoundError:
                    continue
            self.raw[n] = np.where(flying_pixel_mask(d), d, np.nan).astype(np.float32)
        self.scales: dict[str, float | None] = {}
        self.lidar: dict[str, np.ndarray] = {}
        self.stats: dict = {}
        self._fit()

    def _fit(self) -> None:
        m = self.scale_mode
        if m == "raw":
            self.scales = {n: 1.0 for n in self.raw}
        elif m == "feat":
            for n, d in self.raw.items():
                self.scales[n], _ = feature_scale(self.scene, self.views[n], d, self.session_scale, self.rng)
        elif m == "tri":
            got = triangulated_scales(self.scene, self.T, self.raw, _SIFT.setdefault(self.scene, {}))
            self.scales = {n: s for n, (s, _) in got.items()}
        elif m == "oracle":
            for n, d in self.raw.items():
                self.scales[n] = median_ratio(oracle_depth(self.scene, n), d)
        elif m == "lidar":
            cover = []
            for n, d in self.raw.items():
                L = simulated_lidar(oracle_depth(self.scene, n), self.rng)
                self.lidar[n] = L
                cover.append(float(np.isfinite(L).mean()))
                s = median_ratio(L, d, min_px=500)
                if s is None:  # LiDAR sees nothing within 5 m: fall back to feature points
                    s, _ = feature_scale(self.scene, self.views[n], d, self.session_scale, self.rng)
                self.scales[n] = s
            self.stats["lidar_pixel_share"] = float(np.mean(cover))
            self.stats["lidar_views_with_scale"] = int(sum(np.isfinite(L).sum() >= 500 for L in self.lidar.values()))
        else:
            raise ValueError(m)
        ok = [s for s in self.scales.values() if s is not None]
        self.stats["scaled_views"] = len(ok)
        self.stats["views"] = len(self.raw)
        if ok:
            self.stats["scale_median"] = float(np.median(ok))
            self.stats["scale_p10_p90"] = [float(np.percentile(ok, 10)), float(np.percentile(ok, 90))]

    def depth(self, n: str) -> np.ndarray | None:
        s = self.scales.get(n)
        if s is None or n not in self.raw:
            return None
        d = self.raw[n] * s
        if n in self.lidar:
            L = self.lidar[n]
            d = np.where(np.isfinite(L), L, d)
        return d

    def K(self, n: str) -> np.ndarray:
        return _scene(self.scene).K(n)


def fuse(src: DepthSource, voxel: float = 0.04, trunc_vox: float = 4.0, depth_trunc: float = 12.0,
         min_weight: float = 1.0, names: list[str] | None = None) -> o3d.geometry.TriangleMesh:
    """TSDF fusion with Open3D's tensor VoxelBlockGrid (the legacy ScalableTSDFVolume in Open3D
    0.20 returned empty meshes on this data). `min_weight`: views a voxel needs to be meshed."""
    cpu = o3c.Device("CPU:0")
    vbg = o3d.t.geometry.VoxelBlockGrid(
        attr_names=("tsdf", "weight", "color"), attr_dtypes=(o3c.float32, o3c.float32, o3c.float32),
        attr_channels=((1), (1), (3)), voxel_size=voxel, block_resolution=8, block_count=60_000,
        device=cpu)
    for n in names or sorted(src.raw):
        d = src.depth(n)
        if d is None:
            continue
        rgb, _ = eval_image(src.views[n])
        dt = o3d.t.geometry.Image(o3c.Tensor(np.nan_to_num(d, nan=0.0).astype(np.float32)))
        ct = o3d.t.geometry.Image(o3c.Tensor(np.ascontiguousarray(rgb).astype(np.float32) / 255))
        K = o3c.Tensor(src.K(n), o3c.float64)
        E = o3c.Tensor(np.linalg.inv(src.T[n]), o3c.float64)
        blocks = vbg.compute_unique_block_coordinates(dt, K, E, 1.0, depth_trunc, trunc_voxel_multiplier=trunc_vox)
        vbg.integrate(blocks, dt, ct, K, K, E, 1.0, depth_trunc, trunc_voxel_multiplier=trunc_vox)
    mesh = vbg.extract_triangle_mesh(weight_threshold=min_weight).to_legacy()
    mesh.compute_vertex_normals()
    return mesh


def tag(model: str, pose_mode: str, scale_mode: str, voxel: float) -> str:
    return f"{model}__{pose_mode.replace('@', '-')}__{scale_mode}__v{round(voxel * 100)}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--poses", default="gt")
    ap.add_argument("--scale", default="feat", choices=SCALE_MODES)
    ap.add_argument("--voxel", type=float, default=0.04)
    args = ap.parse_args()
    t = time.perf_counter()
    src = DepthSource(args.scene, args.model, args.poses, args.scale)
    t_scale = time.perf_counter() - t
    mesh = fuse(src, args.voxel)
    out = FUSED / args.scene
    out.mkdir(parents=True, exist_ok=True)
    name = tag(args.model, args.poses, args.scale, args.voxel)
    o3d.io.write_triangle_mesh(str(out / f"{name}.ply"), mesh)
    info = {"scale_s": t_scale, "fuse_s": time.perf_counter() - t - t_scale, "stats": src.stats,
            "pose_info": src.pose_info, "vertices": len(mesh.vertices)}
    (out / f"{name}.json").write_text(json.dumps(info, indent=1))
    print(name, json.dumps(info))


if __name__ == "__main__":
    main()
