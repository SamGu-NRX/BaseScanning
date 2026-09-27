"""GPU entry for the pose stage.

Without ``--packet``, confirms a Modal token and runs a short L4 smoke of
pycolmap 4.2.0. With ``--packet`` (or ``--suite``), maps fixture packets on an
L4. This script does not use the 3.11.1 benchmark image.

Run from the repo root so the image can mount ``server/`` and ``.fixtures/``.
"""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
from pathlib import Path

import modal

app = modal.App("hss-ws04-poses")

# Depth rasters are not read by the pose stage. Features are recomputed.
_SKIP = ["**/*.depth.f32", "**/*.confidence.u8", "**/features/**"]

_PACKETS = (
    ("eth3d-facade", ".fixtures/packets/eth3d-facade"),
    ("eth3d-electro", ".fixtures/packets/eth3d-electro"),
    ("tt-barn", ".fixtures/packets/tt-barn"),
    ("yt-Qm4b1cw8pV4", ".fixtures/packets/yt-Qm4b1cw8pV4"),
    ("yt-LCPBf86oloE", ".fixtures/packets/yt-LCPBf86oloE"),
    ("mini-arkit", "data/fixtures/packets/mini-arkit"),
)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install(
        "libglib2.0-0",
        "libgomp1",
        "libgl1",
    )
    .pip_install(
        "numpy==2.5.3",
        "pycolmap==4.2.0",
        "pillow==12.3.0",
        "jsonschema==4.26.0",
        "pydantic>=2.13.5",
        "pydantic-settings>=2.15.0",
    )
)

for _name, _local in _PACKETS:
    image = image.add_local_dir(_local, remote_path=f"/packets/{_name}", ignore=_SKIP)
# add_local_python_source skips non-Python files, including the report schema.
image = image.add_local_python_source("server")
image = image.add_local_dir("server/poses/schemas", remote_path="/root/server/poses/schemas")

# Item 7 needs two separate electro maps. run_index keeps those calls distinct.
SUITE = (
    ("eth3d-facade", "arkit-lidar", 0),
    ("eth3d-facade", "arkit-nonlidar", 0),
    ("eth3d-facade", "pose-free", 0),
    ("eth3d-electro", "arkit-lidar", 0),
    ("eth3d-electro", "arkit-nonlidar", 1),
    ("eth3d-electro", "arkit-nonlidar", 2),
    ("tt-barn", "arkit-nonlidar", 0),
    ("yt-Qm4b1cw8pV4", "arkit-nonlidar", 0),
    ("yt-LCPBf86oloE", "arkit-nonlidar", 0),
    ("mini-arkit", "packet", 0),
)


@app.function(image=image, gpu="L4", timeout=180)
def toolchain_smoke() -> dict:
    """Return the pin check the brief asks for. No SfM."""
    import pycolmap

    prior = pycolmap.PosePrior()
    devices = [name for name in ("auto", "cpu", "cuda") if hasattr(pycolmap.Device, name)]
    return {
        "pycolmap": pycolmap.__version__,
        "corr_data_id": hasattr(prior, "corr_data_id"),
        "use_prior_position": hasattr(pycolmap.IncrementalPipelineOptions(), "use_prior_position"),
        "devices": devices,
    }


def _packet_path(packet: str, variant: str) -> Path:
    root = Path("/packets") / packet
    if variant in ("", "packet"):
        path = root / "packet.json"
    else:
        path = root / f"packet.{variant}.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


