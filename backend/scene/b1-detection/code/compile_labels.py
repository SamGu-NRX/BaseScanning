"""Turn the raw hand-labelling decisions (labels/raw/*.jsonl) into labels/labels.jsonl.

Raw row: keep = pooled candidate ids that are correct boxes, relabel = {cand id: class},
add = [[class, x0, y0, x1, y1]] manual boxes, present = image-level answers for the presence-only
classes (window, fence, driveway are not boxed on general photos), ignore = classes left unscored.

Output row: objects (boxed classes), present_extra, ignore (unscored everywhere),
instance_ignore (unscored at instance level only), ground_type.
Meter photos (PR #16) carry only image-level truth: electric meter present, gas meter absent.
"""

import json
from collections import Counter

from common import B1, CLASS_NAMES, read_image_list, read_jsonl, write_jsonl

PRESENCE_ONLY = {"window", "fence", "driveway"}
DROPPED_SOURCES = {"tt-barn"}  # a barn, not a house


def main():
    items = read_image_list(B1 / "eval_images_batch1.json")
    raw = {}
    for p in sorted((B1 / "labels/raw").glob("*.jsonl")):
        for r in read_jsonl(p):
            raw[r["id"]] = r
    out, missing = [], []
    for it in items:
        i = it["id"]
        if it["set"] == "meters":
            out.append({"id": i, "objects": [], "present_extra": {"electric_meter": True},
                        "ignore": [c for c in CLASS_NAMES if c not in ("electric_meter", "gas_meter")],
                        "instance_ignore": ["electric_meter", "gas_meter"], "ground_type": None})
            continue
        if i.split("/")[0] in DROPPED_SOURCES:
            continue
        r = raw.get(i)
        if r is None:
            missing.append(i)
            continue
        cands = {d["id"]: d for d in json.loads((B1 / f"labels/candidates/{i.replace('/', '__')}.json").read_text())}
        relabel = {int(k): v for k, v in r.get("relabel", {}).items()}
        objs = []
        for k in r.get("keep", []):
            d = cands[k]
            objs.append({"label": relabel.get(k, d["label"]), "box": [round(v, 4) for v in d["box"]]})
        for c, *box in r.get("add", []):
            objs.append({"label": c, "box": box})
        present = r.get("present", {})
        ignore = set(r.get("ignore", []))
        ignore |= {c for c in PRESENCE_ONLY if c not in present}
        inst_ign = set(PRESENCE_ONLY)
        # windows are boxed on the ETH3D facade frames (they back the 3D evaluation)
        if i.startswith("eth3d-facade/") and any(o["label"] == "window" for o in objs):
            inst_ign.discard("window")
        objs = [o for o in objs if o["label"] not in PRESENCE_ONLY or o["label"] not in inst_ign]
        out.append({"id": i, "objects": objs,
                    "present_extra": {c: bool(v) for c, v in present.items()},
                    "ignore": sorted(ignore), "instance_ignore": sorted(inst_ign - ignore),
                    "ground_type": r.get("ground"), "note": r.get("note", "")})
    write_jsonl(B1 / "labels/labels.jsonl", out)
    n = Counter(o["label"] for r in out for o in r["objects"])
    print(len(out), "rows; missing:", missing)
    print(dict(n))


if __name__ == "__main__":
    main()
