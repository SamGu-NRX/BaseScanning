"""packetgen command line. See README.md."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from .depth import load_eth3d_scan
from .export import export
from .images import normalize_images
from .manifest import run_manifest, write_index
from .packet import HouseBuilder
from .photos import build_photo_only
from .sources import compare_to_reference, from_colmap_model, list_images, run_sfm
from .validate import validate_packet

STORE = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2")
DEFAULT_OUT = STORE / "internal" / "data" / "packets"
WORK = Path.home() / "work" / "packetgen-work"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def validate_house(house_dir: Path) -> list[dict]:
    reports = []
    for m in sorted(house_dir.glob("packet.*.json")):
        r = validate_packet(m)
        reports.append(r)
        log(f"validate {m.name}: {'ok' if r['ok'] else 'FAIL'} ({len(r['errors'])} errors, {len(r['warnings'])} warnings, "
            f"max tap replay error {r['max_tap_replay_error']})")
        for e in r["errors"][:5]:
            log(f"  {e}")
    (house_dir / "_truth").mkdir(exist_ok=True)
    (house_dir / "_truth" / "validation.json").write_text(json.dumps(reports, indent=1) + "\n")
    return reports


def cmd_eth3d(a) -> None:
    root = Path(a.scene_dir)
    rng = np.random.default_rng(0)
    recon = from_colmap_model(root / "dslr_calibration_undistorted", root / "images", metric=True,
                              source="eth3d_ground_truth", up_hint=np.array([0.0, 0.0, 1.0]))
    log(f"ETH3D {root.name}: {len(recon.views)} views, {len(recon.points)} points")
    scan = load_eth3d_scan(root / "dslr_scan_eval", a.scan_points, rng) if not a.no_depth else None
    if scan is not None:
        log(f"laser scan: {len(scan)} points")
    hb = HouseBuilder(a.house_id, recon, Path(a.out), scan_points_src=scan,
                      source_dataset=f"ETH3D high-res multi-view '{root.name}' DSLR, laser-scan ground truth (CC BY-NC-SA 4.0, evaluation only)",
                      notes="Institutional building, not a house; no electric meter, so the only tap is an auto reference point.")
    info = hb.build(a.variants)
    log(f"built {info['house_id']}: {info['keyframes']} keyframes, path {info['path_length_m']} m")
    validate_house(Path(a.out) / a.house_id)


def cmd_sequence(a) -> None:
    src = Path(a.images)
    work = WORK / a.house_id
    norm = normalize_images(src, work / "images")
    log(f"SfM on {len(list_images(norm))} frames")
    model = run_sfm(norm, work, sequential=a.sequential)
    recon = from_colmap_model(model, norm, metric=False, source="pycolmap_sfm")
    names = {p.name for p in list_images(norm)}
    recon.dropped = sorted(names - {v.name for v in recon.views})
    log(f"registered {len(recon.views)}/{len(names)} frames, {len(recon.points)} points")
    if len(recon.views) < 3:
        raise SystemExit("too few registered frames for an ARKit packet; use `photos` instead")
    anchor = json.loads(a.anchor) if a.anchor else None
    hb = HouseBuilder(a.house_id, recon, Path(a.out), anchor=anchor, source_dataset=a.source or str(src), notes=a.notes or "")
    info = hb.build(a.variants)
    log(f"built {info['house_id']}: {info['keyframes']} keyframes, scale from {info['scale_source']}")
    if a.reference:
        ref = from_colmap_model(Path(a.reference), norm, metric=True, source="reference")
        cmp = compare_to_reference(recon, ref)
        (Path(a.out) / a.house_id / "_truth" / "sfm_vs_reference.json").write_text(json.dumps(cmp, indent=1) + "\n")
        log(f"SfM vs reference: {cmp}")
    validate_house(Path(a.out) / a.house_id)


def cmd_photos(a) -> None:
    imgs = list_images(Path(a.images))
    info = build_photo_only(a.house_id, imgs, Path(a.out), source_dataset=a.source or a.images, notes=a.notes or "")
    log(f"built {info['house_id']}: {info['stills']} stills (photo-only)")
    validate_house(Path(a.out) / a.house_id)


def cmd_manifest(a) -> None:
    res = run_manifest(Path(a.manifest), Path(a.out), WORK, only=a.only, force=a.force, log=log)
    for r in res:
        log(f"{r['house_id']}: {r['status']}")
        if r["status"] == "built":
            validate_house(Path(a.out) / r["house_id"])
    write_index(Path(a.out))


def cmd_validate(a) -> None:
    ok = True
    for m in a.packets:
        r = validate_packet(Path(m), check_hashes=not a.no_hashes)
        print(json.dumps(r, indent=1))
        ok &= r["ok"]
    sys.exit(0 if ok else 1)


def cmd_export(a) -> None:
    house = Path(a.house)
    out = export(house / f"packet.{a.variant}.json", Path(a.out))
    log(f"exported {out}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="packetgen")
    sub = p.add_subparsers(dest="cmd", required=True)
    variants = ["arkit-lidar", "arkit-nonlidar", "arkit-nonlidar-2018", "pose-free"]

    e = sub.add_parser("eth3d", help="ETH3D high-res scene with ground-truth poses and laser scan")
    e.add_argument("--scene-dir", required=True)
    e.add_argument("--house-id", default="eth3d-facade")
    e.add_argument("--out", default=str(DEFAULT_OUT))
    e.add_argument("--scan-points", type=int, default=6_000_000)
    e.add_argument("--no-depth", action="store_true")
    e.add_argument("--variants", nargs="+", default=variants, choices=variants)
    e.set_defaults(func=cmd_eth3d)

    s = sub.add_parser("sequence", help="ordered frames (video or walkaround): SfM, then packets")
    s.add_argument("--images", required=True)
    s.add_argument("--house-id", required=True)
    s.add_argument("--out", default=str(DEFAULT_OUT))
    s.add_argument("--sequential", action="store_true", default=None, help="sequential matching (default: >80 frames)")
    s.add_argument("--reference", help="COLMAP model of the same frames to compare against (e.g. ground truth)")
    s.add_argument("--anchor", help='JSON, e.g. {"latitude": 30.3, "longitude": -97.7}')
    s.add_argument("--source")
    s.add_argument("--notes")
    s.add_argument("--variants", nargs="+", default=variants, choices=variants)
    s.set_defaults(func=cmd_sequence)

    ph = sub.add_parser("photos", help="unordered single photos: EXIF-only photo_only packet")
    ph.add_argument("--images", required=True)
    ph.add_argument("--house-id", required=True)
    ph.add_argument("--out", default=str(DEFAULT_OUT))
    ph.add_argument("--source")
    ph.add_argument("--notes")
    ph.set_defaults(func=cmd_photos)

    mf = sub.add_parser("manifest", help="packets for every new or changed house in the data manifest")
    mf.add_argument("--manifest", default=str(STORE / "internal" / "data" / "manifest.json"))
    mf.add_argument("--out", default=str(DEFAULT_OUT))
    mf.add_argument("--only", nargs="+")
    mf.add_argument("--force", action="store_true")
    mf.set_defaults(func=cmd_manifest)

    v = sub.add_parser("validate", help="validate packet manifests")
    v.add_argument("packets", nargs="+")
    v.add_argument("--no-hashes", action="store_true")
    v.set_defaults(func=cmd_validate)

    x = sub.add_parser("export", help="standalone packet folder or .zip with packet.json and session.json")
    x.add_argument("--house", required=True)
    x.add_argument("--variant", required=True)
    x.add_argument("--out", required=True)
    x.set_defaults(func=cmd_export)

    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
