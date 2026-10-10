"""Comparator: the study's model of the shipped calculation against the shipped solvers.

Builds small scenes, runs them through the real solver extracted from `git
archive` at two refs (main and origin/t3/server), and asserts that
models.current_bar reproduces every on-wall clearance bar both solvers emit,
to 1e-9. Writes results/comparator.json and results/comparator.md.

What the shipped solver does (server/solver.py evaluate, scene.py defaults):

- the battery piece's error is the wall's `error_at(max(|s0|, |s1|))`:
  wall base 0.3 ft plus drift 0.16 ft/ft at the battery's farthest edge;
  the t3/server tree additionally charges the meter's own error (rules
  errors.meter_ft, 0.3 ft) to the battery's plan-placed footprint on the
  ground-band checks that read that piece -- main does not;
- a clearance check adds the checked item's own error, which for a tap-sourced
  object defaults to the same shape at the item's farthest span edge, and for
  a ground patch to tap base plus drift at the patch's farthest coordinate;
- the sum is decided by the three-way `at_least`.

So `current_bar(feature_endpoint, battery_endpoint)` must reproduce each bar
when the feature endpoint is the checked item's farthest walked edge. The
scenes below place each item so that edge is unambiguous per check id.

Run from run.py, or directly: `uv run python comparator.py`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from models import TAP_BASE_FT, Endpoint, current_bar

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def comparator_scenes() -> list[dict]:
    """One 40 ft tap wall, a concrete pad, and wall features at the walked
    distances where the drift term dominates. No object carries an explicit
    plus_minus_ft, so every default error is tap base plus drift."""

    def scene(objects: list[dict]) -> dict:
        return {
            "schema_version": "1.0",
            "meter": {"pos": [0, 0, 0], "wall_id": "w1"},
            "walls": [{"id": "w1", "baseline": [[0, 0], [40, 0]], "height_ft": 9, "source": "tap"}],
            "objects": objects,
            "ground": [{"type": "concrete", "polygon": [[0, 1], [40, 1], [40, 3], [0, 3]]}],
        }

    window = lambda a, b: {  # noqa: E731
        "type": "window",
        "wall_id": "w1",
        "span_ft": [a, b],
        "source": "tap",
        "bottom_ft": 3,
        "top_ft": 6,
    }
    gas = lambda a, b: {  # noqa: E731
        "type": "gas_meter",
        "wall_id": "w1",
        "span_ft": [a, b],
        "source": "tap",
        "bottom_ft": 1,
        "top_ft": 2,
    }
    return [
        {"name": "window-near", "scene": scene([window(2, 5)])},
        {"name": "window-far", "scene": scene([window(15, 18)])},
        {"name": "window-and-meter", "scene": scene([window(15, 18), gas(20, 22)])},
    ]


def resolve_ref(ref: str) -> str:
    out = subprocess.run(
        ["git", "rev-parse", ref], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def extract_ref(ref: str, scenes_path: Path, out_dir: Path) -> Path:
    out = out_dir / f"{ref.replace('/', '_')}.json"
    server_dir = out_dir / f"tree_{ref.replace('/', '_')}"
    server_dir.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "archive", ref, "server"], cwd=ROOT, capture_output=True, check=True
    )
    subprocess.run(
        ["tar", "-x", "-C", str(server_dir)],
        input=archive.stdout,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(HERE / "comparator" / "extract_checks.py"),
            str(server_dir / "server"),
            str(scenes_path),
            str(out),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return out


def item_walked_ft(check_id: str, scene_name: str) -> float:
    """The checked item's farthest walked edge, mirroring the solver's default
    error construction for the study's scenes (one item per checked type)."""
    if check_id == "gas_clearance":
        return 22.0  # window-and-meter's gas meter, span [20, 22]
    if check_id == "opening_clearance":
        return 5.0 if scene_name == "window-near" else 18.0
    if check_id == "drive_clearance":
        return 40.0  # the concrete pad's farthest coordinate
    raise ValueError(f"no modeled item for {check_id} in {scene_name}")


# Checks that read the battery's plan-placed piece: the t3/server tree charges
# the meter's own error (errors.meter_ft) to the battery's position on these,
# on top of its wall error. main does not. The 1e-9 assertion below pins this
# per ref: if a tree changes which piece a check reads, the comparator fails
# loudly rather than modeling the wrong tree.
METER_BASE_FT = 0.3
PLAN_PIECE_CHECKS = {"drive_clearance"}


def m0_bar(item_walked: float, s0: float, s1: float, ref: str, check_id: str) -> float:
    """The study's model of the shipped bar for an on-wall check.

    The checked item's default error (tap base plus drift at its farthest
    edge) plus the battery's error at its farthest edge -- the wall's on
    main; plus the meter's error on the t3/server tree's plan-piece checks."""
    feature = Endpoint(walked_ft=item_walked)
    base = TAP_BASE_FT
    if ref == "origin/t3/server" and check_id in PLAN_PIECE_CHECKS:
        base = TAP_BASE_FT + METER_BASE_FT
    battery = Endpoint(walked_ft=max(abs(s0), abs(s1)), base_ft=base)
    return current_bar(feature, battery, 0.0)


def clearance_checks(rows: list[dict]) -> list[dict]:
    """Checks whose rule is a clearance between the battery and a wall feature."""
    out = []
    for row in rows:
        for ch in row["checks"]:
            if "clearance" in str(ch["id"]) and ch["measured"] is not None:
                out.append({**row, "check": ch})
    return out


