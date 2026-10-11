"""Learned-depth rows and the AR scale error for one Measure Lab field session.

    uv run python -m evals.field prepare SESSION.zip           # upright keyframes for MoGe-2
    (MoGe-2 on those keyframes: see the Makefile target `field`)
    uv run python -m evals.field score SESSION.zip [--truth survey.json --map map.json --rules rules.json]

Every point the rig made from taps (ground, wall and two-view points) gets a second position from
learned depth: MoGe-2's depth at the tapped pixel of the tapped keyframe, back-projected with that
keyframe's AR pose. Walls and measurement values are then recomputed exactly as Measure Lab
computes them (`Wall.swift`, `Measurements.swift`), so each survey measurement the team's map ties
to a session measurement gets a learned-depth value for the same quantity. Three rows:

- `moge2`: MoGe-2's own metric scale.
- `moge2-triangulated`: each keyframe's depth rescaled to features triangulated with the session's
  AR poses across it and its 7 nearest keyframes (`evals.triangulate`, method (b) of
  `evals.pose_priors`).
- `moge2-tape`: one scale for the whole session, making the survey's scale reference come out at
  its taped length (only if the map ties the scale reference to a session measurement).

Rows are written as scoring-harness results files (experiments/scoring/README.md on
t3/scoring-harness). They state no uncertainty (`plus_minus_ft` null) because no error bar for them
has been validated, so they make no decisions. The AR scale error is the rig's own values against
the tape, over spans of at least 10 ft, where scale dominates tapping error.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import re
import shutil
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import cv2
import numpy as np

from evals.pairs import scale_for_length
from evals.paths import EVALS_DIR
from evals.recon import camera_points, load_prediction
from evals.triangulate import view_scales

FEET = 0.3048
UP = np.array([0.0, 1.0, 0.0])
ARKIT_TO_OPENCV = np.diag([1.0, -1.0, -1.0])
NEIGHBOURS = 7
SCALE_SPAN_MIN_FT = 10.0
# The scale needs one long span: 2 in of tapping error is 1.7% of 10 ft but 0.6% of 30 ft. A nominal
# 30 ft span taped a little short still counts.
REQUIRED_SPAN_FT = 29.0
TAP_ERROR_IN = 2.0  # assumed per-span tapping error until the field test measures it
SCALE_KEYS = ("straight", "horizontal", "alongWall")
FIELD_DIR = EVALS_DIR / "field"


# --- Session ---------------------------------------------------------------------------------


# Bounds on a session zip before anything is written. A real session is a few hundred files and
# well under 1 GB (the ADVIO replay: 81 files, 17 MB); these leave room for long LiDAR sessions while
# stopping a crafted archive from filling the shared disk.
MAX_ZIP_MEMBERS = 10_000
MAX_ZIP_BYTES = 2 * 1024**3
# The archive itself, checked before it is hashed or opened, and every central-directory entry
# (folders and __MACOSX included), so neither the file nor its index can be arbitrarily large.
MAX_ZIP_ARCHIVE_BYTES = 2 * 1024**3
MAX_ZIP_ENTRIES = 2 * MAX_ZIP_MEMBERS
# The central directory ZipFile reads whole and parses into one object per entry. 20,000 entries
# with 100-byte names take about 3 MB; the cap also bounds entries with minimal 46-byte records.
MAX_ZIP_DIRECTORY_BYTES = 8 * 1024**2
MIN_FREE_AFTER_UNPACK = 3 * 1024**3
# Pixels in one session image, read from its header before OpenCV decodes it. The zip limits count
# encoded bytes, and a few-KB JPEG can declare 65,535 x 65,535 pixels (13 GB decoded in colour).
# ARKit keyframes are 1920 x 1440 (2.8 MP) and a 48 MP iPhone photo is 8064 x 6048 (48.8 MP), so
# 50 MP admits both, and decoding it in colour takes about 150 MB of the 4 GB per-process budget.
MAX_IMAGE_PIXELS = 50_000_000
# Pixels held at once while fitting scales. Triangulation needs a keyframe and its NEIGHBOURS nearest
# together, so only that group's grayscale image (1 byte per pixel) and depth (4) are kept, about
# 5 bytes a pixel whatever the session's length. 200 MP is then about 1 GB, leaving the rest of the
# 4 GB per-process budget for feature matching. Eight ARKit keyframes are 22 MP; eight 48 MP photos
# are 390 MP and are refused from their headers before any is decoded.
MAX_GROUP_PIXELS = 200_000_000


def safe_member(name: str) -> PurePosixPath:
    """A zip member name as a relative path with no way out of the extraction root."""
    path = PurePosixPath(name)
    parts = [p for p in path.parts if p not in ("", ".")]
    if (
        not parts
        or path.is_absolute()
        or "\\" in name
        or ":" in parts[0]
        or any(p == ".." for p in parts)
    ):
        raise ValueError(f"zip member {name!r} is not a plain relative path")
    return PurePosixPath(*parts)


def file_sha256(path: Path) -> str:
    """sha256 of a file, read 1 MB at a time so a large file is never held in memory."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def check_zip_directory(session: Path) -> None:
    """Refuse a zip whose central directory is too large or lists too many entries, reading only
    its end record. `zipfile.ZipFile` reads the whole directory and builds every entry while it is
    constructed, so a later count comes too late. The end record is read by `zipfile._EndRecData`,
    the function ZipFile itself uses (ZIP64 included), so the sizes checked here are the ones it
    will read."""
    with session.open("rb") as f:
        try:
            end = zipfile._EndRecData(f)
        except OSError as e:
            raise ValueError(f"{session}: not a zip file ({e})") from e
    if not end:
        raise ValueError(f"{session}: not a zip file (no end-of-central-directory record)")
    entries, directory_bytes = end[zipfile._ECD_ENTRIES_TOTAL], end[zipfile._ECD_SIZE]
    if entries > MAX_ZIP_ENTRIES:
        raise ValueError(f"{session}: {entries} zip entries, more than {MAX_ZIP_ENTRIES}")
    if directory_bytes > MAX_ZIP_DIRECTORY_BYTES:
        raise ValueError(
            f"{session}: {directory_bytes}-byte zip directory, more than {MAX_ZIP_DIRECTORY_BYTES}"
        )


