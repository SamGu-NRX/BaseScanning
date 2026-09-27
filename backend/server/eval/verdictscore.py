"""Verdicts vs hand labels (contract section 6.2; PR #4 semantics).

Truth: pass / fail / borderline (a correct path says unsure) / not_pass (fail or unsure both fine) /
unknown (not scored). Start-range labels are sampled every 2 in of battery start position against
the verdict's candidates; site-scope labels against the verdict's site criteria, with `placement`
read from the site decision (pass -> pass, manual_review -> unsure, reject -> fail).

Any pass that breaks the coverage contract (section 3.1) counts as unsafe whatever the label.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from server.eval.frame import M_PER_FT
from server.eval.sceneio import load_json, verdict_contract_errors

STEP_M = 2 * 0.0254
DECISION_OUTCOME = {"pass": "pass", "manual_review": "unsure", "reject": "fail"}
ACCEPTABLE = {"pass": {"pass"}, "fail": {"fail"}, "borderline": {"unsure"}, "not_pass": {"fail", "unsure"}}


def classify(truth: str, outcome: str) -> str:
    if outcome == "not_evaluated":
        return "not_evaluated"
    if truth == "unknown":
        return "pass_on_unknown" if outcome == "pass" else "unscored"
    if outcome in ACCEPTABLE[truth]:
        return "agree"
    if outcome == "pass":
        return "unsafe_pass"
    if truth == "pass" and outcome == "fail":
        return "false_rejection"
    if truth == "borderline" and outcome == "fail":
        return "missed_review"
    return "over_caution"  # unsure where truth is pass or fail


def criterion_outcome(cand: dict | None, criterion: str) -> str:
    """A candidate's outcome for one criterion (`placement` = its overall outcome). Uncovered
    positions count as unsure. A passing candidate passes every criterion; on a failing or unsure
    candidate a criterion it does not list is `not_evaluated` (engines stop at the deciding check,
    e.g. PR #11 does not evaluate positions out of cable reach), which is not scored."""
    if cand is None:
        return "unsure"
    if criterion == "placement":
        return cand["outcome"]
    if criterion in cand.get("failing", []):
        return "fail"
    if criterion in cand.get("unsure", []):
        return "unsure"
    return "pass" if cand["outcome"] == "pass" else "not_evaluated"


def _candidate_at(cands: list[dict], wall_id: str, s: float) -> dict | None:
    for c in cands:
        if c["wall_id"] == wall_id and c["start_s_m"][0] - 1e-6 <= s <= c["start_s_m"][1] + 1e-6:
            return c
    return None


def c5_errors(verdict: dict) -> list[str]:
    """Criteria whose outcome contradicts their own margin (PR #13 resultcheck.margin_problem):
    pass needs margin > +-, fail needs margin < -+-."""
    errs = []
    for c in verdict["criteria"]:
        m, mou = c.get("measured"), c.get("margin_over_uncertainty")
        if not m or mou is None or c.get("margin_ft") is None:
            continue
        if c["outcome"] == "pass" and mou <= 1:
            errs.append(f"{c['id']}: pass with margin {c['margin_ft']} ft <= +-{m['plus_minus_ft']}")
        if c["outcome"] == "fail" and mou >= -1:
            errs.append(f"{c['id']}: fail with margin {c['margin_ft']} ft >= -{m['plus_minus_ft']}")
    return errs


def score(verdict: dict, labels: dict) -> dict:
    contract = verdict_contract_errors(verdict)
    unsupported_pass = [e for e in contract if ": pass on " in e or "pass with no measurement" in e]
    kinds = (
        "agree",
        "unsafe_pass",
        "missed_review",
        "over_caution",
        "false_rejection",
        "pass_on_unknown",
        "unscored",
        "not_evaluated",
    )
    pos = {k: 0 for k in kinds}
    pos_len = {k: 0.0 for k in kinds}
    rows = []
    cands = verdict.get("candidates", [])
    for lab in labels["labels"]:
        if lab["scope"] != "start_range":
            continue
        a, b = lab["start_s_m"]
        ss = np.arange(a, b + 1e-9, STEP_M)
        counts = {k: 0 for k in kinds}
        for s in ss:
            c = _candidate_at(cands, lab["wall_id"], float(s))
            outcome = criterion_outcome(c, lab["criterion"])
            k = classify(lab["truth"], outcome)
            counts[k] += 1
        for k, n in counts.items():
            pos[k] += n
            pos_len[k] += n * STEP_M
        rows.append(
            {
                "label": lab["id"],
                "scope": "start_range",
                "wall_id": lab["wall_id"],
                "truth": lab["truth"],
                "positions": len(ss),
                **{k: v for k, v in counts.items() if v},
            }
        )

    site_rows = []
    by_id = {c["id"]: c for c in verdict["criteria"] if c["scope"] == "site"}
    for lab in labels["labels"]:
        if lab["scope"] != "site":
            continue
        if lab["criterion"] == "placement":
            outcome, src = DECISION_OUTCOME[verdict["site"]["decision"]], "site.decision"
        elif lab["criterion"] in by_id:
            outcome, src = by_id[lab["criterion"]]["outcome"], "criteria"
        else:
            outcome, src = None, "not_evaluated"
        k = (
            classify(lab["truth"], outcome)
            if outcome
            else ("unscored" if lab["truth"] == "unknown" else "not_evaluated")
        )
        site_rows.append(
            {
                "label": lab["id"],
                "criterion": lab["criterion"],
                "truth": lab["truth"],
                "outcome": outcome,
                "from": src,
                "result": k,
            }
        )

    sd_label = labels.get("site_decision", "unknown")
    sd = verdict["site"]["decision"]
    site_decision = {
        "label": sd_label,
        "verdict": sd,
        "result": "unscored"
        if sd_label == "unknown"
        else (
            "agree"
            if sd == sd_label
            else "unsafe_pass"
            if sd == "pass"
            else "false_rejection"
            if sd == "reject" and sd_label == "pass"
            else "missed_review"
            if sd == "reject"
            else "over_caution"
        ),
    }
    pass_agree = pass_over = pass_reject = labelled_pass_positions = 0
    for row in rows:
        if row["truth"] != "pass":
            continue
        labelled_pass_positions += row["positions"]
        pass_agree += row.get("agree", 0)
        pass_over += row.get("over_caution", 0)
        pass_reject += row.get("false_rejection", 0)
    for row in site_rows:
        if row["truth"] != "pass" or row["result"] in ("unscored", "not_evaluated"):
            continue
        if row["result"] == "agree":
            pass_agree += 1
        elif row["result"] == "over_caution":
            pass_over += 1
        elif row["result"] == "false_rejection":
            pass_reject += 1
    pass_denom = pass_agree + pass_over + pass_reject
    pass_recall = round(pass_agree / pass_denom, 4) if pass_denom else None
    scored = sum(pos[k] for k in kinds if k not in ("unscored", "pass_on_unknown", "not_evaluated"))
    unsure_pos = sum(
        1
        for lab in labels["labels"]
        if lab["scope"] == "start_range"
        for s in np.arange(lab["start_s_m"][0], lab["start_s_m"][1] + 1e-9, STEP_M)
        if (_candidate_at(cands, lab["wall_id"], float(s)) or {"outcome": "unsure"})["outcome"] == "unsure"
    )
    total_pos = sum(r["positions"] for r in rows)
    site_scored = [r for r in site_rows if r["result"] not in ("unscored", "not_evaluated")]
    return {
        "format": "base-verdictscore/v0",
        "verdict_id": verdict["verdict_id"],
        "labels_site": labels["site"],
        "labels_profile": labels.get("rules_profile"),
        "evaluator": verdict["evaluator"]["path"],
        "headline": {
            "unsafe_passes": pos["unsafe_pass"]
            + sum(r["result"] == "unsafe_pass" for r in site_rows)
            + (site_decision["result"] == "unsafe_pass"),
            "unsafe_pass_positions": pos["unsafe_pass"],
            "unsafe_pass_wall_m": round(pos_len["unsafe_pass"], 3),
            "unsafe_pass_wall_ft": round(pos_len["unsafe_pass"] / M_PER_FT, 2),
            "unsafe_site_criteria": sum(r["result"] == "unsafe_pass" for r in site_rows),
            "unsafe_site_decision": site_decision["result"] == "unsafe_pass",
            "coverage_contract_unsafe_passes": len(unsupported_pass),
            "pass_recall": pass_recall,
            "labelled_pass_positions": labelled_pass_positions,
        },
        "start_range": {
            "positions": pos,
            "wall_m": {k: round(v, 3) for k, v in pos_len.items()},
            "agreement": round(pos["agree"] / scored, 4) if scored else None,
            "manual_review_rate": round(unsure_pos / total_pos, 4) if total_pos else None,
            "rows": rows,
        },
        "site": {
            "decision": site_decision,
            "pass_recall": pass_recall,
            "criteria": site_rows,
            "agreement": round(sum(r["result"] == "agree" for r in site_scored) / len(site_scored), 4)
            if site_scored
            else None,
            "manual_review_rate": round(
                sum(r["outcome"] == "unsure" for r in site_rows if r["outcome"])
                / max(1, sum(1 for r in site_rows if r["outcome"])),
                4,
            ),
            "not_evaluated": sum(r["result"] == "not_evaluated" for r in site_rows),
        },
        "coverage_contract_errors": contract,
        "c5_margin_errors": c5_errors(verdict),
    }


def markdown(r: dict) -> str:
    h = r["headline"]
    L = [
        f"# Verdict score: {r['verdict_id']} vs labels {r['labels_site']} ({r['labels_profile']})",
        "",
        f"**Unsafe passes: {h['unsafe_passes']}** (start positions {h['unsafe_pass_positions']} = {h['unsafe_pass_wall_ft']} ft of wall; "
        f"site criteria {h['unsafe_site_criteria']}; site decision {h['unsafe_site_decision']}; coverage-contract passes {h['coverage_contract_unsafe_passes']}).",
        "",
    ]
    sr = r["start_range"]
    if sr["rows"]:
        L += [
            f"Start ranges: agreement {sr['agreement']}, manual-review rate {sr['manual_review_rate']}; positions {sr['positions']}.",
            "",
            "| Label | Wall | Truth | Positions | Results |",
            "|---|---|---|---|---|",
        ]
        for row in sr["rows"]:
            res = {k: v for k, v in row.items() if k not in ("label", "scope", "wall_id", "truth", "positions")}
            L.append(f"| {row['label']} | {row['wall_id']} | {row['truth']} | {row['positions']} | {res} |")
    s = r["site"]
    L += [
        "",
        f"Site decision: verdict `{s['decision']['verdict']}` vs label `{s['decision']['label']}` → {s['decision']['result']}. "
        f"Site criteria agreement {s['agreement']}, not evaluated {s['not_evaluated']}.",
        "",
        "| Criterion | Truth | Outcome | Result |",
        "|---|---|---|---|",
    ]
    for row in s["criteria"]:
        L.append(f"| {row['criterion']} | {row['truth']} | {row['outcome']} | {row['result']} |")
    if r["coverage_contract_errors"]:
        L += ["", "Coverage-contract errors:", *[f"- {e}" for e in r["coverage_contract_errors"]]]
    return "\n".join(L) + "\n"


def score_files(
    verdict_path: Path, labels_paths: list[Path], out_dir: Path | None = None, any_profile: bool = False
) -> list[dict]:
    """Scores a verdict against each labels file of its own rules profile (evaluator.rules_profile
    == labels rules_profile); other profiles are skipped unless `any_profile`."""
    v = load_json(verdict_path)
    prof = v["evaluator"].get("rules_profile")
    out = []
    for lp in labels_paths:
        lab = load_json(lp)
        if not any_profile and lab.get("rules_profile") != prof:
            continue
        r = score(v, lab)
        r["labels_file"] = str(lp)
        out.append(r)
        if out_dir:
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            stem = f"{Path(verdict_path).stem}__{Path(lp).stem}"
            (out_dir / f"{stem}.json").write_text(json.dumps(r, indent=1, default=float) + "\n")
            (out_dir / f"{stem}.md").write_text(markdown(r))
    return out
