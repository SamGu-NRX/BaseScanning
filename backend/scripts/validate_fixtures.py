#!/usr/bin/env python3
"""`make validate` / `make validate-full` for the committed golden fixtures."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "data" / "fixtures"
PY = [sys.executable, "-m"]


def _run(args: list[str]) -> int:
    print("+ " + " ".join(args), flush=True)
    return subprocess.call(args, cwd=REPO)


def _scenes() -> list[Path]:
    return sorted(FIXTURES.glob("scenes/**/scene.json"))


def _verdicts() -> list[Path]:
    return sorted(FIXTURES.glob("verdicts/**/*.json"))


def _labels() -> list[Path]:
    return sorted(FIXTURES.glob("labels/*.json"))


def _packet_manifests() -> list[Path]:
    """Every committed manifest. `mini-arkit` is named explicitly (SD-43), never by glob."""

    found = sorted(FIXTURES.glob("packets/*/packet.*.json"))
    found.append(FIXTURES / "packets" / "mini-arkit" / "packet.json")
    found.extend(sorted((FIXTURES / "packets" / "examples").glob("*.json")))
    return found


def _drop_labels_file(document: dict) -> dict:
    copy = dict(document)
    copy.pop("labels_file", None)
    return copy


def _validate_committed() -> int:
    files = _scenes() + _verdicts() + _labels()
    if not files:
        print("no committed scenes, verdicts or labels", file=sys.stderr)
        return 1
    code = _run(PY + ["server.eval.cli", "validate", *[str(path.relative_to(REPO)) for path in files]])
    if code != 0:
        return code
    manifests = _packet_manifests()
    missing = [str(path) for path in manifests if not path.is_file()]
    if missing:
        print("missing manifests: " + " ".join(missing), file=sys.stderr)
        return 1
    code = _run(
        PY
        + ["server.contracts.cli", "check-packets", "--no-files", *[str(path.relative_to(REPO)) for path in manifests]]
    )
    if code != 0:
        return code
    print(
        "validate ok "
        f"scenes={len(_scenes())} verdicts={len(_verdicts())} "
        f"labels={len(_labels())} packets={len(manifests)}"
    )
    return 0


def _score_self() -> int:
    sites = ["eth3d-facade", "eth3d-electro", "eth3d-delivery"]
    for site in sites:
        scene = FIXTURES / "scenes" / site / "gt-laser" / "scene.json"
        ply = scene.parent / "laser.ply"
        if not ply.is_file():
            print(f"missing: {site}-laser", file=sys.stderr)
            return 2
        out = REPO / ".fixtures" / "scores" / site / "self"
        code = _run(
            PY
            + [
                "server.eval.cli",
                "score-geometry",
                "--gt",
                str(scene.relative_to(REPO)),
                "--recon",
                str(scene.relative_to(REPO)),
                "--out",
                str(out.relative_to(REPO)),
            ]
        )
        if code != 0:
            return code
        if site != "eth3d-facade":
            continue
        summary = json.loads((out / "geomscore.json").read_text())
        reference_path = FIXTURES / "reference-scores" / "eth3d-facade" / "geomscore-self.json"
        reference = json.loads(reference_path.read_text())
        overall = summary["key_distances"]["overall"]
        scale = summary["scale"]["scale_error_pct"]
        # The brief's n==55 is status_counts.scored. overall.n is 53 in the committed reference.
        if summary["key_distances"] != reference["key_distances"]:
            print(
                f"facade self-check key_distances differ from {reference_path.relative_to(REPO)}",
                file=sys.stderr,
            )
            return 1
        if overall["median_in"] != 0 or overall["p90_in"] != 0 or scale != 0:
            print(f"facade self-check {overall} scale_error_pct={scale}", file=sys.stderr)
            return 1
    return 0


def _score_verdicts() -> int:
    pairs = sorted((FIXTURES / "reference-scores").glob("*/*/*.json"))
    # verdictscores live one directory down; geomscore files are siblings and are not re-scored here.
    pairs = [path for path in pairs if path.parent.name == "verdictscores"]
    if not pairs:
        print("no reference verdict scores", file=sys.stderr)
        return 1
    for reference_path in pairs:
        site = reference_path.parents[1].name
        verdict_name = reference_path.name.split("__", 1)[0] + ".json"
        labels_name = reference_path.name.split("__", 1)[1].removesuffix(".json") + ".json"
        verdict = FIXTURES / "verdicts" / site / verdict_name
        labels = FIXTURES / "labels" / labels_name
        if not verdict.is_file() or not labels.is_file():
            print(f"missing score inputs for {reference_path.name}", file=sys.stderr)
            return 1
        out = REPO / ".fixtures" / "scores" / "verdicts" / site
        code = _run(
            PY
            + [
                "server.eval.cli",
                "score-verdicts",
                "--verdict",
                str(verdict.relative_to(REPO)),
                "--labels",
                str(labels.relative_to(REPO)),
                "--out",
                str(out.relative_to(REPO)),
            ]
        )
        if code != 0:
            return code
        produced = json.loads((out / reference_path.name).read_text())
        reference = json.loads(reference_path.read_text())
        if _drop_labels_file(produced) != _drop_labels_file(reference):
            print(f"score drift {reference_path.relative_to(REPO)}", file=sys.stderr)
            return 1
    return 0


def _check_extracted() -> int:
    cache = REPO / ".fixtures" / "packets"
    if not cache.is_dir():
        print("missing: extracted packets (make fixtures ONLY=all)", file=sys.stderr)
        return 2
    manifests = sorted(cache.glob("*/packet.json")) + sorted(cache.glob("*/packet.*.json"))
    if not manifests:
        print("missing: extracted packets", file=sys.stderr)
        return 2
    return _run(
        PY + ["server.contracts.cli", "check-packets", "--files", *[str(path.relative_to(REPO)) for path in manifests]]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args(argv)
    code = _validate_committed()
    if code != 0 or not args.full:
        return code
    for step in (_check_extracted, _score_self, _score_verdicts):
        code = step()
        if code != 0:
            return code
    print("validate-full ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
