"""Fit the drift parameters of each profile to PR #12's measured ARKit error, then write
profiles.json and calibration.md.

    python -m packetgen.calibrate

The metric reproduces PR #12's `evals/modern_arkit.py::walk_errors`: windows start at every frame
and end where the true horizontal path has grown by D; the error is |ARKit displacement turned by
the heading offset at the window start - true displacement|. Simulated walks are house-like: a
person walking along walls with the camera facing the wall, 0.3 m between samples, 45 m long.

Targets (inches, median / p90 at 3, 10, 20, 30 ft of walking):
- lidar_modern: PR #12 results/modern_arkit.md, "Position error after walking", column
  "As tracked, without those walks" (MARViN, iPhone 14 Pro Max with LiDAR, 35 outdoor walks
  minus those with >10% scale disagreement). Scale model fitted to "30 of 35 walks within 2%"
  and the per-site spread (0.28-0.75%) plus site offsets (-1.7% to +1.2%).
- nonlidar_2018: PR #12 results/advio_drift.md. Scale per walk against the GPS-rescaled truth
  (0.838, 0.943, 0.951). Beyond-scale drift: the per-sequence three-cornered-hat 1-sigma of ARKit
  alone (about 3 / 6 / 13 / 18 in at 3 / 10 / 20 / 30 ft; negative splits dropped), turned into a
  2-D position error (median 1.18 sigma, p90 2.15 sigma). One of four walks lost tracking.
- nonlidar_modern: no measurement exists (PR #7's tape test has not run). Assumed halfway
  (geometric mean) between the two measured profiles for drift, and PR #12's `modern_assumed`
  scale (2% short) with a 2% spread. Replace with the first real non-LiDAR phone session.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

from .noise import PROFILES_PATH, Profile, ScaleComponent

FEET = 0.3048
INCH = 0.0254
DISTANCES_FT = (3, 10, 20, 30)

LIDAR_TARGET_IN = {3: (1.4, 4.0), 10: (3.1, 8.6), 20: (5.1, 13.4), 30: (6.9, 18.5)}
ADVIO_SIGMA_IN = {3: 3.0, 10: 6.0, 20: 13.0, 30: 18.0}
ADVIO_TARGET_IN = {d: (1.18 * s, 2.15 * s) for d, s in ADVIO_SIGMA_IN.items()}
ADVIO_SCALES = (0.8377, 0.9425, 0.9509)


def simulate_walks(n_walks: int, n_steps: int, rng: np.random.Generator):
    """House-like walks: y-up positions (W, N, 3) and camera headings (W, N)."""
    step = 0.3
    heading = np.zeros((n_walks, n_steps))
    h = rng.uniform(-np.pi, np.pi, n_walks)
    for i in range(n_steps):
        h = h + rng.normal(0, np.radians(4), n_walks)
        turn = rng.random(n_walks) < 0.015
        h = h + turn * rng.choice([-np.pi / 2, np.pi / 2], n_walks)
        heading[:, i] = h
    d = np.stack([np.sin(heading), np.zeros_like(heading), np.cos(heading)], axis=2) * step
    d[:, 0] = 0
    p = np.cumsum(d, axis=1)
    p[:, :, 1] = 1.4 + rng.normal(0, 0.03, (n_walks, n_steps))
    cam_heading = heading + np.pi / 2 + rng.normal(0, np.radians(10), (n_walks, n_steps))
    return p, cam_heading


def window_errors(est: np.ndarray, truth: np.ndarray, delta: np.ndarray, d_m: float) -> np.ndarray:
    """PR #12 metric for one walk: |turn(est displacement, delta_i) - true displacement|."""
    walked = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(truth[:, [0, 2]], axis=0), axis=1))])
    i = np.arange(len(truth))
    target = walked + d_m
    j0 = np.searchsorted(walked, target)
    keep = (j0 < len(truth)) & (j0 > 0)
    i, j0, target = i[keep], j0[keep], target[keep]
    frac = (target - walked[j0 - 1]) / (walked[j0] - walked[j0 - 1])

    def at(p):
        return p[j0 - 1] * (1 - frac[:, None]) + p[j0] * frac[:, None]

    de = at(est) - est[i]
    dt = at(truth) - truth[i]
    a = delta[i]
    turned = np.stack([np.cos(a) * de[:, 0] + np.sin(a) * de[:, 2], de[:, 1], -np.sin(a) * de[:, 0] + np.cos(a) * de[:, 2]], axis=1)
    return np.linalg.norm(turned - dt, axis=1)


