"""Reading, writing and validating scene / verdict / labels files and their assets."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from jsonschema import Draft202012Validator

from server.eval import SCHEMA_DIR
from server.eval.frame import apply, mat4_from_list

SCHEMAS = {
    "base-scene/v0": "scene.v0.schema.json",
    "base-verdict/v0": "verdict.v0.schema.json",
    "base-labels/v0": "labels.v0.schema.json",
}


def schema_errors(obj: dict) -> list[str]:
    name = SCHEMAS.get(obj.get("format"))
    if name is None:
        return [f"unknown format {obj.get('format')!r}"]
    schema = json.loads((SCHEMA_DIR / name).read_text())
    errs = sorted(Draft202012Validator(schema).iter_errors(obj), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message[:300]}" for e in errs]


def validate(obj: dict) -> None:
    errs = schema_errors(obj)
    if errs:
        raise ValueError(f"{obj.get('format')} invalid:\n  " + "\n  ".join(errs[:20]))
    if obj["format"] == "base-scene/v0":
        errs = scene_consistency_errors(obj)
        if errs:
            raise ValueError("scene inconsistent:\n  " + "\n  ".join(errs))
    if obj["format"] == "base-verdict/v0":
        errs = verdict_contract_errors(obj)
        if errs:
            raise ValueError("verdict breaks the coverage contract:\n  " + "\n  ".join(errs))


def verdict_contract_errors(verdict: dict) -> list[str]:
    """Contract section 3.1: pass or fail must rest on coverage evidence graded `observed`, with
    enough views and no grazing; everything else must be unsure (unobserved / grazing / ...)."""
    errs = []
    for c in verdict["criteria"]:
        if c["outcome"] == "unsure":
            continue
        ev = []
        for key in ("measured", "fact"):
            if c.get(key):
                ev += c[key]["coverage"]
        where = f"criteria[{c['id']}{'@' + c['candidate_id'] if c.get('candidate_id') else ''}]"
        if not ev:
            if c["outcome"] == "pass":
                errs.append(f"{where}: pass with no measurement or fact, so no coverage evidence")
            continue
        for e in ev:
            rule = e["rule"]
            bad = []
            if e["status"] != "observed":
                bad.append(f"status {e['status']}")
            vm = e.get("views_median", e.get("views_min", 0))
            if vm < rule["min_views"]:
                bad.append(f"views {vm} < {rule['min_views']}")
            wb, lim = e.get("best_incidence_deg"), rule.get("max_incidence_deg")
            if wb is not None and lim is not None and wb > lim:
                bad.append(f"viewing angle {wb} deg > {lim}")
            if bad:
                errs.append(f"{where}: {c['outcome']} on {e['region']}: {', '.join(bad)} (must be unsure)")
    return errs


def scene_consistency_errors(scene: dict) -> list[str]:
    """Cross-references the schema cannot express."""
    errs = []
    wall_ids = {w["id"] for w in scene["walls"]}
    obj_ids = [o["id"] for o in scene["objects"]]
    if len(set(obj_ids)) != len(obj_ids):
        errs.append("duplicate object ids")
    anchor = scene["frame"]["anchor"]["element_id"]
    meters = [o for o in scene["objects"] if o["id"] == anchor]
    if not meters or meters[0]["class"] != "meter":
        errs.append(f"frame.anchor.element_id {anchor!r} is not a meter object")
    for wid in scene["chain"]["wall_ids"]:
        if wid not in wall_ids:
            errs.append(f"chain names unknown wall {wid!r}")
    for o in scene["objects"]:
        g = o["geometry"]
        if g["kind"] == "wall_rect" and g["wall_id"] not in wall_ids:
            errs.append(f"object {o['id']} on unknown wall {g['wall_id']!r}")
    asset_ids = {a["id"] for a in scene.get("assets", [])}
    grid = scene["coverage"].get("grid")
    if grid and grid["asset"] not in asset_ids:
        errs.append(f"coverage grid asset {grid['asset']!r} not in assets")
    return errs


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def save_json(obj: dict, path: Path, check: bool = True) -> str:
    if check and "format" in obj:
        validate(obj)
    text = json.dumps(obj, indent=1) + "\n"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text)
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- PLY -------------------------------------------------------------------------------------

_PLY_TYPES = {"float": "<f4", "float32": "<f4", "double": "<f8", "uchar": "u1", "uint8": "u1",
              "int": "<i4", "int32": "<i4", "uint": "<u4", "short": "<i2", "ushort": "<u2"}


def write_ply(path: Path, xyz: np.ndarray, fields: dict[str, np.ndarray] | None = None) -> None:
    fields = fields or {}
    cols = [("x", "<f4"), ("y", "<f4"), ("z", "<f4")]
    names = {np.dtype("u1"): "uchar", np.dtype("<f4"): "float", np.dtype("<i4"): "int"}
    for k, v in fields.items():
        v = np.asarray(v)
        dt = np.dtype("u1") if v.dtype in (np.bool_, np.uint8) else np.dtype("<f4") if v.dtype.kind == "f" else np.dtype("<i4")
        cols.append((k, dt.str))
    arr = np.empty(len(xyz), dtype=cols)
    arr["x"], arr["y"], arr["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    for k, v in fields.items():
        arr[k] = np.asarray(v).astype(arr.dtype[k])
    header = ["ply", "format binary_little_endian 1.0", f"element vertex {len(xyz)}"]
    header += [f"property {names[np.dtype(dt)]} {n}" for n, dt in cols]
    header.append("end_header")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(("\n".join(header) + "\n").encode())
        f.write(arr.tobytes())


def read_ply(path: Path) -> dict[str, np.ndarray]:
    """Vertex element of a binary little-endian PLY (faces ignored)."""
    with open(path, "rb") as f:
        header = b""
        while not header.endswith(b"end_header\n"):
            line = f.readline()
            if not line:
                raise ValueError(f"{path}: no end_header")
            header += line
        text = header.decode("ascii")
        if "binary_little_endian" not in text:
            raise ValueError(f"{path}: only binary_little_endian PLY is supported")
        m = re.search(r"element vertex (\d+)\n((?:property [^\n]+\n)+)", text)
        n = int(m.group(1))
        dt = []
        for ln in m.group(2).strip().splitlines():
            _, typ, name = ln.split()
            dt.append((name, _PLY_TYPES[typ]))
        arr = np.frombuffer(f.read(n * np.dtype(dt).itemsize), dtype=dt, count=n)
    return {name: np.asarray(arr[name]) for name, _ in dt}


# --- Scene assets ----------------------------------------------------------------------------


def asset(scene: dict, asset_id: str) -> dict:
    for a in scene.get("assets", []):
        if a["id"] == asset_id:
            return a
    raise KeyError(asset_id)


def load_points(scene: dict, base: Path, kind: str = "point_cloud", asset_id: str | None = None):
    """(xyz in scene frame, generated flags or None, all fields) of the first matching asset."""
    for a in scene.get("assets", []):
        if (asset_id and a["id"] == asset_id) or (not asset_id and a["kind"] == kind):
            d = read_ply(Path(base) / a["path"])
            xyz = np.c_[d["x"], d["y"], d["z"]].astype(float)
            if not a.get("in_scene_frame", True):
                xyz = apply(mat4_from_list(scene["frame"]["from_source"]), xyz)
            gf = a.get("generated_field")
            gen = d[gf].astype(bool) if gf and gf in d else None
            return xyz, gen, d
    return None, None, None


@dataclass
class CoverageGrid:
    state: np.ndarray  # uint8 [nx, ny, nz]: 0 unknown, 1 observed free, 2 observed occupied
    views: np.ndarray | None
    origin: np.ndarray  # scene coords of the minimum corner
    voxel: float
    min_views: int = 1

    UNKNOWN, FREE, OCCUPIED = 0, 1, 2

    @property
    def shape(self):
        return self.state.shape

    def index(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        ijk = np.floor((np.asarray(X, float) - self.origin) / self.voxel).astype(np.int64)
        inside = np.all((ijk >= 0) & (ijk < np.array(self.shape)), axis=-1)
        return ijk, inside

    def state_at(self, X: np.ndarray) -> np.ndarray:
        """Per point: 0 unknown (also outside the grid), 1 free, 2 occupied."""
        ijk, inside = self.index(X)
        out = np.zeros(len(ijk), np.uint8)
        i = ijk[inside]
        st = self.state[i[:, 0], i[:, 1], i[:, 2]]
        if self.views is not None and self.min_views > 1:
            st = np.where(self.views[i[:, 0], i[:, 1], i[:, 2]] >= self.min_views, st, 0)
        out[inside] = st
        return out

    def observed_at(self, X: np.ndarray) -> np.ndarray:
        return self.state_at(X) > 0

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        kw = {"state": self.state, "origin": self.origin, "voxel": np.float64(self.voxel)}
        if self.views is not None:
            kw["views"] = self.views
        np.savez_compressed(path, **kw)

    def meta(self, asset_id: str) -> dict:
        return {"asset": asset_id, "voxel_m": self.voxel, "origin_m": [round(float(v), 4) for v in self.origin],
                "shape": [int(v) for v in self.shape], "min_views": self.min_views}


def load_coverage(scene: dict, base: Path) -> CoverageGrid | None:
    g = scene["coverage"].get("grid")
    if not g:
        return None
    d = np.load(Path(base) / asset(scene, g["asset"])["path"])
    return CoverageGrid(d["state"], d["views"] if "views" in d else None, np.asarray(g["origin_m"], float),
                        float(g["voxel_m"]), int(g.get("min_views", 1)))
