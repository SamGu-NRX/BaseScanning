"""Measure where the live placement solver and the criteria engine agree.

For every committed synthetic capture case (the sha-pinned manifests under
``backend/server/placement/tests/data/``), this script runs both engines on the
same source scene and records what each one decided, in its own words:

- the placement path exactly as the ``placement`` stage runs it in production:
  ``placement.scene_bridge.convert_scene`` validates and converts the merged
  base-scene/v0 document, then the unchanged top-level solver (loaded by
  ``placement.adapter.load_solver``) parses and solves the converted bytes. A
  scene the bridge refuses is recorded with the bridge's own code and pointer.
- the criteria engine exactly as the ``criteria`` stage runs it:
  ``server.criteria.run.evaluate_scene`` over the source scene folder, one
  verdict per rules profile (A, B, C, S), with the site decision and the
  engine's own reasons.

The script imports both engines and never edits them; it adds no evaluator, no
threshold and no policy of its own — it measures. Everything runs in-process on
CPU from committed synthetic fixtures: no network, no Modal, no model calls.

C2 and the C3 judge are off, on purpose and measurably: no committed case ships
the coverage grid ``c2.grid_adapter.from_scene`` sweeps (it returns ``None``
without one), and the judge is the only networked step. The hybrid chain fill
stays on (the shipped default) and ``allow_missing_occluders`` is set because
the committed synthetic scenes ship no occluder geometry or keyframes — the
criteria report is marked provisional by the engine itself in that state.

Output: a deterministic JSON table (no timestamps, no timings) with one record
per case, a per-profile confusion table, and the reasons each engine gave for
every disagreement. Exit code 0 means every case was evaluated or refused
exactly as recorded; any unexpected engine exception is an error, is reported
with its traceback, and fails the run loudly (exit 1).

Usage (from ``backend/``)::

    uv run --frozen --extra api python scripts/placement_criteria_agreement.py \
        [--manifest capture_cases_v1|cases] [--case ID]... [--out PATH] [--work-dir DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]

# Both committed manifests, in the order they were authored (burst8 packet first).
DATA_DIRS: tuple[str, ...] = ("capture_cases_v1", "cases")
MANIFEST_NAME = "manifest.json"
SCHEMA_VERSION = "placement-criteria-agreement/v0"
DECISIONS: tuple[str, ...] = ("pass", "manual_review", "reject")

# The criteria knobs the measurement pins, echoed into the output so the table
# says exactly which engine configuration produced it.
CRITERIA_CONFIG_ECHO = {
    "profiles": ["A", "B", "C", "S"],
    "c2": False,
    "c2_reason": "no committed case ships the coverage grid C2 sweeps (c2.grid_adapter.from_scene returns None)",
    "c3": False,
    "c3_reason": "the C3 judge is the only networked step; measurements run offline",
    "hybrid": True,
    "hybrid_reason": "the shipped default (HSS_CRITERIA_HYBRID on)",
    "absent_facts": "ignore",
    "meter_number_required": True,
    "external_vision": False,
    "allow_missing_occluders": True,
    "allow_missing_occluders_reason": "committed synthetic scenes ship no occluder geometry or keyframes",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_head(repo_root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


def _bootstrap_backend() -> None:
    """Make ``server.*`` importable whether run from backend/ or anywhere else."""

    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))


def load_cases(data_dirs: tuple[str, ...] = DATA_DIRS) -> list[dict[str, Any]]:
    """Every committed case from every committed manifest, hash-verified.

    A drifted or missing fixture fails the run loudly: the manifests pin each
    source by sha256, and measuring against drifted bytes would measure nothing.
    """

    cases: list[dict[str, Any]] = []
    for name in data_dirs:
        manifest_dir = BACKEND / "server" / "placement" / "tests" / "data" / name
        manifest = json.loads((manifest_dir / MANIFEST_NAME).read_text())
        for entry in manifest["cases"]:
            source_path = manifest_dir / entry["source"]["path"]
            raw = source_path.read_bytes()
            actual = _sha256(raw)
            if actual != entry["source"]["sha256"]:
                raise SystemExit(
                    f"{source_path}: sha256 {actual} does not match the manifest pin "
                    f"{entry['source']['sha256']}; refusing to measure drifted fixtures"
                )
            cases.append(
                {
                    "id": entry["id"],
                    "manifest": name,
                    "kind": entry["kind"],
                    "source_bytes": raw,
                    "source_sha256": actual,
                    "wire_sha256": (entry.get("wire") or {}).get("sha256"),
                    "expected_error": entry.get("expected_error"),
                }
            )
    return cases


# ---------------------------------------------------------------------------
# Engine 1: the placement path (bridge + top-level solver), as the stage runs it.
# ---------------------------------------------------------------------------


def run_placement(source_bytes: bytes) -> dict[str, Any]:
    """Convert and solve one source scene; record refusals in the bridge's words.

    Only the engines' declared refusals are caught here (SceneBridgeError). Any
    other exception propagates: the caller records it as an error and fails the
    run loudly, because a crash is not a placement decision.
    """

    _bootstrap_backend()
    from server.placement.adapter import load_solver
    from server.placement.scene_bridge import SceneBridgeError, convert_scene

    document = json.loads(source_bytes.decode("utf-8"))
    try:
        converted = convert_scene(document)
    except SceneBridgeError as exc:
        return {"status": "refused", "code": exc.code, "pointer": exc.pointer, "reason": str(exc)}
    scene_bytes = json.dumps(converted, sort_keys=True, allow_nan=False).encode("utf-8")

    modules = load_solver()
    scene_mod, solver_mod = modules["scene"], modules["solver"]
    rules = modules["rules"].load_rules()
    parsed = scene_mod.parse_scene(json.loads(scene_bytes.decode("utf-8")), rules.rules, scene_bytes)
    result = solver_mod.solve(parsed, rules)

    checks = []
    for check in result["checks"]:
        row = {
            "id": check["id"],
            "outcome": check["outcome"],
            "reason": check["reason"],
            "measured_ft": check["measured_ft"],
            "plus_minus_ft": check["plus_minus_ft"],
        }
        if check.get("unsure_cause"):
            row["unsure_cause"] = check["unsure_cause"]
        checks.append(row)
    return {
        "status": "solved",
        "decision": result["decision"],
        "summary": result["summary"],
        "reasons": result["reasons"],
        "spot": result["spot"],
        "checks": checks,
        "missing_evidence": result["missing_evidence"],
    }


# ---------------------------------------------------------------------------
# Engine 2: the criteria engine, as the stage runs it.
# ---------------------------------------------------------------------------


def run_criteria(source_bytes: bytes, work_dir: Path) -> dict[str, Any]:
    """Evaluate one source scene folder; record declared refusals in engine words.

    Only the engine's declared CriteriaInputError is caught; anything else
    propagates and fails the run loudly.
    """

    _bootstrap_backend()
    from server.criteria.config import CriteriaConfig
    from server.criteria.run import CriteriaInputError, evaluate_scene

    scene_dir = work_dir / "scene"
    out_dir = work_dir / "verdicts"
    scene_dir.mkdir(parents=True, exist_ok=True)
    (scene_dir / "scene.json").write_bytes(source_bytes)

    cfg = CriteriaConfig(
        profiles=("A", "B", "C", "S"),
        c2=CRITERIA_CONFIG_ECHO["c2"],
        c3=CRITERIA_CONFIG_ECHO["c3"],
        hybrid=CRITERIA_CONFIG_ECHO["hybrid"],
        absent_facts=CRITERIA_CONFIG_ECHO["absent_facts"],
        meter_number_required=CRITERIA_CONFIG_ECHO["meter_number_required"],
        external_vision=CRITERIA_CONFIG_ECHO["external_vision"],
        allow_missing_occluders=CRITERIA_CONFIG_ECHO["allow_missing_occluders"],
    )
    try:
        verdicts = evaluate_scene(scene_dir, out_dir, config=cfg)
    except CriteriaInputError as exc:
        return {"status": "refused", "code": exc.code, "reason": f"criteria engine refused: {exc.code}"}

    profiles: dict[str, Any] = {}
    for name, verdict in verdicts.items():
        site = verdict["site"]
        chosen = None
        for cand in verdict.get("candidates") or []:
            if cand["id"] == site["chosen_candidate"]:
                chosen = cand
                break
        profiles[name] = {
            "decision": site["decision"],
            "reasons": site["reasons"],
            "chosen_candidate": site["chosen_candidate"],
            "chosen_outcome": chosen["outcome"] if chosen else None,
            "chosen_failing": sorted(chosen["failing"]) if chosen and chosen.get("failing") else [],
            "chosen_unsure": sorted(chosen["unsure"]) if chosen and chosen.get("unsure") else [],
            "views_needed": [{"kind": v["kind"], "why": v["why"]} for v in verdict["views_needed"]],
        }
    report_path = out_dir / "criteria_report.json"
    degraded: list[str] = []
    provisional = None
    if report_path.is_file():
        report = json.loads(report_path.read_text())
        degraded = report.get("degraded", [])
        provisional = report.get("provisional")
    return {"status": "evaluated", "provisional": provisional, "degraded": degraded, "profiles": profiles}


# ---------------------------------------------------------------------------
# Measurement: run both engines per case, classify agreement, build the table.
# ---------------------------------------------------------------------------


def _guarded(
    fn: Callable[..., dict[str, Any]],
    *args: Any,
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    """Run one engine; an unexpected exception becomes a loud error record.

    Declared refusals come back as normal records with status ``refused``; only
    a crash lands here, and a crash fails the whole run (see ``has_errors``).
    """

    try:
        return fn(*args), None
    except Exception as exc:  # noqa: BLE001 - any engine crash must surface, not pass silently
        return None, {
            "engine": getattr(fn, "__name__", "engine"),
            "exception": type(exc).__name__,
            "message": str(exc)[:500],
            "traceback": "".join(traceback.format_exception(exc))[-2000:],
        }


def _agreement(placement: dict[str, Any], criteria: dict[str, Any]) -> dict[str, Any]:
    """Classify one case from the two engines' own outcomes, no third opinion."""

    p, c = placement["status"], criteria["status"]
    if p == "error" or c == "error":
        verdict = "engine_error"
    elif p == "solved" and c == "evaluated":
        pairs = [
            {
                "profile": name,
                "placement": placement["decision"],
                "criteria": prof["decision"],
                "agree": placement["decision"] == prof["decision"],
            }
            for name, prof in criteria["profiles"].items()
        ]
        verdict = "agree" if all(pair["agree"] for pair in pairs) else "disagree"
        return {"verdict": verdict, "pairs": pairs}
    elif p == "refused" and c == "evaluated":
        verdict = "placement_refused"
    elif p == "solved" and c == "refused":
        verdict = "criteria_refused"
    elif p == "refused" and c == "refused":
        verdict = "neither_engine_evaluated"
    else:  # pragma: no cover - statuses are closed above
        verdict = "unknown"
    return {"verdict": verdict, "pairs": []}


