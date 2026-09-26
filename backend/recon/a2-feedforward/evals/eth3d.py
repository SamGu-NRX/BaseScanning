"""ETH3D high-resolution scenes (Schöps et al., CVPR 2017): DSLR images, poses and laser scans.

Files used, per scene (`<scene>/` after extracting the two archives in `datasets.py`):

- `dslr_calibration_undistorted/cameras.txt`, `images.txt`, `points3D.txt`: COLMAP text model of the
  undistorted images. PINHOLE cameras; each image line holds the world-to-camera rotation (QW, QX,
  QY, QZ) and translation, OpenCV camera axes (+x right, +y down, +z forward), and pixel centres
  at half-integers (COLMAP's convention; `read_views` converts to OpenCV's). Units are meters,
  because the images were registered to the laser scans.
- `images/dslr_images_undistorted/*.JPG`: the undistorted images, about 6200 x 4130 px.
- `dslr_scan_eval/scan*.ply` and `scan_alignment.mlp`: the laser scans and the 4x4 matrix that
  takes each scan into the images' world frame.

ETH3D also publishes rendered depth maps, but they match the original distorted images, whose
camera model is not in these archives, so this code scores against the scan points directly:
`visible_scan_points` projects them into a view and keeps the ones on the visible surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial import cKDTree

from evals.geometry import quat_wxyz_to_matrix
from evals.paths import ETH3D_DIR

SCENES = ("facade", "electro")
VOXEL_M = 0.01


@dataclass(frozen=True)
class View:
    name: str  # e.g. "DSC_9278"
    image_path: Path
    width: int
    height: int
    K: np.ndarray  # 3x3, OpenCV convention, full resolution
    R_wc: np.ndarray  # world-to-camera rotation
    t_wc: np.ndarray  # world-to-camera translation

    @property
    def center(self) -> np.ndarray:
        return -self.R_wc.T @ self.t_wc

    @property
    def cam_to_world(self) -> np.ndarray:
        T = np.eye(4)
        T[:3, :3] = self.R_wc.T
        T[:3, 3] = self.center
        return T

    @property
    def forward(self) -> np.ndarray:
        """Viewing direction in the world frame."""
        return self.R_wc.T @ np.array([0.0, 0.0, 1.0])

    def scaled_K(self, width: int, height: int) -> np.ndarray:
        """Intrinsics for the image resized to width x height (OpenCV integer-centred pixels)."""
        sx, sy = width / self.width, height / self.height
        K = self.K.copy()
        K[0, 0] *= sx
        K[1, 1] *= sy
        K[0, 2] = (K[0, 2] + 0.5) * sx - 0.5
        K[1, 2] = (K[1, 2] + 0.5) * sy - 0.5
        return K


def read_views(scene_dir: Path) -> list[View]:
    cal = scene_dir / "dslr_calibration_undistorted"
    cams = {}
    for line in (cal / "cameras.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        cid, model, w, h, *params = line.split()
        if model != "PINHOLE":
            raise ValueError(f"{cal}/cameras.txt: camera {cid} is {model}, expected PINHOLE")
        fx, fy, cx, cy = map(float, params)
        # COLMAP puts the first pixel's centre at (0.5, 0.5); OpenCV, used from here on, at (0, 0).
        cx, cy = cx - 0.5, cy - 0.5
        cams[cid] = (int(w), int(h), np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]]))
    views = []
    lines = [x for x in (cal / "images.txt").read_text().splitlines() if not x.startswith("#")]
    # Two lines per image: the pose line, then its 2-D observations.
    for pose_line in lines[0::2]:
        if not pose_line.strip():
            continue
        f = pose_line.split()
        q = np.array(list(map(float, f[1:5])))
        t = np.array(list(map(float, f[5:8])))
        w, h, K = cams[f[8]]
        rel = f[9]
        views.append(
            View(
                name=Path(rel).stem,
                image_path=scene_dir / "images" / rel,
                width=w,
                height=h,
                K=K,
                R_wc=quat_wxyz_to_matrix(q),
                t_wc=t,
            )
        )
    return sorted(views, key=lambda v: v.name)


def read_sparse_points(scene_dir: Path) -> np.ndarray:
    """COLMAP's triangulated points (N, 3), world frame."""
    rows = []
    for line in (
        (scene_dir / "dslr_calibration_undistorted" / "points3D.txt").read_text().splitlines()
    ):
        if line and not line.startswith("#"):
            rows.append(list(map(float, line.split()[1:4])))
    return np.asarray(rows)


def read_alignment(mlp: Path) -> dict[str, np.ndarray]:
    """scan file name -> 4x4 matrix into the images' world frame, from a MeshLab project."""
    text = mlp.read_text()
    out = {}
    for name, body in re.findall(
        r'filename="([^"]+)".*?<MLMatrix44>(.*?)</MLMatrix44>', text, re.S
    ):
        M = np.array(body.split(), dtype=np.float64).reshape(4, 4)
        out[name] = M
    if not out:
        raise ValueError(f"{mlp}: no MLMesh matrices found")
    return out


