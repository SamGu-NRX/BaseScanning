"""Drift anatomy: A1 to A5 of README.md on MARViN (bar, church) and ADVIO 20 to 22.

    uv run python run.py        # writes results/drift_anatomy.md and results/drift_anatomy.json

Needs the evals harness (EVALS_HARNESS, branch t3/evals) and its data under HOUSE_SCANNING_DATA;
see walks.py. Takes no network access.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.stats import norm, spearmanr

from metrics import (
    along_across,
    at,
    chord_deviation,
    find_loops,
    local_scale,
    loop_errors,
    path_windows,
    range_correct,
    robust_sd,
    split_by_length,
    turn,
)
from walks import (
    EVALS_HARNESS,
    EXPECTED_COMMIT,
    Walk,
    advio_walks,
    harness_commit,
    harness_modules,
    marvin_walks,
)

FEET = 0.3048
INCH = 0.0254
ALLOWANCE = 0.16  # the server's error allowance, ft per ft
RESULTS = Path(__file__).resolve().parent / "results"

# A1, fixed in the README before the run.
LOOP_GAP_S, LOOP_DIST_M, LOOP_SEPARATION_S = 30.0, 0.3, 5.0
PEAK_FLOOR_M = 2 * INCH
PEAK_FACTOR = 2.0
MIN_LOOPS, LOOP_SHARE = 8, 0.90
# A2. A window whose reference chord is under this share of its path turned back on itself; its
# ratio divides by a short chord and mostly measures endpoint noise, so it is left out.
SCALE_WINDOWS_M = (5.0, 10.0)
SCALE_EVERY_M = 1.0
MIN_CHORD_SHARE = 0.6
# A3 and A4.
WINDOW_FT = (10, 20, 30)
RANGE_SIGMAS_M = (0.05, 0.10, 0.20)
GRADED_SIGMA_M = 0.10
RANGE_DRAWS = 20
# Chord deviation selects straight paths; without wall annotations it cannot select paths
# parallel to a wall. The widths are untuned and not pre-registered.
STRAIGHT_PATH_M = (0.25, 0.5)
SEED = 0


def p90(x: np.ndarray) -> float:
    return float(np.percentile(x, 90))


def by_dataset(walks: list[Walk]) -> dict[str, list[Walk]]:
    out: dict[str, list[Walk]] = {}
    for w in walks:
        out.setdefault(w.dataset, []).append(w)
    return out


# A1


def closest_revisit_m(w: Walk) -> float:
    """Smallest 3-D reference distance between two samples more than LOOP_GAP_S apart."""
    best = np.inf
    for i in range(len(w.times)):
        later = w.times - w.times[i] > LOOP_GAP_S
        if not later.any():
            break
        best = min(best, float(np.linalg.norm(w.ref[later] - w.ref[i], axis=1).min()))
    return best


def a1(walks: list[Walk]) -> dict:
    loops = []
    revisits = {w.name: closest_revisit_m(w) for w in walks}
    for w in walks:
        mean_offset = np.angle(np.mean(np.exp(1j * w.delta)))
        for i, j in find_loops(w.times, w.ref, LOOP_GAP_S, LOOP_DIST_M, LOOP_SEPARATION_S):
            e = loop_errors(w.ark, w.ref, w.delta, i, j)
            # Sensitivity, not graded: one heading offset for the whole walk instead of the sample
            # at t0, to see how much a single orientation sample's noise drives the peak.
            e_mean = loop_errors(w.ark, w.ref, np.full(len(w.delta), mean_offset), i, j)
            # Added after the run: a scale or heading error grows with distance from the start and
            # vanishes on return, so r cannot see it. Dividing out the walk's own scale shows how
            # much of the peak is scale.
            e_scaled = loop_errors(w.ark / w.scale, w.ref, w.delta, i, j)
            bound = max(PEAK_FACTOR * e["r"], PEAK_FLOOR_M)
            loops.append(
                {
                    "dataset": w.dataset,
                    "walk": w.name,
                    "t0_s": float(w.times[i] - w.times[0]),
                    "t1_s": float(w.times[j] - w.times[0]),
                    "ref_gap_m": float(np.linalg.norm(w.ref[j] - w.ref[i])),
                    "path_m": e["path_m"],
                    "excursion_m": e["excursion_m"],
                    "r_in": e["r"] / INCH,
                    "peak_in": e["peak"] / INCH,
                    "bound_in": bound / INCH,
                    "ok": bool(e["peak"] <= bound),
                    "allowance_in": ALLOWANCE * e["excursion_m"] / INCH,
                    "peak_walk_offset_in": e_mean["peak"] / INCH,
                    "ok_walk_offset": bool(
                        e_mean["peak"] <= max(PEAK_FACTOR * e_mean["r"], PEAK_FLOOR_M)
                    ),
                    "ok_walk_scale_removed": bool(
                        e_scaled["peak"] <= max(PEAK_FACTOR * e_scaled["r"], PEAK_FLOOR_M)
                    ),
                }
            )
    n = len(loops)
    share = float(np.mean([x["ok"] for x in loops])) if n else float("nan")
    # Not pre-registered: loops from one walk share its tracking, so check one loop per walk.
    first = {}
    for x in sorted(loops, key=lambda x: x["ref_gap_m"]):
        first.setdefault(x["walk"], x)
    ratio = np.array([x["peak_in"] / x["r_in"] for x in loops]) if n else np.array([])
    over = np.array([x["peak_in"] / x["allowance_in"] for x in loops]) if n else np.array([])
    return {
        "loops": loops,
        "closest_revisit_m": revisits,
        "count_by_dataset": {d: sum(x["dataset"] == d for x in loops) for d in ("marvin", "advio")},
        "count": n,
        "share_ok": share,
        "pass": bool(n >= MIN_LOOPS and share >= LOOP_SHARE),
        "one_per_walk": {
            "count": len(first),
            "share_ok": float(np.mean([x["ok"] for x in first.values()])) if first else None,
        },
        "walk_offset_share_ok": float(np.mean([x["ok_walk_offset"] for x in loops])) if n else None,
        "walk_scale_removed_share_ok": float(np.mean([x["ok_walk_scale_removed"] for x in loops]))
        if n
        else None,
        "peak_over_r": {"median": float(np.median(ratio)), "p90": p90(ratio)} if n else None,
        "peak_over_allowance": {
            "median": float(np.median(over)),
            "p90": p90(over),
            "share_within": float(np.mean(over <= 1)),
        }
        if n
        else None,
        "r_in": {"median": float(np.median([x["r_in"] for x in loops]))} if n else None,
    }


# A2


def scale_windows(w: Walk, length_m: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    i, j = path_windows(w.ref, length_m, SCALE_EVERY_M)
    chord = np.linalg.norm((at(w.ref, j) - w.ref[i])[:, [0, 2]], axis=1)
    keep = chord >= MIN_CHORD_SHARE * length_m
    i, j = i[keep], j[keep]
    return i, j, local_scale(w.ark, w.ref, i, j)


def pooled_spread(per_walk: list[np.ndarray]) -> tuple[float, float]:
    """Within-walk SD (deviations from each walk's mean) and robust SD (from each walk's median)."""
    dev = np.concatenate([s - s.mean() for s in per_walk])
    dof = sum(len(s) - 1 for s in per_walk)
    rdev = np.concatenate([s - np.median(s) for s in per_walk])
    return float(np.sqrt((dev**2).sum() / dof)), robust_sd(rdev)


def a2(walks: list[Walk]) -> dict:
    out = {}
    for dataset, ws in by_dataset(walks).items():
        per_len = {L: [scale_windows(w, L)[2] for w in ws] for L in SCALE_WINDOWS_M}
        rows = []
        for k, w in enumerate(ws):
            s5, s10 = per_len[5.0][k], per_len[10.0][k]
            rows.append(
                {
                    "walk": w.name,
                    "windows_5m": len(s5),
                    "median_5m": float(np.median(s5)),
                    "sd_5m": float(s5.std(ddof=1)),
                    "robust_sd_5m": robust_sd(s5),
                    "sd_10m": float(s10.std(ddof=1)),
                    "robust_sd_10m": robust_sd(s10),
                }
            )
        sd5, rsd5 = pooled_spread(per_len[5.0])
        sd10, rsd10 = pooled_spread(per_len[10.0])
        dropped = {
            L: sum(len(path_windows(w.ref, L, SCALE_EVERY_M)[0]) for w in ws)
            - sum(len(s) for s in per_len[L])
            for L in SCALE_WINDOWS_M
        }
        out[dataset] = {
            "walks": rows,
            "windows_5m": sum(len(s) for s in per_len[5.0]),
            "windows_dropped_as_turning": dropped,
            "sd_5m": sd5,
            "robust_sd_5m": rsd5,
            "sd_10m": sd10,
            "robust_sd_10m": rsd10,
            "split_sd": dict(
                zip(
                    ("falls_with_length_at_5m", "persistent"),
                    split_by_length(sd5, sd10, 5.0, 10.0),
                    strict=True,
                )
            ),
            "split_robust_sd": dict(
                zip(
                    ("falls_with_length_at_5m", "persistent"),
                    split_by_length(rsd5, rsd10, 5.0, 10.0),
                    strict=True,
                )
            ),
            "verdict": "pass" if sd5 < 0.01 else ("drop" if sd5 > 0.03 else "between"),
            # Added after the run: the SD is several times the robust SD because of windows where
            # ARKit barely moves while the reference walks on; this counts them.
            "share_over_10pct_from_walk_median_5m": float(
                np.mean(np.concatenate([np.abs(s - np.median(s)) > 0.10 for s in per_len[5.0]]))
            ),
        }
    return out


# A3 and A4


def window_errors(
    ws: list[Walk], ft: int, window_pairs
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """ARKit's displacement turned by the heading offset at each window's start, the reference's,
    and how far the reference path strays from its start-to-end line, pooled over walks; the
    evals' windows (every start sample, exactly ft of path)."""
    est, ref, dev = [], [], []
    for w in ws:
        i, j = window_pairs(w.ref, ft * FEET, w.start_step)
        est.append(turn(at(w.ark, j) - w.ark[i], w.delta[i]))
        ref.append(at(w.ref, j) - w.ref[i])
        dev.append(chord_deviation(w.ref, i, j))
    return np.concatenate(est), np.concatenate(ref), np.concatenate(dev)


