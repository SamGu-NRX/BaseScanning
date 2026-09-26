"""List every real image the gate is evaluated on -> images.json.

Sets:
- meter:  B2 / PR #16 meter close-ups (83), $B3_DATA/meter/images
- panel:  B2 / PR #17 panel photos (67 unique), $B3_DATA/panel/images
- extra:  B2 extra panel set (61), $B3_DATA/extra/images
- commons: B1 Commons photos (36), /home/ubuntu/b1/data/commons (B1's fetch_images.py)
- walk:   every frame of the walkaround / GT houses in internal/data/houses (tt-barn excluded, as in B1).
          B1's 48 labelled house frames are a subset (same files), so B1 labels carry over.
B1's 80 meter close-ups are the same Commons photos as B2's meter set and are not listed twice.
"""

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORE = HERE.parents[2]
DATA = Path(os.environ.get("B3_DATA", "/tmp/b3/data"))
HOUSES = STORE / "internal/data/houses"
B1_COMMONS = Path("/home/ubuntu/b1/data/commons")


def main():
    rows = []
    for s in ("meter", "panel", "extra"):
        for p in sorted((DATA / s / "images").glob("*.jpg")):
            rows.append({"id": f"{s}/{p.stem}", "set": s, "path": str(p)})
    for p in sorted(B1_COMMONS.glob("*.jpg")):
        rows.append({"id": f"commons/{p.stem}", "set": "commons", "path": str(p), "b1_id": f"commons/{p.stem}"})
    for h in sorted(HOUSES.iterdir()):
        if h.name == "tt-barn" or not (h / "frames").is_dir():
            continue
        for p in sorted((h / "frames").glob("*.jpg")):
            rows.append({"id": f"{h.name}/{p.stem}", "set": "walk", "house": h.name, "path": str(p), "b1_id": f"{h.name}/{p.stem}"})
    (HERE / "images.json").write_text(json.dumps(rows, indent=0))
    counts = {}
    for r in rows:
        counts[r["set"]] = counts.get(r["set"], 0) + 1
    print(counts, len(rows))


if __name__ == "__main__":
    main()
