"""Run the relative-error study: provenance audit, error-field sweep, sensitivity,
solver comparator.

Writes everything under results/. Deterministic: fixed seeds, no wall-clock
input. The sweep is 2 phone classes x 2 regimes x 5 residual correlation
lengths x 3 walked distances x 8 separations x 20,000 draws per cell, a few
seconds on one core.

Modes:

- `python run.py --freeze-manifest manifest.json` resolves the server refs and
  freezes every parameter, seed, phone-class constant and source-file hash the
  study consumes. Commit the manifest before running.
- `python run.py --manifest manifest.json` verifies the frozen inputs against
  the working tree (any source-hash or parameter drift fails the run) and
  executes the study on the frozen trajectories. An assumed correlation is not
  measured calibration: the residual correlation length is a swept assumption,
  and the manifest says so.
- `python run.py --replay results` re-runs the study and asserts every result
  artifact is byte-identical to the committed copy.

`--skip-comparator` runs the sweep and audit without the shipped-solver
comparator (which needs both refs in the local clone).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import asdict
from pathlib import Path

import numpy as np
from scipy import stats

import audit as audit_mod
from comparator import resolve_ref
from comparator import run as run_comparator
from models import (
    DRIFT_PER_FT,
    EPS,
    LOCAL_P90_IN_AT_6FT,
    TAP_BASE_FT,
    Endpoint,
    current_bar,
    local_clearance_bar,
    rate_on_gap_bar,
    rate_only_bar,
    scale_error_bar_factory,
)
from trajectories import (
    MODERN,
    PHONE_2018,
    PhoneClass,
    calibrated_k,
    implied_k,
    tracked_pair,
    tracked_pair_cross_session,
)

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

SEED = 20261009
N_DRAWS = 20_000
N_CAL = 200_000
ELL_FT = (2.0, 5.0, 10.0, 20.0, math.inf)
WALKED_FT = (10.0, 20.0, 30.0)
SEPARATIONS_FT = (1.0, 2.0, 2.7, 2.9, 3.0, 3.3, 4.0, 6.0)
RULE_FT = 3.0

SERVER_REFS = ("main", "origin/t3/server")

# Every artifact a replay compares. The audit's difference scan reads
# experiments/*/results, so committed study results also feed later audits;
# replay pins their bytes.
TRACKED_RESULTS = (
    "relative_error.json",
    "relative_error.md",
    "audit.json",
    "audit.md",
    "comparator.json",
    "comparator.md",
    "sensitivity.md",
)

MANIFEST_NOTES = {
    "marvin_reference": (
        "The MARViN (modern_arkit) reference is a COLMAP metric reconstruction, "
        "not tape- or laser-verified truth; a reference that may be self-scaled "
        "is not independent truth, and its scale spreads inherit that doubt."
    ),
    "correlation": (
        "An assumed correlation is not measured calibration: the residual "
        "correlation length is swept as an assumption, and no committed data "
        "measures a two-tap difference error."
    ),
    "citations": (
        "Rules-value citation review is reused from PR #217 (obv/basescanning-024); "
        "historical measured values are retained in the audit, not restated here."
    ),
    "rule_unchanged": "The default rule (server/rules.yaml) is left unchanged by this study.",
}

MODEL_FNS = {
    "current": current_bar,
    "rate_only": rate_only_bar,
    "rate_on_gap": rate_on_gap_bar,
    "local_clearance": local_clearance_bar,
}
MODEL_ORDER = (
    "current",
    "rate_only",
    "rate_on_gap",
    "scale_error",
    "local_clearance",
    "common_mode_oracle",
)


def model_fns_for(cls: PhoneClass) -> dict:
    """The class-independent bars plus this class's measured-scale bar."""
    fns = dict(MODEL_FNS)
    fns["scale_error"] = scale_error_bar_factory(cls.scale_sd)
    return fns


def outcomes(d: np.ndarray, bar: np.ndarray | float) -> np.ndarray:
    """Vectorized mirror of models.decide."""
    out = np.full(d.shape, "unsure", dtype="U6")
    out[d - bar - RULE_FT > EPS] = "pass"
    out[RULE_FT - (d + bar) > EPS] = "fail"
    return out


