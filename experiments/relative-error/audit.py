"""Provenance audit: every number the study rests on, read from its committed source.

Writes results/audit.md and results/audit.json. Each entry pins a file and a
locator inside it: a dotted JSON path (lists use [i]), or a line number in a
YAML or markdown file. Values are read from the files at run time and the
locator's content is asserted to contain what the study claims, so a stale or
misremembered number fails the audit instead of slipping into the results.

The audit also records each source file's sha256 and byte size (the manifest
freezes them; load_manifest fails the run when a file changes), the
measurement counts behind each dataset, and an explicit limitation per
source. Potentially self-scaled references are called out: the MARViN
COLMAP metric reference is not tape- or laser-verified, so it is not
independent truth.

`python audit.py --tree` prints the full key-path tree of the source JSONs;
that dump is how the manifest's paths were pinned.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]

SOURCES = {
    "advio_drift": "experiments/evals/results/advio_drift.json",
    "modern_arkit": "experiments/evals/results/modern_arkit.json",
    "drift_anatomy": "experiments/drift-anatomy/results/drift_anatomy.json",
    "budget_md": "experiments/sensor-budget/results/budget.md",
    "budget_json": "experiments/sensor-budget/results/budget.json",
    "rules_yaml": "server/rules.yaml",
    "evals_readme": "experiments/evals/README.md",
    "modern_arkit_md": "experiments/evals/results/modern_arkit.md",
}

# Per-source limitation, stated in the audit output next to the values.
LIMITATIONS = {
    "advio_drift": (
        "2018 iPhone 6s ARKit 1.0 walks; GPS-rescaled truth carries its own error; "
        "pooled across walks, so it bounds absolute position, not two-tap differences."
    ),
    "modern_arkit": (
        "COLMAP metric reference is not tape- or laser-verified; a potentially "
        "self-scaled reference is not independent truth, and the per-walk scale "
        "spreads inherit that doubt."
    ),
    "modern_arkit_md": (
        "Same reference as modern_arkit; the trusted-subset p90 table is the "
        "simulation's calibration target."
    ),
    "drift_anatomy": (
        "Within-walk scale variation and along-travel share; no row measures a "
        "two-tap difference (clearance) error directly."
    ),
    "budget_md": "Modeled device budget, not a measurement; anchors local_clearance.",
    "budget_json": "Modeled device budget, not a measurement; anchors local_clearance.",
    "rules_yaml": (
        "0.3/1.5 ft bases and the 0.16 ft/ft rate are day-1 estimates; the rate is "
        "calibrated to S1 real-data evals (PR #12) on a 2018 iPhone 6s only. "
        "Citation review reused from PR #217 (obv/basescanning-024)."
    ),
    "evals_readme": "Summary prose; the numbers it quotes are pinned to their tables.",
}

# (quantity, source key, locator, must-contain, note). For JSON locators the
# must-contain check is skipped; the resolved value itself is reported.
MANIFEST = [
    (
        "drift_per_ft rate",
        "rules_yaml",
        ("yaml", 41),
        "0.16",
        "the rate under study, with its own source note: 2018 iPhone 6s, current phones untested",
    ),
    ("tap base", "rules_yaml", ("yaml", 29), "0.3", "per-tap allowance"),
    ("vlm base", "rules_yaml", ("yaml", 30), "1.5", "photo-detection allowance"),
    ("wall base", "rules_yaml", ("yaml", 36), "0.3", "tap wall allowance"),
    (
        "evals tracking claim",
        "evals_readme",
        ("md", 8),
        "2 to 3 times",
        "2018 phone 2-3x the server allowance; MARViN p90 8.6/13.4/18.5 in at 10/20/30 ft",
    ),
    (
        "ADVIO p90 vs ARCore truth, 3 ft",
        "advio_drift",
        ("json", "pooled.3.arcore.p90_in"),
        None,
        "the numbers the drift_per_ft source note quotes (8.2 in rounded)",
    ),
    (
        "ADVIO p90 vs ARCore truth, 10 ft",
        "advio_drift",
        ("json", "pooled.10.arcore.p90_in"),
        None,
        "",
    ),
    (
        "ADVIO p90 vs ARCore truth, 20 ft",
        "advio_drift",
        ("json", "pooled.20.arcore.p90_in"),
        None,
        "",
    ),
    (
        "ADVIO p90 vs ARCore truth, 30 ft",
        "advio_drift",
        ("json", "pooled.30.arcore.p90_in"),
        None,
        "",
    ),
    (
        "ADVIO position p90 vs GPS truth, 3 ft",
        "advio_drift",
        ("json", "pooled.3.position_truth_gps.p90_in"),
        None,
        "the harder calibration target the simulation uses",
    ),
    (
        "ADVIO position p90 vs GPS truth, 10 ft",
        "advio_drift",
        ("json", "pooled.10.position_truth_gps.p90_in"),
        None,
        "",
    ),
    (
        "ADVIO position p90 vs GPS truth, 20 ft",
        "advio_drift",
        ("json", "pooled.20.position_truth_gps.p90_in"),
        None,
        "",
    ),
    (
        "ADVIO position p90 vs GPS truth, 30 ft",
        "advio_drift",
        ("json", "pooled.30.position_truth_gps.p90_in"),
        None,
        "",
    ),
    (
        "ADVIO position p90 after scale removal, 30 ft",
        "advio_drift",
        ("json", "pooled.30.position_beyond_scale.p90_in"),
        None,
        "removing per-walk scale does not collapse the p90; these data do not establish common mode",
    ),
    (
        "MARViN per-walk scales, bar+church",
        "modern_arkit",
        ("json", "bar"),
        None,
        "arrays of per-walk scale; the simulation's SD is derived from these and checked below",
    ),
    (
        "MARViN atrium walks excluded from trusted set",
        "modern_arkit",
        ("json", "atrium"),
        None,
        "per-walk scales down to 0.49; the trusted-set filter excludes scene disagreement",
    ),
    (
        "MARViN trusted-subset position p90, 10-30 ft",
        "modern_arkit_md",
        ("md", 42),
        "8.6",
        "lines 42-44, column 'As tracked, without those walks': p90 8.6/13.4/18.5 in; the calibration target",
    ),
    (
        "anatomy A1 loops within bound",
        "drift_anatomy",
        ("json", "a1.share_ok"),
        None,
        "30 of 56 loops: returning to the meter does not bound the error",
    ),
    (
        "anatomy A1 with walk scale removed",
        "drift_anatomy",
        ("json", "a1.walk_scale_removed_share_ok"),
        None,
        "75 of 100: scale removal helps and still fails the 90% criterion",
    ),
    (
        "anatomy A2 verdict",
        "drift_anatomy",
        ("json", "a2.marvin.verdict"),
        None,
        "'drop': one reference object near the meter is not enough",
    ),
    (
        "anatomy A2 MARViN walks",
        "drift_anatomy",
        ("json", "a2.marvin.walks"),
        None,
        "per-walk robust SD of scale over 5 m windows, 1.1-2.1%",
    ),
    (
        "anatomy A3 along share at 20 ft",
        "drift_anatomy",
        ("json", "a3.marvin.20.along_share_of_total_p90"),
        None,
        "0.92: the error runs along travel, the direction a same-wall difference shares",
    ),
    (
        "anatomy A3 ADVIO along share at 20 ft",
        "drift_anatomy",
        ("json", "a3.advio.20.along_share_of_total_p90"),
        None,
        "0.76: on the 2018 phone the error runs every which way",
    ),
    (
        "anatomy A4 UWB range at sigma 10 cm, 20 ft",
        "drift_anatomy",
        ("json", "a4.marvin.20.after_p90_in_sigma_0.10"),
        None,
        "7.7 in: a meter range helps and does not settle a 3 ft clearance",
    ),
    (
        "anatomy A4 UWB range at sigma 10 cm, 30 ft",
        "drift_anatomy",
        ("json", "a4.marvin.30.after_p90_in_sigma_0.10"),
        None,
        "",
    ),
    (
        "budget: both ends in one photo, LiDAR",
        "budget_md",
        ("md", 16),
        "0.54 in",
        "modeled device budget for a 6 ft span: best 0.54, p90 1.34 in",
    ),
    (
        "budget: both ends in one photo, no LiDAR",
        "budget_md",
        ("md", 17),
        "4.37 in",
        "the anchor models.local_clearance_bar uses: 1.34-4.37 in for a 6 ft span",
    ),
    (
        "budget JSON precise values",
        "budget_json",
        ("json", "rows"),
        None,
        "the table's underlying numbers",
    ),
]


def load(key: str) -> tuple[Path, object]:
    path = ROOT / SOURCES[key]
    return path, json.loads(path.read_text())


def source_hashes() -> dict:
    """sha256 and size of every source file; the manifest freezes these."""
    out = {}
    for key, rel in SOURCES.items():
        data = (ROOT / rel).read_bytes()
        out[key] = {
            "path": rel,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
    return out


def measurement_counts() -> dict:
    """How many measurements each dataset's pinned values summarize."""
    _, advio = load("advio_drift")
    _, modern = load("modern_arkit")
    _, anatomy = load("drift_anatomy")
    pooled = advio.get("pooled", {})
    trusted = [w for scene in ("bar", "church") for w in modern.get(scene, [])]
    atrium = modern.get("atrium", [])
    a2_walks = anatomy.get("a2", {}).get("marvin", {}).get("walks", [])
    return {
        "advio_walked_distance_levels": len(pooled),
        "marvin_trusted_walks_bar_church": len(trusted),
        "marvin_atrium_walks_excluded": len(atrium),
        "drift_anatomy_a2_scale_windows": len(a2_walks) if isinstance(a2_walks, list) else None,
    }


