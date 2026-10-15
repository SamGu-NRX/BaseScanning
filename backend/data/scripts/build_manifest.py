#!/usr/bin/env python3
"""Rebuild ../manifest.json from every houses/<id>/house.json.

Each house.json is the source of truth; this script adds the frame file list and a summary.
Usage: python3 build_manifest.py
"""
import datetime
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOUSES = os.path.join(ROOT, "houses")

houses = []
for hid in sorted(os.listdir(HOUSES)):
    path = os.path.join(HOUSES, hid, "house.json")
    if not os.path.isfile(path):
        continue
    h = json.load(open(path))
    fdir = os.path.join(HOUSES, hid, (h.get("frames") or {}).get("dir", "frames/"))
    files = sorted(f for f in os.listdir(fdir) if f.lower().endswith((".jpg", ".jpeg", ".png"))) if os.path.isdir(fdir) else []
    h.setdefault("frames", {})
    h["frames"]["count"] = len(files)
    h["frames"]["files"] = files
    size = 0
    for dp, _, fs in os.walk(os.path.join(HOUSES, hid)):
        size += sum(os.path.getsize(os.path.join(dp, f)) for f in fs)
    h["bytes_on_disk"] = size
    houses.append(h)

by_type = {}
for h in houses:
    by_type[h["source_type"]] = by_type.get(h["source_type"], 0) + 1

manifest = {
    "schema_version": "0.1",
    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "root": "internal/data/houses/<id>/",
    "visible_tags": ["meter", "meter_can", "panel", "gas_meter", "ac_unit", "openings_near_meter", "corners",
                     "ground_14ft", "pool", "driveway", "fence", "overhangs"],
    "visible_states": ["yes", "partial", "no", "unknown"],
    "summary": {
        "houses": len(houses),
        "by_source_type": by_type,
        "meter_visible": sorted(h["id"] for h in houses if (h.get("visible", {}).get("meter") or {}).get("state") in ("yes", "partial")),
        "reconstruction_high": sorted(h["id"] for h in houses if (h.get("reconstruction") or {}).get("suitability") == "high"),
        "with_ground_truth": sorted(h["id"] for h in houses if h.get("ground_truth")),
        "total_frames": sum(h["frames"]["count"] for h in houses),
        "total_bytes": sum(h["bytes_on_disk"] for h in houses),
    },
    "houses": houses,
}
tmp = os.path.join(ROOT, ".manifest.json.tmp")
with open(tmp, "w") as f:
    json.dump(manifest, f, indent=1)
os.replace(tmp, os.path.join(ROOT, "manifest.json"))
print(f"manifest: {len(houses)} houses, {manifest['summary']['total_frames']} frames, "
      f"{manifest['summary']['total_bytes']/1e6:.0f} MB")