def split_p90(err: np.ndarray, ref: np.ndarray) -> dict:
    along, across = along_across(err, ref)
    horiz = np.hypot(along, across)
    return {
        "windows": len(err),
        "p90_along_in": p90(np.abs(along)) / INCH,
        "p90_across_in": p90(np.abs(across)) / INCH,
        "p90_horizontal_in": p90(horiz) / INCH,
        "along_share_of_total_p90": p90(np.abs(along)) / p90(horiz),
    }


def a3_a4(walks: list[Walk]) -> tuple[dict, dict]:
    _, drift, _ = harness_modules()
    rng = np.random.default_rng(SEED)
    a3, a4 = {}, {}
    for dataset, ws in by_dataset(walks).items():
        a3[dataset], a4[dataset] = {}, {}
        for ft in WINDOW_FT:
            est, ref, dev = window_errors(ws, ft, drift.window_pairs)
            err = est - ref
            along, across = along_across(err, ref)
            horiz = np.hypot(along, across)
            a3[dataset][ft] = {
                **split_p90(err, ref),
                "median_horizontal_in": float(np.median(horiz)) / INCH,
                "p90_vertical_in": p90(np.abs(err[:, 1])) / INCH,
                "p90_3d_in": p90(np.linalg.norm(err, axis=1)) / INCH,
                "straight_path": {
                    f"{m:.2f}": split_p90(err[dev <= m], ref[dev <= m])
                    if (dev <= m).any()
                    else None
                    for m in STRAIGHT_PATH_M
                },
            }
            true_range = np.linalg.norm(ref, axis=1)
            a4[dataset][ft] = {"before_p90_in": p90(horiz) / INCH}
            for sigma in RANGE_SIGMAS_M:
                noise = rng.normal(0.0, sigma, size=(RANGE_DRAWS, len(ref)))
                measured = np.maximum(true_range[None] + noise, 0.0)
                fixed = range_correct(np.tile(est, (RANGE_DRAWS, 1)), measured.ravel())
                after = np.linalg.norm((fixed - np.tile(ref, (RANGE_DRAWS, 1)))[:, [0, 2]], axis=1)
                a4[dataset][ft][f"after_p90_in_sigma_{sigma:.2f}"] = p90(after) / INCH
    return a3, a4


