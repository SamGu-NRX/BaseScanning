"""Error-budget draws shared with the earlier scale study. Seed 7."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

FIXTURES = Path(__file__).resolve().parent / "fixtures"
IN = 0.0254
FT = 0.3048
SHAPE_IN = 1.25
N = 20000
RNG = np.random.default_rng(7)


def reset(n: int = 20000) -> None:
    """Start the generator again. Tests use a smaller draw count."""

    global N, RNG
    N = n
    RNG = np.random.default_rng(7)


def arkit_draws(profile: str) -> np.ndarray:
    payload = json.loads((FIXTURES / "scale_study_incremental_tour.json").read_text())
    errors = np.array([row["sfm_ls_scale_err"] for row in payload["profiles"][profile]["rows"]])
    return errors[RNG.integers(0, len(errors), N)] + RNG.normal(0, 0.002, N)


def mdepth_draws() -> tuple[np.ndarray, list]:
    values = []
    for scene in ("electro", "facade", "barn"):
        for model in ("moge2-vits", "metric3d-vits"):
            path = FIXTURES / f"mdepth_{scene}_{model}.json"
            if path.is_file():
                values.append((scene, model, json.loads(path.read_text())["median_of_images_err_pct"] / 100))
    errors = np.array([item[2] for item in values])
    return errors[RNG.integers(0, len(errors), N)] + RNG.normal(0, 0.02, N), values


def recon_term() -> np.ndarray:
    return RNG.normal(0, np.sqrt(2) * 0.0035, N)


def marked_length(length_ft: float, gross: bool = True) -> np.ndarray:
    """A8's marked-length reference draw. Same generator calls as the published row."""

    length = length_ft * FT
    read = RNG.normal(0, 0.25 * IN, N)
    if gross:
        gross_mask = RNG.random(N) < 0.05
        read[gross_mask] += RNG.choice([-1, 1], int(gross_mask.sum())) * RNG.uniform(6, 12, int(gross_mask.sum())) * IN
    ends = RNG.normal(0, 0.006, N) - RNG.normal(0, 0.006, N)
    return (length + read) / (length + ends) - 1


def meter(dist_m: float, f_px: float) -> np.ndarray:
    diameter = 6.95 * IN
    true = diameter * (1 + RNG.normal(0, 0.005, N))
    cover = RNG.random(N) < 0.10
    true[cover] = RNG.uniform(6.29, 6.42, int(cover.sum())) * IN
    pixels = f_px * true / dist_m
    measured_px = pixels + RNG.normal(0, 0.7 * np.sqrt(2), N)
    return diameter / (measured_px * dist_m / f_px) - 1


def summarise(name: str, error: np.ndarray) -> dict:
    absolute = np.abs(error)
    shape = RNG.normal(0, SHAPE_IN, N)
    row = {
        "method": name,
        "median_abs_pct": 100 * float(np.median(absolute)),
        "p90_abs_pct": 100 * float(np.percentile(absolute, 90)),
        "p95_abs_pct": 100 * float(np.percentile(absolute, 95)),
        "bias_pct": 100 * float(np.median(error)),
    }
    for feet in (15, 26):
        err_in = np.abs(error * feet * 12 + shape)
        row[f"err_{feet}ft_in_median"] = float(np.median(err_in))
        row[f"err_{feet}ft_in_p90"] = float(np.percentile(err_in, 90))
        row[f"share_{feet}ft_within_2in"] = float((err_in <= 2).mean())
        row[f"share_{feet}ft_within_6in"] = float((err_in <= 6).mean())
    return row