def unpack(session: Path) -> tuple[Path, str | None]:
    """The session folder, and the zip's sha256 (the scoring harness's capture id) if zipped.

    Every member is checked before anything is written: a plain relative path, no duplicates, at
    most MAX_ZIP_MEMBERS files and MAX_ZIP_BYTES in all, with MIN_FREE_AFTER_UNPACK left free. Files
    are copied with a running byte count, so a member whose header understates its size still
    stops at the bound. Extraction goes to a temporary folder renamed into place only when complete.
    """
    if session.is_dir():
        return session, None
    size = session.stat().st_size
    if size > MAX_ZIP_ARCHIVE_BYTES:
        raise ValueError(f"{session}: {size} bytes, more than {MAX_ZIP_ARCHIVE_BYTES}")
    check_zip_directory(session)
    digest = file_sha256(session)
    out = FIELD_DIR / digest[:16]
    with zipfile.ZipFile(session) as z:
        infos = z.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise ValueError(f"{session}: {len(infos)} zip entries, more than {MAX_ZIP_ENTRIES}")
        members, seen = [], set()
        for info in infos:
            if info.filename.startswith("__MACOSX/") or info.is_dir():
                continue
            rel = safe_member(info.filename)
            if rel in seen:
                raise ValueError(f"{session}: {rel} appears twice")
            seen.add(rel)
            members.append((info, rel))
        if len(members) > MAX_ZIP_MEMBERS:
            raise ValueError(f"{session}: {len(members)} files, more than {MAX_ZIP_MEMBERS}")
        declared = sum(info.file_size for info, _ in members)
        if declared > MAX_ZIP_BYTES:
            raise ValueError(f"{session}: expands to {declared} bytes, more than {MAX_ZIP_BYTES}")
        roots = [rel for _, rel in members if rel.name == "session.json" and len(rel.parts) <= 2]
        if len(roots) != 1:
            raise ValueError(f"{session}: expected one session.json, found {roots}")
        root = out / roots[0]
        if not root.exists():
            FIELD_DIR.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(FIELD_DIR).free
            if free - declared < MIN_FREE_AFTER_UNPACK:
                raise ValueError(
                    f"{session}: unpacking {declared / 1e9:.2f} GB would leave less than "
                    f"{MIN_FREE_AFTER_UNPACK / 1024**3:.0f} GB free ({free / 1e9:.1f} GB free now)"
                )
            tmp = out.with_name(out.name + ".partial")
            shutil.rmtree(tmp, ignore_errors=True)
            try:
                written = 0
                for info, rel in members:
                    dest = tmp.joinpath(*rel.parts)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, dest.open("wb") as dst:
                        while block := src.read(1 << 20):
                            written += len(block)
                            if written > MAX_ZIP_BYTES:
                                raise ValueError(f"{session}: expands past {MAX_ZIP_BYTES} bytes")
                            dst.write(block)
                shutil.rmtree(out, ignore_errors=True)
                tmp.rename(out)
            except BaseException:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
    folder = root.parent.resolve()
    if not folder.is_relative_to(out.resolve()):
        raise ValueError(f"{session}: session folder {folder} is outside {out}")
    return folder, digest


def load_session(folder: Path) -> dict:
    data = json.loads((folder / "session.json").read_text())
    if data.get("format") != "measure-lab-session" or data.get("formatVersion") != 2:
        raise ValueError(f"{folder}: not a Measure Lab session, format 2")
    return data


