"""Monte Carlo of metric-scale error per method and per combination, and the resulting error on long
distances (15 ft, 26 ft from the meter).

    python combine.py  ->  results/combine.{json,md}

Every source gives a relative scale error e (s_est / s_true - 1). Draws per simulated house:
  measured on real data here (see results/segments_summary.md, results/*/mdepth_*.json):
    recon local-scale fidelity   N(0, sqrt(2) x 0.35 %)  (ETH3D recon vs laser SD; reference and target spots independent)
    tape endpoint localisation   6 mm per endpoint        (facade 0.16 % at 4.96 m, electro 0.26 % at 2.65 m view SD)
    crisp-edge snapping          0.7 px per edge          (siding / tape view SD)
    door outline snapping        1.35 % per door          (barn man door, 5 views)
    brick course, counted        0.12 %                   (facade, 27 courses)
    learned depth, scene level   bootstrap over the measured scene-level errors of MoGe-2 / Metric3D
    ARKit non-LiDAR scale        bootstrap of A1's 50-seed SfM+ARKit fit (simulated drift, real ETH3D poses)
  from standards / datasheets (not measurable in these scenes):
    meter base flange 6.95 in    SD 0.5 % (ANSI C12.10 interchangeability); 10 % of detections outline the cover
                                 (6.29-6.42 in, -8 %) instead of the base
    door leaf height 80 in       80 in 80 %, 78 in 5 %, 84 in 10 %, 96 in 5 %; 30 % of detections take the
                                 frame/trim outline (+4 to +9 %; the barn's outline read 86.8 in)
    brick course (modular)       2.667 in 50 %, 2.625 15 %, 2.75-3.0 25 %, 3.2 10 %
    siding reveal (assume 6 in)  4, 4.5, 5, 6, 6.75, 7, 8 in exposures
    homeowner tape reading       N(0, 1/4 in); 5 % gross (+-6 to 12 in, wrong feature or misread foot)
Combination: robust inverse-variance mean of log-scales with each source's declared SD, 3-sigma gating
against the running estimate (so a 96 in door or a misread tape is dropped when ARKit disagrees).
"""

from __future__ import annotations

import json

import numpy as np

from common import FT, HERE, IN, STORE, dump

A1 = STORE / "internal/breadth/a1-classical/results/facade/scores/scale_study_incremental_tour.json"
RNG = np.random.default_rng(7)
N = 20000
F_HOUSE = 1127.0  # px focal of a 2048 px-wide house photo (ETH3D-like); iPhone 1x at 4032 px is ~2900 px
SHAPE_IN = 1.25  # 1-sigma shape error on observed facade KDs (A1 ARKit-prior mode: median 0.84 in)


def arkit_draws(profile: str) -> np.ndarray:
    d = json.loads(A1.read_text())
    e = np.array([r["sfm_ls_scale_err"] for r in d["profiles"][profile]["rows"]])
    return e[RNG.integers(0, len(e), N)] + RNG.normal(0, 0.002, N)


def mdepth_draws() -> tuple[np.ndarray, list]:
    vals = []
    for sc in ("electro", "facade", "barn"):
        for m in ("moge2-vits", "metric3d-vits"):
            p = HERE / "results" / sc / f"mdepth_{m}.json"
            if p.exists():
                vals.append((sc, m, json.loads(p.read_text())["median_of_images_err_pct"] / 100))
    e = np.array([v[2] for v in vals])
    return e[RNG.integers(0, len(e), N)] + RNG.normal(0, 0.02, N), vals


def recon_term() -> np.ndarray:
    return RNG.normal(0, np.sqrt(2) * 0.0035, N)


def tape(L_ft: float, gross: bool = True) -> np.ndarray:
    L = L_ft * FT
    read = RNG.normal(0, 0.25 * IN, N)
    if gross:
        g = RNG.random(N) < 0.05
        read[g] += RNG.choice([-1, 1], g.sum()) * RNG.uniform(6, 12, g.sum()) * IN
    ends = RNG.normal(0, 0.006, N) - RNG.normal(0, 0.006, N)
    # scale estimate = L_tape / L_measured: error = tape error - measurement error
    return (L + read) / (L + ends) - 1