class Simulator:
    """Common random numbers, so the objective is smooth in the parameters."""

    def __init__(self, n_walks=160, n_steps=150, seed=7):
        rng = np.random.default_rng(seed)
        self.p, self.cam_h = simulate_walks(n_walks, n_steps, rng)
        W, N = self.p.shape[:2]
        self.z_yaw = rng.normal(size=(W, N))
        self.z_drift = rng.normal(size=(W, N, 3))
        self.z_jit = rng.normal(size=(W, N, 3))
        self.z_jyaw = rng.normal(size=(W, N))
        self.u_scale = rng.random(W)
        self.z_scale = rng.normal(size=W)
        self.pick_scale = rng.random(W)
        self.z_sess = rng.normal(size=W)

    def scales(self, prof: Profile) -> np.ndarray:
        w = np.array([c.weight for c in prof.scale])
        cum = np.cumsum(w / w.sum())
        k = np.searchsorted(cum, self.pick_scale)
        mean = np.array([c.mean for c in prof.scale])[k]
        sd = np.array([c.sd for c in prof.scale])[k]
        return 1.0 + np.clip(mean + sd * self.z_scale, *prof.scale_clip)

    def errors(self, prof: Profile, scales: np.ndarray | None = None) -> dict[int, np.ndarray]:
        p = self.p
        W, N = p.shape[:2]
        step = np.diff(p, axis=1, prepend=p[:, :1])
        ds = np.linalg.norm(step[:, :, [0, 2]], axis=2)
        m = np.exp(self.z_sess * prof.session_sigma_lognormal)[:, None]
        yaw = np.cumsum(self.z_yaw * m * np.radians(prof.rw_yaw_deg_per_sqrt_m) * np.sqrt(ds), axis=1)
        sig = np.array([prof.rw_pos_h_m_per_sqrt_m, prof.rw_pos_v_m_per_sqrt_m, prof.rw_pos_h_m_per_sqrt_m])
        drift = np.cumsum(self.z_drift * np.sqrt(ds)[:, :, None] * m[:, :, None] * sig, axis=1)
        k = self.scales(prof) if scales is None else scales
        cy, sy = np.cos(yaw), np.sin(yaw)
        rs = np.stack([cy * step[:, :, 0] + sy * step[:, :, 2], step[:, :, 1], -sy * step[:, :, 0] + cy * step[:, :, 2]], axis=2)
        est = p[:, :1] + k[:, None, None] * np.cumsum(rs, axis=1) + drift + self.z_jit * m[:, :, None] * prof.jitter_pos_m
        delta = -(yaw + self.z_jyaw * m * np.radians(prof.jitter_rot_deg))
        out = {}
        for ft in DISTANCES_FT:
            out[ft] = np.concatenate([window_errors(est[w], p[w], delta[w], ft * FEET) for w in range(W)])
        return out

    def stats_in(self, prof: Profile, scales=None) -> dict[int, tuple[float, float]]:
        e = self.errors(prof, scales)
        return {ft: (float(np.median(v) / INCH), float(np.percentile(v, 90) / INCH)) for ft, v in e.items()}


PARAMS = ("jitter_pos_m", "rw_pos_h_m_per_sqrt_m", "rw_yaw_deg_per_sqrt_m", "session_sigma_lognormal")


def fit(sim: Simulator, prof: Profile, target: dict, free: tuple[str, ...], scales=None, weights=None) -> Profile:
    """Nelder-Mead on the log of the free parameters, best of several starts."""
    weights = weights or {3: 0.5, 10: 1.0, 20: 1.0, 30: 1.0}
    v_ratio = prof.rw_pos_v_m_per_sqrt_m / max(prof.rw_pos_h_m_per_sqrt_m, 1e-12)

    def with_params(x):
        q = Profile.from_dict(prof.to_dict())
        for name, v in zip(free, np.exp(x)):
            setattr(q, name, float(v))
        q.rw_pos_v_m_per_sqrt_m = v_ratio * q.rw_pos_h_m_per_sqrt_m
        return q

    def objective(x):
        st = sim.stats_in(with_params(x), scales)
        return sum(
            weights[ft] * (np.log(st[ft][0] / target[ft][0]) ** 2 + np.log(st[ft][1] / target[ft][1]) ** 2)
            for ft in DISTANCES_FT
        )

    x0 = np.log([max(getattr(prof, n), 1e-3) for n in free])
    best = None
    for shift in (0.0, -1.0, 1.0):
        res = minimize(objective, x0 + shift, method="Nelder-Mead", options={"xatol": 1e-3, "fatol": 1e-6, "maxiter": 600})
        if best is None or res.fun < best.fun:
            best = res
    out = with_params(best.x)
    out.evidence = dict(out.evidence, fit_objective=float(best.fun), fitted=list(free))
    return out


