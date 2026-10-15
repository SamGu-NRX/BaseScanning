"""Dispatch-only GPU bench. Rate card matches the measured Modal pipeline."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

GPU_PRICES_USD_PER_S = {
    "L4": 0.000222,
    "A10G": 0.000306,
    "A100": 0.000583,
    "A100-40GB": 0.000583,
    "A100-80GB": 0.000694,
    "H100": 0.001097,
}
CPU_USD_PER_S = 0.0000131
MEM_USD_PER_GIB_S = 0.00000222
CPU = 8
MEMORY_MIB = 24576
SPEND_STOP_USD = 40.0
P50_S = 180.0
P95_S = 480.0


def rate(gpu: str) -> float:
    return GPU_PRICES_USD_PER_S[gpu] + CPU * CPU_USD_PER_S + (MEMORY_MIB / 1024) * MEM_USD_PER_GIB_S


def estimate_usd(gpu: str, seconds: float) -> float:
    return rate(gpu) * max(0.0, seconds)


def _record(**fields: object) -> dict[str, object]:
    base: dict[str, object] = {
        "kind": "run",
        "house": "",
        "tag": "",
        "gpu": "",
        "ok": False,
        "enter_s": None,
        "method_s": None,
        "client_s": None,
        "overhead_s": None,
        "est_usd": None,
        "est_usd_busy": None,
        "timings_s": {},
        "frames_registered": None,
        "gpu_memory": None,
        "error": None,
        "git_sha": os.environ.get("GIT_SHA", ""),
    }
    base.update(fields)
    return base


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the house-scanning GPU bench on Modal dev.")
    parser.add_argument("--env", default="dev")
    parser.add_argument("--gpu", default="L4")
    parser.add_argument("--fixture", default="eth3d-facade")
    parser.add_argument("--variant", default="arkit-nonlidar")
    parser.add_argument("--max-frames", default="40,60,70")
    parser.add_argument("--cold", action="store_true")
    parser.add_argument("--spend-stop-usd", type=float, default=SPEND_STOP_USD)
    parser.add_argument("--out", type=Path, default=Path("runs/bench.jsonl"))
    args = parser.parse_args(argv)
    if args.spend_stop_usd > 60:
        raise SystemExit("spend-stop-usd must be <= 60")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.spend_stop_usd <= 0:
        record = _record(kind="stopped", house=args.fixture, gpu=args.gpu, error="spend stop")
        args.out.write_text(json.dumps(record) + "\n")
        return 0

    token_id = os.environ.get("MODAL_TOKEN_ID")
    token_secret = os.environ.get("MODAL_TOKEN_SECRET")
    if not token_id or not token_secret:
        record = _record(
            kind="stopped",
            house=args.fixture,
            gpu=args.gpu,
            tag=args.variant,
            error="MODAL_TOKEN_ID or MODAL_TOKEN_SECRET is unset",
        )
        args.out.write_text(json.dumps(record) + "\n")
        return 0

    import modal

    from server.modal_app import GPU_IMAGE, volume

    bench_app = modal.App("house-scanning-bench")

    @bench_app.cls(
        image=GPU_IMAGE,
        cpu=CPU,
        memory=MEMORY_MIB,
        volumes={"/vol": volume},
        timeout=1200,
        gpu=args.gpu,
    )
    class BenchRunner:
        @modal.method()
        def run(self, house: str, spec: dict) -> dict:
            try:
                from server.orchestrator.legacy_run_packet import run_packet
            except ImportError as exc:
                return {"ok": False, "error": f"legacy_run_packet missing: {exc}"}
            return run_packet(house, spec)

    spend = 0.0
    lines: list[str] = []
    frames = [int(part) for part in args.max_frames.split(",") if part]
    with bench_app.run():
        runner = BenchRunner()
        for count in frames:
            if spend >= args.spend_stop_usd:
                lines.append(json.dumps(_record(kind="stopped", house=args.fixture, gpu=args.gpu, error="spend stop")))
                break
            spec = {
                "variant": args.variant,
                "max_frames": count,
                "cold": args.cold,
                "env": args.env,
            }
            result = runner.run.remote(args.fixture, spec)
            seconds = float(result.get("client_s") or result.get("method_s") or 0)
            est = round(estimate_usd(args.gpu, seconds), 4)
            spend += est
            result.update(
                {
                    "kind": "run",
                    "house": args.fixture,
                    "gpu": args.gpu,
                    "est_usd": est,
                    "git_sha": os.environ.get("GIT_SHA", ""),
                }
            )
            lines.append(json.dumps(result))
    args.out.write_text("\n".join(lines) + ("\n" if lines else ""))
    spend_path = args.out.parent / "spend_estimate.json"
    spend_path.write_text(json.dumps({"estimate_usd": round(spend, 4), "n": len(lines)}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
