"""Single-command evaluation suite for the committed fixtures.

    uv run --frozen python -m server.eval.suite \
        [--config server/eval/gates.yaml] [--inputs data/fixtures] [--out runs/suite/latest]

Runs the real criteria engine (``server.criteria`` through the harness adapter) on
each committed ETH3D ground-truth scene, then scores what it produced — and the
committed recon scenes — against the fixtures' ground truth and expected labels:

- ``pipeline``:  the engine's verdicts and criteria report for profiles
                 A/B/C/S/public with the ground rule ``make criteria-golden``
                 documents (footprint3in, C2 on, C3 off). A stage that cannot run
                 is FAIL, never SKIP.
- ``criteria``:  the produced report and verdict decisions must reproduce the
                 committed evidence under ``data/fixtures/criteria`` and
                 ``verdicts/`` (that evidence is sha-pinned in gates.yaml).
- ``verdicts``:  produced verdicts scored against the committed labels. The only
                 bars are the ones gates.yaml already documents.
- ``geometry``:  committed recon scenes geomscored against the GT laser scene.
                 Equal numbers to the committed reference scores pass; the suite
                 invents no tolerance beyond the reference's published precision.

Writes ``suite.json`` (machine-readable, ``hss-eval-suite/v0``) and a short
``SUMMARY.md`` under ``--out``. Exit 1 when any row fails.

The laser and occluder PLYs are gitignored; ``make gt`` builds them into
``.fixtures/gt/<site>/gt-laser``. Without them the visibility oracle cannot run
and the pipeline rows FAIL with that hint — a missing dataset is a failure of
this machine, not a pass.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import yaml

from server.eval.adapters.criteria import CriteriaConfig, evaluate_scene
from server.eval.coverage import MissingOccluderError
from server.eval.gates import _false_rejections
from server.eval.geomscore import score_scene
from server.eval.sceneio import load_json, validate
from server.eval.verdictscore import score_files

FORMAT = "hss-eval-suite/v0"
PROFILES = ("A", "B", "C", "S", "public")
GROUND_RULE = "footprint3in"
# Criteria-report fields the produced run must reproduce exactly. ``degraded`` is
# reported in the note instead: it records machine-local input conditions.
REPORT_FIELDS = ("profiles", "decidability", "c2", "c3", "downgrades", "ground_rule", "scale", "provisional")


def _row(row_id: str, group: str, target: str, metric: str, value, threshold, status: str, evidence=None, note=""):
    return {
        "id": row_id,
        "group": group,
        "target": target,
        "metric": metric,
        "value": value,
        "threshold": threshold,
        "status": status,
        "evidence_path": evidence,
        "note": note,
    }


def _err(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text[:300]


def _git_sha(repo: Path) -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, document) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, default=float) + "\n")


def _engine_config(cfg: dict) -> CriteriaConfig:
    """The documented criteria-golden configuration; gates.yaml may override C2."""
    suite_cfg = cfg.get("suite") or {}
    criteria = suite_cfg.get("criteria") or {}
    return CriteriaConfig(
        profiles=tuple(criteria.get("profiles") or PROFILES),
        ground_rule=criteria.get("ground_rule") or GROUND_RULE,
        c2=bool(criteria.get("c2", True)),
        c3=False,
    )


def _stage(site_dir: Path, site: str, repo: Path, work: Path) -> Path:
    """Committed scene plus, when they exist, the gitignored laser/occluder PLYs.

    Cached PLYs (``make gt`` → ``.fixtures/gt/<site>/gt-laser``) are symlinked in;
    a scene dir that carries its own PLYs (the synthetic test scenes) is copied.
    """
    dest = work / site / "gt-laser"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(site_dir / "scene.json", dest / "scene.json")
    if (site_dir / "coverage.npz").is_file():
        shutil.copy2(site_dir / "coverage.npz", dest / "coverage.npz")
    for name in ("laser.ply", "occluders.ply"):
        cached = repo / ".fixtures" / "gt" / site / "gt-laser" / name
        if cached.is_file():
            target = dest / name
            if not target.exists():
                target.symlink_to(cached.resolve())
        elif (site_dir / name).is_file():
            shutil.copy2(site_dir / name, dest / name)
    return dest


def _has_visibility_inputs(staged: Path) -> bool:
    return (staged / "laser.ply").is_file() and (staged / "occluders.ply").is_file()


def _pipeline_rows(sites, inputs: Path, repo: Path, work: Path, engine_cfg: CriteriaConfig):
    """Run the real criteria engine per site. Broken stage = FAIL row, never SKIP."""
    rows = []
    for site in sites:
        site_dir = inputs / "scenes" / site / "gt-laser"
        target = f"{site}/gt-laser"
        out_dir = work / site / "criteria"
        try:
            staged = _stage(site_dir, site, repo, work)
        except Exception as exc:
            traceback.print_exc()
            rows.append(
                _row(
                    f"pipeline/{site}",
                    "pipeline",
                    target,
                    "criteria_engine",
                    None,
                    f"verdicts for {', '.join(engine_cfg.profiles)}",
                    "FAIL",
                    str(site_dir),
                    f"scene fixture missing: {_err(exc)}",
                )
            )
            continue
        hint = "" if _has_visibility_inputs(staged) else " (laser PLY absent; run make gt)"
        try:
            produced = evaluate_scene(staged, out_dir, config=engine_cfg)
            for profile, verdict in produced.items():
                _write_json(out_dir / f"{profile}.json", verdict)
        except Exception as exc:
            traceback.print_exc()
            rows.append(
                _row(
                    f"pipeline/{site}",
                    "pipeline",
                    target,
                    "criteria_engine",
                    None,
                    f"verdicts for {', '.join(engine_cfg.profiles)}",
                    "FAIL",
                    str(out_dir),
                    f"criteria engine failed: {_err(exc)}{hint}",
                )
            )
            continue
        missing = [p for p in engine_cfg.profiles if p not in produced]
        decisions = {p: v.get("site", {}).get("decision") for p, v in sorted(produced.items())}
        status = "PASS" if not missing else "FAIL"
        rows.append(
            _row(
                f"pipeline/{site}",
                "pipeline",
                target,
                "criteria_engine",
                decisions,
                f"verdicts for {', '.join(engine_cfg.profiles)}",
                status,
                str(out_dir / "criteria_report.json"),
                f"ground rule {engine_cfg.ground_rule}, c2={engine_cfg.c2}, c3 off"
                + (f"; missing profiles {missing}" if missing else ""),
            )
        )
    return rows


def _report_diff(produced: dict, committed: dict) -> list[str]:
    diffs = []
    for field in REPORT_FIELDS:
        if produced.get(field) != committed.get(field):
            diffs.append(f"{field}: produced {produced.get(field)!r} vs committed {committed.get(field)!r}")
    return diffs


def _criteria_rows(sites, inputs: Path, work: Path):
    """Produced reports and decisions vs the committed evidence the repo pins."""
    rows = []
    for site in sites:
        report_path = work / site / "criteria" / "criteria_report.json"
        if not report_path.is_file():
            rows.append(
                _row(
                    f"criteria/{site}/report",
                    "criteria",
                    f"{site}/gt-laser",
                    "criteria_report",
                    None,
                    "reproduces committed evidence",
                    "FAIL",
                    None,
                    "the pipeline stage produced no criteria report",
                )
            )
            continue
        try:
            produced = json.loads(report_path.read_text())
        except ValueError as exc:
            rows.append(
                _row(
                    f"criteria/{site}/report",
                    "criteria",
                    f"{site}/gt-laser",
                    "criteria_report",
                    None,
                    "reproduces committed evidence",
                    "FAIL",
                    str(report_path),
                    f"produced criteria report is unreadable: {_err(exc)}",
                )
            )
            continue
        shares = {
            p: (produced.get("decidability") or {}).get(p, {}).get("share") for p in produced.get("profiles") or []
        }
        committed_path = inputs / "criteria" / site / "gt-laser" / "criteria_report.json"
        if not committed_path.is_file():
            rows.append(
                _row(
                    f"criteria/{site}/report",
                    "criteria",
                    f"{site}/gt-laser",
                    "criteria_report",
                    shares,
                    "no committed reference",
                    "MEASURED",
                    str(report_path),
                    "no committed criteria_report for this site; shares recorded, no documented bar",
                )
            )
            continue
        try:
            committed = json.loads(committed_path.read_text())
        except ValueError as exc:
            rows.append(
                _row(
                    f"criteria/{site}/report",
                    "criteria",
                    f"{site}/gt-laser",
                    "criteria_report",
                    shares,
                    "reproduces committed evidence",
                    "FAIL",
                    str(committed_path),
                    f"committed criteria report is unreadable: {_err(exc)}",
                )
            )
            continue
        diffs = _report_diff(produced, committed)
        rows.append(
            _row(
                f"criteria/{site}/report",
                "criteria",
                f"{site}/gt-laser",
                "criteria_report",
                shares,
                "reproduces committed evidence",
                "PASS" if not diffs else "FAIL",
                str(committed_path),
                "; ".join(diffs) if diffs else f"degraded={produced.get('degraded')!r}",
            )
        )
    # The committed reference verdicts for facade were produced by this engine;
    # the run must reach the same per-profile site decisions.
    for site, folder in (("eth3d-facade", "c1-3d"),):
        ref_dir = inputs / "verdicts" / site / folder
        if not ref_dir.is_dir():
            continue
        diffs = []
        decisions = {}
        for profile in PROFILES:
            produced_path = work / site / "criteria" / f"{profile}.json"
            ref_path = ref_dir / f"{profile}.json"
            if not produced_path.is_file() or not ref_path.is_file():
                diffs.append(f"{profile}: missing produced or reference verdict")
                continue
            try:
                got = load_json(produced_path).get("site", {}).get("decision")
                want = load_json(ref_path).get("site", {}).get("decision")
            except Exception as exc:
                diffs.append(f"{profile}: unreadable produced or reference verdict: {_err(exc)}")
                continue
            decisions[profile] = got
            if got != want:
                diffs.append(f"{profile}: produced {got!r} vs reference {want!r}")
        rows.append(
            _row(
                f"criteria/{site}/verdict_decisions",
                "criteria",
                f"{site}/{folder}",
                "site.decision",
                decisions,
                "matches committed reference verdicts",
                "PASS" if not diffs else "FAIL",
                str(ref_dir),
                "; ".join(diffs) if diffs else f"all of {', '.join(PROFILES)} agree",
            )
        )
    return rows


def _verdict_rows(sites, inputs: Path, work: Path, limits: dict):
    """Produced verdicts against the committed labels, under gates.yaml's bars."""
    rows = []
    unsafe_max = limits.get("unsafe_passes_max", 0)
    frej_max = limits.get("false_rejections_max", 0)
    contract_max = limits.get("coverage_contract_errors_max", 0)
    c5_max = limits.get("c5_margin_errors_max", 0)
    agree_min = limits.get("site_agreement_min")
    for site in sites:
        for profile in PROFILES:
            target = f"{site}/{profile}"
            verdict_path = work / site / "criteria" / f"{profile}.json"
            labels_path = inputs / "labels" / f"{site}.{profile}.json"
            if not labels_path.is_file():
                rows.append(
                    _row(
                        f"verdict/{target}",
                        "verdicts",
                        target,
                        "verdictscore",
                        None,
                        None,
                        "MEASURED",
                        None,
                        "no committed labels for this profile",
                    )
                )
                continue
            if not verdict_path.is_file():
                rows.append(
                    _row(
                        f"verdict/{target}",
                        "verdicts",
                        target,
                        "verdictscore",
                        None,
                        "scored against committed labels",
                        "FAIL",
                        str(labels_path),
                        "the pipeline stage produced no verdict for this profile",
                    )
                )
                continue
            try:
                results = score_files(verdict_path, [labels_path], out_dir=work / "verdict-scores" / site)
            except Exception as exc:
                traceback.print_exc()
                rows.append(
                    _row(
                        f"verdict/{target}",
                        "verdicts",
                        target,
                        "verdictscore",
                        None,
                        "scored against committed labels",
                        "FAIL",
                        str(labels_path),
                        f"scoring failed: {_err(exc)}",
                    )
                )
                continue
            if not results:
                rows.append(
                    _row(
                        f"verdict/{target}",
                        "verdicts",
                        target,
                        "verdictscore",
                        None,
                        "scored against committed labels",
                        "FAIL",
                        str(labels_path),
                        "the scorer produced no result for this labels file",
                    )
                )
                continue
            r = results[0]
            headline = r["headline"]
            unsafe = headline["unsafe_passes"]
            frej = _false_rejections(r)
            contract = len(r["coverage_contract_errors"])
            c5 = len(r["c5_margin_errors"])
            agreement = r["site"]["agreement"]
            value = {
                "unsafe_passes": unsafe,
                "false_rejections": frej,
                "coverage_contract_errors": contract,
                "c5_margin_errors": c5,
                "site_agreement": agreement,
                "pass_recall": headline.get("pass_recall"),
                "manual_review_rate": r["start_range"]["manual_review_rate"],
            }
            failures = []
            if unsafe > unsafe_max:
                failures.append(f"unsafe_passes {unsafe} > {unsafe_max}")
            if isinstance(frej, int) and frej > frej_max:
                failures.append(f"false_rejections {frej} > {frej_max}")
            if contract > contract_max:
                failures.append(f"coverage_contract_errors {contract} > {contract_max}")
            if c5 > c5_max:
                failures.append(f"c5_margin_errors {c5} > {c5_max}")
            if agreement is None:
                agreement_note = "no scorable site labels"
            elif agree_min is not None and agreement < agree_min:
                failures.append(f"site_agreement {agreement} < {agree_min}")
                agreement_note = ""
            else:
                agreement_note = ""
            rows.append(
                _row(
                    f"verdict/{target}",
                    "verdicts",
                    target,
                    "verdictscore",
                    value,
                    f"unsafe<={unsafe_max}, false_rej<={frej_max}, contract<={contract_max}, "
                    f"c5<={c5_max}, site_agreement>={agree_min}",
                    "FAIL" if failures else "PASS",
                    str(labels_path),
                    "; ".join(failures) or (agreement_note or "within the documented bars"),
                )
            )
    return rows


