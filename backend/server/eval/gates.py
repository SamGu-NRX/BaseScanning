"""Regression gates. Every threshold is read from gates.yaml.

A missing input is SKIP, never a fabricated PASS. ``--strict`` turns SKIP into a failing exit.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

from server.eval.report import GateReport, markdown, write
from server.eval.sceneio import verdict_contract_errors
from server.eval.verdictscore import score as score_verdict

# The brief's window is rounded to 0.001 m. The re-derived endpoints are
# -3.891369… and -3.383268…, so overlap uses a 1 mm tolerance.
SPAN_TOL_M = 0.001
_ORDER = {"fail": 0, "unsure": 1, "pass": 2}
_REPO = Path(__file__).resolve().parents[2]


def evaluate(
    config: Path, inputs: Path, *, repo: Path | None = None, allow_missing_occluders: bool = False
) -> GateReport:
    cfg = yaml.safe_load(Path(config).read_text())
    root = Path(inputs)
    repo = repo or _REPO
    rows: list[dict] = []
    if allow_missing_occluders:
        rows.append(
            _row(
                "provisional/allow-missing-occluders",
                "safety",
                "occluders",
                "provisional",
                True,
                False,
                "FAIL",
                None,
                "provisional scoring is not a result a gate can accept",
            )
        )
    rows.extend(_exclusion_rows(cfg))
    rows.extend(_geometry_rows(cfg, root))
    rows.extend(_known_warning_rows(cfg, root))
    rows.extend(_verdict_rows(cfg, root, repo))
    rows.extend(_positive_rows(cfg, root, repo))
    rows.extend(_decidability_rows(cfg, root))
    rows.extend(_safety_rows(cfg, root))
    rows.extend(_lamar_rows(cfg, root))
    rows.extend(_runtime_rows(cfg, root))
    summary = {
        name: sum(r["status"] == name.upper() for r in rows) for name in ("pass", "fail", "warn", "skip", "known_fail")
    }
    return GateReport(stamp=_stamp(), git_sha=_git_sha(repo), rows=rows, summary=summary)


def exit_code(report: GateReport, *, strict: bool = False) -> int:
    if report.summary.get("fail"):
        return 1
    if strict and report.summary.get("skip"):
        return 1
    return 0


def _exclusion_rows(cfg: dict) -> list[dict]:
    banned = set(cfg.get("placement_excluded_sites") or {})
    rows = []
    for packet in cfg.get("e2e_packets") or []:
        site = str(packet).split(":", 1)[0]
        if site in banned:
            rows.append(
                _row(
                    f"exclusion/{packet}",
                    "policy",
                    packet,
                    "e2e_packets",
                    site,
                    "absent",
                    "FAIL",
                    None,
                    cfg["placement_excluded_sites"][site],
                )
            )
    return rows


def _geometry_rows(cfg: dict, inputs: Path) -> list[dict]:
    rows = []
    for key, limits in (cfg.get("geometry") or {}).items():
        if key == "harness_sha" or not isinstance(limits, dict):
            continue
        site, _, variant = key.partition("/")
        evidence = _first(
            inputs / "geometry" / site / variant / "geomscore.json",
            inputs / "geomscore" / f"{site}__{variant}.json",
        )
        target = key
        if evidence is None:
            rows.append(
                _row(
                    f"geometry/{key}",
                    "geometry",
                    target,
                    "geomscore",
                    None,
                    "present",
                    "SKIP",
                    None,
                    "no geomscore for this packet on this machine",
                )
            )
            continue
        score = json.loads(evidence.read_text())
        if score.get("provisional") is True:
            rows.append(
                _row(
                    f"geometry/{key}/provisional",
                    "geometry",
                    target,
                    "provisional",
                    True,
                    False,
                    "FAIL",
                    str(evidence),
                    "provisional scoring is not a result a gate can accept",
                )
            )
        kd = score.get("key_distances") or {}
        if "by_wall.w1.p90_in_max" in limits:
            p90 = ((kd.get("by_wall") or {}).get("w1") or {}).get("p90_in")
            rows.append(
                _cmp(
                    f"geometry/{key}/w1.p90_in",
                    "geometry",
                    target,
                    "by_wall.w1.p90_in",
                    p90,
                    limits["by_wall.w1.p90_in_max"],
                    evidence,
                    at_most=True,
                )
            )
        if "on_truly_observed_elements.p90_in_max" in limits:
            p90 = (kd.get("on_truly_observed_elements") or {}).get("p90_in")
            rows.append(
                _cmp(
                    f"geometry/{key}/observed.p90_in",
                    "geometry",
                    target,
                    "on_truly_observed_elements.p90_in",
                    p90,
                    limits["on_truly_observed_elements.p90_in_max"],
                    evidence,
                    at_most=True,
                )
            )
        if "false_observed_claims_max" in limits:
            claims = kd.get("false_observed_claims")
            if claims is None:
                rows.append(
                    _row(
                        f"geometry/{key}/false_observed",
                        "geometry",
                        target,
                        "false_observed_claims",
                        None,
                        limits["false_observed_claims_max"],
                        "SKIP",
                        str(evidence),
                        "visibility oracle did not run",
                    )
                )
            else:
                rows.append(
                    _cmp(
                        f"geometry/{key}/false_observed",
                        "geometry",
                        target,
                        "false_observed_claims",
                        len(claims),
                        limits["false_observed_claims_max"],
                        evidence,
                        at_most=True,
                    )
                )
        if "scale_error_pct_abs_max" in limits:
            scale = (score.get("scale") or {}).get("scale_error_pct")
            if scale is None:
                rows.append(
                    _row(
                        f"geometry/{key}/scale",
                        "geometry",
                        target,
                        "abs(scale_error_pct)",
                        None,
                        limits["scale_error_pct_abs_max"],
                        "SKIP",
                        str(evidence),
                        "scale missing",
                    )
                )
            else:
                rows.append(
                    _cmp(
                        f"geometry/{key}/scale",
                        "geometry",
                        target,
                        "abs(scale_error_pct)",
                        abs(scale),
                        limits["scale_error_pct_abs_max"],
                        evidence,
                        at_most=True,
                    )
                )
    return rows


def _verdict_file(inputs: Path, site: str, profile: str) -> Path | None:
    """A verdict copied under ``--inputs``. The committed fixture is found when inputs is ``data/fixtures``."""
    return _first(
        inputs / "verdicts" / site / "c1-3d" / f"{profile}.json",
        inputs / site / "c1-3d" / f"{profile}.json",
    )


def _labels_file(inputs: Path, site: str, profile: str) -> Path | None:
    return _first(
        inputs / "labels" / f"{site}.{profile}.json",
        inputs / "labels" / f"{site}.{profile}.labels.json",
    )


def _profile_verdict(inputs: Path, site: str, profile: str) -> Path | None:
    """The profile verdict to score.

    ``c1-3d/<profile>.json`` wins. Otherwise the committed hs11 file
    ``gt.C1.g80.json`` when its ``rules_profile`` matches, then any other
    verdict in that site folder with the same profile.
    """
    baked = _verdict_file(inputs, site, profile)
    if baked is not None:
        return baked
    folder = inputs / "verdicts" / site
    if not folder.is_dir():
        return None
    matches: list[Path] = []
    for path in sorted(folder.glob("*.json")):
        try:
            document = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        evaluator = document.get("evaluator") if isinstance(document.get("evaluator"), dict) else {}
        if evaluator.get("rules_profile") == profile:
            matches.append(path)
    preferred = folder / "gt.C1.g80.json"
    if preferred in matches:
        return preferred
    return matches[0] if matches else None


def _candidate_observed(candidate: dict) -> bool:
    evidence = candidate.get("coverage") or []
    if isinstance(evidence, dict):
        evidence = [evidence]
    return any(isinstance(item, dict) and item.get("status") == "observed" for item in evidence)


def _evidence_backed_candidates(verdict: dict, errors: list[str]) -> set[str]:
    """Candidate ids whose ``pass`` rests on a contract-clean criterion."""
    backed: set[str] = set()
    for criterion in verdict.get("criteria") or []:
        if criterion.get("outcome") != "pass":
            continue
        where = f"criteria[{criterion.get('id')}"
        if any(str(err).startswith(where) for err in errors):
            continue
        candidate_id = criterion.get("candidate_id")
        if candidate_id:
            backed.add(candidate_id)
    return backed


def _recomputed_unsafe(verdict: dict) -> int:
    """Passes that the coverage contract does not support.

    A candidate ``pass`` backed by an observed criterion (or by its own observed
    ``coverage``) is not unsafe. Flipping an ``unsure`` candidate to ``pass``
    with no such criterion still counts. A stored score file is not trusted.
    """
    errors = verdict_contract_errors(verdict) if verdict.get("criteria") else []
    backed = _evidence_backed_candidates(verdict, errors)
    unsafe = sum(1 for err in errors if "pass" in err)
    for candidate in verdict.get("candidates") or []:
        if candidate.get("outcome") != "pass":
            continue
        if candidate.get("id") in backed or _candidate_observed(candidate):
            continue
        unsafe += 1
    site = verdict.get("site") if isinstance(verdict.get("site"), dict) else {}
    contract = verdict.get("coverage_contract") if isinstance(verdict.get("coverage_contract"), dict) else None
    if site.get("decision") == "pass" and unsafe == 0:
        if errors or (contract is not None and contract.get("satisfied") is not True):
            unsafe = 1
    return unsafe


def _rescored_rows(
    limits: dict,
    target: str,
    verdict: dict,
    verdict_path: Path,
    score_path: Path | None,
    stored_doc: dict | None = None,
) -> list[dict]:
    """Recompute unsafe passes from the verdict and compare them to a label score."""
    recomputed = _recomputed_unsafe(verdict)
    rows = []
    stored = None
    if stored_doc is None and score_path is not None:
        stored_doc = json.loads(score_path.read_text())
    if stored_doc is not None:
        stored = (stored_doc.get("headline") or {}).get("unsafe_passes")
    embedded = ((verdict.get("score") or {}).get("headline") or {}).get("unsafe_passes")
    compare = stored if stored is not None else embedded
    if compare is not None and int(compare) != int(recomputed):
        rows.append(
            _row(
                f"verdict/{target}/score_mismatch",
                "verdicts",
                target,
                "unsafe_passes",
                recomputed,
                int(compare),
                "FAIL",
                str(verdict_path),
                "recomputed unsafe_passes does not match the stored score",
            )
        )
    rows.append(
        _cmp(
            f"verdict/{target}/unsafe_passes",
            "verdicts",
            target,
            "unsafe_passes",
            recomputed,
            limits.get("unsafe_passes_max", 0),
            verdict_path,
            at_most=True,
        )
    )
    if stored_doc is None:
        for metric in ("false_rejections", "coverage_contract_errors", "c5_margin_errors", "pass_recall"):
            rows.append(
                _row(
                    f"verdict/{target}/{metric}",
                    "verdicts",
                    target,
                    metric,
                    None,
                    limits.get(f"{metric}_max", 0),
                    "SKIP",
                    str(verdict_path),
                    "recomputed from the verdict; no labels file to score the other metrics",
                )
            )
        return rows
    headline = stored_doc.get("headline") or {}
    false_rejections = _false_rejections(stored_doc)
    rows.append(
        _cmp(
            f"verdict/{target}/false_rejections",
            "verdicts",
            target,
            "false_rejections",
            false_rejections,
            limits.get("false_rejections_max", 0),
            score_path,
            at_most=True,
        )
    )
    rows.append(
        _cmp(
            f"verdict/{target}/coverage_contract",
            "verdicts",
            target,
            "coverage_contract_errors",
            len(stored_doc.get("coverage_contract_errors") or []),
            limits.get("coverage_contract_errors_max", 0),
            score_path,
            at_most=True,
        )
    )
    rows.append(
        _cmp(
            f"verdict/{target}/c5",
            "verdicts",
            target,
            "c5_margin_errors",
            len(stored_doc.get("c5_margin_errors") or []),
            limits.get("c5_margin_errors_max", 0),
            score_path,
            at_most=True,
        )
    )
    recall = headline.get("pass_recall", None)
    if "pass_recall" not in headline or recall is None:
        rows.append(
            _row(
                f"verdict/{target}/pass_recall",
                "verdicts",
                target,
                "pass_recall",
                None,
                1.0,
                "SKIP",
                str(score_path),
                "no scorable labelled pass",
            )
        )
    else:
        rows.append(
            _cmp(
                f"verdict/{target}/pass_recall",
                "verdicts",
                target,
                "pass_recall",
                recall,
                1.0,
                score_path,
                at_least=True,
            )
        )
    rows.extend(_agreement_rows(limits, target, stored_doc, score_path or verdict_path))
    return rows


def _agreement_rows(limits: dict, target: str, stored_doc: dict | None, evidence: Path) -> list[dict]:
    """Site and start-range agreement from a live label score. Thresholds stay in gates.yaml."""
    if stored_doc is None:
        return []
    rows = []
    start = (stored_doc.get("start_range") or {}).get("agreement")
    start_limits = limits.get("start_agreement_min") or {}
    if isinstance(start_limits, dict) and target in start_limits:
        rows.append(
            _cmp(
                f"verdict/{target}/start_agreement",
                "verdicts",
                target,
                "start_agreement",
                start,
                start_limits[target],
                evidence,
                at_least=True,
            )
        )
    elif start is None:
        rows.append(
            _row(
                f"verdict/{target}/start_agreement",
                "verdicts",
                target,
                "start_agreement",
                None,
                None,
                "SKIP",
                str(evidence),
                "no scorable labelled start range",
            )
        )
    else:
        rows.append(
            _row(
                f"verdict/{target}/start_agreement",
                "verdicts",
                target,
                "start_agreement",
                start,
                None,
                "SKIP",
                str(evidence),
                "no start_agreement threshold for this target",
            )
        )
    if "site_agreement_min" not in limits:
        return rows
    site_agree = (stored_doc.get("site") or {}).get("agreement")
    if site_agree is None:
        rows.append(
            _row(
                f"verdict/{target}/site_agreement",
                "verdicts",
                target,
                "site.agreement",
                None,
                limits["site_agreement_min"],
                "SKIP",
                str(evidence),
                "no scorable site labels",
            )
        )
        return rows
    rows.append(
        _cmp(
            f"verdict/{target}/site_agreement",
            "verdicts",
            target,
            "site.agreement",
            site_agree,
            limits["site_agreement_min"],
            evidence,
            at_least=True,
        )
    )
    return rows


def _verdict_rows(cfg: dict, inputs: Path, repo: Path) -> list[dict]:
    limits = cfg.get("verdicts") or {}
    rows = []
    for site, spec in (cfg.get("sites") or {}).items():
        for profile in spec.get("labels") or []:
            target = f"{site}/{profile}"
            verdict_path = _profile_verdict(inputs, site, profile)
            labels_path = _labels_file(inputs, site, profile)
            score_path = inputs / "verdict-scores" / site / f"{profile}.json"
            verdict = json.loads(verdict_path.read_text()) if verdict_path is not None else None
            if isinstance(verdict, dict) and ("candidates" in verdict or isinstance(verdict.get("site"), dict)):
                stored = None
                evidence = score_path if score_path.is_file() else None
                if evidence is None and labels_path is not None:
                    stored = score_verdict(verdict, json.loads(labels_path.read_text()))
                    evidence = labels_path
                rows.extend(_rescored_rows(limits, target, verdict, verdict_path, evidence, stored_doc=stored))
                continue
            if not score_path.is_file():
                rows.append(
                    _row(
                        f"verdict/{target}",
                        "verdicts",
                        target,
                        "score-verdicts",
                        None,
                        "present",
                        "SKIP",
                        str(labels_path) if labels_path is not None else None,
                        "no verdict for this profile"
                        if labels_path is not None
                        else "no score-verdicts JSON (labels or reference verdict missing)",
                    )
                )
                continue
            evidence = score_path
            score = json.loads(evidence.read_text())
            headline = score.get("headline") or {}
            rows.append(
                _cmp(
                    f"verdict/{target}/unsafe_passes",
                    "verdicts",
                    target,
                    "unsafe_passes",
                    headline.get("unsafe_passes"),
                    limits.get("unsafe_passes_max", 0),
                    evidence,
                    at_most=True,
                )
            )
            false_rejections = _false_rejections(score)
            rows.append(
                _cmp(
                    f"verdict/{target}/false_rejections",
                    "verdicts",
                    target,
                    "false_rejections",
                    false_rejections,
                    limits.get("false_rejections_max", 0),
                    evidence,
                    at_most=True,
                )
            )
            rows.append(
                _cmp(
                    f"verdict/{target}/coverage_contract",
                    "verdicts",
                    target,
                    "coverage_contract_errors",
                    len(score.get("coverage_contract_errors") or []),
                    limits.get("coverage_contract_errors_max", 0),
                    evidence,
                    at_most=True,
                )
            )
            rows.append(
                _cmp(
                    f"verdict/{target}/c5",
                    "verdicts",
                    target,
                    "c5_margin_errors",
                    len(score.get("c5_margin_errors") or []),
                    limits.get("c5_margin_errors_max", 0),
                    evidence,
                    at_most=True,
                )
            )
            recall = headline.get("pass_recall", None)
            if "pass_recall" not in headline or recall is None:
                rows.append(
                    _row(
                        f"verdict/{target}/pass_recall",
                        "verdicts",
                        target,
                        "pass_recall",
                        None,
                        1.0,
                        "SKIP",
                        str(evidence),
                        "no scorable labelled pass",
                    )
                )
            else:
                rows.append(
                    _cmp(
                        f"verdict/{target}/pass_recall",
                        "verdicts",
                        target,
                        "pass_recall",
                        recall,
                        1.0,
                        evidence,
                        at_least=True,
                    )
                )
    return rows


def _positive_rows(cfg: dict, inputs: Path, repo: Path) -> list[dict]:
    rows = []
    for spec in cfg.get("positive_pass") or []:
        ident = f"{spec['site']}/{spec['scene']}/{spec['profile']}/{spec['ground_rule']}"
        evidence = _positive_verdict(repo, inputs, spec)
        if evidence is None:
            rows.append(
                _row(
                    f"positive/{ident}",
                    "positive_pass",
                    ident,
                    "site.decision",
                    None,
                    spec["expect"],
                    "SKIP",
                    None,
                    "criteria verdict overlay is absent",
                )
            )
            continue
        verdict = json.loads(evidence.read_text())
        rows.append(_positive_row(ident, spec, verdict, evidence))
    return rows


def _positive_row(ident: str, spec: dict, verdict: dict, evidence: Path) -> dict:
    decision = (verdict.get("site") or {}).get("decision")
    expect = spec["expect"]
    if decision == expect:
        note = ""
        status = "PASS"
        if expect == "pass" and "start_s_m" in spec:
            wall = spec.get("wall", "w1")
            passes = [
                c
                for c in verdict.get("candidates") or []
                if c.get("outcome") == "pass" and c.get("wall_id") == wall and "start_s_m" in c
            ]
            if not any(_overlaps(c["start_s_m"], spec["start_s_m"]) for c in passes):
                status = "FAIL"
                note = "no pass candidate on the wall overlaps the chain-s window"
            route_max = spec.get("route_ft_max")
            if status == "PASS" and route_max is not None:
                route = _route_ft(verdict)
                if route is None:
                    status = "FAIL"
                    note = "route_length measured value is missing"
                elif route > float(route_max):
                    status = "FAIL"
                    note = f"route {route} ft exceeds {route_max}"
        return _apply_known_failure(
            _row(
                f"positive/{ident}",
                "positive_pass",
                ident,
                "site.decision",
                decision,
                expect,
                status,
                str(evidence),
                note,
            ),
            spec,
        )
    if (
        decision == "manual_review"
        and spec.get("allow_warn")
        and (verdict.get("site") or {}).get("unsure_cause") == "margin"
    ):
        return _apply_known_failure(
            _row(
                f"positive/{ident}",
                "positive_pass",
                ident,
                "site.decision",
                decision,
                expect,
                "WARN",
                str(evidence),
                "manual_review with unsure_cause margin",
            ),
            spec,
        )
    return _apply_known_failure(
        _row(
            f"positive/{ident}",
            "positive_pass",
            ident,
            "site.decision",
            decision,
            expect,
            "FAIL",
            str(evidence),
            f"decision {decision} != {expect}",
        ),
        spec,
    )


def _decidability_rows(cfg: dict, inputs: Path) -> list[dict]:
    block = cfg.get("decidability") or {}
    rows = []
    for site, minimum in (block.get("gt_laser_min") or {}).items():
        evidence = _first(
            inputs / "decidability" / site / "gt-laser.json",
            inputs / "criteria" / site / "gt-laser" / "criteria_report.json",
        )
        rows.extend(_share_rows(f"decidability/{site}/gt-laser", site, minimum, evidence, "FAIL"))
    for site, minimum in (block.get("photo_warn_below") or {}).items():
        evidence = _first(inputs / "decidability" / site / "colmap-nonlidar.json")
        rows.extend(
            _share_rows(
                f"decidability/{site}/colmap-nonlidar",
                site,
                minimum,
                evidence,
                "WARN",
                missing="no photo decidability share",
            )
        )
    return rows


def _profile_shares(payload: dict) -> list[tuple[str | None, float]]:
    """WS-09 writes ``decidability.<profile>.share``. A flat ``share`` is the older shape."""
    block = payload.get("decidability")
    if isinstance(block, dict):
        nested = [
            (str(profile), float(body["share"]))
            for profile, body in block.items()
            if isinstance(body, dict) and isinstance(body.get("share"), (int, float))
        ]
        if nested:
            return nested
        if isinstance(block.get("share"), (int, float)):
            return [(None, float(block["share"]))]
    if isinstance(payload.get("share"), (int, float)):
        return [(None, float(payload["share"]))]
    return []


def _share_rows(
    ident: str, target: str, minimum: float, evidence: Path | None, low_status: str, missing: str = ""
) -> list[dict]:
    if evidence is None:
        return [
            _row(
                ident,
                "decidability",
                target,
                "decidability.share",
                None,
                minimum,
                "SKIP",
                None,
                missing or "run-criteria did not write criteria_report.json",
            )
        ]
    found = _profile_shares(json.loads(evidence.read_text()))
    if not found:
        return [
            _row(
                ident,
                "decidability",
                target,
                "decidability.<profile>.share",
                None,
                minimum,
                "SKIP",
                str(evidence),
                "decidability.<profile>.share missing",
            )
        ]
    rows = []
    for profile, share in found:
        row_id = f"{ident}/{profile}" if profile else ident
        metric = f"decidability.{profile}.share" if profile else "decidability.share"
        status = "PASS" if share >= float(minimum) else low_status
        rows.append(_row(row_id, "decidability", target, metric, share, minimum, status, str(evidence), ""))
    return rows


def _safety_rows(cfg: dict, inputs: Path) -> list[dict]:
    if not (cfg.get("safety") or {}).get("no_coverage_no_pass", True):
        return []
    rows = []
    folder = inputs / "safety" / "no_coverage"
    if not folder.is_dir():
        rows.append(
            _row(
                "safety/no_coverage_no_pass",
                "safety",
                "all-profiles",
                "site.decision",
                None,
                "!= pass",
                "SKIP",
                None,
                "no_coverage directory is absent",
            )
        )
    else:
        files = sorted(folder.glob("*.json"))
        if not files:
            rows.append(
                _row(
                    "safety/no_coverage_no_pass",
                    "safety",
                    "all-profiles",
                    "site.decision",
                    None,
                    "!= pass",
                    "SKIP",
                    None,
                    "no_coverage directory is empty",
                )
            )
        for path in files:
            payload = json.loads(path.read_text())
            reason = _stripped_coverage_pass(payload)
            decision = _site_decision(payload)
            rows.append(
                _row(
                    f"safety/no_coverage/{path.stem}",
                    "safety",
                    path.stem,
                    "site.decision",
                    decision,
                    "!= pass",
                    "FAIL" if reason else "PASS",
                    str(path),
                    reason or "",
                )
            )
    veto = inputs / "safety" / "veto"
    if not veto.is_dir():
        rows.append(
            _row(
                "safety/veto_monotone",
                "safety",
                "candidates",
                "outcome_order",
                None,
                "fail < unsure < pass",
                "SKIP",
                None,
                "no paired C2 / --no-c2 verdicts",
            )
        )
    else:
        for path in sorted(veto.glob("*.json")):
            payload = json.loads(path.read_text())
            held = _monotone(payload.get("with_c2"), payload.get("without_c2"))
            rows.append(
                _row(
                    f"safety/veto/{path.stem}",
                    "safety",
                    path.stem,
                    "veto_monotone",
                    payload.get("with_c2"),
                    "<= without_c2",
                    "PASS" if held else "FAIL",
                    str(path),
                    "" if held else "C2 upgraded an outcome",
                )
            )
    rows.append(
        _row(
            "safety/judge_monotone",
            "safety",
            "site",
            "judge_monotone",
            None,
            "reject < manual_review < pass",
            "SKIP",
            None,
            "the C3 judge is not wired",
        )
    )
    if not (inputs / "safety" / "bounds").is_dir():
        rows.append(
            _row(
                "safety/bounds_honest",
                "safety",
                "pipeline-scenes",
                "plus_minus_m",
                None,
                "honest",
                "SKIP",
                None,
                "no pipeline scene to check",
            )
        )
    return rows


def _lamar_rows(cfg: dict, inputs: Path) -> list[dict]:
    block = cfg.get("lamar") or {}
    limit = block.get("openings_p90_in_max", 4.6)
    rows: list[dict] = []
    evidence = inputs / "lamar" / "summary.json"
    if not evidence.is_file():
        rows.append(
            _row(
                "lamar/openings_p90_in",
                "lamar",
                "five-segments",
                "max(openings_p90_in)",
                None,
                limit,
                "SKIP",
                None,
                "LaMAR five-segment summary is not on this machine",
            )
        )
    else:
        payload = json.loads(evidence.read_text())
        rows.append(
            _cmp(
                "lamar/openings_p90_in",
                "lamar",
                "five-segments",
                "max(openings_p90_in)",
                payload.get("openings_p90_in_max"),
                limit,
                evidence,
                at_most=True,
            )
        )
    phone = inputs / "lamar" / "2022-02-10_s0.json"
    if phone.is_file():
        measured = json.loads(phone.read_text())
        row = _cmp(
            "lamar/2022-02-10_s0/openings_p90_in",
            "lamar",
            "2022-02-10_s0",
            "openings_p90_in",
            measured.get("openings_p90_in"),
            limit,
            phone,
            at_most=True,
        )
        host_name = measured.get("gate_host") or block.get("gate_host") or ""
        row["note"] = (
            f"real phone on {host_name}; openings median {measured.get('openings_median_in')} in; "
            f"far {measured.get('far_median_in')} / {measured.get('far_p90_in')} in; "
            f"frames {measured.get('frames')}; per-machine, not cross-machine"
        )
        rows.append(row)
    rows.extend(_lamar_host_rows(block, phone if phone.is_file() else None))
    return rows


def _lamar_host_rows(block: dict, phone: Path | None) -> list[dict]:
    """PASS only when a measurement file records the pinned gate host."""
    pinned = block.get("gate_host")
    if not pinned:
        return []
    note = (
        "pose determinism is per machine; pin this host before tightening the metric bar to 4.0 in. "
        "validator VM 66152 points, 0.300814 px, metric in-plane 2.03 in; "
        "builder VM 66770 points, 0.29849 px, metric in-plane 4.21 in"
    )
    if phone is None:
        return [
            _row(
                "lamar/host",
                "lamar",
                str(pinned),
                "gate_host",
                None,
                str(pinned),
                "SKIP",
                None,
                "no LaMAR measurement records gate_host",
            )
        ]
    recorded = json.loads(phone.read_text()).get("gate_host")
    return [
        _row(
            "lamar/host",
            "lamar",
            str(pinned),
            "gate_host",
            recorded,
            str(pinned),
            "PASS" if recorded == pinned else "FAIL",
            str(phone),
            note if recorded == pinned else f"measurement gate_host {recorded} != {pinned}",
        )
    ]


def _known_warning_rows(cfg: dict, inputs: Path) -> list[dict]:
    """Committed warning evidence. A missing or malformed file is FAIL.

    WARN only while the measured value sits within ``tolerance`` of ``recorded``
    and ``claims`` is empty. Anything else on that evidence is FAIL. The note is
    built from the file. SKIP is not used here: these files are committed, not
    produced from the PLY cache at gate time.
    """
    rows = []
    for spec in cfg.get("known_warnings") or []:
        rows.append(_known_warning_row(spec, inputs))
    return rows


def _known_warning_row(spec: dict, inputs: Path) -> dict:
    ident = spec["id"]
    group = spec.get("group", "geometry")
    target = spec.get("target")
    metric = spec.get("metric")
    recorded = spec.get("recorded")
    rel = spec.get("evidence")
    evidence = (inputs / rel) if rel else None
    if evidence is None or not evidence.is_file():
        return _row(ident, group, target, metric, None, recorded, "FAIL", None, "committed evidence invalid")
    try:
        payload = json.loads(evidence.read_text())
    except json.JSONDecodeError:
        return _row(ident, group, target, metric, None, recorded, "FAIL", str(evidence), "committed evidence invalid")
    key = spec.get("value_key") or metric
    value = payload.get(key) if isinstance(payload, dict) else None
    claims = payload.get("claims") if isinstance(payload, dict) else None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not isinstance(claims, list):
        return _row(ident, group, target, metric, None, recorded, "FAIL", str(evidence), "committed evidence invalid")
    if recorded is None or spec.get("tolerance") is None:
        return _row(ident, group, target, metric, value, recorded, "FAIL", str(evidence), "committed evidence invalid")
    note = _known_warning_note(payload, value)
    pinned = abs(float(value) - float(recorded)) <= float(spec["tolerance"]) and claims == []
    return _row(
        ident,
        group,
        target,
        metric,
        value,
        recorded,
        "WARN" if pinned else "FAIL",
        str(evidence),
        note,
    )


def _known_warning_note(payload: dict, value: float) -> str:
    metric = payload.get("metric") if isinstance(payload.get("metric"), str) else "value"
    rule = payload.get("rule")
    floor = payload.get("floor")
    claims = payload.get("claims")
    claim_text = "claims empty" if claims == [] else f"claims {claims}"
    parts = [f"{metric} {value}"]
    if isinstance(rule, str) and rule:
        parts.append(f"on {rule}")
    if isinstance(floor, (int, float)) and not isinstance(floor, bool):
        parts.append(f"floor {floor}")
    parts.append(claim_text)
    return "; ".join(parts)


def _apply_known_failure(row: dict, spec: dict, *, today: date | None = None) -> dict:
    """Turn the recorded route miss into KNOWN_FAIL until its expiry.

    Only a route within ``tolerance_ft`` of ``route_ft`` is masked. A larger
    miss stays FAIL, as does a decision mismatch. After ``until``, the recorded
    miss is FAIL again.
    """
    known = spec.get("known_failure")
    if not isinstance(known, dict) or row["status"] != "FAIL":
        return row
    note = str(row.get("note") or "")
    if not note.startswith("route ") or " exceeds " not in note:
        return row
    until = str(known.get("until") or "")
    current = today or date.today()
    if until and current.isoformat() > until:
        return row
    measured = _route_ft_from_note(note)
    pinned = known.get("route_ft")
    if measured is None or pinned is None:
        return row
    tolerance = float(known.get("tolerance_ft", 0.05))
    if abs(measured - float(pinned)) > tolerance:
        return row
    reason = str(known.get("reason") or "")
    owner = str(known.get("owner") or "")
    return _row(
        row["id"],
        row["group"],
        row["target"],
        row["metric"],
        row["value"],
        row["threshold"],
        "KNOWN_FAIL",
        row["evidence_path"],
        f"{note}; known failure: {reason} (owner {owner}, until {until})",
    )


def _route_ft_from_note(note: str) -> float | None:
    parts = note.split()
    if len(parts) < 2 or parts[0] != "route":
        return None
    try:
        return float(parts[1])
    except ValueError:
        return None


def _runtime_rows(cfg: dict, inputs: Path) -> list[dict]:
    limits = cfg.get("runtime") or {}
    evidence = _first(inputs / "runtime" / "e2e.json")
    if evidence is None:
        return [
            _row(
                "runtime/e2e",
                "runtime",
                "dev",
                "timings",
                None,
                limits.get("time_to_verdict_p50_s_max"),
                "SKIP",
                None,
                "no ReplayRun timings (CAPTURE_API_URL was not smoked)",
            )
        ]
    payload = json.loads(evidence.read_text())
    rows = []
    rows.append(
        _cmp(
            "runtime/capture_check_s",
            "runtime",
            "dev",
            "capture_check_s",
            payload.get("capture_check_s"),
            limits.get("capture_check_s_max"),
            evidence,
            at_most=True,
        )
    )
    rows.append(
        _cmp(
            "runtime/time_to_verdict_p50_s",
            "runtime",
            "dev",
            "time_to_verdict_p50_s",
            payload.get("time_to_verdict_p50_s"),
            limits.get("time_to_verdict_p50_s_max"),
            evidence,
            at_most=True,
        )
    )
    return rows


def _site_decision(payload: dict):
    site = payload.get("site")
    if isinstance(site, dict) and site.get("decision"):
        return site.get("decision")
    return payload.get("decision") or payload.get("site_decision")


def _stripped_coverage_pass(payload: dict) -> str | None:
    """Any pass in a coverage-stripped verdict is fail-closed.

    Reads ``site.decision``, top-level ``decision`` / ``pass_candidates``, and ``candidates[]``.
    A criterion that is still ``pass`` counts too: stripping coverage must leave every criterion
    ``unsure`` or ``not_evaluated``.
    """
    decision = _site_decision(payload)
    passes = int(payload.get("pass_candidates") or 0)
    passes += sum(
        1
        for candidate in payload.get("candidates") or []
        if candidate.get("outcome") == "pass" or candidate.get("decision") == "pass"
    )
    criterion_pass = any(item.get("outcome") == "pass" for item in payload.get("criteria") or [])
    if decision == "pass" or passes > 0 or criterion_pass:
        return "a pass with no observed coverage is fail-closed"
    return None


def _positive_verdict(repo: Path, inputs: Path, spec: dict) -> Path | None:
    overlay = inputs / "positive" / spec["site"] / spec["scene"] / spec["profile"] / f"{spec['ground_rule']}.json"
    if overlay.is_file():
        return overlay
    if spec["scene"] == "gt-laser" and spec["ground_rule"] == "footprint3in":
        override = inputs / "verdicts" / spec["site"] / "c1-3d" / f"{spec['profile']}.json"
        if override.is_file():
            return override
        baked = repo / "data" / "fixtures" / "verdicts" / spec["site"] / "c1-3d" / f"{spec['profile']}.json"
        if baked.is_file():
            return baked
    return None


def _false_rejections(score: dict) -> int:
    positions = ((score.get("start_range") or {}).get("positions") or {}).get("false_rejection", 0)
    site_rows = (score.get("site") or {}).get("criteria") or []
    return int(positions) + sum(1 for row in site_rows if row.get("result") == "false_rejection")


def _route_ft(verdict: dict) -> float | None:
    for criterion in verdict.get("criteria") or []:
        if criterion.get("id") != "route_length":
            continue
        measured = criterion.get("measured") or {}
        if measured.get("value_ft") is not None:
            return float(measured["value_ft"])
    return None


def _overlaps(span: list, window: list) -> bool:
    lo, hi = sorted(float(x) for x in span)
    wlo, whi = float(window[0]), float(window[1])
    return hi >= wlo - SPAN_TOL_M and lo <= whi + SPAN_TOL_M


def _monotone(with_c2: str | None, without_c2: str | None) -> bool:
    if with_c2 not in _ORDER or without_c2 not in _ORDER:
        return False
    return _ORDER[with_c2] <= _ORDER[without_c2]


def _cmp(ident, group, target, metric, value, threshold, evidence: Path, *, at_most=False, at_least=False) -> dict:
    if value is None:
        return _row(ident, group, target, metric, None, threshold, "SKIP", str(evidence), "metric missing")
    ok = (
        (float(value) <= float(threshold))
        if at_most
        else (float(value) >= float(threshold))
        if at_least
        else value == threshold
    )
    return _row(ident, group, target, metric, value, threshold, "PASS" if ok else "FAIL", str(evidence), "")


def _row(ident, group, target, metric, value, threshold, status, evidence, note) -> dict:
    return {
        "id": ident,
        "group": group,
        "target": target,
        "metric": metric,
        "value": value,
        "threshold": threshold,
        "status": status,
        "evidence_path": evidence,
        "note": note,
    }


def _first(*paths: Path) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _git_sha(repo: Path) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="server.eval.gates")
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--inputs", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument(
        "--allow-missing-occluders",
        action="store_true",
        help="debug only: score without occluder PLYs. The report is provisional and this gate fails",
    )
    args = ap.parse_args(argv)
    report = evaluate(args.config, args.inputs, allow_missing_occluders=args.allow_missing_occluders)
    write(report, args.out)
    sys.stdout.write(markdown(report))
    return exit_code(report, strict=args.strict)