def read_ply_xyz(path: Path) -> np.ndarray:
    """Vertex positions from a binary little-endian PLY whose vertex properties are all float32."""
    with path.open("rb") as fh:
        header = []
        while True:
            line = fh.readline().decode("ascii").strip()
            header.append(line)
            if line == "end_header":
                break
        if "format binary_little_endian 1.0" not in header:
            raise ValueError(f"{path}: expected binary_little_endian PLY, header {header[:3]}")
        n = None
        props: list[str] = []
        in_vertex = False
        for line in header:
            if line.startswith("element"):
                in_vertex = line.split()[1] == "vertex"
                if in_vertex:
                    n = int(line.split()[2])
            elif line.startswith("property") and in_vertex:
                kind, name = line.split()[1], line.split()[2]
                if kind != "float":
                    raise ValueError(f"{path}: vertex property {name} is {kind}, expected float")
                props.append(name)
        if n is None or props[:3] != ["x", "y", "z"]:
            raise ValueError(f"{path}: vertex element missing or not starting with x, y, z")
        data = np.fromfile(fh, dtype="<f4", count=n * len(props))
    return data.reshape(n, len(props))[:, :3].astype(np.float64)


def voxel_downsample(points: np.ndarray, voxel: float) -> np.ndarray:
    """One point per occupied voxel: the mean of the points in it."""
    keys = np.floor(points / voxel).astype(np.int64)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    inverse = inverse.ravel()
    sums = np.zeros((len(counts), 3))
    np.add.at(sums, inverse, points)
    return sums / counts[:, None]


def scan_points(scene: str, voxel: float = VOXEL_M) -> np.ndarray:
    """All of a scene's laser scans in the image world frame, voxel-downsampled, cached as .npy."""
    scene_dir = ETH3D_DIR / scene
    cache = scene_dir / f"scan_points_{round(voxel * 1000)}mm.npy"
    if cache.exists():
        return np.load(cache)
    eval_dir = scene_dir / "dslr_scan_eval"
    align = read_alignment(eval_dir / "scan_alignment.mlp")
    parts = []
    for name, M in sorted(align.items()):
        xyz = read_ply_xyz(eval_dir / name)
        world = xyz @ M[:3, :3].T + M[:3, 3]
        parts.append(voxel_downsample(world, voxel))
    pts = voxel_downsample(np.concatenate(parts), voxel).astype(np.float32)
    np.save(cache, pts)
    return pts


def occluder_points(scene: str, voxel: float = 0.02) -> np.ndarray:
    """Vertices of ETH3D's occlusion splats (`occlusion/splats.ply`): surfaces the scanner missed
    that still block the view, such as vegetation. They enter the visibility test as blockers only,
    never as ground truth. Voxel-downsampled and cached as .npy."""
    scene_dir = ETH3D_DIR / scene
    cache = scene_dir / f"occluders_{round(voxel * 1000)}mm.npy"
    if cache.exists():
        return np.load(cache)
    pts = voxel_downsample(read_ply_xyz(scene_dir / "occlusion" / "splats.ply"), voxel)
    np.save(cache, pts.astype(np.float32))
    return pts.astype(np.float32)


def excluded_pixels(
    view: View, width: int, height: int, dilate: int = 13, labels: tuple[int, ...] = (1, 2)
) -> np.ndarray:
    """Pixels ETH3D masks out with one of `labels`, in the undistorted image resized to width x height.

    ETH3D's masks (`masks_for_images/dslr_images/*.png`, value 1 for glass, 2 for objects missing
    from the scan such as trees, trams and people) are drawn on the original distorted images, whose
    camera model is not in these archives. The undistorted image keeps the distorted image's whole
    field of view (content reaches every border), so the mask is stretched to fill the frame: exact
    at the centre and at the borders. In between, lens distortion displaces it by up to about 13
    pixels at 1024 wide, which the dilation covers.
    """
    path = view.image_path.parents[2] / "masks_for_images" / "dslr_images" / f"{view.name}.png"
    if not path.exists():
        # ETH3D ships no mask for an image with nothing to exclude.
        return np.zeros((height, width), bool)
    mask = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise ValueError(f"{path}: unreadable mask")
    out = cv2.resize(
        np.isin(mask, labels).astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST
    )
    if dilate:
        out = cv2.dilate(out, np.ones((2 * dilate + 1, 2 * dilate + 1), np.uint8))
    return out.astype(bool)


@dataclass(frozen=True)
class VisiblePoints:
    index: np.ndarray  # (N,) indices into the evaluation subset of scan points
    uv: np.ndarray  # (N, 2) pixel coordinates in the resized image, OpenCV integer-centred
    edge: np.ndarray  # (N,) True on the near side of a depth edge (window, equipment, fence edges)


