"""Zero-effort metric scale. Monte Carlo on the A8 terms, hypothesis-mixture fusion.

Customers bring no measuring tool. The marked-length row is the published reference only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from server.scale.sim import a8_terms as A8

GRID = np.arange(-0.35, 0.35 + 1e-9, 0.0005)
EPS_OUT = 0.03
OFFCAT = {"w": 0.0, "sd": 0.08}
COVERS_GENERIC = np.array([6.29, 6.35, 6.42, 6.50, 6.60])
COVERS_P = np.array([0.35, 0.20, 0.15, 0.15, 0.15])


class Src:
    def __init__(self, name, observation, present, sd, offsets=None, weights=None, student=False):
        self.name = name
        self.o = observation
        self.present = present
        self.sd = sd
        self.offsets = offsets
        self.weights = weights
        self.student = student

    def loglik(self, idx):
        observation = self.o[idx][:, None]
        theta = GRID[None, :]
        sd = self.sd[idx][:, None] if isinstance(self.sd, np.ndarray) else self.sd
        if self.offsets is None:
            z = (observation - theta) / sd
            if self.student:
                nu = 4.0
                loglik = -(nu + 1) / 2 * np.log1p(z * z / nu)
            else:
                loglik = -0.5 * z * z
            loglik = loglik - np.log(sd)
            return np.logaddexp(np.log(1 - EPS_OUT) + loglik, np.log(EPS_OUT) + np.log(1.0))
        weights = (
            self.weights[idx]
            if self.weights.ndim == 2
            else np.broadcast_to(self.weights, (len(idx), len(self.weights)))
        )
        acc = np.full((len(idx), len(GRID)), -np.inf)
        for index, offset in enumerate(self.offsets):
            z = (observation - theta - offset) / sd
            acc = np.logaddexp(acc, np.log(np.maximum(weights[:, index], 1e-12))[:, None] - 0.5 * z * z - np.log(sd))
        if OFFCAT["w"] > 0 and len(self.offsets) > 1:
            mean_offset = (weights * self.offsets[None, :]).sum(1)[:, None]
            z = (observation - theta - mean_offset) / OFFCAT["sd"]
            acc = np.logaddexp(np.log(1 - OFFCAT["w"]) + acc, np.log(OFFCAT["w"]) - 0.5 * z * z - np.log(OFFCAT["sd"]))
        return np.logaddexp(np.log(1 - EPS_OUT) + acc, np.log(EPS_OUT))


def always():
    return np.ones(A8.N, bool)


def bern(probability):
    return A8.RNG.random(A8.N) < probability


def rec():
    return A8.recon_term()


def arkit(profile, sd):
    error = A8.arkit_draws(profile)
    return Src(f"ARKit {profile}", np.log1p(error), always(), sd, student=True)


def learned_depth():
    error, _values = A8.mdepth_draws()
    return Src("learned depth", np.log1p(error), always(), 0.12, student=True)


def meter(mode, p_table=0.90, p_register=0.85, depth_known=0.3, p_label=0.03):
    if mode == "base_a8":
        error = A8.meter(1.0, 2900.0) + rec()
        return Src("meter base 6.95 in (A8 model)", np.log1p(error), always(), 0.01)
    distance = A8.RNG.uniform(0.9, 1.2, A8.N) / A8.IN
    true_cover = A8.RNG.choice(COVERS_GENERIC, A8.N, p=COVERS_P)
    pixels = 0.7 * np.sqrt(2) / (2900 * 6.4 * A8.IN / (distance * A8.IN))
    if mode == "exact":
        known = bern(0.95 * p_table)
        dual = bern(0.25)
        material_wrong = dual & bern(0.10)
        size_err = np.where(material_wrong, A8.RNG.choice([-1, 1], A8.N) * 0.0205, A8.RNG.normal(0, 0.003, A8.N))
        depth_sd = np.where(known, depth_known, 0.8)
        label_confused = bern(p_label)
    else:
        known = np.zeros(A8.N, bool)
        size_err = np.zeros(A8.N)
        depth_sd = np.full(A8.N, 0.8)
        label_confused = bern(0.10)
    reference = np.where(known, true_cover * (1 + size_err), true_cover)
    depth = A8.RNG.normal(0, 1, A8.N) * depth_sd / distance
    measured = A8.RNG.normal(0, 1, A8.N) * pixels + depth
    wrong_true = A8.RNG.choice([7.065, 7.3, 6.95], A8.N, p=[0.6, 0.15, 0.25])
    true_measured = np.where(label_confused, wrong_true, true_cover) * (1 + measured)
    fidelity = rec()
    if mode == "exact":
        observation = np.log(reference / true_measured) + fidelity
        sd = np.where(known, np.sqrt(0.003**2 + (depth_known / 39) ** 2 + 0.0025**2 + 0.005**2), 0.03)
        generic = Src(
            "",
            np.log(6.40 / true_measured) + fidelity,
            always(),
            0.022,
            offsets=np.log(6.40 / COVERS_GENERIC),
            weights=COVERS_P,
        )
        registered = bern(p_register)
        far = np.log1p(A8.meter(4.5, 1450.0)) + rec()
        observation = np.where(registered, observation, far)
        sd = np.where(registered, sd, 0.03)
        generic.o = np.where(registered, generic.o, far)
        generic.sd = np.where(registered, 0.022, 0.03)
        source = Src(f"meter close-up, exact model (table {p_table:.0%})", observation, always(), sd)
        source.fallback = generic
        source.known = known & registered
        return source
    registered = bern(p_register)
    far = np.log1p(A8.meter(4.5, 1450.0)) + rec()
    return Src(
        "meter close-up, generic cover prior",
        np.where(registered, np.log(6.40 / true_measured) + fidelity, far),
        always(),
        np.where(registered, 0.022, 0.03),
        offsets=np.log(6.40 / COVERS_GENERIC),
        weights=COVERS_P,
    )


def door_leaf(p_present=0.6, p_misres=0.10):
    sizes = np.array([80, 78, 84, 96]) * A8.IN
    prior = np.array([0.80, 0.05, 0.10, 0.05])
    true = A8.RNG.choice(sizes, A8.N, p=prior)
    missed = bern(p_misres)
    true = np.where(missed, true * (1 + A8.RNG.uniform(0.04, 0.09, A8.N)), true)
    measured = true * (1 + A8.RNG.normal(0, 0.0135, A8.N)) * (1 + A8.RNG.normal(0, 0.007, A8.N))
    observation = np.log(80 * A8.IN / measured) + rec()
    return Src(
        "entry door leaf, leaf/frame resolved",
        observation,
        bern(p_present),
        0.016,
        offsets=np.log(80 * A8.IN / sizes),
        weights=prior,
    )


def typed(name, sizes, aspects, prior, p_present, meas_sd, aspect_sd, size_sd, p_gross):
    sizes, aspects, prior = map(np.asarray, (sizes, aspects, prior))
    choice = A8.RNG.choice(len(sizes), A8.N, p=prior)
    aspect = aspects[choice] * (1 + A8.RNG.normal(0, aspect_sd, A8.N))
    log_w = np.log(prior)[None, :] - 0.5 * ((aspect[:, None] / aspects[None, :] - 1) / aspect_sd) ** 2
    weights = np.exp(log_w - log_w.max(1, keepdims=True))
    weights /= weights.sum(1, keepdims=True)
    true = sizes[choice] * (1 + A8.RNG.normal(0, size_sd, A8.N))
    measured = true * (1 + A8.RNG.normal(0, meas_sd, A8.N))
    gross = bern(p_gross)
    measured = np.where(gross, measured * (1 + A8.RNG.choice([-1, 1], A8.N) * A8.RNG.uniform(0.1, 0.3, A8.N)), measured)
    reference = sizes[0]
    return Src(
        name,
        np.log(reference / measured) + rec(),
        bern(p_present),
        float(np.hypot(meas_sd, size_sd)),
        offsets=np.log(reference / sizes),
        weights=weights,
    )


def garage():
    skus = [
        (16, 7, 0.30),
        (16, 8, 0.10),
        (18, 7, 0.05),
        (18, 8, 0.03),
        (8, 7, 0.10),
        (9, 7, 0.25),
        (9, 8, 0.07),
        (10, 7, 0.04),
        (10, 8, 0.03),
        (8, 8, 0.03),
    ]
    widths = [item[0] * A8.FT for item in skus]
    aspects = [item[0] / item[1] for item in skus]
    prior = np.array([item[2] for item in skus])
    prior = prior / prior.sum()
    return typed("garage door, SKU from aspect ratio", widths, aspects, prior, 0.35, 0.01, 0.014, 0.006, 0.05)


def brick():
    course = [2.667, 2.625, 3.0, 3.2, 3.2]
    aspect = [7.625 / 2.25, 8 / 2.25, 9.625 / 2.75, 7.625 / 2.75, 7.625 / 2.8125]
    prior = [0.50, 0.15, 0.10, 0.15, 0.10]
    return typed(
        "brick course, type from face aspect", np.array(course) * A8.IN, aspect, prior, 0.20, 0.0012, 0.02, 0.01, 0.08
    )


def siding(present):
    exposure = np.array([4, 4.5, 5, 6, 7, 8]) * A8.IN
    by_material = {
        "vinyl": [0.45, 0.30, 0.20, 0.05, 0, 0],
        "fiber": [0.10, 0, 0.25, 0.25, 0.40, 0],
        "wood": [0.2, 0, 0.3, 0.3, 0.1, 0.1],
    }
    share = {"vinyl": 0.6, "fiber": 0.25, "wood": 0.15}
    materials = list(by_material)
    material = A8.RNG.choice(3, A8.N, p=[share[name] for name in materials])
    table = np.array([by_material[name] for name in materials])
    picked = np.array([A8.RNG.choice(6, p=table[index]) for index in material])
    called = np.where(bern(0.85), material, (material + A8.RNG.integers(1, 3, A8.N)) % 3)
    weights = table[called] + 1e-6
    weights = weights / weights.sum(1, keepdims=True)
    true = exposure[picked] * (1 + A8.RNG.normal(0, 0.01, A8.N))
    measured = true * (1 + A8.RNG.normal(0, 0.0034, A8.N))
    return Src(
        "siding exposure, material typed",
        np.log(6 * A8.IN / measured) + rec(),
        present,
        0.011,
        offsets=np.log(6 * A8.IN / exposure),
        weights=weights,
    )


def footprint():
    length = A8.RNG.uniform(25, 60, A8.N) * A8.FT
    county = bern(0.5)
    sigma = np.where(county, 1.0, 1.6) * A8.FT
    err = A8.RNG.normal(0, 1, A8.N) * np.sqrt(2) * sigma
    roof = bern(0.7)
    overhang_true = A8.RNG.uniform(0.0, 2.0, A8.N) * A8.FT * 2
    overhang_bias = np.where(roof, A8.RNG.normal(0, 0.3 * A8.FT * np.sqrt(2), A8.N), overhang_true - 1.0 * A8.FT * 2)
    gross = bern(np.where(county, 0.08, 0.15))
    gross_draw = np.where(gross, A8.RNG.choice([-1, 1], A8.N) * A8.RNG.uniform(0.1, 0.3, A8.N), 0)
    observation = np.log((length + err + overhang_bias) / length * (1 + gross_draw)) + rec()
    sd = (
        np.sqrt((np.sqrt(2) * sigma) ** 2 + np.where(roof, (0.3 * A8.FT * np.sqrt(2)) ** 2, (0.58 * A8.FT * 2) ** 2))
        / length
    )
    return Src("footprint wall length (county / Microsoft)", observation, bern(0.6), sd, student=True)


def camera_height():
    return Src(
        "camera height prior", A8.RNG.normal(0, np.hypot(0.095, 0.08) / 1.40, A8.N), always(), 0.09, student=True
    )


def stride():
    return Src(
        "step count x stride prior",
        A8.RNG.normal(0.0, 0.15, A8.N) + A8.RNG.normal(0, 0.05, A8.N),
        always(),
        0.16,
        student=True,
    )


def truedepth():
    ok = bern(0.7)
    error = A8.RNG.normal(0, 0.012, A8.N)
    gross = bern(0.05)
    error = np.where(gross, A8.RNG.uniform(-0.05, 0.05, A8.N), error)
    return Src("TrueDepth meter scan (near-zero effort)", np.log1p(error) + rec(), ok, 0.015)


def fuse(sources, chunk=1000):
    estimate = np.zeros(A8.N)
    low = np.zeros(A8.N)
    high = np.zeros(A8.N)
    for start in range(0, A8.N, chunk):
        idx = np.arange(start, min(A8.N, start + chunk))
        log_posterior = np.zeros((len(idx), len(GRID)))
        for source in sources:
            if getattr(source, "fallback", None) is not None:
                loglik = np.where(source.known[idx][:, None], source.loglik(idx), source.fallback.loglik(idx))
            else:
                loglik = source.loglik(idx)
            log_posterior += np.where(source.present[idx][:, None], loglik, 0.0)
        posterior = np.exp(log_posterior - log_posterior.max(1, keepdims=True))
        posterior /= posterior.sum(1, keepdims=True)
        cumulative = np.cumsum(posterior, 1)
        estimate[idx] = GRID[np.argmax(cumulative >= 0.5, 1)]
        low[idx] = GRID[np.argmax(cumulative >= 0.025, 1)]
        high[idx] = GRID[np.argmax(cumulative >= 0.975, 1)]
    error = np.expm1(estimate)
    bound = (np.expm1(high) - np.expm1(low)) / 2
    covered = (0 >= low) & (0 <= high)
    return error, bound, covered


def summarise(name, error, bound=None, covered=None):
    row = A8.summarise(name, error)
    if bound is not None:
        row["bound95_median_pct"] = float(100 * np.median(bound))
        row["bound95_p90_pct"] = float(100 * np.percentile(bound, 90))
        row["coverage95"] = float(covered.mean())
        for limit in (0.01, 0.015, 0.02, 0.03, 0.06):
            row[f"share_bound_le_{limit}"] = float((bound <= limit).mean())
    return row


def _sources() -> dict:
    sources = {
        "ark_m": arkit("nonlidar_modern", 0.03),
        "ark_o": arkit("nonlidar_2018", 0.10),
        "depth": learned_depth(),
        "meter_exact": meter("exact"),
        "meter_generic": meter("generic"),
        "meter_ds": meter("exact", p_table=0.35),
        "meter_ideal": meter("exact", p_register=1.0),
        "meter_two": meter("exact", p_register=0.95, depth_known=0.15, p_label=0.02),
        "meter_a8": meter("base_a8"),
        "door": door_leaf(),
        "meter_stress": meter("exact", p_register=0.70, depth_known=0.6, p_label=0.08),
        "door_stress": door_leaf(p_misres=0.20),
        "garage": garage(),
        "brick": brick(),
        "footprint": footprint(),
        "cam_h": camera_height(),
        "stride": stride(),
        "truedepth": truedepth(),
    }
    sided = ~sources["brick"].present & bern(0.55)
    sources["siding"] = siding(sided)
    return sources


def _combos() -> dict[str, list[str]]:
    return {
        "ARKit modern only": ["ark_m"],
        "ARKit 2018 only": ["ark_o"],
        "Meter close-up, generic cover prior (+ARKit modern)": ["ark_m", "meter_generic"],
        "Meter close-up, exact model (+ARKit modern)": ["ark_m", "meter_exact"],
        "Meter close-up, exact model (+ARKit 2018)": ["ark_o", "meter_exact"],
        "Meter close-up, exact model, no ARKit": ["meter_exact"],
        "Meter close-up, exact model, public datasheets only (table 35 %) (+ARKit modern)": ["ark_m", "meter_ds"],
        "Meter close-up, exact model, every close-up registers (+ARKit modern)": ["ark_m", "meter_ideal"],
        "Two meter close-ups (head-on + ~30 deg), exact model (+ARKit modern)": ["ark_m", "meter_two"],
        "Two meter close-ups + TrueDepth scan (+ARKit modern)": ["ark_m", "meter_two", "truedepth"],
        "Typed objects + ARKit 2018": ["ark_o", "door", "garage", "brick", "siding"],
        "Typed objects only (door, garage, brick, siding) + ARKit modern": [
            "ark_m",
            "door",
            "garage",
            "brick",
            "siding",
        ],
        "Footprint + ARKit modern": ["ark_m", "footprint"],
        "Passive priors (camera height, stride, depth) + ARKit modern": ["ark_m", "cam_h", "stride", "depth"],
        "Meter exact + typed objects + ARKit modern": ["ark_m", "meter_exact", "door", "garage", "brick", "siding"],
        "All zero-effort (meter exact, objects, footprint, priors, ARKit modern)": [
            "ark_m",
            "meter_exact",
            "door",
            "garage",
            "brick",
            "siding",
            "footprint",
            "cam_h",
            "stride",
            "depth",
        ],
        "Stress: all zero-effort with meter registration 70 %, rim depth +-0.6 in, 8 % contour confusion, 20 % door frame/leaf errors": [
            "ark_m",
            "meter_stress",
            "door_stress",
            "garage",
            "brick",
            "siding",
            "footprint",
            "cam_h",
            "stride",
            "depth",
        ],
        "All zero-effort, ARKit 2018": [
            "ark_o",
            "meter_exact",
            "door",
            "garage",
            "brick",
            "siding",
            "footprint",
            "cam_h",
            "stride",
            "depth",
        ],
        "All zero-effort + TrueDepth meter scan (near-zero effort)": [
            "ark_m",
            "meter_exact",
            "door",
            "garage",
            "brick",
            "siding",
            "footprint",
            "cam_h",
            "stride",
            "depth",
            "truedepth",
        ],
    }


def main(n: int | None = None, out: str | None = None, offcat: float = 0.0) -> list[dict]:
    A8.reset(20000 if n is None else n)
    OFFCAT["w"] = offcat
    sources = _sources()
    extra = {"door2": door_leaf(p_present=1.0, p_misres=0.05), "garage2": garage(), "brick2": brick()}
    combos = _combos()
    rows = []
    kept = {}
    for name, keys in combos.items():
        error, bound, covered = fuse([sources[key] for key in keys])
        row = summarise(name, error, bound, covered)
        rows.append(row)
        kept[name] = (error, bound)
    base_name = "All zero-effort (meter exact, objects, footprint, priors, ARKit modern)"
    base_keys = combos[base_name]
    _error0, bound0 = kept[base_name]
    need = bound0 > 0.02
    garage_ok = sources["garage"].present
    pick = np.where(garage_ok, 1, np.where(sources["brick"].present, 2, 0))
    for key, value in extra.items():
        value.present = need & (pick == {"door2": 0, "garage2": 1, "brick2": 2}[key])
    error1, bound1, covered1 = fuse([sources[key] for key in base_keys] + list(extra.values()))
    row = summarise("All zero-effort + one targeted extra photo when bound > 2 %", error1, bound1, covered1)
    row["extra_photo_rate"] = float(need.mean())
    rows.append(row)
    for tag, meter_key in (("one close-up", "meter_exact"), ("two close-ups", "meter_two")):
        meter_error, meter_bound, meter_covered = fuse([sources["ark_m"], sources[meter_key]])
        object_error, object_bound, _object_covered = fuse(
            [
                sources["ark_m"],
                sources["door"],
                sources["garage"],
                sources["brick"],
                sources["siding"],
                sources["footprint"],
            ]
        )
        delta = np.abs(np.log1p(meter_error) - np.log1p(object_error))
        conflicted = (object_bound < 0.04) & (delta > 3 * np.hypot(meter_bound / 2, object_bound / 2))
        reshoot_error, reshoot_bound, _reshoot_covered = fuse([sources["ark_m"], sources["meter_two"]])
        policy_error = np.where(conflicted, reshoot_error, meter_error)
        policy_bound = np.where(conflicted, np.maximum(reshoot_bound, delta), meter_bound)
        coverage = np.where(conflicted, np.abs(reshoot_error) <= policy_bound, meter_covered)
        row = summarise(
            f"Recommended: meter anchor ({tag}) + ARKit, objects/footprint as a check, re-shoot meter on conflict",
            policy_error,
            policy_bound,
            coverage,
        )
        row["conflict_rate"] = float(conflicted.mean())
        row["abs_err_p90_pct_conflicts"] = (
            float(100 * np.percentile(np.abs(meter_error[conflicted]), 90)) if conflicted.any() else None
        )
        row["abs_err_p90_pct_clean"] = float(100 * np.percentile(np.abs(meter_error[~conflicted]), 90))
        rows.append(row)
    reference = Src("marked length 10 ft", np.log1p(A8.marked_length(10) + rec()), always(), 0.006)
    ref_error, ref_bound, ref_covered = fuse([sources["ark_m"], sources["meter_exact"], reference])
    rows.append(
        summarise(
            "Reference: marked length 10 ft + meter exact + ARKit modern (A8-style)", ref_error, ref_bound, ref_covered
        )
    )
    if out:
        destination = Path(out)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "scale_mc.json").write_text(json.dumps({"n": A8.N, "rows": rows}, indent=1) + "\n")
    return rows


def _cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20000)
    parser.add_argument("--out", default="runs/mc")
    parser.add_argument("--offcat", type=float, default=0.0)
    args = parser.parse_args()
    rows = main(n=args.n, out=args.out, offcat=args.offcat)
    for row in rows:
        print(
            f"{row['method']}: med {row['median_abs_pct']:.2f} p90 {row['p90_abs_pct']:.2f} p95 {row['p95_abs_pct']:.2f}",
            flush=True,
        )


if __name__ == "__main__":
    _cli()
