"""The judge: claims from the worker's output scene.json, audited against the reference.

Every claim is mapped into true world coordinates through the output baseline (the fitted line
itself is part of the claim): a cell at s on the fitted frame becomes a world point, and the
reference answers whether wall/ground exists there, whether any photo could see it, and whether
the space the worker calls clear is physically clear. Violations are typed: FALSE_OBSERVED
(claims where no photo could see), FALSE_CLEAR (clear where occupied), CLEAR_UNSEEN (clear
where nothing could see), OVERREACH (claims where nothing exists), PHANTOM_OBSTACLE (a facing
measurement with nothing there), EVIDENCE_DEFICIT (claims resting on fewer views than the
worker's own two-position bar), and UNSOUND_PASS (a server pass whose deciding coverage
depends on unobserved area).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .reference import CELL, Reference
from .scenes import Scene

FEET = 0.3048
BAND_CELL_ROWS = 1.68 / 18  # m of wall height per oracle row


@dataclass
class Violation:
    kind: str
    band: str
    lo_ft: float
    hi_ft: float
    detail: str
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "band": self.band,
            "span_ft": [round(self.lo_ft, 3), round(self.hi_ft, 3)],
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass
class FrameMap:
    """The fitted wall frame in true world coordinates, from the output baseline."""

    origin: np.ndarray  # world point of s = 0 (the projected meter)
    along: np.ndarray
    outward: np.ndarray
    ground_y: float  # fitted ground height at the meter, metres
    tilt: float

    @classmethod
    def from_output(cls, scene_out: dict, geometry: dict | None) -> "FrameMap":
        if geometry is not None:
            m = np.array(geometry["meter"]["pos"], float) * FEET  # world plan, metres
            along = np.array(geometry["walls"][0]["along"], float)  # world, unit, metres
            outward = np.array(geometry["walls"][0]["outward"], float)
            ground_y = float(geometry["ground"]["height_ft"]) * FEET  # world y at the meter
            tilt = float(geometry["ground"].get("tilt_deg", 0.0))
            origin = np.array([m[0], ground_y, m[2]])  # s = 0 sits at the meter's foot
        else:
            # No sidecar: reconstruct the frame from the app-schema wall and meter. The ground
            # height stays unknown here; the judge substitutes the truth and records that.
            b0, b1 = (np.array(p, float) * FEET for p in scene_out["walls"][0]["baseline"])
            meter = np.array(scene_out["meter"]["pos"], float) * FEET
            along = b1 - b0
            along = along / np.linalg.norm(along)
            outward = np.array([-along[2], 0.0, along[0]])
            origin = np.array([meter[0], np.nan, meter[2]])
            ground_y, tilt = np.nan, 0.0
        return cls(origin=origin, along=along, outward=outward, ground_y=ground_y, tilt=tilt)


def claimed_cells(span_ft: list[float], cells: np.ndarray, fmap: FrameMap) -> np.ndarray:
    """True s cells a claimed span covers. The span is in the fitted frame; it maps to the world
    through the fitted line itself, so a drifted fit claims drifted world cells (that error is
    the claim's, not the judge's)."""
    lo = float((fmap.origin + fmap.along * (span_ft[0] * FEET))[0]) - CELL
    hi = float((fmap.origin + fmap.along * (span_ft[1] * FEET))[0]) + CELL
    return cells[(cells > lo) & (cells < hi)]


def judge_scene(
    scene: Scene,
    walk,
    ref: Reference,
    out_dir: Path,
    server_results: dict[str, dict],
    witness_s: list[float],
) -> dict:
    """Full audit of one run. `out_dir` holds the worker's scene.json and geometry.json."""
    out = json.loads((out_dir / "scene.json").read_text())
    try:
        geometry = json.loads((out_dir / "geometry.json").read_text())
    except FileNotFoundError:
        geometry = None
    fmap = FrameMap.from_output(out, geometry)
    violations: list[Violation] = []
    metrics: dict = {}
    cov = out.get("coverage", {}).get("observed", [])
    cells = np.arange(-2.0, 26.0, CELL)  # true s cells, metres
    claim_lo = min([e["span_ft"][0] for e in cov], default=-1) * FEET - 1.0
    claim_hi = max([e["span_ft"][1] for e in cov], default=1) * FEET + 1.0
    probe = cells[(cells > claim_lo - 2) & (cells < claim_hi + 2)]  # oracle work stays near claims
    probe = np.unique(np.concatenate([probe, np.asarray(witness_s, float)]))

    # ---- the wall band -----------------------------------------------------------------
    wall_truth = ref.wall_band(probe)
    exists = wall_truth["exists"].mean(axis=(1, 2)) > 0
    seen = wall_truth["seen"].mean(axis=(1, 2))
    two = wall_truth["two_pos"].mean(axis=(1, 2)) > 0
    unseen_rows = 18 - wall_truth["seen"].sum(axis=(1, 2))
    ft_unseen = unseen_rows * BAND_CELL_ROWS / FEET
    wall_claimed = np.zeros(len(probe), bool)
    for e in cov:
        if e["band"] == "wall":
            wall_claimed |= np.isin(probe, claimed_cells(e["span_ft"], probe, fmap))
    for i, c in enumerate(probe):
        if not wall_claimed[i]:
            continue
        if not exists[i]:
            violations.append(
                Violation(
                    "OVERREACH", "wall", c / FEET - 0.25, c / FEET + 0.25,
                    "wall claimed where the true face is absent (opening or beyond the end)",
                )
            )
        elif ft_unseen[i] >= 0.2:  # >= 0.2 ft of the 5.5 ft band unseeable by any frame
            violations.append(
                Violation(
                    "FALSE_OBSERVED", "wall", c / FEET - 0.25, c / FEET + 0.25,
                    f"wall band claimed; {ft_unseen[i]:.2f} ft of it unseeable by any frame",
                )
            )
    metrics["wall"] = {
        "exists_cells": int(exists.sum()),
        "seen_cells": int((exists & (seen > 0)).sum()),
        "two_pos_cells": int((exists & two).sum()),
        "missed_cells": int((exists & (seen > 0) & ~wall_claimed).sum()),
    }

    # ---- ground ------------------------------------------------------------------------
    ground_truth = ref.ground_band(probe)
    g_exists = ground_truth["exists"].mean(axis=(1, 2)) > 0
    g_seen = ground_truth["seen"].mean(axis=(1, 2))
    g_claimed = np.zeros(len(probe), bool)
    for e in cov:
        if e["band"] == "ground":
            g_claimed |= np.isin(probe, claimed_cells(e["span_ft"], probe, fmap))
    for i, c in enumerate(probe):
        if not g_claimed[i]:
            continue
        if not g_exists[i]:
            violations.append(
                Violation("OVERREACH", "ground", c / FEET - 0.25, c / FEET + 0.25,
                          "ground claimed off every true patch")
            )
        elif g_seen[i] < 0.95:
            violations.append(
                Violation("FALSE_OBSERVED", "ground", c / FEET - 0.25, c / FEET + 0.25,
                          f"ground claimed; only {g_seen[i]:.0%} of its out samples seeable")
            )
    metrics["ground"] = {
        "claimed_cells": int(g_claimed.sum()),
        "seen_frac_median": float(np.median(g_seen[g_claimed])) if g_claimed.any() else None,
        "missed_cells": int((g_exists & (g_seen > 0.5) & ~g_claimed).sum()),
    }

    # ---- facing / overhead claims -------------------------------------------------------
    # Bands are built per claimed span (+/- one cell), not over the full probe range: the
    # facing/overhead audits only ever read claimed spans, and a full-range band is
    # O(cells x tris x frames) -- hours on a confound scene with ~2k-triangle foliage.
    # Oracle resolutions (outs x heights x across) are unchanged.
    for entry_type, out_key, band_fn in (
        ("facing", "out_ft", ref.facing_band),
        ("overheads", "clearance_ft", ref.overhead_band),
    ):
        for e in out.get(entry_type, []):
            rlo, rhi = sorted(e["span_ft"])
            wlo = float((fmap.origin + fmap.along * (rlo * FEET))[0]) / FEET
            whi = float((fmap.origin + fmap.along * (rhi * FEET))[0]) / FEET
            lo, hi = min(wlo, whi), max(wlo, whi)
            sel = (probe >= lo - CELL) & (probe <= hi + CELL)
            if not sel.any():
                continue
            cells = probe[sel]
            band = band_fn(cells)
            if entry_type == "facing" and "depth_ft" in e:
                d = e["depth_ft"] * FEET
                near = np.abs(band["outs"] - d) < 0.15
                if not band["occupied"][:, :, near, :].any():
                    violations.append(
                        Violation("PHANTOM_OBSTACLE", entry_type, lo, hi,
                                  f"facing depth {e['depth_ft']:.2f} ft with nothing there")
                    )
                continue
            limit = e[out_key] * FEET
            if entry_type == "facing":
                mo = band["outs"] < limit
                occ = band["occupied"][:, :, mo, :].any(axis=(1, 2, 3))
                unseen = (~band["clear_observed"][:, :, mo, :] & ~band["occupied"][:, :, mo, :]).any(axis=(1, 2, 3))
            else:
                mh = band["heights"] < limit
                occ = band["occupied"][:, :, :, mh].any(axis=(1, 2, 3))
                unseen = (
                    ~band["clear_observed"][:, :, :, mh] & ~band["occupied"][:, :, :, mh]
                ).any(axis=(1, 2, 3))
            for i, c in enumerate(cells):
                if c / FEET < lo or c / FEET > hi:
                    continue
                if occ[i]:
                    violations.append(
                        Violation("FALSE_CLEAR", entry_type, c / FEET - 0.25, c / FEET + 0.25,
                                  f"{entry_type} clear to {e[out_key]:.2f} ft with an occupied sample inside")
                    )
                elif unseen[i]:
                    violations.append(
                        Violation("CLEAR_UNSEEN", entry_type, c / FEET - 0.25, c / FEET + 0.25,
                                  f"{entry_type} clear to {e[out_key]:.2f} ft where no frame could see")
                    )

    # ---- evidence audit: views behind each measurement entry -----------------------------
    for entry_type in ("facing", "overheads"):
        for e in out.get(entry_type, []):
            if entry_type == "facing" and "depth_ft" not in e:
                continue
            lo, hi = e["span_ft"]
            mid_s = (lo + hi) / 2
            mid_w = float((fmap.origin + fmap.along * (mid_s * FEET))[0]) / FEET
            sel = np.abs(probe - mid_w) <= CELL
            if not sel.any():
                continue
            band = (ref.facing_band if entry_type == "facing" else ref.overhead_band)(probe[sel])
            if entry_type == "facing":
                d = e["depth_ft"] * FEET
                near = np.abs(band["outs"] - d) < 0.15
                nf = int(band["n_frames"][:, :, near, :].max()) if near.any() else 0
            else:
                mh = band["heights"] < e["clearance_ft"] * FEET
                nf = int(band["n_frames"][:, :, :, mh].max()) if mh.any() else 0
            if nf < 2:
                violations.append(
                    Violation("EVIDENCE_DEFICIT", entry_type, lo, hi,
                              f"measurement rests on {nf} view(s); the contract's bar is two positions",
                              {"max_frames_seen": nf})
                )

    # ---- server decisions ----------------------------------------------------------------
    for ref_name, result in server_results.items():
        for check in result.get("checks", []):
            if check.get("outcome") != "pass":
                continue
            spot = result.get("spot") or {}
            span = spot.get("s_range_ft") or (geometry["walls"][0].get("s_range_ft") if geometry else None)
            if span and _pass_rests_on_unseen(check, span, ref, fmap):
                violations.append(
                    Violation(
                        "UNSOUND_PASS", f"server@{ref_name}", span[0], span[1],
                        f"check {check.get('id')} passed with its deciding band unobservable",
                    )
                )
                break

    # ---- ground height at the meter vs truth ----------------------------------------------
    true_h = scene.true_ground_height(fmap.origin[0], fmap.origin[2])
    if np.isfinite(true_h):
        metrics["ground_height_error_m"] = float(fmap.ground_y - float(true_h))

    if geometry is None:
        th = scene.true_ground_height(fmap.origin[0], fmap.origin[2])
        fmap.ground_y = float(th) if np.isfinite(th) else 0.0
        metrics["ground_height_source"] = "truth-fallback"

    # ---- witnesses --------------------------------------------------------------------------
    witnesses = {}
    for s in witness_s:
        i = int(np.argmin(np.abs(probe - s)))
        witnesses[f"s={s:.2f}m"] = {
            "wall_exists": bool(exists[i]),
            "wall_seen_frac": round(float(seen[i]), 2),
            "wall_two_pos": bool(two[i]),
            "ground_seen_frac": round(float(g_seen[i]), 2),
        }

    return {
        "violations": [v.as_dict() for v in violations],
        "metrics": metrics,
        "uncertainty": {
            # samples that EXIST in the world but no frame could see: unknown, not observed
            "wall_unknown_samples": int((wall_truth["exists"] & ~wall_truth["seen"]).sum()),
            "wall_samples_total": int(wall_truth["exists"].sum()),
            "ground_unknown_samples": int((ground_truth["exists"] & ~ground_truth["seen"]).sum()),
            "ground_samples_total": int(ground_truth["exists"].sum()),
            "empty_denominator_note": (
                "unobserved regions are UNKNOWN (nan), never counted as completely observed"
            ),
        },
        "witnesses": witnesses,
        "fingerprint": _fingerprint(out, geometry),
        "decision_by_ref": {k: v.get("decision") for k, v in server_results.items()},
        "unsure_causes_by_ref": {
            k: sorted({c for chk in v.get("checks", []) for c in [chk.get("unsure_cause")] if c})
            for k, v in server_results.items()
        },
        "s_range_ft": (geometry["walls"][0].get("s_range_ft") if geometry else None),
        "ground_height_m": fmap.ground_y,
    }


