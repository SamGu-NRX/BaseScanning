"""Coverage checks on verdicts (contract section 3.1).

- `contract_errors`: pass / fail resting on anything but `observed` coverage evidence (also run by
  `validate`). Every such criterion is scored as an unsupported decision.
- `audit_claims`: recompute each coverage claim against the truth (GT laser + the cameras the
  scene actually used) and flag claims of `observed` that the truth grades grazing / partial /
  unobserved. A decisive outcome on such a claim is the C3 failure mode (tools answering on a wall
  the photos only show edge-on).
"""

from __future__ import annotations

from pathlib import Path

from server.eval.coverage import CoverageQuery, query_region
from server.eval.match import match_walls
from server.eval.sceneio import load_json, verdict_contract_errors

contract_errors = verdict_contract_errors


def audit_claims(verdict: dict, gt_path: Path, recon_path: Path | None = None) -> dict:
    gt_path = Path(gt_path)
    gt = load_json(gt_path)
    wall_map: dict[str, str] = {}
    ids = None
    if recon_path:
        rc = load_json(recon_path)
        wall_map = match_walls(gt, rc)
        ids = [k["id"] for k in rc.get("keyframes", [])] or None
    gt_walls = {w["id"] for w in gt["walls"]}
    q = CoverageQuery.from_scene(gt, gt_path.parent, ids)
    rows = []
    for c in verdict["criteria"]:
        for key in ("measured", "fact"):
            for e in (c.get(key) or {}).get("coverage", []):
                reg = dict(e["region"])
                row = {"criterion": c["id"], "candidate_id": c.get("candidate_id"), "outcome": c["outcome"],
                       "claimed": e["status"], "region": reg}
                wid = wall_map.get(reg.get("wall_id"), reg.get("wall_id"))
                if wid in gt_walls and "s_m" in reg:
                    reg["wall_id"] = wid
                    t = query_region(gt, gt_path.parent, reg, q)
                    row.update({"truth": t["status"], "truth_observed_share": t["observed_share"],
                                "truth_views_median": t["views_median"], "truth_best_incidence_deg": t["best_incidence_deg"]})
                    row["false_observed_claim"] = e["status"] == "observed" and t["status"] != "observed"
                    row["decisive_on_false_claim"] = row["false_observed_claim"] and c["outcome"] in ("pass", "fail")
                else:
                    row["truth"] = None
                rows.append(row)
    checked = [r for r in rows if r["truth"] is not None]
    return {"claims": len(rows), "checked": len(checked),
            "false_observed_claims": sum(r.get("false_observed_claim", False) for r in checked),
            "decisive_on_false_claims": sum(r.get("decisive_on_false_claim", False) for r in checked),
            "contract_errors": contract_errors(verdict), "rows": rows}