def quantile(x: np.ndarray, q: float) -> float:
    return float(np.quantile(x, q))


def calibrate(cls: PhoneClass) -> dict:
    """Fit the residual spread and report how the simulated p90 lands against
    the committed pooled p90 at the fit distances."""
    k = calibrated_k(cls)
    rng = np.random.default_rng(SEED)
    checks = []
    for w in cls.fit_distances_ft:
        eps = rng.normal(cls.scale_mu, cls.scale_sd, size=N_CAL)
        g = rng.standard_normal(N_CAL)
        err = np.abs(w * (eps + k * g))
        p90_in = quantile(err, 0.9) * 12.0
        checks.append(
            {
                "walked_ft": w,
                "implied_k": implied_k(cls, w),
                "published_p90_in": cls.pos_p90_in[w],
                "simulated_p90_in": round(p90_in, 2),
            }
        )
    return {"k_per_ft": k, "checks": checks}


def sweep(cls: PhoneClass, k: float, seed_offset: int) -> list[dict]:
    fns = model_fns_for(cls)
    rows = []
    cell = 0
    for regime in ("same_walk", "cross_session"):
        for ell in ELL_FT:
            for w1 in WALKED_FT:
                for c in SEPARATIONS_FT:
                    w2 = w1 + c
                    rng = np.random.default_rng([seed_offset, cell, SEED])
                    cell += 1
                    if regime == "same_walk":
                        p1, p2, eps = tracked_pair(rng, cls, k, w1, w2, ell, N_DRAWS)
                        oracle_bar = np.abs(eps) * c
                    else:
                        p1, p2 = tracked_pair_cross_session(rng, cls, k, w1, w2, N_DRAWS)
                        oracle_bar = None
                    d = np.abs(p2 - p1)
                    true_err = np.abs(d - c)
                    a, b = Endpoint(walked_ft=w1), Endpoint(walked_ft=w2)
                    violated = c < RULE_FT
                    row = {
                        "class": cls.name,
                        "regime": regime,
                        "ell_ft": None if math.isinf(ell) else ell,
                        "w1_ft": w1,
                        "w2_ft": round(w2, 4),
                        "c_ft": c,
                        "violated": violated,
                        "true_diff_err_p50_in": round(quantile(true_err, 0.5) * 12, 2),
                        "true_diff_err_p90_in": round(quantile(true_err, 0.9) * 12, 2),
                        "models": {},
                    }
                    for name, fn in fns.items():
                        bar = fn(a, b, c)
                        out = outcomes(d, bar)
                        entry = {
                            "bar_ft": round(bar, 4),
                            "pass_rate": float(np.mean(out == "pass")),
                            "unsure_rate": float(np.mean(out == "unsure")),
                            "fail_rate": float(np.mean(out == "fail")),
                            "coverage": float(np.mean(bar >= true_err)),
                        }
                        if violated:
                            entry["wrong_clear_rate"] = entry["pass_rate"]
                        else:
                            entry["unsure_when_ok"] = entry["unsure_rate"]
                            entry["false_reject_rate"] = entry["fail_rate"]
                        row["models"][name] = entry
                    if oracle_bar is not None:
                        out = outcomes(d, oracle_bar)
                        entry = {
                            "bar_ft": None,
                            "pass_rate": float(np.mean(out == "pass")),
                            "coverage": float(np.mean(oracle_bar >= true_err)),
                        }
                        if violated:
                            entry["wrong_clear_rate"] = entry["pass_rate"]
                        row["models"]["common_mode_oracle"] = entry
                    rows.append(row)
    return rows


def sens_bars(base: float, drift: float, scale_sd: float, w1: float, w2: float, c: float) -> dict:
    """The five bars with every coefficient explicit. The sensitivity mirror of
    models.py; tests assert it reproduces the model bars at the frozen values."""
    return {
        "current": (base + drift * w1) + (base + drift * w2),
        "rate_only": drift * (w1 + w2),
        "rate_on_gap": 2 * base + drift * c,
        "scale_error": 2 * base + scale_sd * c,
        "local_clearance": (LOCAL_P90_IN_AT_6FT["no_lidar"] / 72.0) * c,
    }