# MARViN's heading offset holds to 0.6 deg (median walk, circular std over 83-196 m). For a yaw
# random walk of s per sqrt m, the spread about its mean over a walk of L m is s * sqrt(L / 6), so
# s = 0.6 / sqrt(140 / 6). It includes the COLMAP reference's own heading error: an upper bound.
LIDAR_YAW_DEG_PER_SQRT_M = 0.6 / np.sqrt(140 / 6)


def base_profiles() -> dict[str, Profile]:
    common = dict(scale_clip=(-0.10, 0.10), init_seconds=1.0, limited_inflation=3.0)
    lidar = Profile(
        name="lidar_modern",
        description="Current LiDAR iPhone (12 Pro and later Pro models). Calibrated on MARViN (iPhone 14 Pro Max, ARKit 6) via PR #12.",
        lidar=True,
        scale=[ScaleComponent(0.88, 0.001, 0.011), ScaleComponent(0.12, 0.0, 0.04)],
        jitter_pos_m=0.01,
        jitter_rot_deg=0.05,
        rw_pos_h_m_per_sqrt_m=0.01,
        rw_pos_v_m_per_sqrt_m=0.005,
        rw_yaw_deg_per_sqrt_m=float(LIDAR_YAW_DEG_PER_SQRT_M),
        session_sigma_lognormal=0.4,
        reset_probability=0.02,
        focal_sd_rel=0.003,
        principal_sd_px=2.0,
        excessive_motion_deg_per_s=120.0,
        insufficient_features_min=40,
        **common,
    )
    advio = Profile(
        name="nonlidar_2018",
        description="2018-era non-LiDAR iPhone (iPhone 6s, ARKit 1.5). Calibrated on ADVIO via PR #12. Worst case.",
        lidar=False,
        scale=[ScaleComponent(1 / 3, s - 1, 0.01) for s in ADVIO_SCALES],
        jitter_pos_m=0.03,
        jitter_rot_deg=0.2,
        rw_pos_h_m_per_sqrt_m=0.03,
        rw_pos_v_m_per_sqrt_m=0.015,
        rw_yaw_deg_per_sqrt_m=0.3,
        session_sigma_lognormal=0.3,
        reset_probability=0.25,
        focal_sd_rel=0.005,
        principal_sd_px=4.0,
        excessive_motion_deg_per_s=60.0,
        insufficient_features_min=80,
        **dict(common, scale_clip=(-0.25, 0.10)),
    )
    return {"lidar_modern": lidar, "nonlidar_2018": advio}


def main() -> None:
    sim = Simulator()
    profiles = base_profiles()

    lidar = fit(sim, profiles["lidar_modern"], LIDAR_TARGET_IN, free=("jitter_pos_m", "rw_pos_h_m_per_sqrt_m", "session_sigma_lognormal"))
    lidar.evidence.update(
        source="PR #12 experiments/evals/results/modern_arkit.md (MARViN iPhone 14 Pro Max, 35 walks)",
        target_in=LIDAR_TARGET_IN,
        achieved_in=sim.stats_in(lidar),
        metric="position error after walking D ft, as tracked (scale included), walks with >10% scale disagreement excluded",
    )

    advio = fit(sim, profiles["nonlidar_2018"], ADVIO_TARGET_IN, free=("jitter_pos_m", "rw_pos_h_m_per_sqrt_m", "rw_yaw_deg_per_sqrt_m"), scales=np.ones(sim.p.shape[0]))
    advio.evidence.update(
        source="PR #12 experiments/evals/results/advio_drift.md (ADVIO iPhone 6s, walks 20-22; walk 23 lost tracking)",
        target_in=ADVIO_TARGET_IN,
        achieved_in=sim.stats_in(advio, np.ones(sim.p.shape[0])),
        achieved_with_scale_in=sim.stats_in(advio),
        metric="position error after walking D ft with the walk's scale removed; scale drawn separately from the three measured walk scales",
    )

    # The fits push per-frame jitter to zero (the random walk explains the short windows); keep a
    # 2 mm floor so consecutive frames are never exactly consistent.
    for prof in (lidar, advio):
        prof.jitter_pos_m = max(prof.jitter_pos_m, 0.002)

    modern = Profile.from_dict(lidar.to_dict())
    modern.name = "nonlidar_modern"
    modern.description = (
        "Current non-LiDAR iPhone. ASSUMED, no measurement exists: drift is the geometric mean of the "
        "LiDAR and 2018 profiles; scale is PR #12's modern_assumed (2% short) with a 2% spread."
    )
    modern.lidar = False
    modern.scale = [ScaleComponent(0.9, -0.02, 0.02), ScaleComponent(0.1, -0.04, 0.05)]
    modern.scale_clip = (-0.15, 0.10)
    for attr in ("session_sigma_lognormal", "jitter_pos_m", "jitter_rot_deg", "rw_pos_h_m_per_sqrt_m", "rw_pos_v_m_per_sqrt_m", "rw_yaw_deg_per_sqrt_m",
                 "focal_sd_rel", "principal_sd_px"):
        setattr(modern, attr, float(np.sqrt(getattr(lidar, attr) * getattr(advio, attr))))
    modern.reset_probability = 0.06
    modern.excessive_motion_deg_per_s = 90.0
    modern.insufficient_features_min = 60
    modern.evidence = {
        "source": "assumption (see description); PR #12 evals/ar_poses.py modern_assumed for the scale",
        "achieved_in": sim.stats_in(modern),
        "metric": "same as lidar_modern (scale included)",
    }

    out = {"profiles": {p.name: p.to_dict() for p in (lidar, modern, advio)}}
    PROFILES_PATH.write_text(json.dumps(out, indent=2, default=float) + "\n")
    write_report(out["profiles"], Path(__file__).resolve().parent.parent / "calibration.md")
    print(json.dumps({k: v["evidence"].get("achieved_in") for k, v in out["profiles"].items()}, indent=1))