# A5


def a5(walks: list[Walk]) -> dict:
    """Exploratory: per ADVIO 5 m window, |local scale - 1| against the SD of |a| (m/s^2)."""
    rows = {}
    pooled_x, pooled_y = [], []
    for w in walks:
        if w.dataset != "advio":
            continue
        i, j, s = scale_windows(w, 5.0)
        t_end = np.interp(j, np.arange(len(w.times)), w.times)
        excite = np.empty(len(i))
        for k, (a, b) in enumerate(zip(w.times[i], t_end, strict=True)):
            m = (w.accel_t >= a) & (w.accel_t <= b)
            if m.sum() < 50:
                raise ValueError(
                    f"{w.name}: only {m.sum()} accelerometer samples in {a:.1f}-{b:.1f} s"
                )
            excite[k] = w.accel_norm[m].std()
        err = np.abs(s - 1)
        rho = spearmanr(excite, err).statistic
        rows[w.name] = {
            "windows": len(i),
            "spearman": float(rho),
            "median_excitation_m_s2": float(np.median(excite)),
        }
        pooled_x.append(excite)
        pooled_y.append(err)
    rows["pooled"] = {
        "windows": int(sum(len(x) for x in pooled_x)),
        "spearman": float(spearmanr(np.concatenate(pooled_x), np.concatenate(pooled_y)).statistic),
    }
    return rows