def _reference(inputs: Path, site: str, recon: str) -> dict | None:
    """Committed reference numbers for a recon scene, preferring the visibility=True remeasure."""
    ref_dir = inputs / "reference-scores" / site
    if not ref_dir.is_dir():
        return None
    best = None
    for path in sorted(ref_dir.glob("*.json")):
        try:
            document = json.loads(path.read_text())
        except ValueError:
            continue
        if path.name.startswith("geomscore-"):
            if str(document.get("recon_scene_id", "")).split("/")[-1] == recon and best is None:
                best = {
                    "overall": (document.get("key_distances") or {}).get("overall"),
                    "by_wall_p90_in": None,
                    "scale_error_pct": (document.get("scale") or {}).get("scale_error_pct"),
                    "visibility": None,
                    "source": path.name,
                }
            continue
        visibility = "visibility=True" in str(document.get("method", ""))
        for row in document.get("rows") or []:
            if row.get("recon") != recon:
                continue
            candidate = {
                "overall": row.get("overall"),
                "by_wall_p90_in": (row.get("by_wall") or {}).get("w1", {}).get("p90_in"),
                "scale_error_pct": row.get("scale_error_pct"),
                "visibility": visibility,
                "source": path.name,
            }
            if best is None or (visibility and not best.get("visibility")):
                best = candidate
    return best


