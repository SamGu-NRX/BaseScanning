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

Resource budget: every measurement is taken with the process pinned to two
cores (`taskset -c 0,1`) and `OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
MKL_NUM_THREADS=2 HSS_POSES_THREADS=2`. A stage over 600 s or a process peak
RSS over 8 GiB fails the budget the target host (2 CPUs, 8 GB) imposes.

No weights, no network, no GPU: the VLM readers stay off
(`VISION_EXTERNAL_CALLS`), so `reads` runs only the local barcode decoder, and
`poses` triangulates over the ARKit priors with pycolmap on CPU.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")
os.environ.setdefault("HSS_POSES_THREADS", "2")

from server.contracts.jobs import StageContext, StageResult  # noqa: E402
from server.orchestrator.graph import STAGE_INPUTS, STAGES  # noqa: E402
from server.orchestrator.runner import _publish  # noqa: E402
from server.packet import run as validate_run  # noqa: E402
from server.poses import run as poses_run  # noqa: E402
from server.reads import run as reads_run  # noqa: E402
from server.reads.cache import MemoryStorage  # noqa: E402
from server.scale.run import execute as scale_run  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LIDAR_PACKET = ROOT / "ios-kit" / "fixtures" / "mini-arkit-lidar"
MINI_ARKIT_PACKET = ROOT / "data" / "fixtures" / "packets" / "mini-arkit"

WALL_BUDGET_S = 600.0
RSS_BUDGET_MB = 8 * 1024

STAGE_ENTRY = {
    "validate": validate_run,
    "poses": poses_run,
    "reads": reads_run,
    "scale": scale_run,
}


class _Settings:
    """The settings the coordinator passes on the CPU path: external vision off."""

    VISION_EXTERNAL_CALLS = False


def _vm_hwm_mb() -> float:
    """Process peak RSS so far. Monotonic, so a stage's entry is the high-water mark through it."""
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmHWM"):
            return int(line.split()[1]) / 1024
    return -1.0


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
    tmp_path: Path,
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
        work_dir = tmp_path / stage
        work_dir.mkdir(parents=True, exist_ok=True)
        _hydrate(work_dir, STAGE_INPUTS[stage], storage, prefix)
        started = time.perf_counter()
        result: StageResult = fn(_ctx(stage, work_dir, packet_dir, storage, capture_id, run_id, branch, tier))
        wall_s = time.perf_counter() - started
        stored = _publish(_ctx(stage, work_dir, packet_dir, storage, capture_id, run_id, branch, tier), result)
        report[stage] = {
            "wall_s": round(wall_s, 2),
            "vm_hwm_mb": round(_vm_hwm_mb(), 1),
            "artifacts": sorted(stored),
            "metrics": {k: round(float(v), 4) for k, v in result.metrics.items()},
            "warnings": list(result.warnings),
        }
    if os.environ.get("REALCPU_REPORT"):
        print(f"REALCPU_REPORT {json.dumps({'packet': packet_dir.name, 'stages': report}, sort_keys=True)}")
    return report


def _assert_budget(report: dict[str, dict[str, Any]]) -> None:
    for stage, entry in report.items():
        assert entry["wall_s"] < WALL_BUDGET_S, f"{stage} took {entry['wall_s']}s (budget {WALL_BUDGET_S}s)"
        assert 0 < entry["vm_hwm_mb"] < RSS_BUDGET_MB, f"peak RSS {entry['vm_hwm_mb']}MB exceeds the 8 GB budget"


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


def test_real_validate_poses_reads_scale_on_mini_arkit_lidar(tmp_path) -> None:
    """The committed LiDAR packet walks all four real stages on CPU.

    The fixture has too few frames for free SfM (`source.notes`), so poses must
    ship the ARKit-guided path: the mapper runs with ARKit priors, finds no free
    model, and `choose_model` ships the fixed-pose triangulation over the ARKit
    poses (`arkit_fixed`). All six frames register with `pose_source` recorded.
    """
    capture_id, run_id = "cap_realcpu_lidar", "run_realcpu_lidar"
    report = _run_chain(tmp_path, LIDAR_PACKET, capture_id, run_id, branch="lidar", tier="arkit_lidar")
    _assert_budget(report)

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

    # Scale carries a source and a 95% relative bound. The LiDAR depth and the
    # meter tap both feed it; `source` names what shipped.
    scale = json.loads((tmp_path / "scale" / "scale" / "scale.json").read_text())
    assert scale["source"]
    assert scale["source"] != "none"
    assert isinstance(scale["rel_bound_95"], float) and scale["rel_bound_95"] >= 0.0


def test_mini_arkit_refuses_metric_scale_loudly(tmp_path) -> None:
    """The 64x48 mini-arkit packet (tier `arkit`, depth null) gets the same real
    chain and refuses to certify a scale instead of passing.

    With `VISION_EXTERNAL_CALLS` off the meter close-up is never read: reads
    publishes `status: "disabled"` (barcodes still decode), and scale refuses —
    `scale_unresolved` / `meter_model_unread`, confidence weak, ARKit-only
    source — rather than claiming a metric scale."""
    capture_id, run_id = "cap_realcpu_arkit", "run_realcpu_arkit"
    report = _run_chain(tmp_path, MINI_ARKIT_PACKET, capture_id, run_id, branch="image", tier="arkit")
    _assert_budget(report)

    assert set(report["validate"]["artifacts"]) == set(STAGES["validate"].artifacts)
    assert _covers(STAGES["poses"].artifacts, report["poses"]["artifacts"])
    assert set(report["reads"]["artifacts"]) == set(STAGES["reads"].artifacts)

    reads = json.loads((tmp_path / "scale" / "reads" / "reads.json").read_text())
    assert reads["status"] == "disabled"

    scale = json.loads((tmp_path / "scale" / "scale" / "scale.json").read_text())
    assert scale["status"] == "unverified"
    assert scale["reason"] == "scale_unresolved"
    assert scale["reason_detail"] == "meter_model_unread"
    assert scale["confidence"] == "weak"