def visible_scan_points(
    blockers: np.ndarray,
    candidates: np.ndarray,
    view: View,
    width: int,
    height: int,
    excluded: np.ndarray | None = None,
    zbuffer_scale: float = 0.5,
    window: int = 5,
    tolerance: float = 0.04,
    edge_range: float = 0.10,
) -> VisiblePoints:
    """Which `candidates` (scan points used for scoring) lie on the visible surface of a view.

    Visibility: every blocker (all scan points plus occluders) is splatted into a z-buffer at
    `zbuffer_scale` of the resized image, keeping the nearest depth per cell; holes are closed with
    a `window` x `window` minimum filter; a candidate is kept when its depth is within `tolerance`
    (relative) of the filtered buffer, so points behind a wall seen through a gap in the splat fail.
    Kept candidates where the buffer's depth varies by more than `edge_range` (relative) inside the
    window are flagged `edge`: they sit on the near side of a depth edge, where a clearance is
    usually measured from, and where one pixel can mix two surfaces. Candidates on `excluded`
    pixels are dropped.
    """
    K = view.scaled_K(width, height)
    zw, zh = max(1, round(width * zbuffer_scale)), max(1, round(height * zbuffer_scale))

    def project(pts: np.ndarray):
        Xc = pts @ view.R_wc.T.astype(pts.dtype) + view.t_wc.astype(pts.dtype)
        z = Xc[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = K[0, 0] * Xc[:, 0] / z + K[0, 2]
            v = K[1, 1] * Xc[:, 1] / z + K[1, 2]
        ok = (z > 0.1) & (u >= 0) & (u <= width - 1) & (v >= 0) & (v <= height - 1)
        return u, v, z, ok

    u, v, z, ok = project(blockers)
    u, v, z = u[ok], v[ok], z[ok]
    cu = np.clip(((u + 0.5) * zbuffer_scale).astype(np.int64), 0, zw - 1)
    cv_ = np.clip(((v + 0.5) * zbuffer_scale).astype(np.int64), 0, zh - 1)
    zbuf = np.full((zh, zw), np.inf, dtype=np.float32)
    np.minimum.at(zbuf, (cv_, cu), z.astype(np.float32))
    kernel = np.ones((window, window), np.uint8)
    zmin = cv2.erode(np.where(np.isfinite(zbuf), zbuf, np.float32(1e6)), kernel)
    zmax = cv2.dilate(np.where(np.isfinite(zbuf), zbuf, np.float32(0)), kernel)

    u, v, z, ok = project(candidates)
    idx = np.flatnonzero(ok)
    u, v, z = u[ok], v[ok], z[ok]
    cu = np.clip(((u + 0.5) * zbuffer_scale).astype(np.int64), 0, zw - 1)
    cv_ = np.clip(((v + 0.5) * zbuffer_scale).astype(np.int64), 0, zh - 1)
    near = zmin[cv_, cu]
    keep = z <= near * (1 + tolerance)
    if excluded is not None:
        keep &= ~excluded[np.round(v).astype(int), np.round(u).astype(int)]
    edge = (zmax[cv_, cu] - near) > edge_range * near
    return VisiblePoints(
        index=idx[keep].astype(np.int64), uv=np.c_[u[keep], v[keep]], edge=edge[keep]
    )


def point_normals(points: np.ndarray, k: int = 12, chunk: int = 200_000) -> np.ndarray:
    """Unit surface normal per point from the smallest principal axis of its k nearest neighbours
    (sign arbitrary). Chunked so memory stays near a few hundred MB for 1.5 M points."""
    tree = cKDTree(points)
    normals = np.empty_like(points, dtype=np.float64)
    for start in range(0, len(points), chunk):
        block = points[start : start + chunk].astype(np.float64)
        _, nn = tree.query(block, k=k)
        nbrs = points[nn].astype(np.float64)
        centred = nbrs - nbrs.mean(axis=1, keepdims=True)
        cov = np.einsum("nki,nkj->nij", centred, centred)
        _, vecs = np.linalg.eigh(cov)
        normals[start : start + chunk] = vecs[:, :, 0]
    return normals


def up_direction(views: list[View]) -> np.ndarray:
    """World up, as the mean of the cameras' up axes: DSLR photos are taken held level."""
    ups = np.array([v.R_wc.T @ np.array([0.0, -1.0, 0.0]) for v in views])
    up = ups.mean(axis=0)
    return up / np.linalg.norm(up)


def resized_image(view: View, width: int) -> tuple[np.ndarray, int, int]:
    """The view's image resized to `width` (height by aspect), cached as JPEG next to the scene."""
    height = round(view.height * width / view.width)
    cache = view.image_path.parents[2] / f"images_{width}" / f"{view.name}.jpg"
    if cache.exists():
        img = cv2.imread(str(cache))
    else:
        full = cv2.imread(str(view.image_path))
        if full is None:
            raise FileNotFoundError(view.image_path)
        if full.shape[:2] != (view.height, view.width):
            raise ValueError(
                f"{view.image_path}: {full.shape[:2]} != calibration {view.height}x{view.width}"
            )
        img = cv2.resize(full, (width, height), interpolation=cv2.INTER_AREA)
        cache.parent.mkdir(exist_ok=True)
        cv2.imwrite(str(cache), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return img, width, height
