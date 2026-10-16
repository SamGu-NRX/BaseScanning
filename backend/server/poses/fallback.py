"""When to throw away a guided SfM model and triangulate on ARKit poses.

The logged triggers stay the brief's: mapper share under 90 % (``sfm_split``),
rigid RMS over 0.25 m (``sfm_bent``), and scale over 5 % (``sfm_scale_off``).
SD-88 ships the prior unless scale is off, the prior model's registered share
is under 60 %, or there is no prior model. ``sfm_bent`` is reported as
``sfm_arkit_disagree`` and does not swap. The 3 % scale band stays a warning.
"""

from __future__ import annotations

from server.poses.params import PoseParams


def trigger_details(
    *,
    mapper_registered: int,
    mapper_given: int,
    rms_m: float | None,
    scale_pct: float | None,
    params: PoseParams,
) -> list[dict]:
    """Each fallback rule with the measured value beside its threshold.

    Thresholds stay the brief's values. ``fired`` is the comparison that
    ``triggers`` already uses; this list is the per-run explanation.
    """
    fraction = (mapper_registered / mapper_given) if mapper_given > 0 else None
    abs_scale = None if scale_pct is None else abs(scale_pct)
    return [
        {
            "code": "sfm_split",
            "metric": "mapper_registered_fraction",
            "value": fraction,
            "threshold": params.fallback_min_registered,
            "comparison": "lt",
            "fired": fraction is not None and fraction < params.fallback_min_registered,
            "mapper_registered": mapper_registered,
            "mapper_given": mapper_given,
        },
        {
            "code": "sfm_bent",
            "metric": "arkit_fit_rms_m",
            "value": rms_m,
            "threshold": params.bent_rms_m,
            "comparison": "gt",
            "fired": rms_m is not None and rms_m > params.bent_rms_m,
        },
        {
            "code": "sfm_scale_off",
            "metric": "abs_arkit_fit_scale_pct",
            "value": abs_scale,
            "threshold": params.fallback_max_scale_dev_pct,
            "comparison": "gt",
            "fired": abs_scale is not None and abs_scale > params.fallback_max_scale_dev_pct,
        },
    ]


def triggers(
    *,
    mapper_registered: int,
    mapper_given: int,
    rms_m: float | None,
    scale_pct: float | None,
    params: PoseParams,
) -> list[str]:
    return [
        row["code"]
        for row in trigger_details(
            mapper_registered=mapper_registered,
            mapper_given=mapper_given,
            rms_m=rms_m,
            scale_pct=scale_pct,
            params=params,
        )
        if row["fired"]
    ]


def scale_warning(scale_pct: float | None, params: PoseParams) -> str | None:
    if scale_pct is None:
        return None
    if abs(scale_pct) > params.scale_warn_pct:
        return "scale_vs_arkit_off"
    return None


def use_fixed(fixed_registered: int, prior_registered: int, codes: list[str]) -> bool:
    """Fixed poses win only when something triggered and they keep more frames."""
    return bool(codes) and fixed_registered > prior_registered