def keyframe_pose_cv(kf: dict) -> np.ndarray:
    """Camera-to-world with OpenCV camera axes, from the session's ARKit pose (column-major)."""
    T = np.array(kf["pose"], dtype=np.float64).reshape(4, 4).T
    T[:3, :3] = T[:3, :3] @ ARKIT_TO_OPENCV
    return T


def keyframe_K_cv(kf: dict) -> np.ndarray:
    """Intrinsics with OpenCV's integer pixel centres (the session's are continuous)."""
    fx, fy, cx, cy = kf["intrinsics"]
    return np.array([[fx, 0, cx - 0.5], [0, fy, cy - 0.5], [0, 0, 1.0]])


# --- Upright images for the model ------------------------------------------------------------


def upright_turns(T_cv: np.ndarray) -> int:
    """np.rot90 turns (counter-clockwise) that put world up at the top of this keyframe's image.

    Session images are unrotated sensor images, sideways for a phone held upright, and depth
    models are trained on upright photos."""
    up = T_cv[:3, :3].T @ UP  # world up in OpenCV camera axes; image "up" is -y
    x, y = up[0], -up[1]
    if abs(y) >= abs(x):
        return 0 if y > 0 else 2
    return 1 if x > 0 else 3


def rotated_K(K: np.ndarray, w: int, h: int, turns: int) -> np.ndarray:
    """Intrinsics of the image turned by np.rot90(image, turns), for a w x h original."""
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    new = {
        0: (fx, fy, cx, cy),
        1: (fy, fx, cy, w - 1 - cx),
        2: (fx, fy, w - 1 - cx, h - 1 - cy),
        3: (fy, fx, h - 1 - cy, cx),
    }[turns % 4]
    return np.array([[new[0], 0, new[2]], [0, new[1], new[3]], [0, 0, 1.0]])


SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def safe_id(value: str, what: str) -> str:
    """An id from the session that becomes a file name: one plain path component, never an
    absolute path or `..`, so a crafted session cannot write outside the work folder."""
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value) or value in (".", ".."):
        raise ValueError(f"{what} {value!r} is not a plain name (letters, digits, . _ -)")
    return value


# JPEG start-of-frame markers carry the image size; C4 (DHT), C8 (JPG) and CC (DAC) share the range
# but are not frames.
_JPEG_SOF = set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def image_size(path: Path) -> tuple[int, int]:
    """(width, height) from a JPEG or PNG header, reading no pixel data. Other formats are
    refused: sessions carry JPEG keyframes."""
    with path.open("rb") as f:
        head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
            return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
        if head[:2] != b"\xff\xd8":
            raise ValueError(f"{path}: not a JPEG or PNG image")
        f.seek(2)
        while True:
            byte = f.read(1)
            if not byte:
                break
            if byte != b"\xff":
                continue
            marker = f.read(1)
            while marker == b"\xff":  # fill bytes before a marker
                marker = f.read(1)
            if not marker:
                break
            m = marker[0]
            if m in (0x01, *range(0xD0, 0xD8)):  # markers with no length field
                continue
            if m in (0xD9, 0xDA):  # end of image, or start of scan before any frame header
                break
            length = int.from_bytes(f.read(2), "big")
            if m in _JPEG_SOF:
                frame = f.read(5)
                if len(frame) == 5:
                    return int.from_bytes(frame[3:5], "big"), int.from_bytes(frame[1:3], "big")
                break
            if length < 2:
                break
            f.seek(length - 2, 1)
    raise ValueError(f"{path}: JPEG with no readable frame header")


def read_session_image(path: Path, flags: int = cv2.IMREAD_COLOR) -> np.ndarray:
    """Decode a session image after checking its header size against MAX_IMAGE_PIXELS."""
    w, h = image_size(path)
    if w <= 0 or h <= 0 or w * h > MAX_IMAGE_PIXELS:
        raise ValueError(f"{path}: {w} x {h} pixels, more than {MAX_IMAGE_PIXELS} or empty")
    img = cv2.imread(str(path), flags)
    if img is None:
        raise ValueError(f"{path}: OpenCV could not decode it")
    return img


def session_file(folder: Path, relative: str) -> Path:
    """A file the session names, which must lie inside the session folder: an absolute path or
    `..` in session.json would otherwise read an unrelated local photo."""
    root = folder.resolve()
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root):
        raise ValueError(f"{folder}: session names {relative!r}, outside the session folder")
    return path


def capture_id(folder: Path, digest: str | None) -> str:
    """The zip's sha256 (the scoring harness's capture id), or for a folder a sha256 over
    session.json and every keyframe image, so predictions can be tied to the exact capture."""
    if digest is not None:
        return digest
    h = hashlib.sha256((folder / "session.json").read_bytes())
    for kf in sorted(load_session(folder)["keyframes"], key=lambda k: k["id"]):
        with session_file(folder, kf["img"]).open("rb") as f:
            while block := f.read(1 << 20):
                h.update(block)
    return h.hexdigest()


