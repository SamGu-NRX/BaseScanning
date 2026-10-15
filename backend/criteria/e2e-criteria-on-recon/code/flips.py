"""Per-criterion flips between the laser-GT verdict and photo-model verdicts of the same engine/profile.

Candidate outcomes are sampled every 2 in of battery start position (as score-verdicts does) along
the positions the laser verdict covers. Per criterion: fail if listed in `failing`, unsure if in
`unsure`, pass otherwise (the criterion did not block), `none` where no candidate covers the
position (the photo model did not produce that wall). A flip is any change of that value.

Cause, per flipped position:
- missing: the photo verdict has no candidate there (wall not reconstructed / not on the chain);
- scale: the true-scale photo verdict agrees with the laser there (so only the scale mode changed it);
- shape: the true-scale photo verdict already disagrees (geometry or coverage of the model).

    python flips.py OUT.json  (reads ~/e2e/work/*/verdicts/*)
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

W = Path.home() / "e2e/work"
STEP = 0.0508
SITES = {"facade": "eth3d-facade", "electro": "eth3d-electro", "delivery_area": "eth3d-delivery"}


def engines(vdir: Path) -> dict[str, Path]:
    out = {}
    if (vdir / "C1pr11.demo.json").exists():
        out["C1pr11.demo"] = vdir / "C1pr11.demo.json"
    for f in sorted((vdir / "c13d").glob("*.json")):
        out["C1-3d." + f.stem.rsplit("__", 1)[1]] = f
    for p in ("demo", "S"):
        if (vdir / f"C2.{p}.json").exists():
            out[f"C2.{p}"] = vdir / f"C2.{p}.json"
    return out


def sample(v: dict, s: np.ndarray, crit: list[str]) -> dict[str, np.ndarray]:
    out = {c: np.full(len(s), "none", object) for c in crit}
    for cd in v.get("candidates", []):
        a, b = cd["start_s_m"]
        m = (s >= a - 1e-6) & (s <= b + 1e-6)
        if not m.any():
            continue
        for c in crit:
            o = "fail" if c in cd.get("failing", []) else ("unsure" if c in cd.get("unsure", []) else "pass")
            out[c][m] = o
    return out


def overall(v: dict, s: np.ndarray) -> np.ndarray:
    o = np.full(len(s), "none", object)
    for cd in v.get("candidates", []):
        a, b = cd["start_s_m"]
        o[(s >= a - 1e-6) & (s <= b + 1e-6)] = cd["outcome"]
    return o


def main() -> None:
    res = {}
    for key, site in SITES.items():
        base = W / key / "verdicts"
        if not (base / "gt").exists():
            continue
        E = engines(base / "gt")
        for eng, gpath in E.items():
            g = json.loads(gpath.read_text())
            cands = g.get("candidates", [])
            if not cands:
                s = np.zeros(0)
            else:
                lo = min(c["start_s_m"][0] for c in cands)
                hi = max(c["start_s_m"][1] for c in cands)
                s = np.arange(lo, hi + 1e-9, STEP)
            crit = sorted({c for cd in cands for c in cd.get("failing", []) + cd.get("unsure", [])} |
                          {c["id"] for c in g["criteria"] if c.get("scope") == "candidate"})
            gs, go = sample(g, s, crit), overall(g, s)
            tags = sorted((d.name for d in base.iterdir() if d.is_dir() and d.name != "gt"),
                          key=lambda t: (t.split(".")[0], t.split(".")[-1] != "true"))
            trs = {}
            for tag in tags:
                vp = engines(base / tag).get(eng)
                if not vp:
                    continue
                v = json.loads(vp.read_text())
                vs, vo = sample(v, s, crit), overall(v, s)
                cloud = tag.split(".")[0]
                if tag.endswith(".true"):
                    trs[cloud] = (vs, vo)
                tr = trs.get(cloud)
                per = {}
                for c in crit:
                    diff = gs[c] != vs[c]
                    if not diff.any():
                        continue
                    causes = Counter()
                    kinds = Counter()
                    for i in np.flatnonzero(diff):
                        kinds[f"{gs[c][i]}->{vs[c][i]}"] += 1
                        if vs[c][i] == "none":
                            causes["missing"] += 1
                        elif not tag.endswith(".true") and tr is not None and tr[0][c][i] == gs[c][i]:
                            causes["scale"] += 1
                        else:
                            causes["shape"] += 1
                    per[c] = {"positions": int(diff.sum()), "share": round(float(diff.mean()), 3),
                              "kinds": dict(kinds), "causes": dict(causes)}
                od = go != vo
                res[f"{site}|{eng}|{tag}"] = {
                    "positions": int(len(s)), "criteria_compared": len(crit), "criteria_flipped": len(per),
                    "overall_flip_share": round(float(od.mean()), 3) if len(s) else None,
                    "overall_kinds": dict(Counter(f"{a}->{b}" for a, b in zip(go[od], vo[od]))),
                    "site_decision": {"laser": g["site"]["decision"], "photo": v["site"]["decision"]},
                    "per_criterion": per,
                    "site_criteria": {c["id"]: (next((x["outcome"] for x in g["criteria"] if x["id"] == c["id"] and x.get("scope") == "site"), None), c["outcome"], c.get("unsure_cause"))
                                      for c in v["criteria"] if c.get("scope") == "site"},
                    "chosen_spot_causes": dict(Counter(c.get("unsure_cause") for c in v["criteria"] if c.get("outcome") == "unsure")),
                }
    out = Path(sys.argv[1])
    out.write_text(json.dumps(res, indent=1))
    for k, r in res.items():
        print(k, r["site_decision"], "flipped", r["criteria_flipped"], "/", r["criteria_compared"], "overall", r["overall_flip_share"])


if __name__ == "__main__":
    main()
