"""The first four real stages on a real app-format capture, on CPU.

The dev journey runs every stage behind stubs (`journey.json` answers come from
stand-ins) and backend/docs/STATUS.md records that every real stage behind the
stubs is unverified. This module runs the real `validate`, `poses`, `reads` and
`scale` entries, in order, on the committed app-format packet
`ios-kit/fixtures/mini-arkit-lidar` (six ETH3D electro frames at 1920x1440,
ARKit poses, LiDAR depth and confidence, one reference-point tap), with
`ctx.gpu = None` — exactly what the CPU worker passes. A second chain runs the
same stages on `data/fixtures/packets/mini-arkit`, whose 64x48 frames carry no
depth, and pins the loud refusal that packet gives instead of a scale claim.

The StageContexts are built the way the orchestrator builds them (see
`server/orchestrator/runner.py` and `server/orchestrator/artifacts.py`): the
packet lands in a local `packet_dir`, artifacts are published to storage under
`captures/{capture_id}/runs/{run_id}/`, and each later stage's work dir is
hydrated from `graph.STAGE_INPUTS` before its entry runs.

Resource budget: each chain runs in its own process, started through
`realcpu_launcher.py`, pinned to two CPUs, with `OMP_NUM_THREADS`,
`OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS` and `HSS_POSES_THREADS` set to 2 in
that process only. A stage over 600 s or a peak RSS over 8 GiB fails the budget
the target host (2 CPUs, 8 GB) imposes. The peak RSS is the chain process's
own, so earlier tests in the same pytest run cannot raise it. Where the OS
cannot pin CPUs (macOS), the correctness and RSS checks still run and the test
skips the wall-time budget instead of claiming it.

No weights, no network, no GPU: the VLM readers stay off
(`VISION_EXTERNAL_CALLS`), so `reads` runs only the local barcode decoder, and
`poses` triangulates over the ARKit priors with pycolmap on CPU.
"""

from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from server.contracts.jobs import StageContext, StageResult
from server.orchestrator.graph import STAGE_INPUTS, STAGES
from server.orchestrator.runner import _publish
from server.packet import run as validate_run
from server.poses import run as poses_run
from server.reads import run as reads_run
from server.reads.cache import MemoryStorage
from server.scale.run import execute as scale_run

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = Path(__file__).with_name("realcpu_launcher.py")
LIDAR_PACKET = ROOT / "ios-kit" / "fixtures" / "mini-arkit-lidar"
MINI_ARKIT_PACKET = ROOT / "data" / "fixtures" / "packets" / "mini-arkit"

WALL_BUDGET_S = 600.0
RSS_BUDGET_MB = 8 * 1024
THREAD_ENV = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "HSS_POSES_THREADS")

STAGE_ENTRY = {
    "validate": validate_run,
    "poses": poses_run,
    "reads": reads_run,
    "scale": scale_run,
}


class _Settings:
    """The settings the coordinator passes on the CPU path: external vision off."""

    VISION_EXTERNAL_CALLS = False


def _peak_rss_mb() -> float:
    """This process's peak RSS so far, in MiB. Monotonic, so a stage's entry is the
    high-water mark through it. ru_maxrss counts KiB on Linux and bytes on macOS."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def _proc_vm_hwm_mb() -> float:
    """Linux's own peak-RSS figure for this process (VmHWM, in kB), to cross-check `_peak_rss_mb`."""
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmHWM:"):
            return int(line.split()[1]) / 1024
    raise AssertionError("/proc/self/status has no VmHWM line")


def _ctx(
    stage: str,
    work_dir: Path,
    packet_dir: Path,
    storage: MemoryStorage,
    capture_id: str,
    run_id: str,
    branch: str,
    tier: str,
) -> StageContext:
    """The StageContext the coordinator hands a CPU stage entry."""
    return StageContext(
        capture_id=capture_id,
        run_id=run_id,
        stage=stage,  # type: ignore[arg-type]
        attempt=1,
        branch=branch,  # type: ignore[arg-type]
        flow="guided",
        tier=tier,  # type: ignore[arg-type]
        work_dir=work_dir,
        inputs={"packet_dir": str(packet_dir)},
        deadline_at="2026-10-17T00:00:00Z",
        heartbeat=lambda _substep: None,
        gpu=None,
        vlm_budget_usd=0.40,
        storage=storage,
        state=None,
        settings=_Settings(),
    )