SENS_W1, SENS_W2 = 20.0, 23.0
SENS_C_VIOLATED, SENS_C_MET = 2.9, 3.3
SENS_LEVELS = {
    "drift_per_ft": (0.12, 0.16, 0.20),
    "tap_base_ft": (0.15, 0.30, 0.60),
    "scale_sd_x": (0.5, 1.0, 2.0),
    "residual_k_x": (0.5, 1.0, 1.5),
}


def sensitivity(cls: PhoneClass, k: float, seed_offset: int) -> list[dict]:
    """How the headline rates move when one coefficient moves.

    Bar-only parameters (drift, tap base, scale SD) reuse one sample set per
    cell and re-decide; the residual spread re-simulates, because it shapes
    the samples themselves. Same geometry for every row: taps at 20 and 23 ft,
    the violated gap at 2.9 ft and the met gap at 3.3 ft, residual correlation
    length 10 ft in the same-walk regime.
    """
    rows = []
    for regime in ("same_walk", "cross_session"):
        for c, metric in ((SENS_C_VIOLATED, "wrong_clear"), (SENS_C_MET, "unsure_when_ok")):
            rng = np.random.default_rng([seed_offset, 9_000 + int(c * 100), SEED])
            if regime == "same_walk":
                p1, p2, _ = tracked_pair(rng, cls, k, SENS_W1, SENS_W2, 10.0, N_DRAWS)
            else:
                p1, p2 = tracked_pair_cross_session(rng, cls, k, SENS_W1, SENS_W2, N_DRAWS)
            d0 = np.abs(p2 - p1)
            for param, levels in SENS_LEVELS.items():
                for level in levels:
                    drift = DRIFT_PER_FT if param != "drift_per_ft" else level
                    base = TAP_BASE_FT if param != "tap_base_ft" else level
                    scale_sd = cls.scale_sd * (1.0 if param != "scale_sd_x" else level)
                    kk = k * (1.0 if param != "residual_k_x" else level)
                    d = d0
                    if param == "residual_k_x" and level != 1.0:
                        rng2 = np.random.default_rng([seed_offset, 9_000 + int(c * 100), SEED])
                        if regime == "same_walk":
                            p1, p2, _ = tracked_pair(rng2, cls, kk, SENS_W1, SENS_W2, 10.0, N_DRAWS)
                        else:
                            p1, p2 = tracked_pair_cross_session(
                                rng2, cls, kk, SENS_W1, SENS_W2, N_DRAWS
                            )
                        d = np.abs(p2 - p1)
                    bars = sens_bars(base, drift, scale_sd, SENS_W1, SENS_W2, c)
                    row = {
                        "class": cls.name,
                        "regime": regime,
                        "gap_ft": c,
                        "parameter": param,
                        "level": level,
                        "models": {},
                    }
                    for name, bar in bars.items():
                        out = outcomes(d, bar)
                        row["models"][name] = {
                            "wrong_clear_rate": float(np.mean(out == "pass")),
                            "unsure_when_ok": float(np.mean(out == "unsure")),
                        }[
                            {"wrong_clear": "wrong_clear_rate", "unsure_when_ok": "unsure_when_ok"}[
                                metric
                            ]
                        ]
                    rows.append(row)
    return rows