def _matches_reference(measured: dict, reference: dict) -> tuple[bool, list[str]]:
    """Equal numbers at the reference's published precision. No new tolerance."""
    diffs = []
    ref_overall = reference.get("overall") or {}
    got_overall = measured.get("overall") or {}
    for key in ("median_in", "p90_in", "max_in"):
        want, got = ref_overall.get(key), got_overall.get(key)
        if want is not None and (got is None or round(float(got), 2) != round(float(want), 2)):
            diffs.append(f"overall.{key}: {got} vs reference {want}")
    want_scale = reference.get("scale_error_pct")
    got_scale = (measured.get("scale") or {}).get("scale_error_pct")
    if want_scale is not None and (got_scale is None or round(float(got_scale), 3) != round(float(want_scale), 3)):
        diffs.append(f"scale_error_pct: {got_scale} vs reference {want_scale}")
    want_p90 = reference.get("by_wall_p90_in")
    got_p90 = ((measured.get("by_wall") or {}).get("w1") or {}).get("p90_in")
    if want_p90 is not None and (got_p90 is None or round(float(got_p90), 2) != round(float(want_p90), 2)):
        diffs.append(f"by_wall.w1.p90_in: {got_p90} vs reference {want_p90}")
    return not diffs, diffs


def _geometry_rows(sites, inputs: Path, repo: Path, work: Path):
    """Geomscore the committed recon scenes against the GT laser scene.

    Reference numbers are the committed reference scores; with the PLYs present
    the run also checks by-wall p90 and that no false-observed claims survive
    (documented in reference-scores/rebaseline-55e7f5).
    """
    rows = []
    for site in sites:
        scenes = inputs / "scenes" / site
        gt_committed = scenes / "gt-laser"
        if not (gt_committed / "scene.json").is_file():
            continue
        gt_staged = _stage(gt_committed, site, repo, work)
        visibility = _has_visibility_inputs(gt_staged)
        recon_dirs = sorted(
            d for d in scenes.iterdir() if d.is_dir() and d.name != "gt-laser" and (d / "scene.json").is_file()
        )
        for recon_dir in recon_dirs:
            target = f"{site}/{recon_dir.name}"
            out_dir = work / "geometry" / site / recon_dir.name
            try:
                score = score_scene(gt_staged / "scene.json", recon_dir / "scene.json", out_dir, visibility=visibility)
            except MissingOccluderError as exc:
                traceback.print_exc()
                rows.append(
                    _row(
                        f"geometry/{target}",
                        "geometry",
                        target,
                        "geomscore",
                        None,
                        "matches committed reference scores",
                        "FAIL",
                        None,
                        f"recon asset missing: {_err(exc)} — the recon PLYs are gitignored Modal-pipeline "
                        "outputs (uv run modal run scripts/modal_scene.py); restore or produce them before "
                        "this row can score",
                    )
                )
                continue
            except Exception as exc:
                traceback.print_exc()
                rows.append(
                    _row(
                        f"geometry/{target}",
                        "geometry",
                        target,
                        "geomscore",
                        None,
                        "matches committed reference scores",
                        "FAIL",
                        None,
                        f"scoring failed: {_err(exc)}" + ("" if visibility else " (laser PLY absent; run make gt)"),
                    )
                )
                continue
            kd = score.get("key_distances") or {}
            measured = {
                "overall": kd.get("overall"),
                "by_wall": kd.get("by_wall"),
                "scale": {k: v for k, v in (score.get("scale") or {}).items() if k != "alignment"},
                "false_observed_claims": kd.get("false_observed_claims"),
            }
            reference = _reference(inputs, site, recon_dir.name)
            if reference is None:
                rows.append(
                    _row(
                        f"geometry/{target}",
                        "geometry",
                        target,
                        "geomscore",
                        measured,
                        "no committed reference",
                        "MEASURED",
                        str(out_dir / "geomscore.json"),
                        f"visibility={visibility}; no committed reference score for this recon",
                    )
                )
                continue
            ok, diffs = _matches_reference(measured, reference)
            if visibility:
                claims = kd.get("false_observed_claims")
                if claims:
                    ok = False
                    diffs.append(f"false_observed_claims {claims} vs documented empty")
            else:
                diffs.append("visibility off (laser PLY absent): by-wall and false-observed checks skipped")
            rows.append(
                _row(
                    f"geometry/{target}",
                    "geometry",
                    target,
                    "geomscore",
                    measured,
                    f"equals {reference['source']} at its published precision",
                    "PASS" if ok else "FAIL",
                    str(out_dir / "geomscore.json"),
                    "; ".join(diffs) or f"visibility={visibility}; matches the committed reference",
                )
            )
    return rows