def _seed_stills(storage: MemoryStorage, capture_id: str, packet_dir: Path) -> None:
    """Promotion stores still bytes under `captures/{capture_id}/`; reads reads them
    from storage (`server/reads/aggregate.py` builds every still path that way), never
    from packet_dir."""
    packet = json.loads((packet_dir / "packet.json").read_text())
    rows = list(packet.get("stills") or [])
    for frame in packet.get("keyframes") or []:
        if isinstance(frame, dict) and frame.get("purpose"):
            rows.append(frame)
    for row in rows:
        img = row.get("img") or (row.get("still") or {}).get("img")
        if not img:
            continue
        src = packet_dir / img
        if src.is_file():
            storage.put_bytes(f"captures/{capture_id}/{img}", src.read_bytes(), "image/jpeg")


def _hydrate(work_dir: Path, keys: tuple[str, ...], storage: MemoryStorage, prefix: str) -> None:
    """Copy the run's published artifact bytes into a stage work dir, the way the
    coordinator hydrates `graph.STAGE_INPUTS` before spawning a worker."""
    for key in keys:
        sk = f"{prefix}/{key}"
        if key.endswith("/"):
            for obj in storage.list_prefix(sk):
                if obj.endswith("/.keep"):
                    continue
                dest = work_dir / obj[len(prefix) + 1 :]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(storage.get_bytes(obj))
        elif storage.exists(sk) and storage.get_bytes(sk):
            dest = work_dir / key
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(storage.get_bytes(sk))


def _run_chain(
    work_root: Path,
    packet_dir: Path,
    capture_id: str,
    run_id: str,
    branch: str,
    tier: str,
) -> dict[str, dict[str, Any]]:
    """validate -> poses -> reads -> scale, each stage real, each publish real."""
    storage = MemoryStorage()
    prefix = f"captures/{capture_id}/runs/{run_id}"
    _seed_stills(storage, capture_id, packet_dir)
    report: dict[str, dict[str, Any]] = {}
    for stage, fn in STAGE_ENTRY.items():
        work_dir = work_root / stage
        work_dir.mkdir(parents=True, exist_ok=True)
        _hydrate(work_dir, STAGE_INPUTS[stage], storage, prefix)
        started = time.perf_counter()
        result: StageResult = fn(_ctx(stage, work_dir, packet_dir, storage, capture_id, run_id, branch, tier))
        wall_s = time.perf_counter() - started
        stored = _publish(_ctx(stage, work_dir, packet_dir, storage, capture_id, run_id, branch, tier), result)
        report[stage] = {
            "wall_s": round(wall_s, 2),
            "peak_rss_mb": round(_peak_rss_mb(), 1),
            "artifacts": sorted(stored),
            "metrics": {k: round(float(v), 4) for k, v in result.metrics.items()},
            "warnings": list(result.warnings),
        }
    return report


def chain_in_child(
    out_json: str, packet_dir: str, work_root: str, capture_id: str, run_id: str, branch: str, tier: str
) -> None:
    """Runs inside the launcher's process. Writes the stage report plus the CPUs
    and thread settings the chain actually ran with."""
    stages = _run_chain(Path(work_root), Path(packet_dir), capture_id, run_id, branch, tier)
    cpus = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
    threads = {key: os.environ.get(key) for key in THREAD_ENV}
    Path(out_json).write_text(json.dumps({"stages": stages, "cpus": cpus, "threads": threads}))


