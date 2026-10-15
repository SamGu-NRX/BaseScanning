"""Point-pair length errors: how far off is the distance between two points the reconstruction gives?

A placement check measures lengths (a clearance, a height, a span), so the score is the error in
the distance between two surface points, compared with the laser scan's distance between the same
two points. Distances do not change under rotation or translation, so no alignment step can hide or
add error: only scale and shape count.

Each evaluated point carries its prediction as `c + s * r`: `r` is the predicted offset from a
known centre `c`, and `s` a global scale factor (1 when the model's metric output is used as is).
For a single image or a pose-free multi-view model `c` is zero and `r` the predicted point. For
per-frame depth placed with known camera poses, `c` is the camera centre and `r` the predicted ray
offset, because scaling a depth map moves points along the rays, not about the origin. Averaging a
point over several views keeps that form: the mean of `c_v + s r_v` is `mean(c) + s mean(r)`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

INCH = 0.0254
BINS_M = ((1.0, 3.0), (3.0, 10.0))


@dataclass(frozen=True)
class Points:
    gt: np.ndarray  # (N, 3) laser-scan positions
    c: np.ndarray  # (N, 3) prediction centre
    r: np.ndarray  # (N, 3) prediction offset: predicted point = c + s r

    def predicted(self, s: float = 1.0) -> np.ndarray:
        return self.c + s * self.r


def sample_pairs(
    gt: np.ndarray, lo: float, hi: float, n: int, rng: np.random.Generator, max_draws: int = 50
) -> np.ndarray:
    """Up to n random index pairs (n, 2) whose ground-truth distance is in [lo, hi)."""
    found: list[np.ndarray] = []
    count = 0
    for _ in range(max_draws):
        i = rng.integers(0, len(gt), size=4 * n)
        j = rng.integers(0, len(gt), size=4 * n)
        d = np.linalg.norm(gt[i] - gt[j], axis=1)
        ok = (d >= lo) & (d < hi) & (i != j)
        found.append(np.c_[i[ok], j[ok]])
        count += int(ok.sum())
        if count >= n:
            break
    pairs = np.concatenate(found) if found else np.empty((0, 2), dtype=int)
    return pairs[:n]


def pair_distances(p: np.ndarray, pairs: np.ndarray) -> np.ndarray:
    return np.linalg.norm(p[pairs[:, 0]] - p[pairs[:, 1]], axis=1)


def scale_for_length(dc: np.ndarray, dr: np.ndarray, d: float) -> float:
    """Scale s > 0 with |dc + s dr| = d: the pair's centre difference dc, offset difference dr and
    true length d. Of two positive roots, the one nearer 1 (the model's own scale) is taken."""
    qa, qb, qc = dr @ dr, 2 * dc @ dr, dc @ dc - d * d
    if qa <= 0:
        raise ValueError("pair has identical predicted offsets; no scale can match it")
    disc = qb * qb - 4 * qa * qc
    if disc < 0:
        raise ValueError(f"no scale makes this pair {d:.3f} m long")
    roots = [(-qb + sgn * np.sqrt(disc)) / (2 * qa) for sgn in (1, -1)]
    positive = [x for x in roots if x > 0]
    if not positive:
        raise ValueError(f"no positive scale makes this pair {d:.3f} m long")
    return float(min(positive, key=lambda x: abs(x - 1)))


def scale_for_known_distance(points: Points, pair: np.ndarray) -> float:
    """Scale s that makes the predicted distance of one pair equal its true (taped) distance.

    Solves |dc + s dr| = d for s > 0, with dc and dr the pair's differences in c and r; with c = 0
    this is simply d / |dr|. With camera centres two positive roots can exist (converging rays can
    shrink or flip the pair); the one nearer 1, the model's own scale, is taken.
    """
    a, b = pair
    d = float(np.linalg.norm(points.gt[a] - points.gt[b]))
    return scale_for_length(points.c[a] - points.c[b], points.r[a] - points.r[b], d)


def length_errors(points: Points, pairs: np.ndarray, s: float = 1.0) -> np.ndarray:
    """Predicted minus true distance for each pair, meters."""
    return pair_distances(points.predicted(s), pairs) - pair_distances(points.gt, pairs)


def summarize(errors_m: np.ndarray, ratios: np.ndarray | None = None) -> dict[str, float]:
    """Median and p90 of |error| in inches, and the median length ratio as a percent scale error.

    Failures (a pair with no prediction, or scaled by a taped reference that could not be matched)
    are infinite errors: they count against the median and p90 instead of being dropped.
    """
    a = np.abs(errors_m) / INCH
    finite = np.isfinite(a)
    # Percentiles interpolate, and inf - inf is NaN, so failures become a finite sentinel first.
    sentinel = 1e12
    q = np.where(finite, a, sentinel)

    def pct(p: float) -> float:
        v = float(np.percentile(q, p))
        return float("inf") if v >= sentinel / 2 else round(v, 2)

    out = {
        "pairs": len(a),
        "failed_pct": round(float(100 * (1 - finite.mean())), 2) if len(a) else 0.0,
        "median_in": pct(50) if len(a) else float("nan"),
        "p90_in": pct(90) if len(a) else float("nan"),
    }
    if ratios is not None and np.isfinite(ratios).any():
        out["scale_error_pct"] = round(float((np.median(ratios[np.isfinite(ratios)]) - 1) * 100), 2)
    return out


def bin_key(b: tuple[float, float]) -> str:
    return f"{b[0]:g}-{b[1]:g}m"


def evaluate_fixed(points: Points, pairs: dict[str, np.ndarray], refs: np.ndarray) -> dict:
    """Length errors on a fixed evaluation set, so every method and view count sees the same pairs.

    `points` rows with NaN predictions have no prediction. `pairs` maps a bin key to index pairs;
    `refs` are the taped reference pairs. Returns {"none" | "one_known_distance": {bin: (errors,
    ratios)}, "refs": count, "ref_failures": count}. A reference fails when either end has no
    prediction or no positive scale matches it; all pairs scaled by a failed reference fail.
    """
    scales: list[float | None] = []
    for ref in refs:
        if not np.isfinite(points.r[ref]).all():
            scales.append(None)
            continue
        try:
            scales.append(scale_for_known_distance(points, ref))
        except ValueError:
            scales.append(None)
    out: dict = {"none": {}, "one_known_distance": {}}
    for key, pr in pairs.items():
        gt_d = pair_distances(points.gt, pr)
        e = length_errors(points, pr)
        e = np.where(np.isnan(e), np.inf, e)
        out["none"][key] = (e, (e + gt_d) / gt_d)
        per_ref = []
        for sc in scales:
            if sc is None:
                per_ref.append(np.full(len(pr), np.inf))
            else:
                es = length_errors(points, pr, sc)
                per_ref.append(np.where(np.isnan(es), np.inf, es))
        e1 = np.concatenate(per_ref) if per_ref else np.empty(0)
        gt_rep = np.tile(gt_d, len(per_ref))
        out["one_known_distance"][key] = (e1, (e1 + gt_rep) / gt_rep)
    out["refs"] = len(scales)
    out["ref_failures"] = sum(sc is None for sc in scales)
    return out


def pool(raws: list[dict]) -> dict[str, dict]:
    """Concatenate `evaluate_fixed` results from several evaluation sets and summarize them."""
    out: dict[str, dict] = {}
    keys = sorted({k for r in raws for k in r["none"]})
    for source in ("none", "one_known_distance"):
        out[source] = {}
        for key in keys:
            parts = [r[source][key] for r in raws if key in r[source]]
            e = np.concatenate([p[0] for p in parts]) if parts else np.empty(0)
            ratios = np.concatenate([p[1] for p in parts]) if parts else np.empty(0)
            out[source][key] = summarize(e, ratios)
    refs = sum(r["refs"] for r in raws)
    out["tape_calibration_success_pct"] = (
        round(100 * (1 - sum(r["ref_failures"] for r in raws) / refs), 1) if refs else None
    )
    return out
