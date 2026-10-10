"""Scenario grid and orchestration: build, capture, reconstruct, solve, judge, report.

Every scenario pairs a procedural scene with a capture and a preregistered prediction of what
honest coverage would do. Results land outside the repo (the tree stays clean): one JSON per
run plus a combined summary table.

Usage:
    python -m capture_coverage.runner --list
    python -m capture_coverage.runner --only baseline,bin --out /tmp/cc-study
    python -m capture_coverage.runner --out /tmp/cc-study --shard 0/4
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .judge import judge_scene
from .reference import Reference
from .scenes import Box, Ellipsoid, Scene, default_grounds
from .sim import CaptureSpec, plan_walk, write_bundle
from .solve import ensure_t3_server, solve_scene

REPO = Path(__file__).resolve().parents[3]  # .../BaseScanning
RECON = REPO / "recon"
SERVER = REPO / "server"

WALL = dict(wall_x0=-2.0, wall_x1=22.0)


def base_scene(**kw) -> Scene:
    """The F0 house: one long wall, one door opening, concrete in front of the battery."""
    kw_openings = kw.pop("openings", [(9.5, 10.5, 0.0, 2.1)])
    s = Scene("f0", openings=kw_openings, **{**WALL, **kw})
    s.grounds = default_grounds(s, (-1.0, 12.0))
    return s


def battery_spec(mark=(0.0, 20.0), meter_x=0.0, **kw) -> CaptureSpec:
    return CaptureSpec(baseline=mark, meter_x=meter_x, wall_height_ft=8.5, **kw)


def _walk(scene, x_from=-1.0, x_to=21.0, **kw):
    return plan_walk(scene, x_from, x_to, **kw)


@dataclass
class Scenario:
    name: str
    family: str  # A reported-capture noise/skew, B physical confounds, C holes/ends, D missing inputs
    prediction: str  # preregistered expectation for an honest pipeline
    witness_s: list[float]  # s positions (metres) the judge reports on
    build: callable  # () -> (Scene, Walk, CaptureSpec, write_bundle kwargs)

    def run(self, root: Path) -> dict:
        scene, walk, spec, kw = self.build()
        d = root / self.name
        (d / "bundle").mkdir(parents=True, exist_ok=True)
        write_bundle(d / "bundle", scene, walk, spec, **kw)
        proc = subprocess.run(
            [
                "uv", "run", "--project", str(RECON), "python", "-m", "recon",
                str(d / "bundle"), "--out", str(d / "out"), "--depth", "lidar", "--no-server",
            ],
            capture_output=True, text=True, timeout=3600, cwd=str(RECON),
        )
        if proc.returncode != 0 or not (d / "out" / "scene.json").exists():
            return {
                "scenario": self.name,
                "family": self.family,
                "error": {"returncode": proc.returncode, "log": proc.stdout[-4000:], "err": proc.stderr[-4000:]},
            }
        scene_path = d / "out" / "scene.json"
        server_results = {"main": solve_scene(scene_path, SERVER, REPO, "main")}
        t3 = ensure_t3_server(REPO)
        server_results["t3"] = solve_scene(scene_path, t3, REPO, "t3")
        verdict = judge_scene(scene, walk, Reference(scene, walk), d / "out", server_results, self.witness_s)
        out = {
            "scenario": self.name,
            "family": self.family,
            "prediction": self.prediction,
            "worker": {"returncode": proc.returncode, "log_tail": proc.stdout[-2000:]},
            **verdict,
        }
        (d / "judge.json").write_text(json.dumps(out, indent=1, default=str))
        return out


SCENARIOS = [
    Scenario(
        "baseline",
        "A",
        "no violations; worker claims the marked span, bounds facing at the door jamb, skips the door",
        witness_s=[6.5, 9.0, 10.0, 15.0],
        build=lambda: (base_scene(), _walk(base_scene()), battery_spec(), {}),
    ),
    Scenario(
        "noise-high",
        "A",
        "5x depth noise: still no violations (visibility truth ignores noise); claims may shrink",
        witness_s=[6.5, 15.0],
        build=lambda: (base_scene(), _walk(base_scene()), battery_spec(), {"depth_sigma": 0.040}),
    ),
    Scenario(
        "skew-path",
        "A",
        "exact poses, path angled 6 deg off the wall: no violations; fewer qualifying frames, claims shrink",
        witness_s=[10.0],
        build=lambda: (base_scene(), _walk(base_scene(), skew_deg=6.0), battery_spec(), {}),
    ),
    Scenario(
        "drift-cross-track",
        "A",
        "reported poses drift 4 cm/m while photons stay true: FALSE_CLEAR/FALSE_OBSERVED in true "
        "coordinates if claims trust the drifted poses; fingerprint stable (self-consistent)",
        witness_s=[10.0, 15.0],
        build=lambda: (
            base_scene(),
            _walk(base_scene(), drift={"cross_track": 0.04, "jitter": True}),
            battery_spec(),
            {},
        ),
    ),
    Scenario(
        "frames-skip",
        "A",
        "every 3rd frame dropped: no violations unless claims exceed what 2/3 of views support",
        witness_s=[10.0],
        build=lambda: (
            base_scene(),
            _walk(base_scene()),
            battery_spec(),
            {"drop_frames": list(range(0, 40, 3))},
        ),
    ),
    Scenario(
        "depthless-frames",
        "A",
        "every 5th frame reports pose but no depth: same expectation as frames-skip",
        witness_s=[10.0],
        build=lambda: (
            base_scene(),
            _walk(base_scene()),
            battery_spec(),
            {"depthless_frames": list(range(1, 40, 5))},
        ),
    ),
    Scenario(
        "no-confidence",
        "A",
        "depth without confidence planes: worker must keep claims (or shrink); no violations expected",
        witness_s=[10.0],
        build=lambda: (base_scene(), _walk(base_scene()), battery_spec(), {"omit_confidence": True}),
    ),
    Scenario(
        "bush-against-wall",
        "B",
        "1.2 m bush flush to the wall: ground strip and low wall behind it are UNSEEABLE; honest "
        "pipeline ends coverage there; FALSE_OBSERVED if it claims through",
        witness_s=[14.5],
        build=lambda: _confound_scene(
            Ellipsoid(np.array([14.5, 0.6, 0.25]), np.array([0.5, 0.6, 0.25]))
        ),
    ),
    Scenario(
        "bin",
        "B",
        "1 m bin 0.75 m tall 0.2 m off the wall: gap behind unseeable; FALSE_OBSERVED if claimed "
        "through; facing clear should bound at ~0.2 m before the bin",
        witness_s=[15.5],
        build=lambda: _confound_scene(Box(np.array([15.0, 0.0, 0.2]), np.array([16.0, 0.75, 1.2]), "bin")),
    ),
    Scenario(
        "fence-parallel",
        "B",
        "fence 4 m back, fully visible: honest facing-clear bounds near 13 ft; FALSE_CLEAR beyond",
        witness_s=[10.0],
        build=lambda: _scene_kw(back_wall_z=4.0),
    ),
    Scenario(
        "grazing-rail",
        "B",
        "5 cm rail flush against the wall: LiDAR sees it at 2.4 m standoff; honest pipeline reports "
        "it as a facing measurement, not clear space",
        witness_s=[17.5],
        build=lambda: _confound_scene(Box(np.array([16.5, 0.70, 0.0]), np.array([18.5, 0.75, 0.05]), "rail")),
    ),
    Scenario(
        "arc-wall",
        "B",
        "wall bows 7.5 cm toward the cameras over [10,16]: straight-line fit rounds it off; claims "
        "stay coarse; hard violations would need the fit to cross the bow",
        witness_s=[13.0],
        build=lambda: _scene_kw(arc_chord=(10.0, 16.0)),
    ),
    Scenario(
        "low-pilaster",
        "B",
        "6 cm proud pilaster: borderline for LiDAR at 2.4 m; either a facing measurement or a "
        "slightly wrong clear claim, both honest",
        witness_s=[12.0],
        build=lambda: _scene_kw(pilasters=[(12.0, 0.4, 0.06)]),
    ),
    Scenario(
        "big-opening",
        "C",
        "2 m unfillable doorway in the marked span: honest pipeline stops at the jamb and marks "
        "ends; OVERREACH if it claims the hole",
        witness_s=[6.5],
        build=lambda: _scene_kw(openings=[(5.5, 7.5, 0.0, 2.1)]),
    ),
    Scenario(
        "short-walk",
        "C",
        "walk and mark cover only [7,13] of a 24 m wall with ends unexplored: claims must stay "
        "inside; OVERREACH beyond the mark",
        witness_s=[10.0],
        build=lambda: (base_scene(), _walk(base_scene(), 6.0, 14.0), battery_spec(mark=(7.0, 13.0), meter_x=7.0), {}),
    ),
    Scenario(
        "fence-support",
        "C",
        "short walk with a parallel fence: facing-clear-to-6ft needs wall-band support beyond the "
        "mark; honest pipeline either extends with evidence or marks UNSURE",
        witness_s=[10.0, 13.0],
        build=lambda: (
            base_scene(back_wall_z=4.0),
            _walk(base_scene(back_wall_z=4.0), 8.0, 16.0),
            battery_spec(mark=(9.0, 15.0), meter_x=10.0),
            {},
        ),
    ),
    Scenario(
        "no-ground",
        "D",
        "capture attests no ground polygons: ground claims must come only from the worker's own "
        "depth; a server pass on ground rules without ground evidence would be UNSOUND_PASS",
        witness_s=[10.0],
        build=lambda: (base_scene(), _walk(base_scene()), battery_spec(grounds=[]), {}),
    ),
    Scenario(
        "one-frame-depth",
        "D",
        "exactly one depth frame: single-view free-space must not become claims; EVIDENCE_DEFICIT "
        "or UNSOUND_PASS if the pipeline certifies clear space from one view",
        witness_s=[10.0],
        build=lambda: (
            base_scene(),
            _walk(base_scene()),
            battery_spec(),
            {"depthless_frames": list(range(1, 40))},
        ),
    ),
]


def _confound_scene(occ):
    """A scene with one extra occluder, the matching walk, the standard battery."""
    kw = dict(occluders=[occ])
    return (base_scene(**kw), _walk(base_scene(**kw)), battery_spec(), {})


def _scene_kw(**kw):
    s = base_scene(**kw)
    return (s, _walk(s), battery_spec(), {})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("/tmp/cc-study"))
    ap.add_argument("--only", type=str, default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--shard", type=str, default="")
    args = ap.parse_args()

    if args.list:
        for s in SCENARIOS:
            print(f"{s.name:18s} {s.family} {s.prediction[:100]}")
        return
    if args.only:
        want = set(args.only.split(","))
        sel = [s for s in SCENARIOS if s.name in want]
    elif args.shard:
        k, n = (int(x) for x in args.shard.split("/"))
        sel = SCENARIOS[k::n]
    else:
        sel = SCENARIOS
    args.out.mkdir(parents=True, exist_ok=True)
    tag = args.shard.replace("/", "-") or "all"
    rows = []
    for s in sel:
        print(f"[runner] {s.name} ...", flush=True)
        t0 = time.time()
        try:
            rows.append(s.run(args.out))
        except Exception as e:  # a scenario failing must not sink the shard
            rows.append({"scenario": s.name, "family": s.family, "error": str(e)})
        print(f"[runner] {s.name} done in {time.time() - t0:.0f}s", flush=True)
        (args.out / f"shard-{tag}.json").write_text(json.dumps(rows, indent=1, default=str))
    print(json.dumps([{k: r.get(k) for k in ("scenario", "error")} for r in rows], default=str))


if __name__ == "__main__":
    main()
