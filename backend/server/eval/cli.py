"""Command line: python -m server.eval.cli {validate, score-geometry, score-cloud, query-coverage, audit-verdict, run-hs11, run-c1, score-verdicts} ...

Run from internal/eval-harness (or put it on PYTHONPATH).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from server.eval.adapters.criteria import CriteriaConfig, evaluate_scene
from server.eval.adapters.hs11 import run as run_c1
from server.eval.coverage import GROUND_RULES, query_region
from server.eval.gates import evaluate, exit_code
from server.eval.geomscore import score_cloud, score_scene, transform_from_arg
from server.eval.report import markdown, write
from server.eval.sceneio import load_json, validate
from server.eval.verdictcheck import audit_claims
from server.eval.verdictscore import score_files


def _summary(r: dict) -> dict:
    out = {
        "key_distances": r["key_distances"]["overall"],
        "status_counts": r["key_distances"]["status_counts"],
        "scale": {k: v for k, v in r["scale"].items() if k != "alignment"},
    }
    if "anchor" in r:
        out["anchor_icp_m"] = r["anchor"]["icp_translation_norm_m"]
    if "completeness" in r:
        out["completeness_within_10cm_declared"] = {
            k: e.get("declared", {}).get("within_10cm") for k, e in r["completeness"].items()
        }
        out["false_observed"] = {k: e["coverage"].get("false_observed") for k, e in r["completeness"].items()}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="server.eval.cli", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="schema + cross-reference check of a scene / verdict / labels file")
    v.add_argument("files", nargs="+", type=Path)

    g = sub.add_parser("score-geometry", help="a scene v0 vs the site's GT scene")
    g.add_argument("--gt", type=Path, required=True)
    g.add_argument("--recon", type=Path, required=True)
    g.add_argument("--out", type=Path, required=True)
    g.add_argument(
        "--no-visibility", action="store_true", help="skip the visibility oracle (faster; no coverage-honesty numbers)"
    )
    g.add_argument(
        "--allow-missing-occluders",
        action="store_true",
        help="debug only: omit a missing occluder PLY and mark the report provisional",
    )

    c = sub.add_parser("score-cloud", help="a bare point cloud / mesh (PLY, any frame) + its cameras vs the GT scene")
    c.add_argument("--gt", type=Path, required=True)
    c.add_argument("--cloud", type=Path, required=True, help="binary PLY; mesh vertices are used as points")
    c.add_argument(
        "--cameras", type=Path, help="COLMAP model dir, or JSON list of {image, pose|R_cw+t_cw, intrinsics, w, h}"
    )
    c.add_argument(
        "--transform", help="4x4 cloud->GT-frame transform (JSON file or inline) instead of --cameras alignment"
    )
    c.add_argument(
        "--metric", action="store_true", help="the cloud claims metric scale: align rigidly and report its scale error"
    )
    c.add_argument("--producer", default="cloud", help="path id, e.g. A1-colmap-dense")
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--no-visibility", action="store_true")
    c.add_argument(
        "--allow-missing-occluders",
        action="store_true",
        help="debug only: omit a missing occluder PLY and mark the report provisional",
    )
    c.add_argument(
        "--align",
        choices=["auto", "centres", "rotations"],
        default="auto",
        help="camera alignment: centres only (Umeyama) or rotations + centres; auto picks rotations when centres are near-collinear",
    )

    q = sub.add_parser(
        "query-coverage", help="coverage evidence for a region of a scene (what a measurement backend must attach)"
    )
    q.add_argument("--scene", type=Path, required=True)
    q.add_argument(
        "--region", required=True, help='JSON, e.g. {"wall_id": "w2", "s_m": [3.2, 4.0], "height_m": [0, 2.13]}'
    )

    au = sub.add_parser("audit-verdict", help="contract check + coverage claims of a verdict vs the GT truth")
    au.add_argument("--verdict", type=Path, required=True)
    au.add_argument("--gt", type=Path, required=True)
    au.add_argument("--recon", type=Path, help="the scene the verdict was made on (maps its wall ids to GT)")

    vs = sub.add_parser("score-verdicts", help="a verdict vs one or more labels files")
    vs.add_argument("--verdict", type=Path, required=True)
    vs.add_argument("--labels", type=Path, nargs="+", required=True)
    vs.add_argument("--out", type=Path, required=True)
    vs.add_argument("--any-profile", action="store_true", help="also score against labels of other rules profiles")

    c.add_argument(
        "--max-points",
        type=int,
        default=400_000,
        help="subsample the aligned cloud to this many points (default 400000; about 12 min). Run one dense score at a time",
    )

    hs = sub.add_parser(
        "run-hs11", help="PR #11's single-chain solver on a scene v0 -> verdict v0 (not the 3D rules engine)"
    )
    hs.add_argument("--scene", type=Path, required=True)
    hs.add_argument("--out", type=Path, required=True, help="verdict JSON path")
    hs.add_argument(
        "--ground-rule", choices=sorted(GROUND_RULES), help="ground rule variant of the bands to use (sensitivity run)"
    )
    hs.add_argument(
        "--ground-limit", type=float, choices=[75.0, 80.0], help="shorthand for --ground-rule angle75 / angle80"
    )
    hs.add_argument("--rules", type=Path)

    gone = sub.add_parser("run-c1", help=argparse.SUPPRESS)
    gone.add_argument("--scene", type=Path)
    gone.add_argument("--out", type=Path)
    gone.add_argument("--ground-rule")
    gone.add_argument("--ground-limit", type=float)
    gone.add_argument("--rules", type=Path)

    rc = sub.add_parser("run-criteria", help="WS-09 criteria engine (fails closed when that package is absent)")
    rc.add_argument("--scene", type=Path, required=True)
    rc.add_argument("--out", type=Path, required=True)
    rc.add_argument("--profiles", nargs="+", default=["A", "B", "C", "S", "public"])
    rc.add_argument("--ground-rule", default="footprint3in", choices=["angle75", "angle80", "footprint3in"])
    rc.add_argument("--no-c2", action="store_true")
    rc.add_argument("--no-c3", action="store_true")

    gg = sub.add_parser("gates", help="evaluate gates.yaml and write the gate table")
    gg.add_argument("--config", type=Path, required=True)
    gg.add_argument("--inputs", type=Path, required=True)
    gg.add_argument("--out", type=Path, required=True)
    gg.add_argument("--strict", action="store_true")
    gg.add_argument(
        "--allow-missing-occluders",
        action="store_true",
        help="debug only: a gate still rejects provisional output",
    )

    a = ap.parse_args(argv)
    if a.cmd == "score-verdicts":
        rs = score_files(a.verdict, a.labels, a.out, a.any_profile)
        if not rs:
            print(
                f"no labels for rules profile {load_json(a.verdict)['evaluator'].get('rules_profile')!r}; nothing scored"
            )
        for r in rs:
            print(
                json.dumps(
                    {
                        "labels": r["labels_file"],
                        "headline": r["headline"],
                        "start_agreement": r["start_range"]["agreement"],
                        "site_decision": r["site"]["decision"],
                        "site_agreement": r["site"]["agreement"],
                    },
                    default=float,
                )
            )
        return 0
    if a.cmd == "gates":
        report = evaluate(a.config, a.inputs, allow_missing_occluders=a.allow_missing_occluders)
        write(report, a.out)
        sys.stdout.write(markdown(report))
        return exit_code(report, strict=a.strict)
    if a.cmd == "run-criteria":
        try:
            produced = evaluate_scene(
                a.scene.parent,
                a.out,
                config=CriteriaConfig(tuple(a.profiles), a.ground_rule, c2=not a.no_c2, c3=not a.no_c3),
            )
        except ImportError as exc:
            print(exc, file=sys.stderr)
            return 2
        print(json.dumps({profile: verdict["site"]["decision"] for profile, verdict in produced.items()}))
        return 0
    if a.cmd == "run-c1":
        print(
            "run-c1 is deprecated and no longer runs the solver; use run-hs11 "
            "(PR #11 single-chain solver). The 3D rules engine is run-criteria.",
            file=sys.stderr,
        )
        return 2
    if a.cmd == "run-hs11":
        rule = a.ground_rule or (f"angle{int(a.ground_limit)}" if a.ground_limit else None)
        v, res = run_c1(a.scene, a.out, rule, a.rules)
        print(
            json.dumps(
                {
                    "decision": v["site"]["decision"],
                    "reasons": v["site"]["reasons"],
                    "checks": {c["id"]: c["outcome"] for c in v["criteria"]},
                    "views_needed": len(v["views_needed"]),
                }
            )
        )
        return 0
    if a.cmd == "query-coverage":
        print(json.dumps(query_region(load_json(a.scene), a.scene.parent, json.loads(a.region)), indent=1))
        return 0
    if a.cmd == "audit-verdict":
        r = audit_claims(load_json(a.verdict), a.gt, a.recon)
        print(json.dumps(r, indent=1, default=float))
        return 1 if r["contract_errors"] or r["decisive_on_false_claims"] else 0
    if a.cmd == "validate":
        bad = 0
        for f in a.files:
            try:
                validate(load_json(f))
                print(f"ok   {f}")
            except ValueError as e:
                bad += 1
                print(f"FAIL {f}\n{e}")
        return 1 if bad else 0
    if a.cmd == "score-geometry":
        r = score_scene(
            a.gt,
            a.recon,
            a.out,
            visibility=not a.no_visibility,
            allow_missing_occluders=a.allow_missing_occluders,
        )
    else:
        r = score_cloud(
            a.gt,
            a.cloud,
            a.cameras,
            a.out,
            a.metric,
            producer=a.producer,
            transform=transform_from_arg(a.transform),
            visibility=not a.no_visibility,
            align_mode=a.align,
            max_points=a.max_points,
            allow_missing_occluders=a.allow_missing_occluders,
        )
    print(json.dumps(_summary(r), indent=1, default=float))
    print(f"wrote {a.out}/geomscore.json and geomscore.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