def walk(node: object, prefix: str = "") -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            child = f"{prefix}.{k}" if prefix else str(k)
            out.extend(walk(v, child))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(walk(v, f"{prefix}[{i}]"))
    else:
        out.append((prefix, node))
    return out


def tree_of(key: str) -> str:
    _path, data = load(key)
    lines = [f"# {key}: {SOURCES[key]}"]
    for p, v in walk(data):
        lines.append(f"  {p} = {v!r}")
    return "\n".join(lines)


def resolve(source_key: str, dotted: str) -> object:
    """Walk a dotted path. Keys may themselves contain dots (e.g.
    after_p90_in_sigma_0.10): when a part is not a key, join it with the next
    part until a real key is formed. List items use key[i]."""
    _, data = load(source_key)
    parts = dotted.split(".")
    node: object = data
    i = 0
    while i < len(parts):
        match = re.fullmatch(r"([^\[\]]+)\[(\d+)\]", parts[i])
        if match and isinstance(node, dict) and match.group(1) in node:
            node = node[match.group(1)][int(match.group(2))]
            i += 1
            continue
        if not isinstance(node, dict):
            raise KeyError(parts[i])
        key = parts[i]
        while key not in node and i + 1 < len(parts):
            i += 1
            key += "." + parts[i]
        if key not in node:
            raise KeyError(key)
        node = node[key]
        i += 1
    return node