def meter(dist_m: float, f_px: float) -> np.ndarray:
    D = 6.95 * IN
    true = D * (1 + RNG.normal(0, 0.005, N))
    cover = RNG.random(N) < 0.10
    true[cover] = RNG.uniform(6.29, 6.42, cover.sum()) * IN
    px = f_px * true / dist_m
    meas_px = px + RNG.normal(0, 0.7 * np.sqrt(2), N)
    return D / (meas_px * dist_m / f_px) - 1


def door() -> np.ndarray:
    nominal = 80 * IN
    true = RNG.choice(np.array([80, 78, 84, 96]) * IN, N, p=[0.80, 0.05, 0.10, 0.05])
    outline = RNG.random(N) < 0.30
    true = np.where(outline, true * (1 + RNG.uniform(0.04, 0.09, N)), true)
    meas = true * (1 + RNG.normal(0, 0.0135, N))
    return nominal / meas - 1


def brick() -> np.ndarray:
    true = RNG.choice(np.array([2.667, 2.625, 2.75, 3.0, 3.2]) * IN, N, p=[0.50, 0.15, 0.15, 0.10, 0.10])
    return 2.667 * IN / (true * (1 + RNG.normal(0, 0.0012, N))) - 1


def siding() -> np.ndarray:
    true = RNG.choice(np.array([4, 4.5, 5, 6, 6.75, 7, 8]) * IN, N, p=[0.15, 0.10, 0.20, 0.15, 0.10, 0.20, 0.10])
    return 6 * IN / (true * (1 + RNG.normal(0, 0.0034, N))) - 1


def robust_combine(ests: list[np.ndarray], sds: list[float]):
    """Per house: inverse-variance mean in log space, iteratively dropping sources > 3 sigma off."""
    L = np.log1p(np.vstack(ests))  # (k, N)
    w = 1 / np.array(sds)[:, None] ** 2 * np.ones_like(L)
    use = np.ones_like(L, bool)
    for _ in range(4):
        m = (L * w * use).sum(0) / (w * use).sum(0)
        new = np.abs(L - m) <= 3 * np.array(sds)[:, None]
        # never drop everything: keep the most precise source
        none = ~new.any(0)
        new[np.argmin(sds), none] = True
        if (new == use).all():
            break
        use = new
    m = (L * w * use).sum(0) / (w * use).sum(0)
    sd_post = 1 / np.sqrt((w * use).sum(0))
    return np.expm1(m), sd_post, use


def summarise(name: str, e: np.ndarray, sd_declared=None) -> dict:
    a = np.abs(e)
    shape = RNG.normal(0, SHAPE_IN, N)
    r = {"method": name, "median_abs_pct": 100 * np.median(a), "p90_abs_pct": 100 * np.percentile(a, 90),
         "p95_abs_pct": 100 * np.percentile(a, 95), "bias_pct": 100 * np.median(e)}
    for ft in (15, 26):
        err_in = np.abs(e * ft * 12 + shape)
        r[f"err_{ft}ft_in_median"] = float(np.median(err_in))
        r[f"err_{ft}ft_in_p90"] = float(np.percentile(err_in, 90))
        r[f"share_{ft}ft_within_2in"] = float((err_in <= 2).mean())
        r[f"share_{ft}ft_within_6in"] = float((err_in <= 6).mean())
    if sd_declared is not None:
        r["declared_sd_pct_median"] = float(100 * np.median(sd_declared))
        r["coverage_2sd"] = float((a <= 2 * sd_declared).mean())
    return {k: (float(v) if isinstance(v, (np.floating, float)) else v) for k, v in r.items()}


