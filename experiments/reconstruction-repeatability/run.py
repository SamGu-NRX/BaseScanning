"""Reconstruction repeatability: stable wrong walls versus history-dependent walls.

Two failures look alike in a single output and are different diseases. A stable wrong wall is
deterministically wrong: an input transformation that should preserve the reconstruction changes
it, and reruns reproduce the wrong answer exactly. A history-dependent wall depends on what ran
before on the same work directory — cache state, a killed run — rather than on declared inputs,
so two honest runs on the same inputs can disagree.

Run (from the repository root; offline, no model, no network):

    uv run --project experiments/reconstruction-repeatability python \
        experiments/reconstruction-repeatability/run.py \
        --manifest experiments/reconstruction-repeatability/manifest.json

    uv run --project experiments/reconstruction-repeatability python \
        experiments/reconstruction-repeatability/run.py \
        --replay experiments/reconstruction-repeatability/results

The manifest freezes the base commit, the source hashes this run evaluated and the declared
tolerances, and names the cases. Every case builds synthetic scan bundles (bundles.py), pushes
them through `pipeline.run` with the depth mode a photos-only phone takes, and compares the
outputs the caller would ship: geometry.json, scene.json, coverage.json, report.md and the mesh.
The MoGe-2 model is stubbed, never run; `recon.depth.rescale` is replaced with an identity pass.
Records land in results/reconstruction_repeatability.json and .md; `--replay` re-runs every case
and diffs the fresh records against the committed ones.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

EXPERIMENT = Path(__file__).resolve().parent
REPO = EXPERIMENT.parents[1]
sys.path.insert(0, str(REPO / "recon"))
sys.path.insert(0, str(EXPERIMENT))

from recon import capture as cap  # noqa: E402
from recon import depth, pipeline  # noqa: E402

import bundles  # noqa: E402
import compare  # noqa: E402

# --- the stubbed model ---------------------------------------------------------------

_ORIGINAL_RUN = subprocess.run
_ORIGINAL_RESCALE = depth.rescale


def _identity_rescale(capture, depths):
    """The patched rescale: the stub's maps are already metric. Deterministic in the depths and
    poses, holds no history, so the bypass cannot hide a history effect (recorded in the README)."""
    report = {
        "fitted": 0,
        "frames": len(depths),
        "median_scale": 1.0,
        "scale_range": [1.0, 1.0],
        "per_frame": {},
    }
    return depths, report


def install_stub(fail_after: int | None = None) -> dict:
    """Replace the MoGe-2 subprocess with the analytic stub and rescale with identity. The same
    module object backs depth.subprocess, so one assignment covers both spellings."""
    run, calls = bundles.make_stub_model(fail_after)
    subprocess.run = run
    depth.rescale = _identity_rescale
    return calls


def restore_stub() -> None:
    subprocess.run = _ORIGINAL_RUN
    depth.rescale = _ORIGINAL_RESCALE


# --- one pipeline run and the comparisons ---------------------------------------------


def run_pipeline(bundle: Path, out: Path, work: Path, calls: dict) -> dict:
    """pipeline.run with the stubbed model, reduced to the outputs the caller ships."""
    before = calls["n"]
    pipeline.run(bundle, out, work, "moge", None, False)
    outputs = compare.collect_outputs(out)
    outputs["model_calls"] = calls["n"] - before
    outputs["cache_folders"] = sorted(p.name for p in (work / "moge2").iterdir() if p.is_dir())
    return outputs


def json_diff_ft(a: dict, b: dict) -> float:
    return compare.json_diff_ft(a, b)


def outputs_equal(a: dict, b: dict) -> bool:
    return compare.outputs_equal(a, b)


# --- the cases ------------------------------------------------------------------------


def case_baseline_repeat(tmp: Path, case: dict) -> dict:
    """The same bundle, two fresh work directories. The floor every other case stands on."""
    bundle = bundles.write_bundle(tmp / "bundle")
    calls = install_stub()
    o1 = run_pipeline(bundle, tmp / "out1", tmp / "work1", calls)
    o2 = run_pipeline(bundle, tmp / "out2", tmp / "work2", calls)
    restore_stub()
    return finish(
        case, o1, o2, notes=[f"model calls per run: {o1['model_calls']}, {o2['model_calls']}"]
    )


def case_warm_cache(tmp: Path, case: dict) -> dict:
    """The same bundle twice in one work directory: the second run hits the content-keyed cache."""
    bundle = bundles.write_bundle(tmp / "bundle")
    calls = install_stub()
    o1 = run_pipeline(bundle, tmp / "out1", tmp / "work", calls)
    o2 = run_pipeline(bundle, tmp / "out2", tmp / "work", calls)
    restore_stub()
    return finish(
        case,
        o1,
        o2,
        notes=[
            f"model calls per run: {o1['model_calls']}, {o2['model_calls']}",
            f"cache folders: {o2['cache_folders']}",
        ],
    )


def case_frames_reordered(tmp: Path, case: dict) -> dict:
    """The same frames with scene.json's keyframes reversed. Frame ids key everything but the
    fusion loop, which sums in list order; the wall must survive the float noise."""
    bundle = bundles.write_bundle(tmp / "bundle")
    reversed_ = bundles.write_bundle(tmp / "bundle-rev", reverse=True)
    calls = install_stub()
    o1 = run_pipeline(bundle, tmp / "out1", tmp / "work1", calls)
    o2 = run_pipeline(reversed_, tmp / "out2", tmp / "work2", calls)
    restore_stub()
    return finish(
        case,
        o1,
        o2,
        notes=[
            f"cache folders: {o1['cache_folders']} vs {o2['cache_folders']} (order is in the key)",
        ],
    )


def case_frame_id_duplicate(tmp: Path, case: dict) -> dict:
    """A keyframe record twice: one depth view counted from two camera positions would fake
    two-view coverage. The capture refuses it before any reconstruction."""
    bundle = bundles.write_bundle(tmp / "bundle", duplicate=True)
    install_stub()
    try:
        cap.load(bundle, tmp / "work")
    except cap.DuplicateFrameId as e:
        restore_stub()
        return {
            **case,
            "verdict": "refused",
            # The message embeds the case's scratch path, which is random per run — scrub
            # it so the committed record replays byte-for-byte.
            "observed": {"refusal": type(e).__name__, "message": str(e).replace(str(tmp), "<tmp>")},
            "notes": [],
        }
    restore_stub()
    raise AssertionError("a duplicate keyframe id was not refused")


def case_image_reencoded(tmp: Path, case: dict) -> dict:
    """The same pixels re-encoded: bytes change, so the key changes and the cache must recompute.
    With the stub deterministic in the frame id, the recomputed wall must be the same one. (A real
    model's invariance to recompression is not claimed and not tested here.)"""
    bundle = bundles.write_bundle(tmp / "bundle")
    calls = install_stub()
    o1 = run_pipeline(bundle, tmp / "out1", tmp / "work1", calls)
    bundles.reencode_jpegs(bundle)
    o2 = run_pipeline(bundle, tmp / "out2", tmp / "work2", calls)
    restore_stub()
    return finish(
        case,
        o1,
        o2,
        notes=[
            f"model calls per run: {o1['model_calls']}, {o2['model_calls']} (recompute evidence)",
            f"cache folders: {o1['cache_folders']} vs {o2['cache_folders']}",
            f"mesh digest: {o1[compare.GLB][:12]} vs {o2[compare.GLB][:12]} (vertex colors carry "
            "the re-encoded image bytes; a JPEG re-encode also drifts the decoded pixels)",
        ],
    )


def case_intrinsics_shift(tmp: Path, case: dict) -> dict:
    """Shifted intrinsics: a declared input, so the result may differ — measured, not graded. The
    key must still change (no stale reuse) with the stubbed maps held fixed."""
    bundle = bundles.write_bundle(tmp / "bundle")
    shifted = bundles.write_bundle(
        tmp / "bundle-k",
        k=(bundles.K[0] + 4.0, bundles.K[1] + 4.0, bundles.K[2] + 2.0, bundles.K[3] + 2.0),
    )
    calls = install_stub()
    o1 = run_pipeline(bundle, tmp / "out1", tmp / "work1", calls)
    o2 = run_pipeline(shifted, tmp / "out2", tmp / "work2", calls)
    restore_stub()
    diff = json_diff_ft(o1, o2)
    reuse = set(o1["cache_folders"]) & set(o2["cache_folders"])
    return {
        **case,
        "verdict": "measured",
        "observed": {
            "max_numeric_diff": diff,
            "wall_metric_runs": [o1["wall"], o2["wall"]],
            "notes": [
                f"model calls per run: {o1['model_calls']}, {o2['model_calls']}",
                f"shared cache folders (must be empty): {sorted(reuse)}",
            ],
        },
    }


def case_unrelated_history(tmp: Path, case: dict) -> dict:
    """Another capture through the same work directory between two runs of this one. Cache
    folders are keyed by capture content, so the other capture must not touch this wall."""
    bundle = bundles.write_bundle(tmp / "bundle")
    other = bundles.write_bundle(tmp / "bundle-other", xs_shift=0.25)
    calls = install_stub()
    o1 = run_pipeline(bundle, tmp / "out1", tmp / "work", calls)
    o_mid = run_pipeline(other, tmp / "out-other", tmp / "work", calls)
    o2 = run_pipeline(bundle, tmp / "out2", tmp / "work", calls)
    restore_stub()
    return finish(
        case,
        o1,
        o2,
        notes=[
            f"cache folders after all three runs: {o2['cache_folders']}",
            f"model calls per run: {o1['model_calls']}, {o_mid['model_calls']}, {o2['model_calls']}",
        ],
    )


def case_stale_cache_pose_edit(tmp: Path, case: dict) -> dict:
    """A keyframe moves after a first run. The key mismatch must send the rerun to a fresh folder,
    never back to the stale maps: the rerun must equal a fresh run of the edited bundle."""
    bundle = bundles.write_bundle(tmp / "bundle")
    calls = install_stub()
    run_pipeline(bundle, tmp / "out-warm", tmp / "work", calls)
    bundles.edit_pose(bundle, "k1", 0.05)
    reference = run_pipeline(bundle, tmp / "out-fresh", tmp / "work-fresh", calls)
    stale = run_pipeline(bundle, tmp / "out-stale", tmp / "work", calls)
    restore_stub()
    return finish(
        case,
        reference,
        stale,
        notes=[
            f"cache folders after the stale rerun: {stale['cache_folders']} (the orphan stays)",
        ],
    )


def case_interrupted_run(tmp: Path, case: dict) -> dict:
    """A model process killed after its first map (the worker's key file is already written). The
    rerun must finish the job and land on the uninterrupted result."""
    bundle = bundles.write_bundle(tmp / "bundle")
    calls = install_stub(fail_after=1)
    try:
        pipeline.run(bundle, tmp / "out-x", tmp / "work-int", "moge", None, False)
    except RuntimeError as e:
        interrupted = str(e)
    else:
        raise AssertionError("the interrupted run did not fail")
    calls = install_stub()
    reference = run_pipeline(bundle, tmp / "out-fresh", tmp / "work-fresh", calls)
    resumed = run_pipeline(bundle, tmp / "out-resumed", tmp / "work-int", calls)
    restore_stub()
    return finish(
        case,
        reference,
        resumed,
        notes=[
            f"interruption: {interrupted}",
            f"model calls, resumed run: {resumed['model_calls']} (the missing maps only)",
            f"cache folders after the resume: {resumed['cache_folders']}",
        ],
    )


def case_same_size_cache_edit(tmp: Path, case: dict) -> dict:
    """One cached map overwritten with a shifted copy: same shape, same dtype, key.json untouched.
    Declared guarded (the rerun must equal the baseline); whatever the rerun returns is the
    history-dependent wall. A third run records whether the wrong answer is itself stable."""
    bundle = bundles.write_bundle(tmp / "bundle")
    calls = install_stub()
    baseline = run_pipeline(bundle, tmp / "out1", tmp / "work-tamper", calls)
    tampered = bundles.poison_cache(tmp / "work-tamper", "k1")
    poisoned = run_pipeline(bundle, tmp / "out2", tmp / "work-tamper", calls)
    again = run_pipeline(bundle, tmp / "out3", tmp / "work-tamper", calls)
    restore_stub()
    record = finish(
        case,
        baseline,
        poisoned,
        notes=[
            f"tampered file: {tampered.name} in {tampered.parent.name} (key.json untouched)",
            f"model calls per run: {baseline['model_calls']}, {poisoned['model_calls']}",
        ],
    )
    record["observed"]["stable_when_repeated"] = outputs_equal(poisoned, again)
    record["observed"]["notes"] = record["observed"]["notes"] + [
        f"third run on the same tampered cache reproduces the second: "
        f"{record['observed']['stable_when_repeated']}"
    ]
    return record


RUNNERS = {
    "baseline-repeat": case_baseline_repeat,
    "warm-cache": case_warm_cache,
    "frames-reordered": case_frames_reordered,
    "frame-id-duplicate": case_frame_id_duplicate,
    "image-reencoded": case_image_reencoded,
    "intrinsics-shift": case_intrinsics_shift,
    "unrelated-history": case_unrelated_history,
    "stale-cache-pose-edit": case_stale_cache_pose_edit,
    "interrupted-run": case_interrupted_run,
    "same-size-cache-edit": case_same_size_cache_edit,
}


def finish(case: dict, reference: dict, other: dict, notes: list[str]) -> dict:
    """Grade a two-run case against its declared check."""
    same = outputs_equal(reference, other)
    diff = json_diff_ft(reference, other)
    check = case["check"]
    if check == "outputs_identical":
        ok = same
    elif check == "json_identical":
        # The JSON outputs must be byte-identical; the mesh digest is exempt (vertex colors
        # carry the input image's bytes, which the case deliberately changed).
        ok = all(reference[n] == other[n] for n in compare.JSON_FILES)
    elif check == "wall_within":
        ok = diff <= case["tolerance_ft"]
    else:
        raise AssertionError(f"unknown check {check!r}")
    verdict = "pass" if ok else "fail"
    return {
        **case,
        "verdict": verdict,
        "observed": {
            "outputs_equal": same,
            "max_numeric_diff": None if diff == float("inf") else diff,
            "wall_metric_runs": [reference["wall"], other["wall"]],
            "notes": notes,
        },
    }


# --- manifest in, results out ---------------------------------------------------------


def run_manifest(manifest_path: Path, results_dir: Path) -> dict:
    manifest = json.loads(manifest_path.read_text())
    names = [c["name"] for c in manifest["cases"]]
    assert sorted(names) == sorted(RUNNERS), f"manifest cases {names} != runners {sorted(RUNNERS)}"
    records = []
    with tempfile.TemporaryDirectory(prefix="recon-repeatability-") as td:
        for case in manifest["cases"]:
            # Each case gets its own scratch space: cases share bundles and work-directory
            # names, and a warm folder left by an earlier case would fake a cache hit or
            # pollute the "shared cache folders" evidence.
            tmp = Path(td) / case["name"]
            tmp.mkdir()
            print(f"recon-repeatability: case {case['name']} ({case['class']})", file=sys.stderr)
            records.append(RUNNERS[case["name"]](tmp, case))
    doc = {
        "manifest": str(manifest_path),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "frozen": manifest["frozen"],
        "tolerances": manifest["tolerances"],
        "cases": records,
        "summary": summary(records),
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "reconstruction_repeatability.json").write_text(json.dumps(doc, indent=1))
    (results_dir / "reconstruction_repeatability.md").write_text(markdown(doc, replay=None))
    return doc


def summary(records: list[dict]) -> dict:
    by_verdict: dict[str, list[str]] = {}
    for r in records:
        by_verdict.setdefault(r["verdict"], []).append(r["name"])
    return {"verdicts": {k: v for k, v in sorted(by_verdict.items())}}


def markdown(doc: dict, replay: dict | None) -> str:
    lines = [
        "# Reconstruction repeatability",
        "",
        "Stable wrong walls versus history-dependent walls in `recon`, evaluated at "
        f"`{doc['frozen']['base_commit'][:12]}` ({doc['frozen']['branch_base']}).",
        "",
        "- **The depth model was not exercised.** MoGe-2 is stubbed (analytic maps, deterministic "
        "in the frame id); no weights, no GPU, no network. `recon.depth.rescale` is an identity "
        "pass. The real `moge_cache` key path, the cache files and the full pipeline are the "
        "actual code.",
        "- Evaluates Hunter's recon at PR #220's snapshot; nothing here evaluates the server "
        "branch, where recon is absent.",
        "",
        "| Case | Class | Check | Verdict | Observed |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in doc["cases"]:
        observed = r["observed"]
        if "refusal" in observed:
            cell = f"refused as {observed['refusal']}"
        else:
            diff = observed["max_numeric_diff"]
            cell = (
                "outputs byte-identical" if observed.get("outputs_equal") else f"max diff {diff} ft"
            )
            if r["verdict"] == "measured":
                cell += " (measured, not graded)"
        if "stable_when_repeated" in observed:
            cell += f"; stable when repeated: {observed['stable_when_repeated']}"
        lines.append(f"| {r['name']} | {r['class']} | {r['check']} | {r['verdict']} | {cell} |")
    if replay is not None:
        lines += [
            "",
            "## Replay",
            "",
            f"Replayed against the committed records ({replay['manifest_sha256'][:12]}): "
            f"{replay['agreed']} of {replay['total']} cases agree exactly.",
            "",
            "| Case | Agreement |",
            "| --- | --- |",
        ]
        for c in replay["cases"]:
            lines.append(f"| {c['name']} | {c['agreement']} |")
    return "\n".join(lines) + "\n"


def replay(results_dir: Path) -> dict:
    """Re-run every case and diff the fresh records against the committed ones."""
    path = results_dir / "reconstruction_repeatability.json"
    committed = json.loads(path.read_text())
    manifest_path = Path(committed["manifest"])
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if digest != committed["manifest_sha256"]:
        raise AssertionError(
            f"{manifest_path} changed since the committed run ({digest} != "
            f"{committed['manifest_sha256']}); the replay would not test the frozen contract"
        )
    cases = []
    with tempfile.TemporaryDirectory(prefix="recon-repeatability-replay-") as td:
        fresh = run_manifest(manifest_path, Path(td))
    for old, new in zip(committed["cases"], fresh["cases"], strict=False):
        agreement = "agree" if old == new else "DIFFER"
        cases.append({"name": old["name"], "agreement": agreement})
    replay_doc = {
        "manifest_sha256": digest,
        "total": len(cases),
        "agreed": sum(c["agreement"] == "agree" for c in cases),
        "cases": cases,
    }
    (results_dir / "replay.json").write_text(json.dumps(replay_doc, indent=1))
    (results_dir / "replay.md").write_text(markdown(committed, replay_doc))
    return replay_doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", type=Path, help="run every case in the manifest")
    ap.add_argument("--replay", type=Path, help="results dir: re-run and diff against committed")
    args = ap.parse_args()
    if (args.manifest is None) == (args.replay is None):
        ap.error("exactly one of --manifest / --replay")
    try:
        if args.manifest is not None:
            run_manifest(args.manifest, args.manifest.parent / "results")
        else:
            doc = replay(args.replay)
            print(
                f"recon-repeatability: replay agreed on {doc['agreed']} of {doc['total']} cases",
                file=sys.stderr,
            )
    finally:
        restore_stub()


if __name__ == "__main__":
    main()
