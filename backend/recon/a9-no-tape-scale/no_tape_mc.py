"""A9: zero-effort metric scale. 20 000-house Monte Carlo on top of A8's (same ARKit, learned-depth,
recon-fidelity and edge-precision terms, same RNG seed), with a hypothesis-mixture fusion instead of
A8's single-nominal gating.

    python no_tape_mc.py [--a8 PATH_TO_A8_CODE]  ->  results/no_tape_mc.{json,md}, results/houses.npz

Every source reports o = log(implied scale if its reference had size R_ref). For a discrete set of
possible true sizes S_k (prior pi_k, optionally re-weighted per house by a scale-free cue such as the
object's aspect ratio), the likelihood of the scale error theta is
    p(o | theta) = (1 - eps) * sum_k pi_k N(o - theta - log(R_ref / S_k); sd) + eps * U(-0.5, 0.5)
ARKit, footprint, camera-height, stride and learned depth are Student-t (nu = 4) around their declared
SD. The posterior on a 0.05 % grid gives the estimate (posterior median) and a 95 % interval, whose
half-width is the verdict's rel_bound_95.

Real inputs (measured, see A8 and results/meter_model_gpt-5.5.jsonl): recon fidelity 0.35 %, crisp-edge
0.7 px, door-outline 1.35 %, siding 0.34 %, brick 0.12 %, learned-depth scene errors, ARKit draws from
A1, VLM meter make/model read right on 21/21 checkable plates. Everything else is modelled from
standards, datasheets and published error figures; see the table in A9-no-tape-scale.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--a8", default=str(HERE.parent / "a8-scale"))
ap.add_argument("--out", default=str(HERE / "results"))
ap.add_argument("--offcat", type=float, default=0.0, help="weight of a broad off-catalogue size component")
ARGS, _ = ap.parse_known_args()
sys.modules.setdefault("pycolmap", types.ModuleType("pycolmap"))
sys.path.insert(0, ARGS.a8)
import combine as C8  # noqa: E402

RNG = C8.RNG
N = C8.N
IN = C8.IN
FT = C8.FT
GRID = np.arange(-0.35, 0.35 + 1e-9, 0.0005)
EPS_OUT = 0.03
OFFCAT = {"w": 0.0, "sd": 0.08}  # off-catalogue component for SKU mixtures (set by --offcat)


def rec():
    return C8.recon_term()


# ---------------------------------------------------------------- sources
class Src:
    """o: (N,) observation; present: (N,) bool; either gaussian/student sd, or a hypothesis mixture."""

    def __init__(self, name, o, present, sd, offsets=None, weights=None, student=False):
        self.name, self.o, self.present, self.sd = name, o, present, sd
        self.offsets, self.weights, self.student = offsets, weights, student

    def loglik(self, idx):
        o = self.o[idx][:, None]
        th = GRID[None, :]
        sd = self.sd[idx][:, None] if isinstance(self.sd, np.ndarray) else self.sd
        if self.offsets is None:
            z = (o - th) / sd
            if self.student:
                nu = 4.0
                ll = -(nu + 1) / 2 * np.log1p(z * z / nu)
            else:
                ll = -0.5 * z * z
            ll = ll - np.log(sd)
            return np.logaddexp(np.log(1 - EPS_OUT) + ll, np.log(EPS_OUT) + np.log(1.0))
        w = self.weights[idx] if self.weights.ndim == 2 else np.broadcast_to(self.weights, (len(idx), len(self.weights)))
        acc = np.full((len(idx), len(GRID)), -np.inf)
        for k, off in enumerate(self.offsets):
            z = (o - th - off) / sd
            acc = np.logaddexp(acc, np.log(np.maximum(w[:, k], 1e-12))[:, None] - 0.5 * z * z - np.log(sd))
        if OFFCAT["w"] > 0 and len(self.offsets) > 1:
            mo = (w * self.offsets[None, :]).sum(1)[:, None]
            z = (o - th - mo) / OFFCAT["sd"]
            acc = np.logaddexp(np.log(1 - OFFCAT["w"]) + acc, np.log(OFFCAT["w"]) - 0.5 * z * z - np.log(OFFCAT["sd"]))
        return np.logaddexp(np.log(1 - EPS_OUT) + acc, np.log(EPS_OUT))


def always():
    return np.ones(N, bool)


def bern(p):
    return RNG.random(N) < p


def arkit(profile, sd):
    e = C8.arkit_draws(profile)
    return Src(f"ARKit {profile}", np.log1p(e), always(), sd, student=True)


def learned_depth():
    e, _ = C8.mdepth_draws()
    return Src("learned depth", np.log1p(e), always(), 0.12, student=True)


# meter: contours are cover (model-specific), sealing ring (ANSI 7.065 in; non-standard 7.3 in),
# base flange 6.95 in (ANSI C12.10). Close-up at 0.9-1.2 m, iPhone 1x (f ~2900 px at 4032 px).
COVERS_GENERIC = np.array([6.29, 6.35, 6.42, 6.50, 6.60])  # CENTRON poly / other solid-state / CENTRON glass / older glass (assumed)
COVERS_P = np.array([0.35, 0.20, 0.15, 0.15, 0.15])


def meter(mode, p_table=0.90, p_register=0.85, depth_known=0.3, p_label=0.03):
    """mode: 'exact' (make/model read, dimension table covers it), 'generic' (standard/prior only),
    'base_a8' (A8's base-flange-only model, for reference). p_table: share of meters whose cover
    dimensions are in the table (0.90 with a Base-built table of the ~15 common models; ~0.35 with
    public datasheets only, which give cover diameters for Itron CENTRON). p_register: share of
    close-ups that register into the house SfM; the rest fall back to the meter measured in the walk
    frames at ~4.5 m (A8's 'meter from a house photo' term, SD 3 %)."""
    if mode == "base_a8":
        e = C8.meter(1.0, 2900.0) + rec()
        return Src("meter base 6.95 in (A8 model)", np.log1p(e), always(), 0.01)
    dist = RNG.uniform(0.9, 1.2, N) / IN  # inches
    true_cover = RNG.choice(COVERS_GENERIC, N, p=COVERS_P)
    px = 0.7 * np.sqrt(2) / (2900 * 6.4 * IN / (dist * IN))  # relative edge error on the ellipse major axis
    if mode == "exact":
        known = bern(0.95 * p_table)  # model read right (21/21 real, 95 % assumed) x table covers the model
        dual = bern(0.25)  # models sold with glass and poly covers (CENTRON): material call
        mat_wrong = dual & bern(0.10)
        size_err = np.where(mat_wrong, RNG.choice([-1, 1], N) * 0.0205, RNG.normal(0, 0.003, N))
        depth_sd = np.where(known, depth_known, 0.8)  # in: cover rim offset in front of the socket face
        label_conf = bern(p_label)
    else:
        known = np.zeros(N, bool)
        size_err = np.zeros(N)
        depth_sd = np.full(N, 0.8)
        label_conf = bern(0.10)
    # unknown model: the assumed cover is the prior mean; the true cover is a prior draw
    ref = np.where(known, true_cover * (1 + size_err), true_cover)
    depth = RNG.normal(0, 1, N) * depth_sd / dist
    meas = RNG.normal(0, 1, N) * px + depth
    # label confusion: the ring (7.065 / 7.3 in) or base (6.95) taken for the cover
    wrong_true = RNG.choice([7.065, 7.3, 6.95], N, p=[0.6, 0.15, 0.25])
    true_meas = np.where(label_conf, wrong_true, true_cover) * (1 + meas)
    r = rec()
    if mode == "exact":
        o = np.log(ref / true_meas) + r
        sd = np.where(known, np.sqrt(0.003 ** 2 + (depth_known / 39) ** 2 + 0.0025 ** 2 + 0.005 ** 2), 0.03)
        # unknown-model houses fall back to the generic mixture below
        gen = Src("", np.log(6.40 / true_meas) + r, always(), 0.022,
                  offsets=np.log(6.40 / COVERS_GENERIC), weights=COVERS_P)
        reg = bern(p_register)
        far = np.log1p(C8.meter(4.5, 1450.0)) + rec()
        o = np.where(reg, o, far)
        sd = np.where(reg, sd, 0.03)
        gen.o = np.where(reg, gen.o, far)
        gen.sd = np.where(reg, 0.022, 0.03)
        s = Src(f"meter close-up, exact model (table {p_table:.0%})", o, always(), sd)
        s.fallback, s.known = gen, known & reg
        return s
    reg = bern(p_register)
    far = np.log1p(C8.meter(4.5, 1450.0)) + rec()
    return Src("meter close-up, generic cover prior", np.where(reg, np.log(6.40 / true_meas) + r, far), always(),
               np.where(reg, 0.022, 0.03), offsets=np.log(6.40 / COVERS_GENERIC), weights=COVERS_P)


def door_leaf(p_present=0.6, p_misres=0.10):
    sizes = np.array([80, 78, 84, 96]) * IN
    pri = np.array([0.80, 0.05, 0.10, 0.05])
    true = RNG.choice(sizes, N, p=pri)
    mis = bern(p_misres)
    true = np.where(mis, true * (1 + RNG.uniform(0.04, 0.09, N)), true)
    meas = true * (1 + RNG.normal(0, 0.0135, N)) * (1 + RNG.normal(0, 0.007, N))
    o = np.log(80 * IN / meas) + rec()
    return Src("entry door leaf, leaf/frame resolved", o, bern(p_present), 0.016,
               offsets=np.log(80 * IN / sizes), weights=pri)


def typed(name, sizes, aspects, prior, p_present, meas_sd, aspect_sd, size_sd, p_gross):
    """Discrete SKUs with a scale-free aspect cue: per-house hypothesis weights from the measured aspect."""
    sizes, aspects, prior = map(np.asarray, (sizes, aspects, prior))
    k = RNG.choice(len(sizes), N, p=prior)
    asp = aspects[k] * (1 + RNG.normal(0, aspect_sd, N))
    lw = np.log(prior)[None, :] - 0.5 * ((asp[:, None] / aspects[None, :] - 1) / aspect_sd) ** 2
    w = np.exp(lw - lw.max(1, keepdims=True))
    w /= w.sum(1, keepdims=True)
    true = sizes[k] * (1 + RNG.normal(0, size_sd, N))
    meas = true * (1 + RNG.normal(0, meas_sd, N))
    g = bern(p_gross)
    meas = np.where(g, meas * (1 + RNG.choice([-1, 1], N) * RNG.uniform(0.1, 0.3, N)), meas)
    ref = sizes[0]
    return Src(name, np.log(ref / meas) + rec(), bern(p_present), float(np.hypot(meas_sd, size_sd)),
               offsets=np.log(ref / sizes), weights=w)


def garage():
    # residential sectional doors, width x height ft; aspect w/h; shares assumed (16x7 and 9x7 most common)
    skus = [(16, 7, .30), (16, 8, .10), (18, 7, .05), (18, 8, .03), (8, 7, .10), (9, 7, .25), (9, 8, .07), (10, 7, .04),
            (10, 8, .03), (8, 8, .03)]
    w = [s[0] * FT for s in skus]
    a = [s[0] / s[1] for s in skus]
    p = np.array([s[2] for s in skus]); p /= p.sum()
    return typed("garage door, SKU from aspect ratio", w, a, p, 0.35, meas_sd=0.01, aspect_sd=0.014,
                 size_sd=0.006, p_gross=0.05)


def brick():
    # course height (in) and face aspect (length / height): modular, standard, king, queen, engineer
    course = [2.667, 2.625, 3.0, 3.2, 3.2]
    aspect = [7.625 / 2.25, 8 / 2.25, 9.625 / 2.75, 7.625 / 2.75, 7.625 / 2.8125]
    p = [0.50, 0.15, 0.10, 0.15, 0.10]
    return typed("brick course, type from face aspect", np.array(course) * IN, aspect, p, 0.20,
                 meas_sd=0.0012, aspect_sd=0.02, size_sd=0.01, p_gross=0.08)


def siding(present):
    # exposure (in) by material; material class right 85 %
    ex = np.array([4, 4.5, 5, 6, 7, 8]) * IN
    by = {"vinyl": [.45, .30, .20, .05, 0, 0], "fiber": [.10, 0, .25, .25, .40, 0], "wood": [.2, 0, .3, .3, .1, .1]}
    share = {"vinyl": 0.6, "fiber": 0.25, "wood": 0.15}
    mats = list(by)
    m = RNG.choice(3, N, p=[share[x] for x in mats])
    P = np.array([by[x] for x in mats])
    k = np.array([RNG.choice(6, p=P[i]) for i in m])
    called = np.where(bern(0.85), m, (m + RNG.integers(1, 3, N)) % 3)
    w = P[called] + 1e-6
    w /= w.sum(1, keepdims=True)
    true = ex[k] * (1 + RNG.normal(0, 0.01, N))
    meas = true * (1 + RNG.normal(0, 0.0034, N))
    return Src("siding exposure, material typed", np.log(6 * IN / meas) + rec(), present, 0.011,
               offsets=np.log(6 * IN / ex), weights=w)


def footprint():
    """Wall length vs a GIS footprint edge. County (stereo planimetrics, ASPRS 1"=100' class 1, 1 ft RMSE
    per coordinate; LA County includes overhangs) where available, else Microsoft ML footprints
    (IoU 0.85-0.86; ~0.5 m boundary error assumed). The recon matches roof-edge to roof-edge when the
    fascia ends are reconstructed (70 %), else walls plus an overhang prior."""
    L = RNG.uniform(25, 60, N) * FT
    county = bern(0.5)
    sv = np.where(county, 1.0, 1.6) * FT
    err = RNG.normal(0, 1, N) * np.sqrt(2) * sv
    roof = bern(0.7)
    over_true = RNG.uniform(0.0, 2.0, N) * FT * 2
    over_bias = np.where(roof, RNG.normal(0, 0.3 * FT * np.sqrt(2), N), over_true - 1.0 * FT * 2)
    gross = bern(np.where(county, 0.08, 0.15))
    g = np.where(gross, RNG.choice([-1, 1], N) * RNG.uniform(0.1, 0.3, N), 0)
    o = np.log((L + err + over_bias) / L * (1 + g)) + rec()
    sd = np.sqrt((np.sqrt(2) * sv) ** 2 + np.where(roof, (0.3 * FT * np.sqrt(2)) ** 2, (0.58 * FT * 2) ** 2)) / L
    return Src("footprint wall length (county / Microsoft)", o, bern(0.6), sd, student=True)


def camera_height():
    # phone height over reconstructed ground vs a population prior (stature SD ~9.5 cm mixed-sex US
    # adults, holding-height style SD ~8 cm, on ~1.40 m)
    return Src("camera height prior", RNG.normal(0, np.hypot(0.095, 0.08) / 1.40, N), always(), 0.09, student=True)


def stride():
    # CMPedometer steps (2-7 % error) x stride-from-height prior; slow capture gait: stride SD ~15 % (published
    # distance errors 10-40 %, +43 % +- 42 % in PAD patients)
    return Src("step count x stride prior", RNG.normal(0.0, 0.15, N) + RNG.normal(0, 0.05, N), always(), 0.16, student=True)


def truedepth():
    # front TrueDepth scan of the meter/wall at 30-45 cm: plane-distance error 0.05-0.36 mm lab; max 5 % in
    # daylight at up to 1 m; outdoors in direct sun the IR pattern is washed out (assumed 30 % failure)
    ok = bern(0.7)
    e = RNG.normal(0, 0.012, N)
    g = bern(0.05)
    e = np.where(g, RNG.uniform(-0.05, 0.05, N), e)
    return Src("TrueDepth meter scan (near-zero effort)", np.log1p(e) + rec(), ok, 0.015)


# ---------------------------------------------------------------- fusion
def fuse(srcs, chunk=1000):
    est = np.zeros(N)
    lo = np.zeros(N)
    hi = np.zeros(N)
    for a in range(0, N, chunk):
        idx = np.arange(a, min(N, a + chunk))
        LL = np.zeros((len(idx), len(GRID)))
        for s in srcs:
            if getattr(s, "fallback", None) is not None:
                ll = np.where(s.known[idx][:, None], s.loglik(idx), s.fallback.loglik(idx))
            else:
                ll = s.loglik(idx)
            LL += np.where(s.present[idx][:, None], ll, 0.0)
        P = np.exp(LL - LL.max(1, keepdims=True))
        P /= P.sum(1, keepdims=True)
        c = np.cumsum(P, 1)
        est[idx] = GRID[np.argmax(c >= 0.5, 1)]
        lo[idx] = GRID[np.argmax(c >= 0.025, 1)]
        hi[idx] = GRID[np.argmax(c >= 0.975, 1)]
    e = np.expm1(est)
    bound = (np.expm1(hi) - np.expm1(lo)) / 2
    covered = (0 >= lo) & (0 <= hi)
    return e, bound, covered


def summarise(name, e, bound=None, covered=None):
    r = C8.summarise(name, e)
    if bound is not None:
        r["bound95_median_pct"] = float(100 * np.median(bound))
        r["bound95_p90_pct"] = float(100 * np.percentile(bound, 90))
        r["coverage95"] = float(covered.mean())
        for b in (0.01, 0.015, 0.02, 0.03, 0.06):
            r[f"share_bound_le_{b}"] = float((bound <= b).mean())
    return r


def main():
    OFFCAT["w"] = ARGS.offcat
    out = Path(ARGS.out)
    out.mkdir(parents=True, exist_ok=True)
    S = {
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
    sided = ~S["brick"].present & bern(0.55)
    S["siding"] = siding(sided)
    # targeted extra photo: when the bound is still > 2 %, ask for one head-on photo of the best-typed
    # object on the meter side (garage door, entry door, brick or siding close-up), no tools
    extra = {"door2": door_leaf(p_present=1.0, p_misres=0.05), "garage2": garage(), "brick2": brick()}
    combos = {
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
        "Typed objects only (door, garage, brick, siding) + ARKit modern": ["ark_m", "door", "garage", "brick", "siding"],
        "Footprint + ARKit modern": ["ark_m", "footprint"],
        "Passive priors (camera height, stride, depth) + ARKit modern": ["ark_m", "cam_h", "stride", "depth"],
        "Meter exact + typed objects + ARKit modern": ["ark_m", "meter_exact", "door", "garage", "brick", "siding"],
        "All zero-effort (meter exact, objects, footprint, priors, ARKit modern)": [
            "ark_m", "meter_exact", "door", "garage", "brick", "siding", "footprint", "cam_h", "stride", "depth"],
        "Stress: all zero-effort with meter registration 70 %, rim depth +-0.6 in, 8 % contour confusion, 20 % door frame/leaf errors": [
            "ark_m", "meter_stress", "door_stress", "garage", "brick", "siding", "footprint", "cam_h", "stride", "depth"],
        "All zero-effort, ARKit 2018": [
            "ark_o", "meter_exact", "door", "garage", "brick", "siding", "footprint", "cam_h", "stride", "depth"],
        "All zero-effort + TrueDepth meter scan (near-zero effort)": [
            "ark_m", "meter_exact", "door", "garage", "brick", "siding", "footprint", "cam_h", "stride", "depth", "truedepth"],
    }
    rows, keep = [], {}
    for name, keys in combos.items():
        e, b, cov = fuse([S[k] for k in keys])
        r = summarise(name, e, b, cov)
        rows.append(r)
        keep[name] = (e, b)
        print(f"{name}: med {r['median_abs_pct']:.2f} p90 {r['p90_abs_pct']:.2f} 26ft6 {r['share_26ft_within_6in']:.2f} "
              f"15ft6 {r['share_15ft_within_6in']:.2f} bound med {r['bound95_median_pct']:.2f} cov {r['coverage95']:.2f}", flush=True)
    # targeted extra photo on the best zero-effort combo
    base_keys = combos["All zero-effort (meter exact, objects, footprint, priors, ARKit modern)"]
    e0, b0 = keep["All zero-effort (meter exact, objects, footprint, priors, ARKit modern)"]
    need = b0 > 0.02
    g_ok = S["garage"].present
    pick = np.where(g_ok, 1, np.where(S["brick"].present, 2, 0))  # 0 door, 1 garage, 2 brick
    for k, v in extra.items():
        v.present = need & (pick == {"door2": 0, "garage2": 1, "brick2": 2}[k])
    e1, b1, cov1 = fuse([S[k] for k in base_keys] + list(extra.values()))
    r = summarise("All zero-effort + one targeted extra photo when bound > 2 %", e1, b1, cov1)
    r["extra_photo_rate"] = float(need.mean())
    rows.append(r)
    keep[r["method"]] = (e1, b1)
    print(r["method"], r["median_abs_pct"], r["p90_abs_pct"], r["share_26ft_within_6in"], r["extra_photo_rate"])
    # recommended policy: the meter (+ARKit) is the anchor; typed objects and the footprint only check it.
    # Conflict when the object-only estimate is itself confident (bound < 4 %) and disagrees with the
    # anchor by more than 3 joint SDs: status unverified, bound widened to cover both, one extra
    # meter close-up requested (re-measured with the two-close-up model).
    for tag, mk in (("one close-up", "meter_exact"), ("two close-ups", "meter_two")):
        em, bm, cm = fuse([S["ark_m"], S[mk]])
        eo, bo, co = fuse([S["ark_m"], S["door"], S["garage"], S["brick"], S["siding"], S["footprint"]])
        d = np.abs(np.log1p(em) - np.log1p(eo))
        conf = (bo < 0.04) & (d > 3 * np.hypot(bm / 2, bo / 2))
        e3, b3, c3 = fuse([S["ark_m"], S["meter_two"]])
        e_pol = np.where(conf, e3, em)
        b_pol = np.where(conf, np.maximum(b3, d), bm)
        cov = np.where(conf, np.abs(e3) <= b_pol, cm)
        r = summarise(f"Recommended: meter anchor ({tag}) + ARKit, objects/footprint as a check, re-shoot meter on conflict",
                      e_pol, b_pol, cov)
        r["conflict_rate"] = float(conf.mean())
        r["abs_err_p90_pct_conflicts"] = float(100 * np.percentile(np.abs(em[conf]), 90)) if conf.any() else None
        r["abs_err_p90_pct_clean"] = float(100 * np.percentile(np.abs(em[~conf]), 90))
        rows.append(r)
        keep[r["method"]] = (e_pol, b_pol)
        print(r["method"], r["median_abs_pct"], r["p90_abs_pct"], r["share_26ft_within_6in"], r["conflict_rate"],
              r["abs_err_p90_pct_conflicts"], r["abs_err_p90_pct_clean"], flush=True)
    # reference: A8's recommended tape path, same machinery
    tp = Src("tape 10 ft", np.log1p(C8.tape(10) + rec()), always(), 0.006)
    e2, b2, cov2 = fuse([S["ark_m"], S["meter_exact"], tp])
    rows.append(summarise("Reference: tape 10 ft + meter exact + ARKit modern (A8-style, needs a tape)", e2, b2, cov2))
    keep[rows[-1]["method"]] = (e2, b2)
    json.dump({"n": N, "rows": rows}, open(out / "no_tape_mc.json", "w"), indent=1)
    np.savez_compressed(out / "houses.npz", **{f"{i}_err": v[0].astype(np.float32) for i, v in enumerate(keep.values())},
                        **{f"{i}_bound": v[1].astype(np.float32) for i, v in enumerate(keep.values())},
                        names=np.array(list(keep)))
    hdr = ("| Method | abs scale err median / p90 / p95 | 15 ft err median / p90 (in) | 26 ft err median / p90 (in) | "
           "15 ft within 6 in | 26 ft within 6 in | 15 ft within 2 in | 95 % bound median / p90 | coverage of 95 % bound |")
    lines = [hdr, "|" + "---|" * 9]
    for r in rows:
        lines.append(f"| {r['method']} | {r['median_abs_pct']:.1f} / {r['p90_abs_pct']:.1f} / {r['p95_abs_pct']:.1f} % | "
                     f"{r['err_15ft_in_median']:.1f} / {r['err_15ft_in_p90']:.1f} | {r['err_26ft_in_median']:.1f} / {r['err_26ft_in_p90']:.1f} | "
                     f"{100 * r['share_15ft_within_6in']:.0f} % | {100 * r['share_26ft_within_6in']:.0f} % | {100 * r['share_15ft_within_2in']:.0f} % | "
                     f"{r.get('bound95_median_pct', 0):.1f} / {r.get('bound95_p90_pct', 0):.1f} % | {100 * r.get('coverage95', 0):.0f} % |")
    (out / "no_tape_mc.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
