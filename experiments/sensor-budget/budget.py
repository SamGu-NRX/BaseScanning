"""Physics error budgets for phone signals that no dataset on disk can test.

Each candidate is a closed-form error model. Its parameters run between an optimistic and a
conservative value, and each carries its source (`PARAMS`). The Monte Carlo draws the random
terms as standard normals and reports the p90 of the absolute error three ways: every parameter
at its optimistic end, every parameter at its conservative end, and parameters drawn uniformly
between the two. The verdict applies the README's rule, fixed before the run, to the first two.

Run: uv run python budget.py   (writes results/budget.md and results/budget.json)
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import NormalDist

import numpy as np

IN_PER_M = 1 / 0.0254
FT = 0.3048
N = 200_000
SEED = 20260926


@dataclass(frozen=True)
class P:
    opt: float
    cons: float
    unit: str
    source: str


# Every value names where it came from. "Assumed" means no source was found; the verdict then
# says which parameter decides it.
PARAMS: dict[str, P] = {
    # Geometry of the capture
    "standoff_m": P(2.5, 2.5, "m", "app guidance: stand 1 to 3 m from the wall (docs/00)"),
    "f_px": P(
        1450.0,
        1450.0,
        "px",
        "wide camera focal length at 1920 px, typical ARKit capturedImage intrinsics",
    ),
    # Dual-camera disparity (AVDepthData, builtInDualWideCamera)
    "dual_f_px": P(
        500.0,
        400.0,
        "px",
        "depth map about 640 px wide: the 1450 px focal length scaled down (assumed)",
    ),
    "dual_baseline_m": P(
        0.020,
        0.012,
        "m",
        "wide to ultra-wide spacing, assumed; the idea lab estimated 1.5 to 2 cm; no Apple spec",
    ),
    "dual_disp_px": P(0.1, 0.5, "px", "disparity noise, assumed; sub-pixel stereo range"),
    # Focus distance from lens position
    "focal_mm": P(
        6.9, 5.7, "mm", "iPhone main camera focal lengths, 13 to 15 Pro (Apple tech specs)"
    ),
    "lens_pos_um": P(
        1.0,
        3.0,
        "um",
        "lens position repeatability including heat and gravity sag, assumed; AVCaptureDevice.lensPosition is not calibrated to distance",
    ),
    # Barometer
    "baro_pa": P(
        1.0,
        6.0,
        "Pa",
        "relative pressure noise plus wind gusts; 1 Pa is a quiet sensor, 6 Pa a 3 m/s gust's dynamic pressure (assumed)",
    ),
    "rho_g": P(12.0, 11.5, "Pa/m", "air density times g near sea level"),
    # UWB range to a phone at the meter
    "uwb_sigma_m": P(
        0.05,
        0.20,
        "m",
        "arXiv 2303.11220: errors under 20 cm in tested conditions; 5 cm assumed line of sight",
    ),
    "uwb_span_ft": P(20.0, 20.0, "ft", "operating distance along the wall"),
    # Finger tap on the screen
    "tap_sigma_mm": P(
        1.5,
        4.0,
        "mm",
        "finger landing spread, assumed; Apple's HIG minimum target is 44 pt (about 7 mm)",
    ),
    "screen_px_per_mm": P(
        12.4,
        12.4,
        "px/mm",
        "iPhone 15 aspect-fill: 1920 px on 852 pt, 5.5 pt/mm, so 2.25 image px per pt",
    ),
    "snap_px": P(
        0.5,
        2.0,
        "px",
        "sub-pixel edge localization: Steger reports under 0.1 px on clean edges; 2 px allows for texture",
    ),
    # The app's wall plane: one raycast normal at the meter
    "anchor_yaw_deg": P(
        2.15 / NormalDist().inv_cdf(0.75),
        6.72 / NormalDist().inv_cdf(0.95),
        "deg",
        "Gaussian sigmas inferred separately from median absolute yaw and p90 absolute yaw of a 0.3 m depth-patch plane on ETH3D electro (experiments/edge-geometry), a proxy for one ARKit raycast normal; the first run assumed 1 to 5; Gaussian shape is assumed, not measured",
    ),
    "tap_view_deg": P(
        35.0,
        35.0,
        "deg",
        "view angle off face-on for an edge 5 ft from the meter tapped from 7 ft out",
    ),
    "tap_span_ft": P(6.0, 6.0, "ft", "edge distance from the meter"),
    # One photo holding both ends of a span
    "span_ft": P(6.0, 6.0, "ft", "a window or gas meter 6 ft from the meter"),
    "one_photo_standoff_m": P(3.0, 3.0, "m", "step back so both ends fit in a landscape frame"),
    "lidar_scale": P(
        0.004,
        0.01,
        "fraction",
        "iPhone LiDAR range error, about 1 cm at 3 m (assumed from Measure-style apps); 1% conservative",
    ),
    "vio_scale": P(
        0.01,
        0.03,
        "fraction",
        "MARViN: 30 of 35 iPhone 14 Pro Max walks within 2% (evals section 6); 3% for a phone without LiDAR, assumed",
    ),
    "wall_yaw_lidar_deg": P(0.2, 0.5, "deg", "plane fitted to thousands of LiDAR points, assumed"),
    "wall_yaw_vio_deg": P(0.5, 2.0, "deg", "plane from feature points or vanishing lines, assumed"),
    # Known-size references, as a scale error (fraction, one standard deviation)
    "meter_cover_unknown_maker": P(
        0.029,
        0.029,
        "fraction",
        "covers span 6.29 to 6.95 in across Itron, Aclara, Elster (maker drawings): ±5% uniform",
    ),
    "meter_cover_known_maker": P(
        0.003, 0.01, "fraction", "one maker's drawing, assumed moulding tolerance"
    ),
    "brick_course": P(
        0.005,
        0.016,
        "fraction",
        "3 courses = 8 in (BIA 9A); a mason's story pole holds coursing, 1/8 in per 8 in taken as the conservative end",
    ),
    "door_slab": P(
        0.006,
        0.013,
        "fraction",
        "exterior slab 79.5 in, opening 80 in: which edge the photo shows is 0.5 to 1 in (Mastercraft)",
    ),
    "letter_sheet": P(
        0.003, 0.006, "fraction", "11 in sheet; cut tolerance unknown, assumed 1/32 to 1/16 in"
    ),
    "id1_card": P(
        0.002,
        0.006,
        "fraction",
        "ISO 7810 85.60 mm, new cards 85.47 to 85.72 (±0.15%), plus 0.3 to 1 px edges at 0.5 m",
    ),
    # Acoustic echo to a facing fence
    "echo_m": P(
        0.015, 0.10, "m", "BatMapper: 1 to 2 cm at p80 indoors to 3.5 m; 10 cm assumed outdoors"
    ),
    # Phone as a contact probe
    "probe_m": P(
        0.005,
        0.03,
        "m",
        "tracking error while the camera faces the wall at contact, assumed; device geometry adds about 1 mm",
    ),
}


@dataclass(frozen=True)
class Candidate:
    name: str
    kind: str  # "length" in inches, "scale" in percent
    operating: str
    model: str  # the formula, in words
    uses: tuple[str, ...]
    error: Callable[[Callable[[str], np.ndarray], np.random.Generator], np.ndarray]


def _p90(x: np.ndarray) -> float:
    return float(np.quantile(np.abs(x), 0.9))


def _rad(deg: np.ndarray) -> np.ndarray:
    return np.deg2rad(deg)


def _dual(v, rng):
    z = v("standoff_m")
    sigma = z**2 * v("dual_disp_px") / (v("dual_f_px") * v("dual_baseline_m"))
    return rng.standard_normal(N) * sigma * IN_PER_M


def _focus(v, rng):
    z = v("standoff_m")
    f = v("focal_mm") * 1e-3
    sigma = z**2 * v("lens_pos_um") * 1e-6 / f**2
    return rng.standard_normal(N) * sigma * IN_PER_M


def _baro(v, rng):
    return rng.standard_normal(N) * v("baro_pa") / v("rho_g") * IN_PER_M


def _uwb(v, rng):
    s = v("uwb_span_ft") * FT
    d = v("standoff_m")
    r = np.sqrt(s**2 + d**2)
    return rng.standard_normal(N) * v("uwb_sigma_m") * r / s * IN_PER_M


def _tap_raw(v, rng):
    px = v("tap_sigma_mm") * v("screen_px_per_mm")
    metres_per_px = v("standoff_m") / v("f_px")
    return rng.standard_normal(N) * px * metres_per_px * IN_PER_M


def _tap_snapped(v, rng):
    metres_per_px = v("standoff_m") / v("f_px")
    return rng.standard_normal(N) * v("snap_px") * metres_per_px * IN_PER_M


def _anchor_plane(v, rng):
    s = v("tap_span_ft") * FT
    psi = _rad(v("anchor_yaw_deg")) * rng.standard_normal(N)
    return s * np.tan(psi) * np.tan(_rad(v("tap_view_deg"))) * IN_PER_M


def _one_photo(scale_key: str, yaw_key: str):
    def err(v, rng):
        s = v("span_ft") * FT
        d = v("one_photo_standoff_m")
        scale = v(scale_key) * rng.standard_normal(N)
        psi = _rad(v(yaw_key)) * rng.standard_normal(N)
        # Meter face-on at bearing 0, feature at bearing atan(s/d). A yaw error psi moves the
        # feature's bearing relative to the assumed normal: s' = d tan(atan(s/d) + psi) - d tan(psi).
        a = np.arctan(s / d)
        s_yaw = d * np.tan(a + psi) - d * np.tan(psi)
        return ((1 + scale) * s_yaw - s) * IN_PER_M

    return err


def _scale(key: str):
    def err(v, rng):
        return rng.standard_normal(N) * v(key) * 100

    return err


def _echo(v, rng):
    return rng.standard_normal(N) * v("echo_m") * IN_PER_M


def _probe(v, rng):
    return rng.standard_normal(N) * v("probe_m") * IN_PER_M


CANDIDATES = [
    Candidate(
        "Dual-camera disparity, depth per pixel",
        "length",
        "2.5 m",
        "Z² σd / (f B)",
        ("standoff_m", "dual_f_px", "dual_baseline_m", "dual_disp_px"),
        _dual,
    ),
    Candidate(
        "Focus distance from lens position",
        "length",
        "2.5 m",
        "Z² σΔ / f²",
        ("standoff_m", "focal_mm", "lens_pos_um"),
        _focus,
    ),
    Candidate(
        "Barometer, height of a feature",
        "length",
        "one height",
        "σP / (ρ g)",
        ("baro_pa", "rho_g"),
        _baro,
    ),
    Candidate(
        "UWB range to a phone at the meter",
        "length",
        "20 ft along the wall",
        "σr · r / s",
        ("uwb_sigma_m", "uwb_span_ft", "standoff_m"),
        _uwb,
    ),
    Candidate(
        "Finger tap, no snap (the app today)",
        "length",
        "2.5 m",
        "σ_tap · px/mm · Z / f",
        ("tap_sigma_mm", "screen_px_per_mm", "standoff_m", "f_px"),
        _tap_raw,
    ),
    Candidate(
        "Tap snapped to an image edge",
        "length",
        "2.5 m",
        "σ_snap · Z / f",
        ("snap_px", "standoff_m", "f_px"),
        _tap_snapped,
    ),
    Candidate(
        "The app's wall plane: one normal at the meter",
        "length",
        "edge 6 ft away, seen 35° off",
        "s · tan ψ · tan α",
        ("anchor_yaw_deg", "tap_view_deg", "tap_span_ft"),
        _anchor_plane,
    ),
    Candidate(
        "One photo holds both ends, LiDAR",
        "length",
        "6 ft span from 3 m",
        "scale · s and yaw through d·tan",
        ("span_ft", "one_photo_standoff_m", "lidar_scale", "wall_yaw_lidar_deg"),
        _one_photo("lidar_scale", "wall_yaw_lidar_deg"),
    ),
    Candidate(
        "One photo holds both ends, no LiDAR",
        "length",
        "6 ft span from 3 m",
        "scale · s and yaw through d·tan",
        ("span_ft", "one_photo_standoff_m", "vio_scale", "wall_yaw_vio_deg"),
        _one_photo("vio_scale", "wall_yaw_vio_deg"),
    ),
    Candidate(
        "Meter cover, maker unknown",
        "scale",
        "at the meter",
        "size spread",
        ("meter_cover_unknown_maker",),
        _scale("meter_cover_unknown_maker"),
    ),
    Candidate(
        "Meter cover, maker read from the nameplate",
        "scale",
        "at the meter",
        "maker's drawing",
        ("meter_cover_known_maker",),
        _scale("meter_cover_known_maker"),
    ),
    Candidate(
        "Brick coursing",
        "scale",
        "any brick wall",
        "course spacing",
        ("brick_course",),
        _scale("brick_course"),
    ),
    Candidate(
        "Exterior door height",
        "scale",
        "any door in view",
        "slab vs opening",
        ("door_slab",),
        _scale("door_slab"),
    ),
    Candidate(
        "US Letter sheet held to the wall",
        "scale",
        "at the meter",
        "sheet size",
        ("letter_sheet",),
        _scale("letter_sheet"),
    ),
    Candidate(
        "ID-1 card held to the wall",
        "scale",
        "at the meter, 0.5 m",
        "card size and edges",
        ("id1_card",),
        _scale("id1_card"),
    ),
    Candidate(
        "Acoustic echo to a facing fence", "length", "3 ft gap", "σ range", ("echo_m",), _echo
    ),
    Candidate(
        "Phone as a contact probe",
        "length",
        "any reachable corner",
        "tracking at contact",
        ("probe_m",),
        _probe,
    ),
]


def verdict(kind: str, opt: float, cons: float) -> str:
    good, bad = (2.0, 4.0) if kind == "length" else (1.0, 3.0)
    if cons <= good:
        return "worth a device test"
    if opt > bad:
        return "drop"
    return "in between"


def run() -> list[dict]:
    rows = []
    for i, c in enumerate(CANDIDATES):
        out = {}
        for mode in ("opt", "cons", "range"):
            rng = np.random.default_rng(SEED + i)

            def value(name: str, mode: str = mode, rng: np.random.Generator = rng) -> np.ndarray:
                p = PARAMS[name]
                if mode == "opt":
                    return np.full(N, p.opt)
                if mode == "cons":
                    return np.full(N, p.cons)
                lo, hi = sorted((p.opt, p.cons))
                return rng.uniform(lo, hi, N)

            out[mode] = _p90(c.error(value, rng))
        rows.append(
            {
                "name": c.name,
                "kind": c.kind,
                "operating": c.operating,
                "model": c.model,
                "uses": list(c.uses),
                "p90_opt": out["opt"],
                "p90_cons": out["cons"],
                "p90_range": out["range"],
                "verdict": verdict(c.kind, out["opt"], out["cons"]),
            }
        )
    return rows


def _fmt(x: float) -> str:
    return f"{x:.2f}" if x < 10 else f"{x:.0f}"


def write(rows: list[dict], out: Path) -> None:
    out.mkdir(exist_ok=True)
    (out / "budget.json").write_text(
        json.dumps({"rows": rows, "params": {k: asdict(v) for k, v in PARAMS.items()}}, indent=2)
        + "\n"
    )
    lines = [
        "# Sensor budget (generated by `uv run python budget.py`)",
        "",
        "p90 of |error|: inches for lengths, percent for scales. Optimistic and conservative put every",
        "parameter at that end; the range column draws them uniformly between. Verdict rule (fixed before",
        "the run): worth a device test if conservative p90 ≤ 2 in or ≤ 1%; drop if optimistic p90 > 4 in or > 3%.",
        "",
        "| Candidate | Used at | Model | Optimistic | Conservative | Range | Verdict |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        unit = "in" if r["kind"] == "length" else "%"
        lines.append(
            f"| {r['name']} | {r['operating']} | {r['model']} | {_fmt(r['p90_opt'])} {unit} | "
            f"{_fmt(r['p90_cons'])} {unit} | {_fmt(r['p90_range'])} {unit} | {r['verdict']} |"
        )
    lines += [
        "",
        "## Parameters",
        "",
        "| Name | Optimistic | Conservative | Unit | Source |",
        "| --- | --- | --- | --- | --- |",
    ]
    for k, p in PARAMS.items():
        lines.append(f"| `{k}` | {p.opt:g} | {p.cons:g} | {p.unit} | {p.source} |")
    (out / "budget.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    rows = run()
    write(rows, Path(__file__).parent / "results")
    for r in rows:
        print(f"{r['name']:<48} {_fmt(r['p90_opt']):>6} {_fmt(r['p90_cons']):>6}  {r['verdict']}")
