"""Write the WS-09 inputs that gates-offline would otherwise SKIP.

A missing laser PLY leaves any committed overlays in place and the rows SKIP.
Nothing here invents a pass: every file is a criteria-engine verdict or a pair
of outcomes taken from two of those verdicts.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from server.eval.adapters.criteria import CriteriaConfig, evaluate_scene

_PROFILES = ("A", "B", "C", "S", "public")
_ORDER = ("fail", "unsure", "pass")
_SITES = ("eth3d-facade", "eth3d-electro")


def strip_coverage(scene: dict) -> dict:
    """Drop bands, the coverage grid, and keyframes. The engine must not pass."""
    out = json.loads(json.dumps(scene))
    coverage = dict(out.get("coverage") or {})
    coverage["bands"] = []
    coverage.pop("grid", None)
    out["coverage"] = coverage
    out["keyframes"] = []
    return out


def veto_pairs(with_c2: dict, without_c2: dict) -> list[dict]:
    """One pair per candidate. C2 may only hold or downgrade an outcome."""
    left = _by_key(with_c2)
    right = _by_key(without_c2)
    pairs = []
    for key in sorted(set(left) | set(right)):
        held = left.get(key)
        bare = right.get(key)
        if held is None:
            held = "unsure" if bare in _ORDER else None
        if bare is None:
            bare = "fail" if held in _ORDER else None
        if held not in _ORDER or bare not in _ORDER:
            continue
        pairs.append({"id": key[0], "with_c2": held, "without_c2": bare})
    return pairs


def produce(repo: Path, inputs: Path, *, work: Path | None = None) -> list[Path]:
    """Regenerate gravel, decidability, no-coverage, and veto overlays."""
    repo = Path(repo)
    inputs = Path(inputs)
    work = Path(work) if work is not None else repo / "runs" / "gates-offline" / "work"
    staged = {site: _stage(repo, site, work) for site in _SITES}
    if staged["eth3d-facade"] is None:
        print("gate producers: laser ply absent; leaving existing overlays")
        return []
    written: list[Path] = []
    facade = staged["eth3d-facade"]
    written.extend(_decidability(facade, inputs, "eth3d-facade", work / "facade-criteria"))
    if staged["eth3d-electro"] is not None:
        written.extend(_decidability(staged["eth3d-electro"], inputs, "eth3d-electro", work / "electro-criteria"))
    written.extend(_gravel(repo, facade, inputs, work))
    written.extend(_no_coverage(facade, inputs, work))
    written.extend(_veto(facade, inputs, work))
    print(f"gate producers: wrote {len(written)} overlays")
    return written


def _decidability(scene_dir: Path, inputs: Path, site: str, out: Path) -> list[Path]:
    print(f"gate producers: decidability {site}")
    evaluate_scene(
        scene_dir,
        out,
        config=CriteriaConfig(profiles=_PROFILES, ground_rule="footprint3in", c2=True, c3=False),
    )
    dest = inputs / "criteria" / site / "gt-laser" / "criteria_report.json"
    report = json.loads((out / "criteria_report.json").read_text())
    _write_json(dest, report)
    return [dest]


def _gravel(repo: Path, facade: Path, inputs: Path, work: Path) -> list[Path]:
    dest_scene = work / "eth3d-facade" / "gt-laser-gravel"
    if dest_scene.exists():
        shutil.rmtree(dest_scene)
    script = repo / "server" / "criteria" / "tests" / "data" / "facade_gravel.py"
    subprocess.run([sys.executable, str(script), str(facade), str(dest_scene)], check=True)
    written = []
    for rule in ("footprint3in", "angle80"):
        print(f"gate producers: gravel {rule}")
        out = work / f"gravel-{rule}"
        # Photo facts are not this row. Both rules use ignore so the pair
        # differs only by the ground rule.
        produced = evaluate_scene(
            dest_scene,
            out,
            config=CriteriaConfig(
                profiles=("C",),
                ground_rule=rule,
                c2=True,
                c3=False,
                absent_facts="ignore",
            ),
        )
        path = inputs / "positive" / "eth3d-facade" / "gt-laser-gravel" / "C" / f"{rule}.json"
        _write_json(path, produced["C"])
        written.append(path)
    return written


def _no_coverage(facade: Path, inputs: Path, work: Path) -> list[Path]:
    bare = work / "eth3d-facade" / "no-coverage"
    if bare.exists():
        shutil.rmtree(bare)
    bare.mkdir(parents=True)
    scene = strip_coverage(json.loads((facade / "scene.json").read_text()))
    (bare / "scene.json").write_text(json.dumps(scene, indent=1) + "\n")
    for name in ("laser.ply", "occluders.ply"):
        (bare / name).symlink_to((facade / name).resolve())
    written = []
    for c2, label in ((True, "c2"), (False, "noc2")):
        print(f"gate producers: no-coverage {label}")
        out = work / f"nocov-{label}"
        produced = evaluate_scene(
            bare,
            out,
            config=CriteriaConfig(profiles=_PROFILES, ground_rule="footprint3in", c2=c2, c3=False),
        )
        for profile, verdict in produced.items():
            path = inputs / "safety" / "no_coverage" / f"facade-{profile}-{label}.json"
            _write_json(path, verdict)
            written.append(path)
    return written


def _veto(facade: Path, inputs: Path, work: Path) -> list[Path]:
    print("gate producers: veto pairs")
    with_c2 = _profile_c(facade, work / "veto-c2", c2=True)
    without = _profile_c(facade, work / "veto-noc2", c2=False)
    folder = inputs / "safety" / "veto"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    written = []
    for index, pair in enumerate(veto_pairs(with_c2, without)):
        path = folder / f"facade-C-{index:04d}.json"
        _write_json(path, pair)
        written.append(path)
    return written


def _profile_c(scene_dir: Path, out: Path, *, c2: bool) -> dict:
    produced = evaluate_scene(
        scene_dir,
        out,
        config=CriteriaConfig(profiles=("C",), ground_rule="footprint3in", c2=c2, c3=False),
    )
    return produced["C"]


def _stage(repo: Path, site: str, work: Path) -> Path | None:
    src = repo / "data" / "fixtures" / "scenes" / site / "gt-laser"
    cache = repo / ".fixtures" / "gt" / site / "gt-laser"
    if not (src / "scene.json").is_file():
        return None
    if not (cache / "laser.ply").is_file() or not (cache / "occluders.ply").is_file():
        return None
    dest = work / site / "gt-laser"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copy2(src / "scene.json", dest / "scene.json")
    coverage = src / "coverage.npz"
    if coverage.is_file():
        shutil.copy2(coverage, dest / "coverage.npz")
    (dest / "laser.ply").symlink_to((cache / "laser.ply").resolve())
    (dest / "occluders.ply").symlink_to((cache / "occluders.ply").resolve())
    return dest


def _by_key(verdict: dict) -> dict[tuple, str]:
    found = {}
    for candidate in verdict.get("candidates") or []:
        span = candidate.get("start_s_m") or [0.0, 0.0]
        key = (
            str(candidate.get("id") or ""),
            str(candidate.get("wall_id") or ""),
            round(float(span[0]), 3),
            round(float(span[1]), 3),
        )
        outcome = candidate.get("outcome")
        if isinstance(outcome, str):
            found[key] = outcome
    return found


def _strip_runtime(payload: dict) -> dict:
    """Drop fields that change every run so a refresh does not dirty git."""
    out = json.loads(json.dumps(payload))
    out.pop("timings_s", None)
    evaluator = out.get("evaluator")
    if isinstance(evaluator, dict):
        evaluator.pop("runtime_s", None)
    return out


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_strip_runtime(payload), indent=1) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write WS-09 overlays for gates-offline")
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--inputs", type=Path, default=Path("data/fixtures"))
    parser.add_argument(
        "--update",
        action="store_true",
        help="Write into --inputs. Without this flag, write under runs/gates-offline/produced.",
    )
    args = parser.parse_args(argv)
    repo = args.repo
    dest = args.inputs if args.update else repo / "runs" / "gates-offline" / "produced"
    produce(repo, dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