def collect_results(
    cases: list[dict[str, Any]],
    *,
    placement_fn: Callable[[bytes], dict[str, Any]] = run_placement,
    criteria_fn: Callable[[bytes, Path], dict[str, Any]] = run_criteria,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """Run both engines on every case and assemble the deterministic table."""

    table: list[dict[str, Any]] = []
    for case in cases:
        record: dict[str, Any] = {
            "id": case["id"],
            "manifest": case["manifest"],
            "kind": case["kind"],
            "source_sha256": case["source_sha256"],
        }
        placement_record, placement_error = _guarded(placement_fn, case["source_bytes"])
        record["placement"] = placement_record if placement_record else {"status": "error", **placement_error}
        with tempfile.TemporaryDirectory(prefix="pc-agreement-", dir=work_dir) as tmp:
            criteria_record, criteria_error = _guarded(criteria_fn, case["source_bytes"], Path(tmp))
        record["criteria"] = criteria_record if criteria_record else {"status": "error", **criteria_error}
        record["agreement"] = _agreement(record["placement"], record["criteria"])
        table.append(record)
    return table


def _confusion(table: list[dict[str, Any]]) -> dict[str, Any]:
    """Profile-pair counts, placement decision x criteria decision.

    Built only from cases both engines evaluated; a refusal is not a decision
    and is counted separately instead of being mistaken for one.
    """

    matrix = {
        p: {c: 0 for c in DECISIONS} for p in DECISIONS
    }
    for record in table:
        for pair in record["agreement"]["pairs"]:
            if pair["placement"] in matrix and pair["criteria"] in matrix[pair["placement"]]:
                matrix[pair["placement"]][pair["criteria"]] += 1
    refusals = {
        "placement_refused_criteria_evaluated": sum(
            1 for r in table if r["agreement"]["verdict"] == "placement_refused"
        ),
        "criteria_refused_placement_solved": sum(
            1 for r in table if r["agreement"]["verdict"] == "criteria_refused"
        ),
        "neither_engine_evaluated": sum(
            1 for r in table if r["agreement"]["verdict"] == "neither_engine_evaluated"
        ),
    }
    agree = sum(1 for r in table if r["agreement"]["verdict"] == "agree")
    disagree = sum(1 for r in table if r["agreement"]["verdict"] == "disagree")
    return {
        "decision_pairs": matrix,
        "agree_cases": agree,
        "disagree_cases": disagree,
        "refusals": refusals,
        "profile_pairs_agree": sum(1 for r in table for p in r["agreement"]["pairs"] if p["agree"]),
        "profile_pairs_disagree": sum(1 for r in table for p in r["agreement"]["pairs"] if not p["agree"]),
    }


def has_errors(table: list[dict[str, Any]]) -> bool:
    return any(r["placement"]["status"] == "error" or r["criteria"]["status"] == "error" for r in table)


def build_output(table: list[dict[str, Any]], repo_root: Path) -> dict[str, Any]:
    errors = []
    for record in table:
        for engine in ("placement", "criteria"):
            if record[engine]["status"] == "error":
                errors.append(
                    {
                        "id": record["id"],
                        "manifest": record["manifest"],
                        "engine": engine,
                        "exception": record[engine].get("exception"),
                        "message": record[engine].get("message"),
                    }
                )
    return {
        "schema_version": SCHEMA_VERSION,
        "base_sha": _git_head(repo_root),
        "engines": {
            "placement": "backend/server/placement: scene_bridge.convert_scene + top-level solver via adapter.load_solver",
            "criteria": "backend/server/criteria: run.evaluate_scene (profiles A,B,C,S)",
            "criteria_config": CRITERIA_CONFIG_ECHO,
            "rules_sha256": _sha256((repo_root / "server" / "rules.yaml").read_bytes())
            if (repo_root / "server" / "rules.yaml").is_file()
            else None,
            "profiles_sha256": _sha256((BACKEND / "server" / "criteria" / "profiles.yaml").read_bytes()),
        },
        "cases": table,
        "summary": {"confusion": _confusion(table), "errors": errors},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", choices=DATA_DIRS, help="restrict to one committed manifest")
    parser.add_argument("--case", action="append", dest="case_ids", help="restrict to these case ids")
    parser.add_argument("--out", type=Path, help="write the JSON table here (default: stdout)")
    parser.add_argument("--work-dir", type=Path, help="directory for engine scratch (default: a temp dir)")
    args = parser.parse_args(argv)

    dirs = (args.manifest,) if args.manifest else DATA_DIRS
    cases = load_cases(dirs)
    if args.case_ids:
        cases = [c for c in cases if c["id"] in args.case_ids]
        missing = set(args.case_ids) - {c["id"] for c in cases}
        if missing:
            print(f"unknown case ids: {sorted(missing)}", file=sys.stderr)
            return 2

    work_dir = None
    if args.work_dir is not None:
        args.work_dir.mkdir(parents=True, exist_ok=True)
        work_dir = args.work_dir
    table = collect_results(cases, work_dir=work_dir)
    output = build_output(table, REPO_ROOT)

    text = json.dumps(output, indent=1, sort_keys=True, allow_nan=False)
    if args.out is not None:
        args.out.write_text(text + "\n")
    else:
        print(text)

    confusion = output["summary"]["confusion"]
    print(
        f"{len(table)} cases: {confusion['agree_cases']} agree, {confusion['disagree_cases']} disagree, "
        f"{confusion['refusals']['placement_refused_criteria_evaluated']} placement-refused, "
        f"{confusion['refusals']['criteria_refused_placement_solved']} criteria-refused, "
        f"{confusion['refusals']['neither_engine_evaluated']} neither evaluated",
        file=sys.stderr,
    )
    if has_errors(table):
        for error in output["summary"]["errors"]:
            print(f"ENGINE ERROR {error['id']} ({error['manifest']}): {error['engine']}: {error['message']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
