"""Frame-level and house-level ground truth for the gate -> results/gt.json.

Frame level (real labels only):
- meter set (83): read_meter. panel (67) and extra (61): read_panel. All 128 panel photos are
  electrical equipment (B2's screens; GPT-5.5 and Grok 4.7 both call 5 + 2 of them industrial).
- B1-labelled frames (35 Commons + 48 walkaround frames): per-class presence from B1's
  labels.jsonl (boxes + present_extra), AI-made labels as in B1.
"must keep" (needs a VLM) = read_meter | read_panel | electric meter | meter can | main panel |
gas meter | AC condenser | pool. Doors, windows, fences, downspouts and hose bibs are in nearly
every house frame and are handled by the 3D / selected-frame pass, not per-frame VLM calls.
House level: internal/data/houses/<id>/house.json `visible` states (hand-worked, yes/partial = present).
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORE = HERE.parents[2]
B1 = STORE / "internal/breadth/b1-detection"
HOUSES = STORE / "internal/data/houses"

EQUIP = {"electric_meter": "meter", "meter_can": "meter", "main_panel": "panel", "gas_meter": "gas_meter",
         "ac_condenser": "ac_condenser", "pool": "pool"}
OTHER = ["door", "window", "downspout", "hose_bib", "fence", "driveway"]
HOUSE_TAGS = {"meter": "meter", "panel": "panel", "gas_meter": "gas_meter", "ac_unit": "ac_condenser", "pool": "pool"}


def main():
    images = json.loads((HERE / "images.json").read_text())
    b1 = {}
    for line in (B1 / "labels/labels.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            b1[r["id"]] = r
    frames = {}
    for im in images:
        g = {"labelled": False, "classes": []}
        if im["set"] == "meter":
            g = {"labelled": True, "classes": ["meter", "read_meter"]}
        elif im["set"] in ("panel", "extra"):
            g = {"labelled": True, "classes": ["panel", "read_panel"]}
        elif im.get("b1_id") in b1:
            lab = b1[im["b1_id"]]
            present = {o["label"] for o in lab["objects"]} | {k for k, v in lab.get("present_extra", {}).items() if v}
            classes = sorted({EQUIP[c] for c in present if c in EQUIP} | {c for c in present if c in OTHER})
            g = {"labelled": True, "classes": classes}
        g["must_keep"] = any(c in ("meter", "panel", "gas_meter", "ac_condenser", "pool") for c in g["classes"])
        frames[im["id"]] = g
    houses = {}
    for h in sorted(HOUSES.iterdir()):
        if h.name == "tt-barn" or not (h / "house.json").exists():
            continue
        vis = json.loads((h / "house.json").read_text()).get("visible", {})
        houses[h.name] = {cls: {"state": vis.get(tag, {}).get("state", "unknown"), "frames": vis.get(tag, {}).get("frames", []),
                                "note": vis.get(tag, {}).get("note", "")} for tag, cls in HOUSE_TAGS.items()}
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results/gt.json").write_text(json.dumps({"frames": frames, "houses": houses}, indent=0))
    lab = [f for f in frames.values() if f["labelled"]]
    print("labelled", len(lab), "must_keep", sum(f["must_keep"] for f in lab))
    for h, v in houses.items():
        print(h, {c: s["state"] for c, s in v.items()})


if __name__ == "__main__":
    main()