def work_dir(folder: Path) -> Path:
    return FIELD_DIR / "work" / safe_id(load_session(folder)["session"]["id"], "session id")


def prepare(folder: Path, digest: str | None = None) -> Path:
    """Upright copies of every keyframe plus the model runner's image list and intrinsics, in a
    work folder tied to this exact capture: a different capture with the same session id clears
    it, so no earlier prediction survives under the new capture's name."""
    session = load_session(folder)
    out = work_dir(folder)
    capture = capture_id(folder, digest)
    stamp = out / "capture.json"
    if out.exists() and (not stamp.exists() or json.loads(stamp.read_text())["capture"] != capture):
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    stamp.write_text(json.dumps({"capture": capture}) + "\n")
    (out / "upright").mkdir(parents=True, exist_ok=True)
    listing, intrinsics, turns = [], {}, {}
    for kf in session["keyframes"]:
        img = read_session_image(session_file(folder, kf["img"]))
        k = upright_turns(keyframe_pose_cv(kf))
        path = out / "upright" / f"{safe_id(kf['id'], 'keyframe id')}.jpg"
        cv2.imwrite(str(path), np.rot90(img, k), [cv2.IMWRITE_JPEG_QUALITY, 95])
        Kr = rotated_K(keyframe_K_cv(kf), img.shape[1], img.shape[0], k)
        listing.append(str(path))
        intrinsics[str(path)] = [Kr[0, 0], Kr[1, 1], Kr[0, 2], Kr[1, 2]]
        turns[kf["id"]] = k
    (out / "images.txt").write_text("\n".join(listing) + "\n")
    (out / "intrinsics.json").write_text(json.dumps(intrinsics, indent=1))
    (out / "turns.json").write_text(json.dumps(turns, indent=1))
    return out


def keyframe_depth(out: Path, kid: str, turns: int) -> np.ndarray:
    """MoGe-2's depth for a keyframe, turned back to the session's sensor orientation."""
    depth, _, _ = load_prediction(out / "moge2" / f"{kid}.npz")
    return np.rot90(depth, -turns)


# --- Scale per keyframe ------------------------------------------------------------------------


def neighbours(poses: dict[str, np.ndarray], kid: str, n: int = NEIGHBOURS) -> list[str]:
    """The keyframe and its n nearest keyframes looking the same way (within 60 degrees)."""
    T = poses[kid]
    fwd = T[:3, 2]
    near = [k for k in poses if k != kid and poses[k][:3, 2] @ fwd > 0.5]
    near.sort(key=lambda k: np.linalg.norm(poses[k][:3, 3] - T[:3, 3]))
    return [kid, *near[:n]]


def triangulated_scales(folder: Path, out: Path, session: dict, kids: list[str]) -> dict:
    """{keyframe: ScaleFit} for the given keyframes, each fitted within its neighbourhood."""
    kfs = {kf["id"]: kf for kf in session["keyframes"]}
    turns = json.loads((out / "turns.json").read_text())
    poses = {k: keyframe_pose_cv(kf) for k, kf in kfs.items()}

    def load(k):
        gray = read_session_image(session_file(folder, kfs[k]["img"]), cv2.IMREAD_GRAYSCALE)
        return gray, keyframe_K_cv(kfs[k]), keyframe_depth(out, k, turns[k])

    def pixels(k):
        w, h = image_size(session_file(folder, kfs[k]["img"]))
        return w * h

    frames = GroupFrames(load, pixels)
    fits = {}
    for kid in kids:
        group = neighbours(poses, kid)
        if len(group) < 2:
            continue
        held = frames.hold(group)
        fits[kid] = view_scales(
            {k: held[k][0] for k in group},
            {k: held[k][1] for k in group},
            {k: poses[k] for k in group},
            {k: held[k][2] for k in group},
        )[kid]
    return fits


class GroupFrames:
    """The decoded inputs of one neighbour group at a time. Frames outside the requested group are
    dropped before new ones load, so memory follows the group, not the session; a group over
    MAX_GROUP_PIXELS is refused from its image headers before anything in it is decoded."""

    def __init__(self, load, pixels):
        self.load, self.pixels = load, pixels
        self.held: dict[str, tuple] = {}

    def hold(self, group: list[str]) -> dict[str, tuple]:
        total = sum(self.pixels(k) for k in group)
        if total > MAX_GROUP_PIXELS:
            raise ValueError(
                f"keyframes {group} hold {total} pixels together, more than {MAX_GROUP_PIXELS}"
            )
        for k in [k for k in self.held if k not in group]:
            del self.held[k]
        for k in group:
            if k not in self.held:
                self.held[k] = self.load(k)
        return self.held


# --- Points, walls, values (Measure Lab's definitions) ------------------------------------------