def main() -> None:
    ark_m, ark_o = arkit_draws("nonlidar_modern"), arkit_draws("nonlidar_2018")
    dep, dep_vals = mdepth_draws()
    rec = recon_term()
    src = {
        "ARKit non-LiDAR, modern (sim.)": (ark_m, 0.03),
        "ARKit non-LiDAR, 2018 (sim. from real ADVIO)": (ark_o, 0.10),
        "Learned metric depth (MoGe-2 / Metric3D, scene median)": (dep, 0.12),
        "Tape 10 ft (with 5 % gross reading errors)": (tape(10) + rec, 0.006),
        "Tape 15 ft (with 5 % gross reading errors)": (tape(15) + rec, 0.005),
        "Tape 10 ft, no gross errors": (tape(10, gross=False) + rec, 0.006),
        "Meter base 6.95 in, close-up photo at 1 m (4032 px)": (meter(1.0, 2900.0) + rec, 0.01),
        "Meter base 6.95 in, from a house photo at 4.5 m": (meter(4.5, F_HOUSE) + rec, 0.03),
        "Door leaf 80 in": (door() + rec, 0.035),
        "Brick course, modular assumed, counted": (brick() + rec, 0.07),
        "Siding reveal, 6 in assumed": (siding() + rec, 0.20),
    }
    rows = [summarise(k, v[0]) for k, v in src.items()]
    combos = {
        "ARKit(2018) + depth": ["ARKit non-LiDAR, 2018 (sim. from real ADVIO)", "Learned metric depth (MoGe-2 / Metric3D, scene median)"],
        "ARKit(modern) + depth": ["ARKit non-LiDAR, modern (sim.)", "Learned metric depth (MoGe-2 / Metric3D, scene median)"],
        "Weak refs, no homeowner action: ARKit(2018) + meter(house photo) + door + depth": [
            "ARKit non-LiDAR, 2018 (sim. from real ADVIO)", "Meter base 6.95 in, from a house photo at 4.5 m", "Door leaf 80 in",
            "Learned metric depth (MoGe-2 / Metric3D, scene median)"],
        "Weak refs + meter close-up: ARKit(2018) + meter close-up + door + depth": [
            "ARKit non-LiDAR, 2018 (sim. from real ADVIO)", "Meter base 6.95 in, close-up photo at 1 m (4032 px)", "Door leaf 80 in",
            "Learned metric depth (MoGe-2 / Metric3D, scene median)"],
        "Tape 10 ft + ARKit(2018) gate": ["Tape 10 ft (with 5 % gross reading errors)", "ARKit non-LiDAR, 2018 (sim. from real ADVIO)"],
        "Tape 10 ft + meter close-up + ARKit(2018)": ["Tape 10 ft (with 5 % gross reading errors)", "Meter base 6.95 in, close-up photo at 1 m (4032 px)",
                                                     "ARKit non-LiDAR, 2018 (sim. from real ADVIO)"],
        "Tape 10 ft + meter close-up + ARKit(modern)": ["Tape 10 ft (with 5 % gross reading errors)", "Meter base 6.95 in, close-up photo at 1 m (4032 px)",
                                                       "ARKit non-LiDAR, modern (sim.)"],
    }
    for name, keys in combos.items():
        e, sd, use = robust_combine([src[k][0] for k in keys], [src[k][1] for k in keys])
        r = summarise(name, e, sd)
        r["share_sources_dropped"] = {k: float(1 - use[i].mean()) for i, k in enumerate(keys)}
        rows.append(r)
    out = {"n": N, "mdepth_scene_errors": dep_vals, "rows": rows}
    dump(HERE / "results" / "combine.json", out)
    hdr = "| Method | median / p90 / p95 abs scale err | 15 ft err median / p90 (in) | 26 ft err median / p90 (in) | 26 ft within 6 in | 15 ft within 2 in | declared SD (median), coverage at 2 SD |"
    lines = [hdr, "|---|---|---|---|---|---|---|"]
    for r in rows:
        cov = f"{r['declared_sd_pct_median']:.1f} %, {100 * r['coverage_2sd']:.0f} %" if "coverage_2sd" in r else "–"
        lines.append(f"| {r['method']} | {r['median_abs_pct']:.1f} / {r['p90_abs_pct']:.1f} / {r['p95_abs_pct']:.1f} % | "
                     f"{r['err_15ft_in_median']:.1f} / {r['err_15ft_in_p90']:.1f} | {r['err_26ft_in_median']:.1f} / {r['err_26ft_in_p90']:.1f} | "
                     f"{100 * r['share_26ft_within_6in']:.0f} % | {100 * r['share_15ft_within_2in']:.0f} % | {cov} |")
    (HERE / "results" / "combine.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("learned-depth scene errors:", dep_vals)


if __name__ == "__main__":
    main()