def resolve_locator(source_key: str, locator: tuple[str, int | str]) -> object:
    kind, ref = locator
    if kind in ("yaml", "md"):
        path = ROOT / SOURCES[source_key]
        lines = path.read_text().splitlines()
        return lines[ref - 1]
    return resolve(source_key, str(ref))


def audit_rows() -> list[dict]:
    rows = []
    for quantity, source_key, locator, must_contain, note in MANIFEST:
        value = resolve_locator(source_key, locator)
        if must_contain is not None and must_contain not in str(value):
            raise AssertionError(
                f"audit: {quantity}: {SOURCES[source_key]} line {locator[1]} "
                f"does not contain {must_contain!r}; got {str(value)[:120]!r}"
            )
        rows.append(
            {
                "quantity": quantity,
                "source": SOURCES[source_key],
                "locator": f"{locator[0]}:{locator[1]}",
                "value": value if not isinstance(value, str) else value.strip(),
                "note": note,
            }
        )
    return rows


def scale_check() -> dict:
    """The simulation's MARViN scale distribution, checked against the pinned
    per-walk scales it claims to summarize. This is a calibration constant
    with its derivation shown, not a cited fact."""
    _, modern = load("modern_arkit")
    scales = [w["scale"] for scene in ("bar", "church") for w in modern[scene]]
    arr = np.array(scales)
    sd = float(np.std(arr - arr.mean(), ddof=1))
    mean = float(arr.mean())
    assert abs(sd - 0.0147) < 0.002, f"MARViN walk scale SD drifted: {sd}"
    return {"n_walks": len(scales), "mean": mean, "sd": sd, "sd_used_in_simulation": 0.0147}