def write_sensitivity_md(sens: list[dict]) -> None:
    lines = [
        "# Sensitivity of the headline rates to one coefficient at a time",
        "",
        f"Taps at {SENS_W1:.0f} and {SENS_W2:.0f} ft; violated gap {SENS_C_VIOLATED} ft,",
        f"met gap {SENS_C_MET} ft; residual correlation length 10 ft (same-walk regime).",
        "Each row moves one parameter and re-decides the frozen rule at 3 ft.",
        "Frozen values: drift 0.16 ft/ft, tap base 0.3 ft, class scale SD, calibrated",
        "residual. An assumed correlation is not measured calibration: the residual",
        "levels are assumptions, like the correlation length itself.",
        "",
    ]
    for gap, title in (
        (SENS_C_VIOLATED, f"Wrongly clears the violated {SENS_C_VIOLATED} ft gap (rate)"),
        (SENS_C_MET, f"UNSURE although the {SENS_C_MET} ft gap is met (rate)"),
    ):
        lines += [
            f"## {title}",
            "",
            "| parameter | level | class | regime | current | rate_only | rate_on_gap | scale_error | local_clearance |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for row in (r for r in sens if r["gap_ft"] == gap):
            vals = row["models"]
            lines.append(
                f"| {row['parameter']} | {row['level']} | {row['class']} | {row['regime']} "
                + " ".join(f"| {vals[m]:.3f} " for m in MODEL_ORDER[:5])
                + "|"
            )
        lines.append("")
    (RESULTS / "sensitivity.md").write_text("\n".join(lines) + "\n")


def cp_upper_95(k_count: int, n: int) -> float:
    """Clopper-Pearson 95% upper bound on a rate observed k in n."""
    if k_count >= n:
        return 1.0
    return float(stats.beta.ppf(0.95, k_count + 1, n - k_count))


def worked_bars(cls: PhoneClass) -> list[dict]:
    """All five bars at example geometries, both tap endpoints unless noted."""
    fns = model_fns_for(cls)
    cases = [
        ("battery 20-23 ft, window 3 ft at 23 ft, gap 3", 20.0, 23.0),
        ("battery 15-18 ft, window 3 ft at 18 ft, gap 3", 15.0, 18.0),
        ("battery edge at 18 ft, window 3 ft at 18 ft, gap 0", 18.0, 18.0),
    ]
    out = []
    for label, w1, w2 in cases:
        a, b = Endpoint(walked_ft=w1), Endpoint(walked_ft=w2)
        c = abs(w2 - w1)
        row = {"case": label, "w1_ft": w1, "w2_ft": w2, "separation_ft": c, "bars_ft": {}}
        for name, fn in fns.items():
            row["bars_ft"][name] = round(fn(a, b, c), 3)
        a_vlm = Endpoint(walked_ft=w1, base_ft=1.5)
        row["bars_ft"]["current_vlm_feature"] = round(current_bar(a_vlm, b, c), 3)
        out.append(row)
    return out


def pivot(rows: list[dict], model: str, c_ft: float, w1_ft: float, key: str) -> list[dict]:
    out = []
    for cls in (MODERN, PHONE_2018):
        for regime in ("same_walk", "cross_session"):
            for ell in ELL_FT:
                ell_key = None if math.isinf(ell) else ell
                row = next(
                    r
                    for r in rows
                    if r["class"] == cls.name
                    and r["regime"] == regime
                    and r["ell_ft"] == ell_key
                    and r["w1_ft"] == w1_ft
                    and r["c_ft"] == c_ft
                )
                entry = row["models"].get(model)
                if entry is None:
                    continue  # the common-mode oracle only exists in the same-walk regime
                out.append(
                    {
                        "class": cls.name,
                        "regime": regime,
                        "ell_ft": None if math.isinf(ell) else ell,
                        key: entry.get(key),
                    }
                )
    return out


def write_md(payload: dict) -> None:
    rows = payload["sweep"]
    lines = [
        "# Relative-error study: results",
        "",
        "Decision accounting for five error bars on one-wall clearances, under error",
        "fields whose marginals are calibrated to committed pooled p90s and whose",
        "residual correlation is swept. [METHODS.md](METHODS.md) fixes the method;",
        "[audit.md](results/audit.md) pins every input; [comparator.md](results/comparator.md)",
        "pins the model of the shipped calculation to both solvers;",
        "[sensitivity.md](results/sensitivity.md) moves one coefficient at a time.",
        "",
        "## Bars at worked examples",
        "",
        "All bars in feet. Both endpoints tapped; the last column swaps the battery",
        "end for a photo-detected one.",
        "",
    ]
    for cls_name, cases in payload["worked_bars"].items():
        lines += [
            f"### {cls_name}",
            "",
            "| case | current | rate_only | rate_on_gap | scale_error | local_clearance | current (vlm feature) |",
            "|---|---|---|---|---|---|---|",
        ]
        for case in cases:
            b = case["bars_ft"]
            lines.append(
                f"| {case['case']} | {b['current']} | {b['rate_only']} | {b['rate_on_gap']} "
                f"| {b['scale_error']} | {b['local_clearance']} | {b['current_vlm_feature']} |"
            )
        lines.append("")

    for title, c_ft, key in (
        (
            "Wrongly clears a violated 3 ft rule (true gap 2.9 ft, window at 20 ft)",
            2.9,
            "wrong_clear_rate",
        ),
        (
            "UNSURE although the rule is met (true gap 3.3 ft, window at 20 ft)",
            3.3,
            "unsure_when_ok",
        ),
        (
            "Coverage: bar >= true difference error (true gap 3.0 ft, window at 20 ft)",
            3.0,
            "coverage",
        ),
    ):
        lines += [
            f"## {title}",
            "",
            "| model | class | regime | residual corr length ft | rate |",
            "|---|---|---|---|---|",
        ]
        for model in MODEL_ORDER:
            for row in pivot(rows, model, c_ft, 20.0, key):
                if row.get(key) is None:
                    continue
                ell = "inf" if row["ell_ft"] is None else row["ell_ft"]
                lines.append(
                    f"| {model} | {row['class']} | {row['regime']} | {ell} | {row[key]:.2%} |"
                )
        lines.append("")

    lines += [
        "## Calibration",
        "",
        "Simulated absolute-position p90 against the committed pooled p90 at the fit",
        "distances, with the residual spread set to the largest implied value.",
        "",
        "| class | walked ft | implied k per ft | published p90 in | simulated p90 in |",
        "|---|---|---|---|---|",
    ]
    for cls_name, cal in payload["calibration"].items():
        for chk in cal["checks"]:
            lines.append(
                f"| {cls_name} | {chk['walked_ft']:.0f} | {chk['implied_k']:.4f} "
                f"| {chk['published_p90_in']} | {chk['simulated_p90_in']} |"
            )

    lines += [
        "",
        "## Comparator",
        "",
        f"- model reproduces both shipped solvers: {payload['comparator']['model_matches_solver']}",
        f"- main and t3/server agree on every bar present in both refs: {payload['comparator']['refs_agree']}",
        f"- main `{payload['comparator']['commits']['main']}`",
        f"- t3/server `{payload['comparator']['commits']['origin/t3/server']}`",
        f"- {payload['comparator']['n_checks']} clearance checks asserted at 1e-9",
    ]
    (RESULTS / "relative_error.md").write_text("\n".join(lines) + "\n")


def parameters() -> dict:
    return {
        "seed": SEED,
        "n_draws_per_cell": N_DRAWS,
        "n_calibration_draws": N_CAL,
        "ell_ft": [None if math.isinf(e) else e for e in ELL_FT],
        "walked_ft": list(WALKED_FT),
        "separations_ft": list(SEPARATIONS_FT),
        "rule_ft": RULE_FT,
        "sensitivity": {
            "w1_ft": SENS_W1,
            "w2_ft": SENS_W2,
            "gaps_ft": [SENS_C_VIOLATED, SENS_C_MET],
            "levels": {k: list(v) for k, v in SENS_LEVELS.items()},
        },
    }


def freeze_manifest(path: Path) -> None:
    """Freeze every input the study consumes: parameters, seeds, class
    constants, source hashes and the resolved server refs."""
    commits = {ref: resolve_ref(ref) for ref in SERVER_REFS}
    payload = {
        "study": "relative-error",
        "frozen_on_branch": "obv/products-relative-error-20261009-r1",
        "server_refs": commits,
        "parameters": parameters(),
        "phone_classes": {cls.name: asdict(cls) for cls in (MODERN, PHONE_2018)},
        "sources": audit_mod.source_hashes(),
        "notes": MANIFEST_NOTES,
    }
    path.write_text(json.dumps(payload, indent=1) + "\n")
    print(f"manifest frozen: {path} ({len(payload['sources'])} sources, refs {commits})")


def load_manifest(path: Path) -> dict:
    payload = json.loads(path.read_text())
    frozen = payload["parameters"]
    live = parameters()
    drifted = {
        key: {"frozen": frozen.get(key), "live": live.get(key)}
        for key in live
        if frozen.get(key) != live.get(key)
    }
    if drifted:
        raise SystemExit(f"manifest drift in parameters: {json.dumps(drifted, indent=1)}")
    live_hashes = audit_mod.source_hashes()
    stale = {
        key: {"frozen": payload["sources"][key]["sha256"], "live": meta["sha256"]}
        for key, meta in live_hashes.items()
        if payload["sources"][key]["sha256"] != meta["sha256"]
    }
    if stale:
        raise SystemExit(f"manifest sources changed since freeze: {json.dumps(stale, indent=1)}")
    return payload


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replay(
    replay_dir: Path, manifest_payload: dict | None, manifest_path: Path | None = None
) -> None:
    """Run the study, then assert every tracked artifact is byte-identical to
    the committed copy in replay_dir. The run itself rewrites results/."""
    before = {
        name: (replay_dir / name).read_text()
        for name in TRACKED_RESULTS
        if (replay_dir / name).exists()
    }
    missing = [name for name in TRACKED_RESULTS if name not in before]
    if missing:
        raise SystemExit(f"replay target is missing committed artifacts: {missing}")

    execute(manifest_payload, manifest_path)

    diffs = [name for name, text in before.items() if (RESULTS / name).read_text() != text]
    if diffs:
        raise SystemExit("replay mismatch in: " + ", ".join(diffs))
    print(f"replay: all {len(before)} result artifacts byte-identical")


def execute(manifest_payload: dict | None, manifest_path: Path | None = None) -> dict:
    """The study itself: audit, calibration, sweep, sensitivity, comparator."""
    audit_mod.main([])

    calibration = {cls.name: calibrate(cls) for cls in (MODERN, PHONE_2018)}
    sweep_rows: list[dict] = []
    sens_rows: list[dict] = []
    for seed_offset, cls in enumerate((MODERN, PHONE_2018)):
        k = calibration[cls.name]["k_per_ft"]
        sweep_rows.extend(sweep(cls, k, seed_offset))
        sens_rows.extend(sensitivity(cls, k, seed_offset))
    if manifest_payload is not None:
        commits = manifest_payload["server_refs"]
        comparator_out = run_comparator(commits=commits)
    else:
        comparator_out = run_comparator()

    payload = {
        "parameters": parameters(),
        "manifest_sha256": (
            None
            if manifest_payload is None or manifest_path is None
            else {
                "sha256": sha256_of(manifest_path),
                "server_refs": manifest_payload["server_refs"],
            }
        ),
        "calibration": calibration,
        "worked_bars": {cls.name: worked_bars(cls) for cls in (MODERN, PHONE_2018)},
        "sweep": sweep_rows,
        "sensitivity": sens_rows,
        "comparator": comparator_out,
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "relative_error.json").write_text(json.dumps(payload, indent=1))
    write_md(payload)
    write_sensitivity_md(sens_rows)
    print(f"sweep: {len(sweep_rows)} cells; comparator checks: {comparator_out['n_checks']}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-comparator", action="store_true")
    parser.add_argument("--manifest", type=Path, help="frozen manifest to verify and run against")
    parser.add_argument("--freeze-manifest", type=Path, help="write a frozen manifest and exit")
    parser.add_argument(
        "--replay", type=Path, help="re-run and diff against this committed results dir"
    )
    args = parser.parse_args()

    if args.freeze_manifest:
        freeze_manifest(args.freeze_manifest)
        return

    manifest_payload = None
    manifest_path = args.manifest
    if manifest_path is None and args.replay is not None and (HERE / "manifest.json").exists():
        manifest_path = HERE / "manifest.json"
    if manifest_path is not None:
        manifest_payload = load_manifest(manifest_path)

    if args.skip_comparator:
        raise SystemExit(
            "--skip-comparator is not compatible with the manifest flow; use it standalone"
        )

    if args.replay:
        replay(args.replay, manifest_payload, manifest_path)
        return

    execute(manifest_payload, manifest_path)


if __name__ == "__main__":
    main()