def run(commits: dict[str, str] | None = None) -> dict:
    refs = ["main", "origin/t3/server"]
    if commits is None:
        commits = {ref: resolve_ref(ref) for ref in refs}
    scenes = comparator_scenes()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        scenes_path = tmp_path / "scenes.json"
        scenes_path.write_text(json.dumps(scenes))
        extracted = {ref: extract_ref(commits[ref], scenes_path, tmp_path) for ref in refs}
        data = {ref: json.loads(extracted[ref].read_text()) for ref in refs}

    rows: list[dict] = []
    failures: list[str] = []
    for ref in refs:
        checks = clearance_checks(data[ref]["candidates"])
        for row in checks:
            ch = row["check"]
            expected = m0_bar(
                item_walked_ft(ch["id"], row["scene"]),
                row["candidate"]["s0"],
                row["candidate"]["s1"],
                ref,
                ch["id"],
            )
            ok = abs(ch["plus_minus"] - expected) <= 1e-9
            rows.append(
                {
                    "ref": ref,
                    "scene": row["scene"],
                    "s0": row["candidate"]["s0"],
                    "s1": row["candidate"]["s1"],
                    "check_id": ch["id"],
                    "measured_ft": ch["measured"],
                    "e_solver_ft": ch["plus_minus"],
                    "e_model_ft": round(expected, 9),
                    "match": ok,
                }
            )
            if not ok:
                failures.append(f"{ref} {row['scene']} s0={row['candidate']['s0']} {ch['id']}")

    by_key: dict[tuple, list[float]] = {}
    for row in rows:
        key = (row["scene"], row["s0"], row["s1"], row["check_id"])
        by_key.setdefault(key, []).append(row["e_solver_ft"])
    cross_ref_equal = all(len({round(v, 9) for v in vals}) == 1 for vals in by_key.values())

    if failures:
        failures.append("model does not reproduce the shipped solver")
    if not cross_ref_equal:
        failures.append("main and origin/t3/server disagree on a clearance bar")

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "comparator.json").write_text(
        json.dumps(
            {
                "commits": commits,
                "checks": rows,
                "model_matches_solver": not any("model" in f or "disagree" in f for f in failures),
                "refs_agree": cross_ref_equal,
                "failures": failures,
            },
            indent=1,
        )
    )
    write_md(commits, rows, cross_ref_equal, failures)
    if failures:
        raise SystemExit("comparator failed:\n" + "\n".join(failures))
    # Reached only when every check matched at 1e-9 and the refs agree.
    return {
        "commits": commits,
        "n_checks": len(rows),
        "refs_agree": cross_ref_equal,
        "model_matches_solver": True,
    }


def write_md(commits: dict, rows: list[dict], cross_ref_equal: bool, failures: list[str]) -> None:
    lines = [
        "# Comparator: study model vs shipped solvers",
        "",
        f"- main: `{commits['main']}`",
        f"- t3/server: `{commits['origin/t3/server']}`",
        "",
        "`current_bar` in [models.py](models.py) is asserted equal to every on-wall",
        f"clearance bar both trees emit, to 1e-9. Refs agree: {cross_ref_equal}.",
        "",
        "The shipped bar decomposes as the study's `current` bar: the checked item's",
        "default error (tap base plus drift at its farthest edge) plus the battery's",
        "error at its farthest edge. The t3/server tree's driveway rows also charge",
        "the meter's own 0.3 ft error to the battery's plan placement (see the",
        "docstring); main emits no measured driveway rows on these scenes. The gas",
        "and driveway rows pin the item side at the item's own far edge, not the",
        "window's.",
        "",
        "| ref | scene | battery s0 ft | check | measured ft | e solver ft | e model ft |",
        "|---|---|---|---|---|---|---|",
    ]
    seen: set[tuple] = set()
    shown: dict[tuple, int] = {}
    totals: dict[tuple, int] = {}
    for row in rows:
        key = (row["ref"], row["scene"], row["s0"], row["check_id"])
        if key in seen:
            continue
        seen.add(key)
        group = (row["ref"], row["scene"], row["check_id"])
        totals[group] = totals.get(group, 0) + 1
    seen.clear()
    n_written = 0
    for row in rows:
        key = (row["ref"], row["scene"], row["s0"], row["check_id"])
        if key in seen:
            continue
        seen.add(key)
        group = (row["ref"], row["scene"], row["check_id"])
        n = shown.get(group, 0)
        total = totals[group]
        shown[group] = n + 1
        # First and last 6 candidates per ref/scene/check; comparator.json has
        # every row, so the report samples the extremes instead.
        if 6 <= n < total - 6:
            if n == 6:
                lines.append(
                    f"| ... | {row['scene']} | ... | {row['check_id']} "
                    f"| ... ({total - 12} of {total} rows elided) | ... | ... |"
                )
            continue
        n_written += 1
        lines.append(
            f"| {row['ref']} | {row['scene']} | {row['s0']:.2f} | {row['check_id']} "
            f"| {row['measured_ft']:.3f} | {row['e_solver_ft']:.4f} | {row['e_model_ft']:.4f} |"
        )
    if failures:
        lines += ["", "Failures:", *[f"- {f}" for f in failures]]
    (RESULTS / "comparator.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    run()