def tap_ray(kf: dict, pixel: list[float], depth: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A tapped pixel as (camera centre, offset to the surface) in the world, from depth; the
    offset is NaN if the depth there is invalid. Scaling depth by s moves the point to c + s r."""
    uv = np.array([[pixel[0] - 0.5, pixel[1] - 0.5]])  # continuous -> OpenCV pixel centres
    pc = camera_points(depth, keyframe_K_cv(kf), uv)[0]
    T = keyframe_pose_cv(kf)
    return T[:3, 3], T[:3, :3] @ pc


def learned_points(session: dict, depth_of, scale_of) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """{point id: (centre, offset)}, each the mean over the point's taps, with each tap's depth
    scaled by `scale_of(keyframe)` (None: no scale, the point is NaN)."""
    kfs = {kf["id"]: kf for kf in session["keyframes"]}
    taps = {t["id"]: t for t in session["taps"]}
    out = {}
    for p in session["points"]:
        centres, offsets = [], []
        for tid in p["taps"]:
            tap = taps[tid]
            s = scale_of(tap["keyframe"])
            c, r = tap_ray(kfs[tap["keyframe"]], tap["pixel"], depth_of(tap["keyframe"]))
            centres.append(c)
            offsets.append(r * s if s is not None else np.full(3, np.nan))
        out[p["id"]] = (np.mean(centres, axis=0), np.mean(offsets, axis=0))
    return out


def positions(points: dict, s: float = 1.0) -> dict[str, np.ndarray]:
    """Point positions with every depth scaled by s: centre + s offset."""
    return {pid: c + s * r for pid, (c, r) in points.items()}


def tape_scale(session: dict, points: dict, measurement_id: str, key: str, meters: float) -> float:
    """The one depth scale that makes the scale reference come out at its taped length.

    Scaling depth moves each point along its ray from its own keyframe's camera, and camera
    positions do not scale, so this solves |dc + s dr| = tape rather than dividing lengths (exact
    only when both taps share a keyframe)."""
    m = {x["id"]: x for x in session["measurements"]}[measurement_id]
    if key != "straight" or m["to"] not in points:
        raise ValueError(
            f"scale reference {measurement_id} must be a point-to-point straight distance "
            f"(got {key} to {m['to']}); measure the two tape marks with Measure, straight"
        )
    (ca, ra), (cb, rb) = points[m["from"]], points[m["to"]]
    return scale_for_length(cb - ca, rb - ra, meters)


def wall_frame(c1: np.ndarray, c2: np.ndarray, camera: np.ndarray) -> dict:
    """Measure Lab's wall: direction u (horizontal c1 -> c2), normal u x up toward the camera."""
    run = c2 - c1
    run[1] = 0.0
    length = float(np.linalg.norm(run))
    u = run / length
    n = np.cross(u, UP)
    if n @ (camera - c1) < 0:
        n = -n
    return {"start": c1, "end": c2, "u": u, "n": n, "length": length}


def values(a: np.ndarray, target, reference: dict | None) -> dict[str, float]:
    """Measure Lab's `measuredValues`: point to point, or point to wall."""
    if isinstance(target, dict):
        w = target
        along = w["u"] @ (a - w["start"])
        ground = w["start"][1] + (w["end"][1] - w["start"][1]) * along / w["length"]
        return {"gapToWall": abs(w["n"] @ (a - w["start"])), "heightAboveGround": a[1] - ground}
    d = target - a
    out = {
        "straight": float(np.linalg.norm(d)),
        "horizontal": float(np.hypot(d[0], d[2])),
        "vertical": abs(float(d[1])),
    }
    if reference is not None:
        out["alongWall"] = abs(float(reference["u"] @ d))
    return out


def learned_values(session: dict, points: dict[str, np.ndarray]) -> dict[str, dict[str, float]]:
    """{session measurement id: values} recomputed from learned-depth points."""
    walls = {}
    for w in session["walls"]:
        c1, c2 = (points[c] for c in w["contacts"][:2])
        walls[w["id"]] = wall_frame(c1, c2, np.array(w["cameraPosition"], dtype=np.float64))
    out = {}
    for m in session["measurements"]:
        target = walls[m["to"]] if m["to"] in walls else points[m["to"]]
        reference = walls.get(m.get("referenceWall")) if m.get("referenceWall") else None
        out[m["id"]] = values(points[m["from"]], target, reference)
    return out


# --- Scoring-harness files --------------------------------------------------------------------


def feet(meters: float) -> float:
    return round(meters / FEET, 6)


def results_file(
    pipeline: str,
    scale_source: str,
    capture: str,
    rules_sha256: str,
    truth: dict,
    mapping: dict,
    recomputed: dict[str, dict[str, float]],
    capture_s: float | None,
    processing_s: float,
) -> dict:
    rows = []
    for m in truth["measurements"]:
        if m["id"] == truth["scale_reference"]:
            continue  # optional in a results file, and never scored
        entry = mapping["measurements"].get(m["id"])
        if entry in ("absent", "unsupported"):
            rows.append({"id": m["id"], "value_ft": None, "missing": entry})
        elif not isinstance(entry, dict) or "session_measurement" not in entry:
            # A rig refusal, or no entry: nothing this pipeline can recompute.
            rows.append({"id": m["id"], "value_ft": None, "missing": "unsupported"})
        else:
            v = recomputed[entry["session_measurement"]].get(entry["key"], np.nan)
            if not np.isfinite(v) or v < 0:
                rows.append({"id": m["id"], "value_ft": None, "missing": "failed"})
            else:
                rows.append({"id": m["id"], "value_ft": feet(v), "plus_minus_ft": None})
    return {
        "format": 1,
        "unit": "ft",
        "pipeline": pipeline,
        "capture": capture,
        "rules_sha256": rules_sha256,
        "scale_source": scale_source,
        "measurements": rows,
        "outcomes": None,
        "timing": {"capture_s": capture_s, "processing_s": round(processing_s, 3)},
    }


def capture_seconds(session: dict) -> float | None:
    """As the harness's importer computes it, so rows on one capture agree."""
    times = [m["time"] for m in session["measurements"]]
    if not times:
        return None
    return round(max(times) - session["session"]["startedAtUptime"], 3)


@dataclass(frozen=True)
class ScaleEstimate:
    scale: float  # AR / tape
    bound: float  # 95% half-width on `scale`
    spans: int
    longest_ft: float


def fit_scale(ar_ft: np.ndarray, tape_ft: np.ndarray, sigma_ft: np.ndarray) -> ScaleEstimate:
    """Weighted least squares for ar = scale * tape with independent errors sigma per span:
    scale = sum(w a t) / sum(w t^2), w = 1 / sigma^2, variance 1 / sum(w t^2). Longer spans weigh
    more, because a tapping error is about the same size on any span."""
    w = 1 / sigma_ft**2
    scale = float(np.sum(w * ar_ft * tape_ft) / np.sum(w * tape_ft**2))
    bound = float(1.96 / np.sqrt(np.sum(w * tape_ft**2)))
    return ScaleEstimate(scale, bound, len(tape_ft), float(tape_ft.max()))


def within(error_pct: float, bound_pct: float, limit_pct: float) -> str:
    """The strict rule: yes only when the error plus its bound is inside the limit."""
    if abs(error_pct) + bound_pct < limit_pct:
        return "yes"
    if abs(error_pct) - bound_pct > limit_pct:
        return "no"
    return "cannot tell"


def ar_scale_report(session: dict, truth: dict, mapping: dict, tap_error_in: float) -> list[str]:
    """The rig's values against the tape, and the AR scale error with its bound.

    Only measurements the rig accepted count (as in the scoring harness's importer). Each span's
    error is the tape's own plus-minus and an assumed tapping error of `tap_error_in` per span, in
    quadrature; the tapping error is an assumption until the field test measures it.
    """
    rig = {m["id"]: m for m in session["measurements"]}
    tape = {m["id"]: m for m in truth["measurements"] if m["status"] == "measured"}
    lines = [
        "| Survey measurement | Key | Tape (ft) | AR (ft) | AR / tape | 95% bound | Used for scale |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    ar, tp, sig = [], [], []
    for sid, entry in mapping["measurements"].items():
        if not isinstance(entry, dict) or "session_measurement" not in entry or sid not in tape:
            continue
        m = rig[entry["session_measurement"]]
        t = tape[sid]["value_ft"]
        if not m["accepted"]:
            lines.append(
                f"| {sid} | {entry['key']} | {t:.3f} | | | | no: the rig did not accept it |"
            )
            continue
        a = m["values"][entry["key"]] / FEET
        sigma = float(np.hypot(tap_error_in / 12, tape[sid]["plus_minus_ft"]))
        use = entry["key"] in SCALE_KEYS and t >= SCALE_SPAN_MIN_FT
        reason = "yes" if use else f"no: not a straight span of {SCALE_SPAN_MIN_FT:g} ft or more"
        # A zero tape value (the return-to-reference gap) has no ratio.
        ratio = f"{a / t:.4f} | ±{1.96 * sigma / t:.4f}" if t > 0 else " | "
        lines.append(f"| {sid} | {entry['key']} | {t:.3f} | {a:.3f} | {ratio} | {reason} |")
        if use:
            ar.append(a)
            tp.append(t)
            sig.append(sigma)
    lines.append("")
    if not tp or max(tp) < REQUIRED_SPAN_FT:
        return [
            *lines,
            f"AR scale error: not resolved. It needs an accepted span of about 30 ft "
            f"(at least {REQUIRED_SPAN_FT:g} ft); see the field checklist.",
        ]
    est = fit_scale(np.array(ar), np.array(tp), np.array(sig))
    err, bound = 100 * (est.scale - 1), 100 * est.bound
    return [
        *lines,
        f"AR scale error: {err:+.2f}% ± {bound:.2f}% (95%, {est.spans} spans, longest "
        f"{est.longest_ft:.1f} ft, weighted by length; tapping error assumed {tap_error_in:g} in "
        f"per span). Within 2%: {within(err, bound, 2.0)}.",
    ]


# --- Stamping the team's survey and map for one session ------------------------------------


TAPE_FORMAT = re.compile(r"(\d+(?:\.\d+)?)(?:\s+(\d+(?:\.\d+)?))?(?:\s+(\d+)/(\d+))?")


def parse_tape(text: str) -> float:
    """A tape reading as written on the sheet: feet, then optional whole inches, then an optional
    fraction of an inch ("30 2 1/4", "4 11", "12"), in feet rounded to a millionth. Anything else,
    including a fraction that is not last, is refused rather than partly read."""
    m = TAPE_FORMAT.fullmatch(text.strip())
    if not m:
        raise ValueError(f"tape reading {text!r}: write feet, inches, fraction, e.g. '30 2 1/4'")
    feet = float(m[1])
    inches = float(m[2]) if m[2] else 0.0
    if m[3]:
        if int(m[4]) == 0:
            raise ValueError(f"tape reading {text!r}: a fraction cannot have 0 below the line")
        inches += int(m[3]) / int(m[4])
    if inches >= 12:
        raise ValueError(f"tape reading {text!r}: inches must be under 12")
    return round(feet + inches / 12, 6)


# The session field of field/map.template.json before `stamp` binds it to a session.
MAP_SESSION_PLACEHOLDER = "filled in by make field"


def stamp(session_path: Path, truth_path: Path, map_path: Path, out_dir: Path) -> Path:
    """Makes the team's files match this session: tape readings typed as text become feet and the
    zip's sha256 joins the survey's captures (both in place), and a copy of the map naming this
    session goes to out_dir/inputs/map.json, which is returned. A map already bound to another
    session is refused before anything is written, since its M numbers belong to that walk. So is
    a survey that already lists another capture: the scoring harness's survey format has no
    session field, so the captures list is its binding, and a survey stamped for one walk would
    otherwise score another."""
    folder, capture = unpack(session_path)
    if capture is None:
        raise ValueError(
            f"{session_path}: pass the zip Measure Lab shared; its sha256 is the capture id"
        )
    session_id = load_session(folder)["session"]["id"]
    mapping = json.loads(map_path.read_text())
    if mapping.get("session") not in (MAP_SESSION_PLACEHOLDER, session_id):
        raise ValueError(
            f"{map_path} is the map for session {mapping.get('session')!r}, not {session_id!r}; "
            f"start from field/map.template.json for a new session"
        )
    survey = json.loads(truth_path.read_text())
    others = [c for c in survey["captures"] if c != capture]
    if others:
        raise ValueError(
            f"{truth_path} is already the survey for capture {others[0]}, not this session's "
            f"{capture}; copy field/survey.template.json for each session"
        )
    for m in survey["measurements"]:
        if m["status"] == "measured" and isinstance(m.get("value_ft"), str):
            if "FILL" in m["value_ft"]:
                raise ValueError(
                    f"{truth_path}: {m['id']} still says {m['value_ft']!r}; type the tape reading"
                )
            m["value_ft"] = parse_tape(m["value_ft"])
            print(f"{m['id']}: {m['value_ft']} ft", file=sys.stderr)
    if capture not in survey["captures"]:
        survey["captures"].append(capture)
        print(f"survey captures += {capture}", file=sys.stderr)
    truth_path.write_text(json.dumps(survey, indent=2) + "\n")
    mapping["session"] = session_id
    stamped = out_dir / "inputs" / "map.json"
    stamped.parent.mkdir(parents=True, exist_ok=True)
    stamped.write_text(json.dumps(mapping, indent=2) + "\n")
    return stamped


# --- Command ----------------------------------------------------------------------------------


ROW_FILES = ("moge2.json", "moge2-triangulated.json", "moge2-tape.json", "measure-lab.json")


def score(
    session_path: Path,
    truth_path,
    map_path,
    rules_path,
    out_dir: Path,
    tap_error_in: float = TAP_ERROR_IN,
) -> str:
    # First: a re-run, even one that stops early below, must not leave an earlier run's rows
    # for `score` to pick up -- nor its scored folder or report, which would dress the old
    # capture's results up as this run's. Everything derived goes together.
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ROW_FILES:
        (out_dir / name).unlink(missing_ok=True)
    (out_dir / "field_report.md").unlink(missing_ok=True)
    shutil.rmtree(out_dir / "scored", ignore_errors=True)
    folder, capture = unpack(session_path)
    session = load_session(folder)
    out = work_dir(folder)
    stamp = out / "capture.json"
    if not stamp.exists() or json.loads(stamp.read_text())["capture"] != capture_id(
        folder, capture
    ):
        raise ValueError(
            f"{out} was prepared for another capture (or not at all): run `prepare` on "
            f"{session_path} and the model again before scoring it"
        )
    turns = json.loads((out / "turns.json").read_text())
    started = time.perf_counter()

    tapped = sorted({t["keyframe"] for t in session["taps"]})
    kids = tapped or [kf["id"] for kf in session["keyframes"]]
    fits = triangulated_scales(folder, out, session, kids)

    # Taps read depth one keyframe at a time; keeping the last two bounds memory to two depth
    # maps however many keyframes were tapped.
    @functools.lru_cache(maxsize=2)
    def depth_of(k):
        return keyframe_depth(out, k, turns[k])

    tri = [f.scale for f in fits.values() if f.scale is not None]
    lines = [
        f"# Field session {session['session']['id']} (generated by `uv run python -m evals.field score`)",
        "",
        f"- Keyframes: {len(session['keyframes'])}; taps: {len(session['taps'])}; points: "
        f"{len(session['points'])}; walls: {len(session['walls'])}; measurements: "
        f"{len(session['measurements'])}.",
        f"- Triangulation scale fitted for {len(tri)} of {len(fits)} "
        f"{'tapped ' if tapped else ''}keyframes"
        + (
            f": MoGe-2 x {np.median(tri):.3f} median (range {min(tri):.3f} to {max(tri):.3f})."
            if tri
            else "."
        ),
    ]
    native = learned_points(session, depth_of, lambda k: 1.0)
    triangulated = learned_points(session, depth_of, lambda k: fits[k].scale if k in fits else None)
    rows = {
        "moge2": ("native_metric", learned_values(session, positions(native))),
        "moge2-triangulated": ("ar_poses", learned_values(session, positions(triangulated))),
    }
    missing = []
    if not session["measurements"]:
        missing.append(
            "measurements: the session has no taps or measurements, so no row has values"
        )
    if truth_path is None or map_path is None or rules_path is None:
        missing.append("a tape survey, its map and the rules file: no results files are written")
    if capture is None:
        missing.append("the session zip: its sha256 is the capture id the survey must list")
    if missing:
        lines += ["", "Missing:", *[f"- {m}" for m in missing]]
        return "\n".join(lines) + "\n"

    truth = json.loads(Path(truth_path).read_text())
    mapping = json.loads(Path(map_path).read_text())
    if mapping.get("session") != session["session"]["id"]:
        raise ValueError(
            f"{map_path}: map is for session {mapping.get('session')!r}, the capture is "
            f"{session['session']['id']!r}; run `stamp` first"
        )
    if capture not in truth.get("captures", []):
        raise ValueError(f"{truth_path}: survey does not list capture {capture}; run `stamp` first")
    rules_sha = hashlib.sha256(Path(rules_path).read_bytes()).hexdigest()
    ref = mapping["measurements"].get(truth["scale_reference"])
    tape = {m["id"]: m for m in truth["measurements"]}.get(truth["scale_reference"])
    if (
        isinstance(ref, dict)
        and "session_measurement" in ref
        and tape
        and tape["status"] == "measured"
    ):
        try:
            s = tape_scale(
                session, native, ref["session_measurement"], ref["key"], tape["value_ft"] * FEET
            )
        except ValueError as e:
            # The tape row fails on its own; the native and triangulated rows still stand.
            s = float("nan")
            lines.append(
                f"- `moge2-tape`: every value failed, because the scale reference did: {e}"
            )
        rows["moge2-tape"] = ("scale_reference", learned_values(session, positions(native, s)))
    else:
        lines.append(
            "- No `moge2-tape` row: the map does not tie the survey's scale reference to a "
            "session measurement."
        )
    processing = time.perf_counter() - started
    for name, (source, recomputed) in rows.items():
        doc = results_file(
            name,
            source,
            capture,
            rules_sha,
            truth,
            mapping,
            recomputed,
            capture_seconds(session),
            processing,
        )
        (out_dir / f"{name}.json").write_text(json.dumps(doc, indent=1) + "\n")
    lines += [
        "",
        "## AR scale error against the tape",
        "",
        *ar_scale_report(session, truth, mapping, tap_error_in),
    ]
    lines += ["", f"Results files: {', '.join(f'{n}.json' for n in rows)} in {out_dir}."]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("step", choices=["stamp", "prepare", "score"])
    ap.add_argument("session", type=Path, help="session zip shared from Measure Lab, or its folder")
    ap.add_argument("--truth", type=Path)
    ap.add_argument("--map", type=Path)
    ap.add_argument("--rules", type=Path)
    ap.add_argument("--out-dir", type=Path, help="default: a folder per session under the data dir")
    ap.add_argument("--tap-error-in", type=float, default=TAP_ERROR_IN)
    args = ap.parse_args()
    out_dir = args.out_dir or FIELD_DIR / "results" / args.session.stem
    if args.step == "stamp":
        print(stamp(args.session, args.truth, args.map, out_dir))
        return
    if args.step == "prepare":
        print(prepare(*unpack(args.session)))
        return
    report = score(args.session, args.truth, args.map, args.rules, out_dir, args.tap_error_in)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "field_report.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