def _fixture_rows(sites, inputs: Path):
    rows = []
    for site in sites:
        scene_path = inputs / "scenes" / site / "gt-laser" / "scene.json"
        try:
            validate(load_json(scene_path))
        except Exception as exc:
            rows.append(
                _row(
                    f"fixtures/{site}",
                    "fixtures",
                    f"{site}/gt-laser",
                    "scene_v0",
                    None,
                    "schema-valid",
                    "FAIL",
                    str(scene_path),
                    _err(exc),
                )
            )
            continue
        labels = sum(1 for profile in PROFILES if (inputs / "labels" / f"{site}.{profile}.json").is_file())
        rows.append(
            _row(
                f"fixtures/{site}",
                "fixtures",
                f"{site}/gt-laser",
                "scene_v0",
                {"labels_present": labels},
                "schema-valid",
                "PASS",
                str(scene_path),
                f"{labels}/{len(PROFILES)} profiles have committed labels",
            )
        )
    return rows


def run_suite(config_path: Path, inputs: Path, out: Path, repo: Path | None = None) -> dict:
    """Run every stage and write suite.json + SUMMARY.md under ``out``."""
    repo = repo or Path(__file__).resolve().parents[2]
    config_path, inputs, out = Path(config_path), Path(inputs), Path(out)
    cfg = yaml.safe_load(config_path.read_text()) or {}
    # Every configured site stays in the run: a site whose scene fixture is missing
    # is a FAIL row, never a silent omission — an empty or wrong --inputs directory
    # must not exit 0 having evaluated nothing.
    sites = list(cfg.get("sites") or {})
    # Each run reads only what it produced: verdicts and reports left in the work
    # directory by an earlier run would otherwise stand in for this run's results.
    work = out / "work"
    if work.exists():
        shutil.rmtree(work)
    engine_cfg = _engine_config(cfg)
    rows: list[dict] = []
    rows.extend(_fixture_rows(sites, inputs))
    rows.extend(_pipeline_rows(sites, inputs, repo, work, engine_cfg))
    rows.extend(_criteria_rows(sites, inputs, work))
    rows.extend(_verdict_rows(sites, inputs, work, cfg.get("verdicts") or {}))
    rows.extend(_geometry_rows(sites, inputs, repo, work))
    counts = {}
    for status in ("PASS", "FAIL", "WARN", "MEASURED"):
        counts[status.lower()] = sum(r["status"] == status for r in rows)
    report = {
        "format": FORMAT,
        "stamp": _stamp(),
        "git_sha": _git_sha(repo),
        "config": str(config_path),
        "inputs": str(inputs),
        "sites": sites,
        "engine": {
            "ground_rule": engine_cfg.ground_rule,
            "c2": engine_cfg.c2,
            "c3": False,
            "profiles": list(engine_cfg.profiles),
        },
        "rows": rows,
        "summary": counts,
    }
    _write_json(out / "suite.json", report)
    (out / "SUMMARY.md").write_text(_markdown(report))
    return report


