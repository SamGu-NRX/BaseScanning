#!/usr/bin/env python3
"""`make validate` / `make validate-full` for the committed golden fixtures."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
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


CAPTURE_JOURNEY_PACKET_ID = "mini-arkit"


def capture_journey_errors(fixtures_dir: Path) -> list[str]:
    """Named missing-input failures for the packet the capture journey test drives.

    The committed packet check runs with --no-files, so the keyframes and streams
    under data/fixtures/packets/mini-arkit were never re-verified: a file that left
    the repository passed `make validate` and only surfaced as a raw FileNotFoundError
    inside tests/test_capture_fixture_flow.py. The manifest now names the packet and
    every file it references; this guard fails loudly, by name, when they disagree
    with what is on disk.
    """

    document = json.loads((fixtures_dir / "manifest.json").read_text())
    items = {item["id"]: item for item in document["items"]}
    packet_item = items.get(CAPTURE_JOURNEY_PACKET_ID)
    if packet_item is None or packet_item.get("kind") != "packet_dir":
        return [f"{CAPTURE_JOURNEY_PACKET_ID}: no packet_dir item in the fixture manifest"]

    packet_dir = fixtures_dir / Path(packet_item["repo_path"]).relative_to("data/fixtures")
    packet_path = packet_dir / "packet.json"
    errors: list[str] = []
    packet_files: dict[str, int] = {}
    if not packet_path.is_file():
        return [f"{CAPTURE_JOURNEY_PACKET_ID}: missing input {packet_item['repo_path']}/packet.json"]
    packet = json.loads(packet_path.read_text())
    for index, entry in enumerate(packet["files"]):
        packet_files[entry["path"]] = index

    def _describe(item: dict) -> str:
        return f"manifest item {item['id']}"

    # The packet_dir entry itself pins packet.json; keep that hash guarded too.
    raw = packet_path.read_bytes()
    if len(raw) != int(packet_item["bytes"]) or hashlib.sha256(raw).hexdigest() != packet_item["sha256"]:
        errors.append(f"{CAPTURE_JOURNEY_PACKET_ID}: packet.json does not match its {_describe(packet_item)}")

    named: dict[str, dict] = {}
    prefix = packet_item["repo_path"] + "/"
    for item in document["items"]:
        repo_path = item.get("repo_path")
        if item is packet_item or not repo_path or not str(repo_path).startswith(prefix):
            continue
        relative = str(repo_path)[len(prefix) :]
        named[relative] = item
        path = fixtures_dir / Path(repo_path).relative_to("data/fixtures")
        if not path.is_file():
            errors.append(
                f"{CAPTURE_JOURNEY_PACKET_ID}: missing input {repo_path} ({_describe(item)}, "
                f"packet.json /files/{packet_files[relative]})"
                if relative in packet_files
                else f"{CAPTURE_JOURNEY_PACKET_ID}: missing input {repo_path} ({_describe(item)})"
            )
            continue
        data = path.read_bytes()
        if len(data) != int(item["bytes"]) or hashlib.sha256(data).hexdigest() != item["sha256"]:
            errors.append(f"{CAPTURE_JOURNEY_PACKET_ID}: {repo_path} does not match {_describe(item)}")

    for relative, index in sorted(packet_files.items(), key=lambda pair: pair[1]):
        if relative not in named:
            errors.append(
                f"{CAPTURE_JOURNEY_PACKET_ID}: packet.json /files/{index} references {relative}, "
                "which the fixture manifest does not name"
            )
    return errors


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
    journey_errors = capture_journey_errors(FIXTURES)
    if journey_errors:
        print("capture-journey fixture guard failed:", file=sys.stderr)
        for error in journey_errors:
            print("  " + error, file=sys.stderr)
        return 1
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


def _refresh_extracted_packets() -> None:
    """Re-extract cached packets whose files disagree with the fixture manifest."""

    path = REPO / "scripts" / "fetch_fixtures.py"
    spec = importlib.util.spec_from_file_location("fetch_fixtures", path)
    if spec is None or spec.loader is None:
        print("missing: scripts/fetch_fixtures.py", file=sys.stderr)
        raise SystemExit(3)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    document = json.loads((REPO / "data" / "fixtures" / "manifest.json").read_text())
    source = os.environ.get("FIXTURE_SOURCE") or module.default_fixture_source()
    module.refresh_present_extractions(REPO / ".fixtures", source, document["items"], done=set())


def _check_extracted() -> int:
    cache = REPO / ".fixtures" / "packets"
    if not cache.is_dir():
        print("missing: extracted packets (make fixtures ONLY=all)", file=sys.stderr)
        return 2
    _refresh_extracted_packets()
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
