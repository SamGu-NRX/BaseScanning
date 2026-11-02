#!/usr/bin/env python3
"""Root-level study runner: frozen-manifest runs and bit-reproducible replays.

Workflow:
    python run.py --manifest              # freeze/verify the fixture manifest (counts, hashes)
    python run.py                         # run every scenario; hash results; write replay witnesses
    python run.py --only short-walk       # run a subset
    python run.py --replay runs/<name>/witness.json   # re-run from the witness; verify the hash

Every run writes a replay witness (git commit, seeds, command, input hashes) and a result hash
over the canonical judgement content. A replay re-executes the scenario and compares hashes:
same hash = reproduced; a mismatch is reported, never papered over. Uncertainty is first-class:
each result summarises unknowns (unseeable samples, empty denominators, missing inputs), and an
empty denominator reports NaN, never 100%.

Assumption (recorded): "root-level" means the experiment root, experiments/capture-coverage/.
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

from capture_coverage.judge import judge_scene  # noqa: E402
from capture_coverage.reference import Reference, coverage_fraction  # noqa: E402
from capture_coverage.runner import SCENARIOS, Scenario  # noqa: E402
from capture_coverage.sim import write_bundle  # noqa: E402

MANIFEST_PATH = HERE / "fixtures" / "manifest.json"


def canonical_hash(obj) -> str:
    """sha256 over canonical JSON: sorted keys, no whitespace, NaN as null."""
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, allow_nan=True)
    return hashlib.sha256(text.encode()).hexdigest()


def build_manifest() -> dict:
    """The frozen fixture manifest: one entry per scenario, grouped by family, with counts."""
    groups: dict[str, list[dict]] = {}
    for s in SCENARIOS:
        scene, walk, spec, kw = s.build()
        groups.setdefault(s.family, []).append(
            {
                "name": s.name,
                "witness_s": s.witness_s,
                "prediction": s.prediction,
                "frames": int(len(walk.positions)),
                "capture_kwargs": {k: (v if k != "drop_frames" and k != "depthless_frames" else len(v)) for k, v in kw.items()},
            }
        )
    return {
        "groups": {k: {"count": len(v), "scenarios": v} for k, v in sorted(groups.items())},
        "scenario_count": len(SCENARIOS),
        "frozen_at_commit": git_commit(),
    }


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=str(HERE.parent.parent)
        ).stdout.strip()
    except Exception:
        return "unknown"


def check_manifest(path: Path) -> dict:
    """Verify the on-disk manifest against the built one; exit nonzero on drift."""
    built = build_manifest()
    if path.exists():
        frozen = json.loads(path.read_text())
        built_no_commit = {k: v for k, v in built.items() if k != "frozen_at_commit"}
        frozen_no_commit = {k: v for k, v in frozen.items() if k != "frozen_at_commit"}
        if built_no_commit != frozen_no_commit:
            print("MANIFEST DRIFT: the scenario grid no longer matches the frozen manifest")
            sys.exit(2)
        print(f"manifest OK: {frozen['scenario_count']} scenarios, "
              + ", ".join(f"{k}={v['count']}" for k, v in frozen["groups"].items()))
        return frozen
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(built, indent=1, sort_keys=True, default=str))
    print(f"frozen manifest written: {path} ({built['scenario_count']} scenarios)")
    return built


def run_scenario(s: Scenario, root: Path) -> dict:
    """One scenario: bundle -> recon -> servers -> judge; attach hash, uncertainty, witness."""
    raw = s.run(root)
    if "error" in raw:
        return raw  # failures are recorded, never retried silently or dropped
    uncertainty = raw.get("uncertainty", {})
    result = {
        "scenario": raw["scenario"],
        "family": raw["family"],
        "prediction": raw["prediction"],
        "violations": raw.get("violations", []),
        "metrics": raw.get("metrics", {}),
        "uncertainty": uncertainty,
    }
    out = {
        **raw,
        "result_hash": canonical_hash(result),
        "commit": git_commit(),
    }
    witness = {
        "scenario": s.name,
        "command": f"python run.py --only {s.name}",
        "commit": out["commit"],
        "result_hash": out["result_hash"],
        "inputs": {
            "manifest": canonical_hash(MANIFEST_PATH.read_text()) if MANIFEST_PATH.exists() else None,
            "bundle": canonical_hash(
                sorted((root / s.name / "bundle").rglob("*"))
                and {str(p.relative_to(root / s.name / "bundle")): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted((root / s.name / "bundle").rglob("*")) if p.is_file()}
            ),
        },
    }
    (root / s.name / "witness.json").write_text(json.dumps(witness, indent=1))
    return out


def replay(witness_path: Path, root: Path) -> None:
    """Re-execute the scenario a witness names and compare the result hash."""
    w = json.loads(witness_path.read_text())
    name = w["scenario"]
    match = [s for s in SCENARIOS if s.name == name]
    if not match:
        print(f"replay FAILED: unknown scenario {name}")
        sys.exit(3)
    fresh_root = root / "replay" / name
    fresh_root.mkdir(parents=True, exist_ok=True)
    out = run_scenario(match[0], fresh_root)
    if "error" in out:
        print(f"replay {name}: run failed, see {fresh_root}")
        sys.exit(4)
    if out["result_hash"] == w["result_hash"]:
        print(f"replay {name}: hash matches {w['result_hash'][:16]}... (reproduced)")
    else:
        print(f"replay {name}: HASH MISMATCH witness={w['result_hash'][:16]} fresh={out['result_hash'][:16]}")
        sys.exit(5)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=HERE / "runs")
    ap.add_argument("--only", type=str, default="")
    ap.add_argument("--manifest", action="store_true", help="freeze/verify the fixture manifest")
    ap.add_argument("--replay", type=Path, default=None, help="replay a run from its witness.json")
    args = ap.parse_args()

    check_manifest(MANIFEST_PATH)
    if args.manifest:
        return
    if args.replay:
        replay(args.replay, args.out)
        return
    sel = [s for s in SCENARIOS if not args.only or s.name in set(args.only.split(","))]
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for s in sel:
        rows.append(run_scenario(s, args.out))
        (args.out / "results.json").write_text(json.dumps(rows, indent=1, default=str))
    ok = [r for r in rows if "error" not in r]
    print(json.dumps(
        [
            {
                "scenario": r.get("scenario"),
                "violations": len(r.get("violations", [])),
                "unknowns": {
                    k: r.get("uncertainty", {}).get(k)
                    for k in ("wall_unknown_samples", "ground_unknown_samples")
                } if "error" not in r else None,
                "hash": r.get("result_hash", "")[:16],
                "error": r.get("error", {}).get("returncode") if "error" in r else None,
            }
            for r in rows
        ],
        default=str,
    ))
    print(f"{len(ok)}/{len(rows)} scenarios completed; results in {args.out / 'results.json'}")


if __name__ == "__main__":
    main()