def write_report(profiles: dict, path: Path) -> None:
    lines = [
        "# Noise profile calibration (generated by `python -m packetgen.calibrate`)",
        "",
        "Position error after walking D ft, inches, median / p90, PR #12's metric (displacement turned by the heading offset at the window start). Simulated house walks: 160 walks of 45 m, 0.3 m steps.",
        "",
        "| Profile | D | Target | Achieved (fit metric) | Achieved with the profile's scale draws |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, p in profiles.items():
        ev = p["evidence"]
        tgt = ev.get("target_in")
        ach = ev.get("achieved_in")
        ach_s = ev.get("achieved_with_scale_in", ach)
        for ft in DISTANCES_FT:
            t = tgt.get(ft) if isinstance(tgt, dict) and ft in tgt else (tgt.get(str(ft)) if isinstance(tgt, dict) else None)
            a = ach.get(ft) if ft in ach else ach.get(str(ft))
            s = ach_s.get(ft) if ft in ach_s else ach_s.get(str(ft))
            tt = f"{t[0]:.1f} / {t[1]:.1f}" if t else "none (assumed)"
            lines.append(f"| {name} | {ft} ft | {tt} | {a[0]:.1f} / {a[1]:.1f} | {s[0]:.1f} / {s[1]:.1f} |")
    lines += [
        "",
        "## Fitted parameters",
        "",
        "| Profile | scale components (weight, mean, sd) | session multiplier (log-normal sigma) | jitter pos (cm) | jitter rot (deg) | pos random walk h / v (cm per sqrt m) | yaw random walk (deg per sqrt m) | reset probability |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, p in profiles.items():
        sc = "; ".join(f"{c['weight']:.2f}, {c['mean'] * 100:+.1f}%, {c['sd'] * 100:.1f}%" for c in p["scale"])
        lines.append(
            f"| {name} | {sc} | {p['session_sigma_lognormal']:.2f} | {p['jitter_pos_m'] * 100:.2f} | {p['jitter_rot_deg']:.2f} | "
            f"{p['rw_pos_h_m_per_sqrt_m'] * 100:.2f} / {p['rw_pos_v_m_per_sqrt_m'] * 100:.2f} | {p['rw_yaw_deg_per_sqrt_m']:.3f} | {p['reset_probability']:.2f} |"
        )
    lines += [
        "",
        "Checks and caveats:",
        "- lidar_modern's yaw random walk is not fitted: it is fixed by MARViN's heading steadiness (0.6 deg median circular spread over 83-196 m walks; spread = s * sqrt(L / 6) gives 0.124 deg per sqrt m at L = 140 m). Fitting it freely gave 0.59, which contradicts that measurement. The session multiplier for nonlidar_2018 is fixed at 0.3 because its targets were built from a Gaussian split and carry no tail information.",
        "- MARViN's reference is COLMAP of unverifiable scale, so the LiDAR targets are an upper bound on ARKit's own error; ADVIO's split assumes independent errors.",
        "- nonlidar_modern is an assumption. The PR #7 tape protocol on a current non-LiDAR iPhone is the measurement that should replace it.",
    ]
    path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