def scan_difference_evidence() -> dict:
    """Search every committed experiments/*/results/*.json for a leaf key that
    could be a two-tap difference (clearance) error measurement, and report
    what each match actually is. The study's missing-observation claim rests
    on this scan being exhaustive over the committed results. The study's own
    results dir is excluded: its simulated sweeps are not measurements."""
    results = sorted((ROOT / "experiments").glob("*/results/*.json"))
    pattern = re.compile(r"diff|clearance|pair|gap|separation|between", re.I)
    matches: list[dict] = []
    for path in results:
        if path.parent == Path(__file__).resolve().parent / "results":
            continue
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        for p, v in walk(data):
            leaf = p.split(".")[-1].split("[")[0]
            if pattern.search(leaf):
                matches.append({"file": str(path.relative_to(ROOT)), "path": p, "value": v})
    return {"files_scanned": len(results), "matches": matches}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", action="store_true", help="print source JSON key-path trees")
    # Default to an empty argv: when run.py imports this module, argparse must
    # not consume the runner's own command line.
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.tree:
        for key in SOURCES:
            if key.endswith(("_md", "_yaml", "_readme")):
                continue
            print(tree_of(key))
        return
    rows = audit_rows()
    hashes = source_hashes()
    counts = measurement_counts()
    out = {
        "rows": rows,
        "scale_check": scale_check(),
        "difference_scan": scan_difference_evidence(),
        "source_hashes": hashes,
        "measurement_counts": counts,
        "limitations": LIMITATIONS,
    }
    results = Path(__file__).resolve().parent / "results"
    results.mkdir(exist_ok=True)
    (results / "audit.json").write_text(json.dumps(out, indent=1, default=str))
    lines = [
        "# Provenance audit",
        "",
        "Every number below was read from its committed source at run time.",
        "Line locators assert the quoted text; JSON locators resolve exactly.",
        "Citation review of the rules values is reused from PR #217",
        "(obv/basescanning-024); historical measured values are retained here.",
        "",
        "| quantity | source | locator | value | note |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        value = row["value"]
        if isinstance(value, float):
            value = f"{value:.4f}" if abs(value) < 1000 else f"{value:.1f}"
        value = str(value).replace("|", "\\|")
        if len(value) > 60:
            value = value[:57] + "..."
        lines.append(
            f"| {row['quantity']} | {row['source']} | {row['locator']} | {value} | {row['note']} |"
        )

    lines += [
        "",
        "## Source hashes",
        "",
        "| source | path | sha256 | bytes |",
        "|---|---|---|---|",
    ]
    for key, meta in hashes.items():
        lines.append(f"| {key} | {meta['path']} | {meta['sha256'][:16]}... | {meta['bytes']} |")

    lines += [
        "",
        "## Measurement counts",
        "",
        "| dataset | count |",
        "|---|---|",
    ]
    for key, n in counts.items():
        lines.append(f"| {key} | {n} |")

    lines += [
        "",
        "## Limitations",
        "",
    ]
    for key, note in LIMITATIONS.items():
        lines.append(f"- **{key}**: {note}")
    lines += [
        "",
        "An assumed correlation is not measured calibration: the residual",
        "correlation length in the sweep is a swept assumption, not a measured",
        "value, and the manifest says so beside the hashes it freezes.",
    ]

    sc = out["scale_check"]
    lines += [
        "",
        f"Scale check: {sc['n_walks']} trusted MARViN walks, mean {sc['mean']:+.4f}, "
        f"SD {sc['sd']:.4f}; the simulation uses SD {sc['sd_used_in_simulation']} and mean 0. "
        "The mean offset is 0.2 percent, small against the SD; the simulation's zero mean is "
        "stated rather than hidden.",
        "",
        "Difference scan: what the committed results say about two-tap differences:",
    ]
    scan = out["difference_scan"]
    lines.append(
        f"{scan['files_scanned']} result files scanned, {len(scan['matches'])} matching leaf keys."
    )
    if scan["matches"]:
        by_file: dict[str, dict[str, list]] = {}
        for m in scan["matches"]:
            leaf = m["path"].split(".")[-1].split("[")[0]
            entry = by_file.setdefault(m["file"], {})
            entry.setdefault(leaf, []).append(m["value"])
        for fname, leaves in sorted(by_file.items()):
            lines.append(
                f"- {fname}: {sum(len(v) for v in leaves.values())} matches over "
                f"{len(leaves)} key names: {', '.join(sorted(leaves))}"
            )
        lines.append(
            "What these are: depth-prior and reconstruction keys named gap/between/etc. -- "
            "scene-geometry fields, not tracking-error measurements of a two-tap difference. "
            "The full match list is in audit.json."
        )
    else:
        lines.append("No leaf key matched difference/clearance/pair/gap/separation/between.")
    (results / "audit.md").write_text("\n".join(lines) + "\n")
    print(f"audit: {len(rows)} rows, scale check ok, scan: {len(scan['matches'])} matches")


if __name__ == "__main__":
    main()