# Report


def fmt_pct(x: float) -> str:
    return f"{100 * x:.2f}%"


def markdown(meta: dict, r1: dict, r2: dict, r3: dict, r4: dict, r5: dict) -> str:
    L = [
        "# Drift anatomy (generated by `uv run python run.py`)",
        "",
        f"Evals harness commit {meta['harness_commit']} (branch t3/evals). Walks: MARViN bar and "
        f"church, {meta['walks']['marvin']} walks, all within {int(100 * 0.10)}% of the reference's "
        "scale; atrium is left out because its reference jitters by up to 0.4 m. ADVIO 20 to 22, "
        f"{meta['walks']['advio']} walks, against the ground truth rescaled to GPS. Heading offset: "
        "the per-sample offset at each loop's or window's start, as the evals use it.",
        "",
        "## A1, return to the meter",
        "",
        f"Loops: reference points more than {LOOP_GAP_S:.0f} s apart and under {LOOP_DIST_M} m "
        f"apart (3-D). Starts within {LOOP_SEPARATION_S:.0f} s of each other form one cluster, "
        "and each cluster keeps its closest pair. r is the horizontal error of ARKit's displacement "
        "at the return; the peak is the largest such error at the samples between.",
        "",
        f"Loops: {r1['count']} (MARViN {r1['count_by_dataset']['marvin']}, ADVIO "
        f"{r1['count_by_dataset']['advio']}). Closest revisit per ADVIO walk, within the span "
        "where ARKit tracks: "
        + ", ".join(
            f"{name} {d:.2f} m" for name, d in r1["closest_revisit_m"].items() if "advio" in name
        )
        + ". On 20 and 21 the truth comes back closer to where it was in its first 2 s, but ARKit "
        "only starts tracking about 4 s in.",
        "",
    ]
    if r1["count"]:
        L += [
            f"Peak within max(2r, 2 in): {sum(x['ok'] for x in r1['loops'])} of {r1['count']} "
            f"({100 * r1['share_ok']:.0f}%). Criterion: at least {int(100 * LOOP_SHARE)}% of at "
            f"least {MIN_LOOPS} loops. **{'PASS' if r1['pass'] else 'FAIL'}.**",
            "",
            f"Not graded: median r {r1['r_in']['median']:.1f} in. Peak / r: median "
            f"{r1['peak_over_r']['median']:.1f}, p90 {r1['peak_over_r']['p90']:.1f}. Peak / "
            f"(0.16 x largest excursion): median {r1['peak_over_allowance']['median']:.2f}, p90 "
            f"{r1['peak_over_allowance']['p90']:.2f}; within the allowance in "
            f"{100 * r1['peak_over_allowance']['share_within']:.0f}% of loops. Checks not in the "
            f"pre-registration: one loop per walk (its closest pair), {r1['one_per_walk']['count']} "
            f"loops, {100 * r1['one_per_walk']['share_ok']:.0f}% within the bound; one heading "
            f"offset per walk (its circular mean) instead of the sample at t0, "
            f"{100 * r1['walk_offset_share_ok']:.0f}% within the bound.",
            "",
            "Added after the run: a scale or heading error grows with distance from the start and "
            "cancels on return, so r cannot see it. With each walk's own scale divided out of "
            f"ARKit, {100 * r1['walk_scale_removed_share_ok']:.0f}% of loops fall within the bound.",
            "",
            "| Walk | t0 (s) | t1 (s) | Gap (m) | Path (m) | Largest excursion (m) | r (in) | Peak (in) | max(2r, 2 in) | Within | 0.16 x excursion (in) | Peak, walk offset (in) |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for x in r1["loops"]:
            L.append(
                f"| {x['walk']} | {x['t0_s']:.0f} | {x['t1_s']:.0f} | {x['ref_gap_m']:.2f} | "
                f"{x['path_m']:.0f} | {x['excursion_m']:.1f} | {x['r_in']:.1f} | {x['peak_in']:.1f} | "
                f"{x['bound_in']:.1f} | {'yes' if x['ok'] else 'no'} | {x['allowance_in']:.1f} | "
                f"{x['peak_walk_offset_in']:.1f} |"
            )
    L += [
        "",
        "## A2, scale within a walk",
        "",
        f"Local scale: ARKit's straight-line displacement over the reference's, on windows of 5 m "
        f"and 10 m of reference path starting every {SCALE_EVERY_M:.0f} m. Windows whose "
        f"reference chord is under {MIN_CHORD_SHARE:.0%} of their path (the walker turned back) "
        "are left out. Spread is within-walk: SD about each walk's mean, and 1.4826 x MAD about "
        "each walk's median, pooled over walks. The split fits SD(L)^2 = persistent^2 + (c/L)^2 "
        "to the 5 m and 10 m spreads. The part falling as 1/L is endpoint noise from either track, "
        "the reference's jitter included.",
        "",
        "| Data | 5 m windows (turning, left out) | SD 5 m | Robust SD 5 m | SD 10 m | Robust SD 10 m | 1/L part at 5 m (SD / robust) | Persistent part (SD / robust) | Criterion |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    names = {"marvin": "MARViN", "advio": "ADVIO"}
    verdicts = {"pass": "under 1%: PASS", "drop": "over 3%: drop", "between": "1% to 3%: neither"}
    for d, x in r2.items():
        L.append(
            f"| {names[d]} | {x['windows_5m']} ({x['windows_dropped_as_turning'][5.0]}) | "
            f"{fmt_pct(x['sd_5m'])} | {fmt_pct(x['robust_sd_5m'])} | {fmt_pct(x['sd_10m'])} | "
            f"{fmt_pct(x['robust_sd_10m'])} | {fmt_pct(x['split_sd']['falls_with_length_at_5m'])} / "
            f"{fmt_pct(x['split_robust_sd']['falls_with_length_at_5m'])} | "
            f"{fmt_pct(x['split_sd']['persistent'])} / {fmt_pct(x['split_robust_sd']['persistent'])} | "
            f"{verdicts[x['verdict']]} |"
        )
    L += [
        "",
        "Added after the run: on MARViN the SD is three times the robust SD because of a few "
        "windows where ARKit barely moves while the reference walks on, the disagreement that put "
        "three atrium walks in doubt in the evals. Share of 5 m windows more than 10% from their "
        "walk's median: "
        + "; ".join(
            f"{names[d]} {100 * x['share_over_10pct_from_walk_median_5m']:.1f}%"
            for d, x in r2.items()
        )
        + ". ADVIO's spread is broad, not a tail. Its truth is an inertial track pinned at fix "
        "points, so its own scale can wander between pins, and that does not fall as 1/L either.",
        "",
        "Per walk, 5 m windows (median local scale, SD, robust SD). MARViN's median is against a "
        "reference whose own scale is unverified; ADVIO's is against GPS.",
        "",
        "| Walk | Windows | Median | SD | Robust SD |",
        "| --- | --- | --- | --- | --- |",
    ]
    for x in r2.values():
        for w in x["walks"]:
            L.append(
                f"| {w['walk']} | {w['windows_5m']} | {w['median_5m']:.3f} | {fmt_pct(w['sd_5m'])} | "
                f"{fmt_pct(w['robust_sd_5m'])} |"
            )
    L += [
        "",
        "## A3, along and across travel",
        "",
        "The evals' windows: from every image (MARViN) or every 0.5 s (ADVIO) until the reference "
        "has covered exactly the distance. The horizontal error splits into the part along the "
        "reference's horizontal displacement and the part across it. p90 of |error|, inches.",
        "",
        "| Data | Walked | Windows | Along | Across | Horizontal total | Along / total | Vertical | 3-D total |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for d, x in r3.items():
        for ft, y in x.items():
            L.append(
                f"| {names[d]} | {ft} ft | {y['windows']} | {y['p90_along_in']:.1f} | "
                f"{y['p90_across_in']:.1f} | {y['p90_horizontal_in']:.1f} | "
                f"{y['along_share_of_total_p90']:.2f} | {y['p90_vertical_in']:.1f} | {y['p90_3d_in']:.1f} |"
            )
    L += [
        "",
        "Criterion at 20 ft: along p90 at most 0.60 of the horizontal total p90. "
        + "; ".join(
            f"{names[d]} {x[20]['along_share_of_total_p90']:.2f}, "
            f"{'PASS' if x[20]['along_share_of_total_p90'] <= 0.60 else 'FAIL'}"
            for d, x in r3.items()
        )
        + ".",
        "",
        "### Straight-path check",
        "",
        "The windows below have reference paths within the given distance of their own "
        "start-to-end line. This checks the along/across split on straight paths. The walks have "
        "no wall annotations, so it cannot identify wall-parallel travel or measure the benefit "
        "of a wall-plane constraint. The widths are untuned and not pre-registered.",
        "",
        "| Data | Walked | Max chord deviation | Windows | Along | Across | Horizontal total | Along / total |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for d, x in r3.items():
        for ft, y in x.items():
            for m, z in y["straight_path"].items():
                if z is None:
                    L.append(f"| {names[d]} | {ft} ft | {m} m | 0 | | | | |")
                    continue
                L.append(
                    f"| {names[d]} | {ft} ft | {m} m | {z['windows']} | {z['p90_along_in']:.1f} | "
                    f"{z['p90_across_in']:.1f} | {z['p90_horizontal_in']:.1f} | "
                    f"{z['along_share_of_total_p90']:.2f} |"
                )
    L += [
        "",
        "## A4, a range to the meter",
        "",
        f"Each window is a session that starts at the meter, where the second phone stays. A simulated range from each window's start to its end: the reference's 3-D distance plus "
        f"N(0, sigma), {RANGE_DRAWS} draws per window (seed {SEED}). ARKit's end moves radially in "
        "the horizontal plane about the start, keeping its height, onto the range. p90 horizontal "
        "error, inches. sigma 0.10 m is graded; 0.05 and 0.20 m are not.",
        "",
        "| Data | Walked | Before | After, 0.05 m | After, 0.10 m | After, 0.20 m |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for d, x in r4.items():
        for ft, y in x.items():
            L.append(
                f"| {names[d]} | {ft} ft | {y['before_p90_in']:.1f} | "
                f"{y['after_p90_in_sigma_0.05']:.1f} | {y['after_p90_in_sigma_0.10']:.1f} | "
                f"{y['after_p90_in_sigma_0.20']:.1f} |"
            )
    L += [
        "",
        "Criterion: p90 at most 6 in at 20 and 30 ft, sigma 0.10 m. "
        + "; ".join(
            f"{names[d]} {x[20]['after_p90_in_sigma_0.10']:.1f} and "
            f"{x[30]['after_p90_in_sigma_0.10']:.1f} in, "
            f"{'PASS' if max(x[20]['after_p90_in_sigma_0.10'], x[30]['after_p90_in_sigma_0.10']) <= 6 else 'FAIL'}"
            for d, x in r4.items()
        )
        + f". The criterion is nearly out of reach at this sigma: the range's own error has a p90 "
        f"of {norm.ppf(0.95) * GRADED_SIGMA_M / INCH:.1f} in, and the correction passes it straight "
        "into the along-travel error while leaving the across-travel error untouched.",
        "",
        "## A5, IMU excitation and local scale (exploratory, not graded)",
        "",
        "ADVIO 5 m windows as in A2. Excitation is the SD of |a| from `iphone/accelerometer.csv` "
        "over the window's time span; the scale error is |local scale - 1| against the "
        "GPS-rescaled truth. A negative Spearman rank correlation would mean more motion goes with "
        "a truer scale. The windows overlap (5 m windows every 1 m), so the independent count is "
        "about a fifth of the window count.",
        "",
        "| Walk | Windows | Median excitation (m/s^2) | Spearman |",
        "| --- | --- | --- | --- |",
    ]
    for name, x in r5.items():
        exc = f"{x['median_excitation_m_s2']:.2f}" if "median_excitation_m_s2" in x else ""
        L.append(f"| {name} | {x['windows']} | {exc} | {x['spearman']:+.2f} |")
    return "\n".join(L) + "\n"


def main() -> None:
    commit = harness_commit()
    if not commit.startswith(EXPECTED_COMMIT):
        raise RuntimeError(
            f"evals harness at {EVALS_HARNESS} is at {commit}, not {EXPECTED_COMMIT}; the loaders "
            "mirror that commit's code"
        )
    mw, dropped = marvin_walks()
    walks = mw + advio_walks()
    meta = {
        "harness_commit": commit,
        "command": "uv run python run.py",
        "walks": {"marvin": len(mw), "advio": len(walks) - len(mw)},
        "marvin_dropped_off_scale": dropped,
        "walk_scale": {w.name: w.scale for w in walks},
        "seed": SEED,
    }
    r1 = a1(walks)
    r2 = a2(walks)
    r3, r4 = a3_a4(walks)
    r5 = a5(walks)
    RESULTS.mkdir(exist_ok=True)
    report = {"meta": meta, "a1": r1, "a2": r2, "a3": r3, "a4": r4, "a5": r5}
    (RESULTS / "drift_anatomy.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    md = markdown(meta, r1, r2, r3, r4, r5)
    (RESULTS / "drift_anatomy.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