@app.function(image=image, gpu="L4", cpu=4, memory=16384, timeout=900)
def map_packet(packet: str, variant: str, run_index: int = 0) -> dict:
    """Map one fixture on an L4 and return the report, cameras, and sparse cloud."""
    import pycolmap

    spec = _packet_path(packet, variant)
    out = Path("/tmp/ws04-out")
    proc = subprocess.run(
        [sys.executable, "-m", "server.poses.run", "--packet", str(spec), "--out", str(out)],
        check=False,
        capture_output=True,
        text=True,
    )
    poses = out / "poses"
    report_path = poses / "registration_report.json"
    cameras_path = poses / "cameras.json"
    ply_path = poses / "sparse.ply"
    report = json.loads(report_path.read_text()) if report_path.is_file() else None
    ply = ply_path.read_bytes() if ply_path.is_file() else b""
    return {
        "packet": packet,
        "variant": variant,
        "run_index": run_index,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-4000:],
        "stderr": (proc.stderr or "")[-8000:],
        "pycolmap": pycolmap.__version__,
        "report": report,
        "cameras_json": cameras_path.read_text() if cameras_path.is_file() else None,
        "ply_gz": gzip.compress(ply, compresslevel=6) if ply else b"",
    }


def token_configured() -> bool:
    proc = subprocess.run(
        [sys.executable, "-m", "modal", "token", "info"],
        check=False,
        capture_output=True,
        text=True,
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        print(f"modal token info exited {proc.returncode}", file=sys.stderr)
        return False
    lowered = text.lower()
    if "not configured" in lowered or "no token" in lowered:
        print("modal token info: not configured", file=sys.stderr)
        return False
    print("modal token info: configured")
    return True


def _slug(packet: str, variant: str, run_index: int) -> str:
    name = f"{packet}-{variant or 'packet'}"
    if run_index:
        name = f"{name}-r{run_index}"
    return name


def _write_result(dest: Path, result: dict) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    poses = dest / "poses"
    poses.mkdir(parents=True, exist_ok=True)
    (dest / "modal.log").write_text(
        f"returncode {result.get('returncode')}\n--- stdout ---\n{result.get('stdout', '')}\n"
        f"--- stderr ---\n{result.get('stderr', '')}\n"
    )
    report = result.get("report")
    if report is not None:
        (poses / "registration_report.json").write_text(json.dumps(report, indent=1) + "\n")
    if result.get("cameras_json"):
        (poses / "cameras.json").write_text(result["cameras_json"])
    if result.get("ply_gz"):
        (poses / "sparse.ply").write_bytes(gzip.decompress(result["ply_gz"]))


def _summarize(result: dict) -> str:
    report = result.get("report") or {}
    timings = report.get("timings_s") or {}
    budget = report.get("frame_budget") or {}
    return json.dumps(
        {
            "packet": result.get("packet"),
            "variant": result.get("variant"),
            "run_index": result.get("run_index"),
            "returncode": result.get("returncode"),
            "registered": report.get("frames_registered"),
            "given": report.get("frames_given"),
            "mode": report.get("mode"),
            "status": report.get("status"),
            "warnings": report.get("warnings"),
            "mapper_frames": budget.get("mapper_frames"),
            "mapper_s": timings.get("mapper_s"),
            "total_s": timings.get("total_s"),
            "gpu": report.get("gpu"),
            "pycolmap": (report.get("params") or {}).get("pycolmap"),
        }
    )


@app.local_entrypoint()
def main(packet: str = "", variant: str = "", out: str = "/tmp/ws04/fixtures", suite: bool = False) -> None:
    if not token_configured():
        raise SystemExit(2)
    if not packet and not suite:
        print(json.dumps(toolchain_smoke.remote()))
        return
    jobs = list(SUITE) if suite or packet == "suite" else [(packet, variant or "arkit-nonlidar", 0)]
    calls = [map_packet.spawn(name, kind, index) for name, kind, index in jobs]
    failed = 0
    for (name, kind, index), call in zip(jobs, calls, strict=True):
        slug = _slug(name, kind, index)
        try:
            result = call.get()
        except Exception as exc:
            failed += 1
            print(json.dumps({"packet": name, "variant": kind, "run_index": index, "error": str(exc)}))
            continue
        _write_result(Path(out) / slug, result)
        if result.get("returncode") != 0 or result.get("report") is None:
            failed += 1
        print(_summarize(result))
    if failed:
        raise SystemExit(f"{failed} fixture map(s) failed")
