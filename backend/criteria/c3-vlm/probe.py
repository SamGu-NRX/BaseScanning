"""Measurement probe on eth3d-facade: ask for six distances with laser/PR #13 truth, photo-only vs with tools.

python3 probe.py [--models ...] [--reps 2] [--tools claude-sonnet-5,claude-opus-5-5]
Results: results/eth3d-facade/probe/<variant>/<model>/rep<N>.json and results/eth3d-facade/probe/summary.json
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor

import geometry as G
import meter_hint as MH
import run_tools as RT
import ssr_prompts as S
import vlm

# Truth in feet. Photo-derived spans are PR #13's multi-view laser-plane values; the laser column in its README agrees within 0.1-0.6 ft.
TRUTH = {
    "gap_w4_w5_ft": (5.11, "laser opening gap 5.10"),
    "window4_width_ft": (5.56, "laser opening 5.60"),
    "window4_sill_height_ft": (3.32, "top of stone sill; laser brick edge 0.1-0.5 ft lower"),
    "meter_to_tower_corner_ft": (9.90, "w1/w2 inside corner"),
    "tower_projection_ft": (11.6, "w2 length, 3.54 m"),
    "door_width_ft": (7.18, "laser 7.65 incl. recess; +/-0.7"),
}

PROBE = """Measure the following from the photos. Photo 8 shows the red cross marking the electric meter spot on the ground-floor wall.
Window 4 = the ground-floor arched window immediately LEFT of the cross in photo 8; window 5 = the one immediately RIGHT of it.
The entrance tower = the projecting bay with the glazed entrance door, to the right of window 5 in photo 8.

1. gap_w4_w5_ft: horizontal clear wall between window 4's right edge and window 5's left edge (the window openings, not the arches above).
2. window4_width_ft: width of window 4's opening.
3. window4_sill_height_ft: height of window 4's sill above the ground in front of it.
4. meter_to_tower_corner_ft: horizontal distance along the wall from the red cross to the inside corner where this wall meets the side of the entrance tower.
5. tower_projection_ft: how far the entrance tower projects out from this wall (the length of the tower's side wall).
6. door_width_ft: width of the entrance door opening in the tower front.

For each, give your best estimate in feet. Estimate from scale cues if you have no tools (brick courses, doors, people, bins); say which cue you used.
Respond with only a single JSON object, no code fences:
{"gap_w4_w5_ft": 0, "window4_width_ft": 0, "window4_sill_height_ft": 0, "meter_to_tower_corner_ft": 0, "tower_projection_ft": 0, "door_width_ft": 0, "scale_cues": "short"}"""


def errors(j: dict | None) -> dict:
    if not j:
        return {}
    out = {}
    for k, (t, _) in TRUTH.items():
        try:
            v = float(j.get(k))
        except (TypeError, ValueError):
            continue
        out[k] = {"est": v, "truth": t, "err_ft": round(v - t, 2), "ratio": round(v / t, 2)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="claude-sonnet-5,gpt-5.5,claude-opus-5-5")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--tools", default="claude-sonnet-5,claude-opus-5-5")
    a = ap.parse_args()
    geo = G.load_eth3d_facade()
    photos = MH.marked_packet(geo)
    root = vlm.RESULTS / MH.HOUSE / "probe"
    prompt = S.SSRPrompt("probe", "measurement probe", PROBE, PROBE)

    def photo_job(model, rep):
        out = root / "photo_only" / model / f"rep{rep}.json"
        if not out.exists():
            r = vlm.call_photo_prompt(model, "", photos, MH.NOTE + PROBE, prompt_first=True)
            r["errors"] = errors(r.get("json"))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(r, indent=1))
        return json.loads(out.read_text())

    def tool_job(model):
        out = root / "tools_laser_gt" / model / "rep1.json"
        if not out.exists():
            r = RT.run_tool_prompt(model, geo, photos, prompt)
            r["errors"] = errors(r.get("json"))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(r, indent=1, default=str))
        return json.loads(out.read_text())

    with ThreadPoolExecutor(max_workers=8) as ex:
        pf = {(m, i): ex.submit(photo_job, m, i) for m in a.models.split(",") for i in range(1, a.reps + 1)}
        tf = {m: ex.submit(tool_job, m) for m in a.tools.split(",") if m}
        summary = {"truth": TRUTH, "photo_only": {f"{m}#{i}": f.result()["errors"] for (m, i), f in pf.items()},
                   "tools_laser_gt": {m: f.result()["errors"] for m, f in tf.items()},
                   "cost_usd": {**{f"photo:{m}#{i}": f.result()["cost_usd"] for (m, i), f in pf.items()},
                                **{f"tools:{m}": f.result()["cost_usd"] for m, f in tf.items()}}}
    (root / "summary.json").write_text(json.dumps(summary, indent=1))
    for variant in ("photo_only", "tools_laser_gt"):
        for run, errs in summary[variant].items():
            print(variant, run, {k: v["est"] for k, v in errs.items()})


if __name__ == "__main__":
    main()