def _markdown(report: dict) -> str:
    lines = [
        f"# Eval suite {report['stamp']}",
        "",
        f"git `{report['git_sha']}` — engine: ground rule `{report['engine']['ground_rule']}`, "
        f"C2 {'on' if report['engine']['c2'] else 'off'}, C3 off, profiles {'/'.join(report['engine']['profiles'])}.",
        "",
        f"**pass {report['summary']['pass']} · fail {report['summary']['fail']} · "
        f"warn {report['summary']['warn']} · measured {report['summary']['measured']}** — "
        "bars are the ones gates.yaml documents; geometry passes by equaling the committed reference scores.",
        "",
    ]
    groups: dict[str, list[dict]] = {}
    for row in report["rows"]:
        groups.setdefault(row["group"], []).append(row)
    for group, group_rows in groups.items():
        lines += [f"## {group}", "", "| id | value | status | note |", "|---|---|---|---|"]
        for row in group_rows:
            value = json.dumps(row.get("value"), default=float)
            if len(value) > 90:
                value = value[:87] + "..."
            note = (row.get("note") or "").replace("|", "/")
            if len(note) > 140:
                note = note[:137] + "..."
            lines.append(f"| {row['id']} | {value.replace('|', '/')} | {row['status']} | {note} |")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="server.eval.suite", description=__doc__)
    ap.add_argument("--config", type=Path, default=Path("server/eval/gates.yaml"))
    ap.add_argument("--inputs", type=Path, default=Path("data/fixtures"))
    ap.add_argument("--out", type=Path, default=Path("runs/suite/latest"))
    args = ap.parse_args(argv)
    report = run_suite(args.config, args.inputs, args.out)
    sys.stdout.write(_markdown(report))
    return 1 if report["summary"]["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