def choose_model(
    *,
    guided: bool,
    amended: bool,
    frames_given: int,
    prior_model_registered: int | None,
    mapper_registered: int,
    fixed_registered: int | None,
    rms_m: float | None,
    scale_pct: float | None,
    triggers: list[dict],
    params: PoseParams,
    mean_reproj_px: float | None = None,
    holds_meter: bool = True,
    is_largest: bool = True,
) -> dict:
    """Ship the prior fragment unless scale, share, or absence fails.

    Registered share is the prior model's image count (mapper plus
    post-registered) over frames given. Carried ARKit frames are not in that
    count. Fixed triangulation replaces the prior only after a swap reason,
    and only when it registers more images than the prior model. It is never
    compared with the mapper subset. A bend, a mean reprojection above 1 px,
    or a fragment that neither holds the meter nor is the largest is noted
    and does not swap.
    """
    registered = 0 if prior_model_registered is None else int(prior_model_registered)
    no_model = prior_model_registered is None or registered <= 0
    fraction = (registered / frames_given) if frames_given > 0 else None
    abs_scale = None if scale_pct is None else abs(scale_pct)
    share_low = (not no_model) and (fraction is None or fraction < params.select_min_registered)
    scale_off = abs_scale is not None and abs_scale > params.fallback_max_scale_dev_pct
    bent = rms_m is not None and rms_m > params.bent_rms_m
    reproj_high = mean_reproj_px is not None and mean_reproj_px > 1.0
    swap_reasons: list[str] = []
    if no_model:
        swap_reasons.append("no_model")
    elif share_low:
        swap_reasons.append("registered_low")
    if scale_off:
        swap_reasons.append("scale_off")
    base = {
        "prior_registered": mapper_registered,
        "prior_model_registered": prior_model_registered,
        "fixed_registered": fixed_registered,
        "registered_fraction": fraction,
        "camera_rms_m": rms_m,
        "mean_reproj_px": mean_reproj_px,
        "select_min_registered": params.select_min_registered,
        "bent_rms_m": params.bent_rms_m,
        "holds_meter": holds_meter,
        "is_largest": is_largest,
        "arkit_disagree": bent,
        "reproj_high": reproj_high,
        "swap_reasons": swap_reasons,
        "triggers": triggers,
    }
    if amended:
        return {
            **base,
            "shipped": "amended",
            "action": "keep",
            "reason": "registered new frames into the supplied base model",
        }
    if not guided:
        return {
            **base,
            "shipped": "image_only",
            "action": "keep",
            "reason": "photo flow has no ARKit prior health check",
        }
    notes = _choice_notes(
        bent=bent,
        rms_m=rms_m,
        reproj_high=reproj_high,
        mean_reproj_px=mean_reproj_px,
        holds_meter=True if no_model else holds_meter,
        is_largest=True if no_model else is_largest,
        params=params,
    )
    if not swap_reasons:
        pct = 0.0 if fraction is None else 100.0 * fraction
        scale_txt = "n/a" if abs_scale is None else f"{abs_scale:.3f}%"
        reason = (
            f"SfM prior kept: {registered}/{frames_given} frames ({pct:.1f}%) is at least "
            f"{100.0 * params.select_min_registered:.0f}% and |scale| {scale_txt} is within "
            f"{params.fallback_max_scale_dev_pct:.1f}%"
        )
        if notes:
            reason = f"{reason}; {'; '.join(notes)}"
        return {**base, "shipped": "arkit_prior", "action": "keep", "reason": reason}
    detail = _swap_detail(
        swap_reasons=swap_reasons,
        registered=registered,
        frames_given=frames_given,
        fraction=fraction,
        abs_scale=abs_scale,
        params=params,
    )
    if notes:
        detail = f"{detail}; {'; '.join(notes)}"
    if fixed_registered is None:
        return {
            **base,
            "shipped": "arkit_prior",
            "action": "try_fixed",
            "reason": f"swap ({detail}); fixed triangulation not run yet",
        }
    if fixed_registered > registered:
        return {
            **base,
            "shipped": "arkit_fixed",
            "action": "replace",
            "reason": (
                f"swap ({detail}) and fixed triangulation registered "
                f"{fixed_registered} frames, more than the prior's {registered}"
            ),
        }
    return {
        **base,
        "shipped": "arkit_prior",
        "action": "keep",
        "reason": (
            f"swap ({detail}) but fixed triangulation registered {fixed_registered} frames, "
            f"not more than the prior's {registered}, so the prior was kept"
        ),
    }


def _choice_notes(
    *,
    bent: bool,
    rms_m: float | None,
    reproj_high: bool,
    mean_reproj_px: float | None,
    holds_meter: bool,
    is_largest: bool,
    params: PoseParams,
) -> list[str]:
    notes: list[str] = []
    if bent and rms_m is not None:
        notes.append(
            f"camera-vs-ARKit RMS {rms_m:.3f} m is over {params.bent_rms_m:.2f} m (sfm_arkit_disagree, not a swap)"
        )
    if reproj_high and mean_reproj_px is not None:
        notes.append(f"mean reprojection {mean_reproj_px:.3f} px is over 1 px (not a swap)")
    if not holds_meter and not is_largest:
        notes.append("fragment does not hold the meter and is not the largest (not a swap)")
    return notes


def _swap_detail(
    *,
    swap_reasons: list[str],
    registered: int,
    frames_given: int,
    fraction: float | None,
    abs_scale: float | None,
    params: PoseParams,
) -> str:
    parts: list[str] = []
    for reason in swap_reasons:
        if reason == "no_model":
            parts.append("no prior model")
        elif reason == "registered_low":
            shown = "n/a" if fraction is None else f"{100.0 * fraction:.1f}%"
            parts.append(
                f"registered {registered}/{frames_given} ({shown}) is under {100.0 * params.select_min_registered:.0f}%"
            )
        elif reason == "scale_off" and abs_scale is not None:
            parts.append(f"|scale| {abs_scale:.3f}% is over {params.fallback_max_scale_dev_pct:.1f}%")
        else:
            unknown: str = reason
            raise RuntimeError(f"unknown swap reason {unknown}")
    return "; ".join(parts)


_SWAP_WARNING = {
    "no_model": "sfm_no_model",
    "registered_low": "sfm_registered_low",
    "scale_off": "sfm_scale_off",
}


def choice_warnings(choice: dict) -> list[str]:
    """Report warnings for a ``choose_model`` result. Bend is never a swap code."""
    found: list[str] = []
    if choice.get("arkit_disagree"):
        found.append("sfm_arkit_disagree")
    if choice.get("reproj_high"):
        found.append("mean_reproj_high")
    shipped_fixed = choice.get("shipped") == "arkit_fixed"
    for reason in choice.get("swap_reasons") or []:
        if reason == "no_model" and not shipped_fixed:
            continue
        code = _SWAP_WARNING.get(str(reason))
        if code is None:
            raise RuntimeError(f"unknown swap reason {reason}")
        found.append(code)
    return found
