"""Assemble the evaluation image list (eval_images.json).

- houses: frames from internal/data/houses/<id>/ as published by the dataset worker. Per house,
  the frames the worker tagged in house.json plus evenly spaced fill-ins, at most PER_HOUSE,
  keeping a minimum gap so near-duplicate video frames don't dominate.
- meters: PR #16's 80 usable Wikimedia Commons meter photos (manifest.csv on t3/meter-closeup).
- commons: extra openly licensed house-exterior photos listed in commons_houses.csv, if present.
"""

import csv
import json
from pathlib import Path

from common import B1, STORE, WORK

PER_HOUSE = 8
ETH3D_FRAMES = ["DSC_0344", "DSC_0359", "DSC_0362", "DSC_0391", "DSC_0401", "DSC_0419"]
FIXED_CAMERA = {"yt-12Ks1idWj40"}  # time-lapse from a tripod: frames differ only by people


def house_items():
    items = []
    for hdir in sorted((STORE / "internal/data/houses").iterdir()):
        frames = sorted((hdir / "frames").glob("*.jpg")) if (hdir / "frames").exists() else []
        if not frames:
            continue
        hid = hdir.name
        meta = json.loads((hdir / "house.json").read_text()) if (hdir / "house.json").exists() else {}
        if hid == "eth3d-facade":
            chosen = [f for f in frames if f.stem in ETH3D_FRAMES]
        elif hid.startswith("eth3d-"):
            chosen = [frames[i] for i in (0, len(frames) // 2, len(frames) - 1)]
        elif meta.get("frames", {}).get("fixed_camera") or hid in FIXED_CAMERA:
            chosen = [frames[i] for i in (0, len(frames) // 2, len(frames) - 1)]
        else:
            names = [f.name for f in frames]
            tagged = []
            for v in meta.get("visible", {}).values():
                for fr in v.get("frames", []) or []:
                    if fr in names and fr not in tagged:
                        tagged.append(fr)
            idx = sorted({names.index(t) for t in tagged})
            gap = max(1, len(frames) // (PER_HOUSE * 2))
            for k in range(PER_HOUSE):
                j = round(k * (len(frames) - 1) / max(1, PER_HOUSE - 1))
                if all(abs(j - i) >= gap for i in idx):
                    idx.append(j)
            idx = sorted(idx)[: PER_HOUSE + 4] if len(idx) > PER_HOUSE + 4 else sorted(idx)
            chosen = [frames[i] for i in idx]
        for f in chosen:
            items.append(
                {
                    "id": f"{hid}/{f.stem}",
                    "path": str(f),
                    "set": "houses",
                    "source": hid,
                    "licence": meta.get("licence", ""),
                    "real": True,
                }
            )
    return items


def meter_items():
    rows = list(csv.DictReader(open(WORK / "data/meter_manifest.csv")))
    return [
        {
            "id": f"meters/{r['id']}",
            "path": str(WORK / f"data/meters_orig/{r['id']}.jpg"),
            "set": "meters",
            "source": r["page_url"],
            "licence": r["license"],
            "real": True,
            "class_label": r["class_label"],
        }
        for r in rows
        if r["usable"] == "yes"
    ]


def commons_items():
    path = B1 / "commons_houses.csv"
    if not path.exists():
        return []
    return [
        {
            "id": f"commons/{r['id']}",
            "path": str(WORK / f"data/commons/{r['id']}.jpg"),
            "set": "commons",
            "source": r["page_url"],
            "licence": r["license"],
            "real": True,
        }
        for r in csv.DictReader(open(path))
        if (WORK / f"data/commons/{r['id']}.jpg").exists()
    ]


def main():
    items = house_items() + commons_items() + meter_items()
    p = B1 / "eval_images.json"
    p.write_text(json.dumps(items, indent=1))
    by = {}
    for it in items:
        by[it["set"]] = by.get(it["set"], 0) + 1
    print(len(items), by)


if __name__ == "__main__":
    main()