def rss_probe_in_child(out_json: str) -> None:
    """Runs inside the launcher's process. Touches 64 MiB, then reads both peak figures."""
    block = bytearray(64 * 1024 * 1024)
    block[::4096] = b"\x01" * (len(block) // 4096)
    Path(out_json).write_text(json.dumps({"peak_mb": _peak_rss_mb(), "vm_hwm_mb": _proc_vm_hwm_mb()}))
    del block


def _child_env(request: pytest.FixtureRequest, threads: str | None) -> dict[str, str]:
    """The environment for a child Python: pytest's `pythonpath` entries, and the
    thread variables set to `threads`, or removed when it is None."""
    env = {k: v for k, v in os.environ.items() if k not in THREAD_ENV}
    if threads is not None:
        env.update(dict.fromkeys(THREAD_ENV, threads))
    paths = [str(p) for p in request.config.getini("pythonpath")]
    if os.environ.get("PYTHONPATH"):
        paths.append(os.environ["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env


def _in_fresh_process(request: pytest.FixtureRequest, out_json: Path, function: str, *args: str) -> dict[str, Any]:
    """Run `function` from this module through `realcpu_launcher.py` (two CPUs,
    two threads per numeric library, a peak-RSS counter of its own) and return
    the JSON it wrote to `out_json`."""
    proc = subprocess.run(
        [sys.executable, str(LAUNCHER), __file__, function, str(out_json), *args],
        cwd=ROOT,
        env=_child_env(request, threads="2"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"{function} failed in its own process (exit {proc.returncode}):\n{proc.stderr[-6000:]}"
    )
    return json.loads(out_json.read_text())


def _chain(
    request: pytest.FixtureRequest,
    work_root: Path,
    packet_dir: Path,
    capture_id: str,
    run_id: str,
    branch: str,
    tier: str,
) -> dict[str, Any]:
    run = _in_fresh_process(
        request,
        work_root / "run.json",
        "chain_in_child",
        str(packet_dir),
        str(work_root),
        capture_id,
        run_id,
        branch,
        tier,
    )
    if os.environ.get("REALCPU_REPORT"):
        print(f"REALCPU_REPORT {json.dumps({'packet': packet_dir.name, **run}, sort_keys=True)}")
    return run


def _assert_budget(run: dict[str, Any]) -> None:
    """Called last in each chain test, because it skips where the wall budget cannot be measured."""
    assert run["threads"] == dict.fromkeys(THREAD_ENV, "2"), run["threads"]
    for stage, entry in run["stages"].items():
        peak = entry["peak_rss_mb"]
        assert 0 < peak < RSS_BUDGET_MB, f"{stage}: peak RSS {peak} MB is outside the 8 GB budget"
    if run["cpus"] is None:
        pytest.skip(f"checks passed, but the 2-CPU wall budget is unmeasured: {sys.platform} cannot pin CPUs")
    assert 1 <= len(run["cpus"]) <= 2, f"the chain ran on CPUs {run['cpus']}, not two"
    for stage, entry in run["stages"].items():
        assert entry["wall_s"] < WALL_BUDGET_S, f"{stage} took {entry['wall_s']}s (budget {WALL_BUDGET_S}s)"


def _covers(declared: tuple[str, ...], published: list[str]) -> bool:
    """graph.py declares files and directories (trailing slash). A directory key
    is covered when at least one published artifact lives inside it."""
    for key in declared:
        if key.endswith("/"):
            if not any(item.startswith(key) for item in published):
                return False
        elif key not in published:
            return False
    return True


def _estimates(scale: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["kind"]: row for row in scale["estimates"]}


def test_real_validate_poses_reads_scale_on_mini_arkit_lidar(tmp_path, request) -> None:
    """The committed LiDAR packet walks all four real stages on CPU.

    The fixture has too few frames for free SfM (`source.notes`), so poses must
    ship the ARKit-guided path: the mapper runs with ARKit priors, finds no free
    model, and `choose_model` ships the fixed-pose triangulation over the ARKit
    poses (`arkit_fixed`). All six frames register with `pose_source` recorded.
    """
    capture_id, run_id = "cap_realcpu_lidar", "run_realcpu_lidar"
    run = _chain(request, tmp_path, LIDAR_PACKET, capture_id, run_id, branch="lidar", tier="arkit_lidar")
    report = run["stages"]

    # Published artifact keys match graph.py's declared artifacts for each stage.
    assert set(report["validate"]["artifacts"]) == set(STAGES["validate"].artifacts)
    # poses publishes the declared keys plus sidecars it legitimately owns
    # (database.db, fragments/); every declared key must be present.
    assert _covers(STAGES["poses"].artifacts, report["poses"]["artifacts"])
    assert set(report["reads"]["artifacts"]) == set(STAGES["reads"].artifacts)
    assert "scale/scale.json" in report["scale"]["artifacts"]

    # Poses registered all 6 frames, every camera with a recorded pose_source.
    cameras = json.loads((tmp_path / "scale" / "poses" / "cameras.json").read_text())
    assert len(cameras) == 6
    assert all(cam["pose_source"] for cam in cameras)
    reg = json.loads((tmp_path / "scale" / "poses" / "registration_report.json").read_text())
    assert reg["frames_registered"] == 6
    assert reg["registered_fraction"] == 1.0
    assert reg["mode"] == "arkit_fixed"
    assert reg["status"] == "fallback_arkit_fixed"
    assert "sfm_no_model" in reg["warnings"]

    # Six frames give the LiDAR-vs-sparse alignment no usable pairs, so LiDAR is
    # recorded as an unused estimate and the ARKit poses alone set the scale,
    # unverified, with a request for the meter close-up the packet lacks.
    scale = json.loads((tmp_path / "scale" / "scale" / "scale.json").read_text())
    assert scale["source"] == "arkit"
    assert scale["status"] == "unverified"
    assert scale["reason"] == "scale_unresolved"
    assert scale["reason_detail"] == "meter_closeup_missing"
    assert scale["confidence"] == "near_only"
    assert scale["rel_bound_95"] > 0.0
    estimates = _estimates(scale)
    assert [kind for kind, row in estimates.items() if row["used"]] == ["arkit_poses"]
    assert estimates["lidar"]["used"] is False
    assert estimates["lidar"]["reason"] == "lidar_too_few_pairs"
    assert estimates["lidar"]["pairs"] == 0

    _assert_budget(run)


def test_mini_arkit_refuses_metric_scale_loudly(tmp_path, request) -> None:
    """The 64x48 mini-arkit packet (tier `arkit`, depth null) gets the same real
    chain and refuses to certify a scale instead of passing.

    With `VISION_EXTERNAL_CALLS` off the meter close-up is never read: reads
    publishes `status: "disabled"` (barcodes still decode), and scale refuses —
    `scale_unresolved` / `meter_model_unread`, confidence weak, ARKit-only
    source — rather than claiming a metric scale."""
    capture_id, run_id = "cap_realcpu_arkit", "run_realcpu_arkit"
    run = _chain(request, tmp_path, MINI_ARKIT_PACKET, capture_id, run_id, branch="image", tier="arkit")
    report = run["stages"]

    assert set(report["validate"]["artifacts"]) == set(STAGES["validate"].artifacts)
    assert _covers(STAGES["poses"].artifacts, report["poses"]["artifacts"])
    assert set(report["reads"]["artifacts"]) == set(STAGES["reads"].artifacts)

    reads = json.loads((tmp_path / "scale" / "reads" / "reads.json").read_text())
    assert reads["status"] == "disabled"

    scale = json.loads((tmp_path / "scale" / "scale" / "scale.json").read_text())
    assert scale["source"] == "arkit"
    assert scale["status"] == "unverified"
    assert scale["reason"] == "scale_unresolved"
    assert scale["reason_detail"] == "meter_model_unread"
    assert scale["confidence"] == "weak"
    assert scale["rel_bound_95"] > 0.0
    estimates = _estimates(scale)
    assert "lidar" not in estimates
    assert [kind for kind, row in estimates.items() if row["used"]] == ["arkit_poses"]

    _assert_budget(run)


def _fake_getrusage(ru_maxrss: int):
    def getrusage(who: int) -> SimpleNamespace:
        assert who == resource.RUSAGE_SELF
        return SimpleNamespace(ru_maxrss=ru_maxrss)

    return getrusage


def test_peak_rss_mb_reads_bytes_on_darwin(monkeypatch) -> None:
    """macOS has no /proc and reports ru_maxrss in bytes."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(resource, "getrusage", _fake_getrusage(2 * 1024 * 1024))
    assert _peak_rss_mb() == 2.0


def test_peak_rss_mb_reads_kib_on_linux(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(resource, "getrusage", _fake_getrusage(2 * 1024 * 1024))
    assert _peak_rss_mb() == 2048.0


@pytest.mark.skipif(not Path("/proc/self/status").is_file(), reason="VmHWM comes from Linux's /proc")
def test_peak_rss_mb_matches_vmhwm_in_the_chain_process(tmp_path, request) -> None:
    """On Linux, ru_maxrss read through `_peak_rss_mb` equals the kernel's VmHWM
    in the process the chains run in.

    This test first raises its own peak by 256 MiB. Without the launcher's fork,
    the probe's ru_maxrss would carry that peak (see `realcpu_launcher.py`) and
    sit well above the probe's VmHWM."""
    ballast = bytearray(256 * 1024 * 1024)
    ballast[::4096] = b"\x01" * (len(ballast) // 4096)
    del ballast
    probe = _in_fresh_process(request, tmp_path / "probe.json", "rss_probe_in_child")
    assert probe["peak_mb"] >= 64
    assert probe["peak_mb"] == pytest.approx(probe["vm_hwm_mb"], abs=1.0)


def test_importing_this_module_leaves_thread_env_alone(request) -> None:
    """Thread limits belong to the chain's own process. Collecting this module must
    not set them for every test that runs after it."""
    script = (
        "import importlib.util, os, sys\n"
        "spec = importlib.util.spec_from_file_location('realcpu_import_probe', sys.argv[1])\n"
        "spec.loader.exec_module(importlib.util.module_from_spec(spec))\n"
        f"print(sorted(k for k in {THREAD_ENV!r} if k in os.environ))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, __file__],
        cwd=ROOT,
        env=_child_env(request, threads=None),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    assert proc.stdout.strip().splitlines()[-1] == "[]"
