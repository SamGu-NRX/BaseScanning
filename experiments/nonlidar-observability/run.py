#!/usr/bin/env python3
"""Root-level study runner: frozen manifest, canonical runs, bit-reproducible replays.

Workflow:

    python run.py --manifest manifest.json    # verify (or write) the frozen manifest, then run
    python run.py                             # same, against manifest.json next to this file
    python run.py --replay results            # recompute from the manifest; verify the hashes

A run writes results/run.json (per scenario: the raw observation map and its
sha256, compatible-world counts per fact, one exactly-equivalent world pair per
unknown fact, and per-action re-runs that say which added views distinguish the
worlds) and results/table.md (the fact-by-capability table and the unknown
outcomes). A replay re-executes every scenario from the frozen manifest and
compares the hashes: a match means reproduced, a mismatch is reported, never
papered over.

Assumption (recorded): "root-level" means the experiment root,
experiments/nonlidar-observability/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from nonlidar_observability.scenarios import (  # noqa: E402
    ACTIONS,
    DEFAULT_ACTIONS,
    GRID,
    IMAGE_H,
    IMAGE_W,
    NOMINAL,
    pan_pair_for,
    reobserve_for,
    scenarios,
)
from nonlidar_observability.worlds import FACTS  # noqa: E402

RESULTS = HERE / "results"

FACT_LABELS = {
    "orientation_deg": "orientation (wall yaw)",
    "distance_ft": "distance (face offset from the meter)",
    "left_end_ft": "extent, left end (s0)",
    "right_end_ft": "extent, right end (s1)",
    "height_ft": "height (wall top)",
}


def canonical_hash(obj) -> str:
    """sha256 over canonical JSON: sorted keys, no whitespace."""
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, allow_nan=True)
    return hashlib.sha256(text.encode()).hexdigest()


def _camera_entry(cam) -> dict:
    """A camera as manifest data: position, aim, focal length, sensor size."""
    forward = -cam.rotation[:, 2]
    target = cam.center + forward * 14.0
    return {
        "id": cam.keyframe_id,
        "position_ft": [round(float(v), 4) for v in cam.center],
        "target_ft": [round(float(v), 4) for v in target],
        "fx": cam.fx,
        "image": [IMAGE_W, IMAGE_H],
    }


def build_manifest() -> dict:
    """The frozen manifest: grid, scenarios, actions, model constants."""
    scenario_entries = [
        {
            "name": s.name,
            "capability": s.capability,
            "true_world": s.true_world.facts(),
            "cameras": [_camera_entry(c) for c in s.cameras],
            "actions": list(s.actions),
        }
        for s in scenarios()
    ]
    action_entries = {
        name: {
            "description": a.description,
            "cameras": [_camera_entry(c) for c in a.cameras],
        }
        for name, a in sorted(ACTIONS.items())
    }
    action_entries["pan_pair"] = {
        "description": pan_pair_for(()).description,
        "cameras": "yawed copies of each base keyframe in place (+0.35 rad about world vertical)",
    }
    action_entries["reobserve"] = {
        "description": reobserve_for(()).description,
        "cameras": "duplicates of each base keyframe, same pose and intrinsics",
    }
    return {
        "study": "nonlidar-observability",
        "question": (
            "Without LiDAR, which wall facts do the exported observations support at all?"
        ),
        "grid": {k: list(v) for k, v in GRID.items()},
        "model": {
            "camera": "pinhole, one per keyframe, bound to keyframes[].pose/intrinsics/w/h",
            "pose": "column-major 16, cam-to-world, translation feet (scene.schema.json)",
            "image": [IMAGE_W, IMAGE_H],
            "quantize_px": 3,
            "equality": "worlds equivalent iff canonical observation hashes match",
        },
        "facts": list(FACTS),
        "true_world_nominal": NOMINAL.facts(),
        "default_actions": list(DEFAULT_ACTIONS),
        "scenarios": scenario_entries,
        "actions": action_entries,
    }


def verify_or_write_manifest(path: Path) -> dict:
    """The manifest is the frozen experiment definition; drift is an error."""
    manifest = build_manifest()
    if path.exists():
        stored = json.loads(path.read_text())
        if canonical_hash(stored) != canonical_hash(manifest):
            sys.exit(
                f"manifest drift: {path} does not match the built manifest. "
                "Regenerate deliberately: delete the file and rerun."
            )
        print(f"manifest verified: {path}")
    else:
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"manifest written: {path}")
    return manifest


def run_study(manifest: dict) -> dict:
    """Run every scenario listed in the manifest, in manifest order."""
    by_name = {s.name: s for s in scenarios()}
    listed = [entry["name"] for entry in manifest["scenarios"]]
    if sorted(listed) != sorted(by_name):
        sys.exit(f"manifest scenarios {listed} do not match the study's {sorted(by_name)}")
    return {"scenarios": [by_name[name].run() for name in listed]}


def render_table(run: dict) -> str:
    """The fact-by-capability table and the unknown outcomes."""
    lines = ["# Fact-by-capability table", ""]
    lines += [
        "Compatible-world counts per fact. A count of 1 means every grid world compatible",
        "with the observations agrees on the fact (supported). A count above 1 means the",
        "observations fit that many distinct fact values (UNKNOWN).",
        "",
    ]
    header = "| fact | " + " | ".join(s["name"] for s in run["scenarios"]) + " |"
    lines += [header, "|" + "---|" * (len(run["scenarios"]) + 1)]
    for fact in FACTS:
        row = [FACT_LABELS[fact]]
        for s in run["scenarios"]:
            v = next(x for x in s["verdicts"] if x["fact"] == fact)
            row.append(
                "1 — supported" if v["supported"] else f"UNKNOWN ({v['compatible_value_count']})"
            )
        lines.append("| " + " | ".join(row) + " |")

    lines += ["", "## Extra actions that distinguish the worlds", ""]
    for s in run["scenarios"]:
        unknown = [v for v in s["verdicts"] if not v["supported"]]
        if not unknown:
            lines.append(
                f"- **{s['name']}**: nothing to distinguish; every fact already supported."
            )
            continue
        lines.append(f"- **{s['name']}** — {s['capability']}")
        for v in unknown:
            settlers = [
                name
                for name, a in sorted(s["actions"].items())
                if a["post_verdicts"][v["fact"]]["supported"]
            ]
            lines.append(
                f"  - {FACT_LABELS[v['fact']]}: UNKNOWN, {v['compatible_value_count']} distinct "
                f"values fit. Equivalent pair: {v['pair'][0]} vs {v['pair'][1]}."
            )
            if settlers:
                lines.append(f"    Actions that distinguish the worlds: {', '.join(settlers)}.")
            else:
                lines.append("    No tested action distinguishes the worlds.")
        control = s["actions"].get("reobserve")
        if control is not None:
            base_supported = {v["fact"] for v in s["verdicts"] if v["supported"]}
            pinned_by_control = [
                f
                for f, p in control["post_verdicts"].items()
                if p["supported"] and f not in base_supported
            ]
            if pinned_by_control:
                lines.append(
                    f"  - CONTROL FAILURE: reobserve pinned {pinned_by_control} "
                    "with zero new information."
                )

    lines += ["", "## Unknown outcomes", ""]
    n_unknown = sum(1 for s in run["scenarios"] for v in s["verdicts"] if not v["supported"])
    lines.append(
        f"{n_unknown} fact-by-capability cells stay UNKNOWN: the observations fit more than one "
        "fact value there. Counts are grid-relative (README.md, assumption A5)."
    )
    return "\n".join(lines) + "\n"


def replay(manifest: dict, stored: dict) -> list[str]:
    """Recompute every scenario; return the mismatch list (empty = reproduced)."""
    fresh = run_study(manifest)
    problems = []
    for was, now in zip(stored["scenarios"], fresh["scenarios"], strict=True):
        if was["observation_hash"] != now["observation_hash"]:
            problems.append(f"{now['name']}: observation hash changed")
        elif was["compatible_count"] != now["compatible_count"]:
            problems.append(f"{now['name']}: compatible count changed")
        for v_was, v_now in zip(was["verdicts"], now["verdicts"], strict=True):
            if (
                v_was["supported"] != v_now["supported"]
                or v_was["compatible_value_count"] != v_now["compatible_value_count"]
            ):
                problems.append(f"{now['name']}/{v_now['fact']}: verdict changed")
    return problems


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=HERE / "manifest.json")
    parser.add_argument("--replay", type=Path, help="results directory to replay against")
    args = parser.parse_args()

    manifest = verify_or_write_manifest(args.manifest)

    if args.replay:
        stored = json.loads((args.replay / "run.json").read_text())
        problems = replay(manifest, stored)
        if problems:
            print("REPLAY MISMATCH:")
            for p in problems:
                print(f"  - {p}")
            sys.exit(1)
        print(f"replay reproduced: {len(stored['scenarios'])} scenarios, all hashes match")
        return

    run = run_study(manifest)
    run["manifest_hash"] = canonical_hash(manifest)
    run["git_commit"] = git_commit()

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "run.json").write_text(json.dumps(run, indent=2, sort_keys=True) + "\n")
    (RESULTS / "table.md").write_text(render_table(run))

    for s in run["scenarios"]:
        unknown = [v["fact"] for v in s["verdicts"] if not v["supported"]]
        print(
            f"{s['name']}: {s['compatible_count']}/{s['grid_count']} worlds compatible; "
            f"unknown: {', '.join(unknown) if unknown else 'none'}"
        )
    print(f"results: {RESULTS / 'run.json'}, {RESULTS / 'table.md'}")


if __name__ == "__main__":
    main()