def _pass_rests_on_unseen(check: dict, span: list[float], ref: Reference, fmap: FrameMap) -> bool:
    """A pass is unsound when the wall band its rule reads is unobservable across the spot span
    extended by the rule's own reach. Band mapping per the input contract's table: every
    observable-dependent check reads the wall band at minimum; clearance checks read ground and
    facing too, which the wall-band test bounds from above (unseen wall at extended span is the
    dominating signal)."""
    rule = check.get("rule") or {}
    val = rule.get("value") or 0.0
    extra = (val + check.get("error", 0.0) + 1.0) * FEET
    lo_s, hi_s = span[0] * FEET - extra, span[1] * FEET + extra
    lo = float((fmap.origin + fmap.along * lo_s)[0])
    hi = float((fmap.origin + fmap.along * hi_s)[0])
    all_cells = np.arange(min(lo, hi), max(lo, hi), CELL)
    truth = ref.wall_band(all_cells)
    seen = truth["seen"].mean(axis=1)
    return bool(((truth["exists"].mean(axis=1) > 0) & (seen < 0.5)).any())


def _fingerprint(out: dict, geometry: dict | None) -> str:
    """Canonical claim identity: what a downstream consumer sees, rounded to claim resolution."""
    geometry = geometry or {}
    cov = out.get("coverage", {}).get("observed", [])
    claims = [
        {
            "band": e["band"],
            "span_ft": [round(e["span_ft"][0], 1), round(e["span_ft"][1], 1)],
            **({"out_ft": round(e["out_ft"], 1)} if "out_ft" in e else {}),
        }
        for e in cov
    ]
    facing = [
        {
            "span_ft": [round(e["span_ft"][0], 1), round(e["span_ft"][1], 1)],
            **{k: round(e[k], 1) for k in ("depth_ft", "out_ft") if k in e},
        }
        for e in out.get("facing", [])
    ]
    over = [
        {
            "span_ft": [round(e["span_ft"][0], 1), round(e["span_ft"][1], 1)],
            "clearance_ft": round(e["clearance_ft"], 1),
        }
        for e in out.get("overheads", [])
    ]
    blob = json.dumps(
        {
            "observed": claims,
            "facing": facing,
            "overheads": over,
            "s_range_ft": [round(x, 1) for x in geometry["walls"][0].get("s_range_ft", [])],
            "ground_height_m": round(float(geometry["ground"]["height_ft"]) * FEET, 2),
            "ground_tilt_deg": round(geometry["ground"].get("tilt_deg", 0.0), 2),
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
