"""``python -m server.orchestrator.cli``. ``run-modal`` drives a small run on Modal dev."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tarfile
import tempfile
import time
from pathlib import Path

import modal

from server.common.backends.state_memory import MemoryState
from server.common.backends.storage_local import LocalFsStorage
from server.common.config import Settings
from server.contracts.jobs import CaptureRecord, CommittedFile
from server.orchestrator.api import dispatch_early, start_run
from server.orchestrator.clock import Clock
from server.orchestrator.config import load_orch
from server.orchestrator.early.capture_check import flush_pending
from server.orchestrator.executor import InlineExecutor
from server.orchestrator.modal_functions import modal_available
from server.orchestrator.services import Services, bind, packet_sha256
from server.orchestrator.spend import KEEP_WARM_USD


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="server.orchestrator.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    local = sub.add_parser("run-local")
    local.add_argument("--tar", required=True)
    local.add_argument("--variant", default="arkit-nonlidar")
    local.add_argument("--out", required=True)
    local.add_argument("--replay-interval", type=float, default=0.0)

    dump = sub.add_parser("dump-run")
    dump.add_argument("--out", required=True)

    modal = sub.add_parser("run-modal")
    modal.add_argument("--env", default="dev")
    modal.add_argument("--tar", default="")
    modal.add_argument("--variant", default="")
    modal.add_argument("--stubs", action="store_true")
    modal.add_argument("--hang-stage", default="")
    modal.add_argument("--kill-coordinator-after", default="")
    modal.add_argument("--fail-stage", default="")
    modal.add_argument("--allow-escalation", action="store_true")

    spend = sub.add_parser("spend")
    spend.add_argument("--env", default="dev")

    sub.add_parser("stop-apps")

    args = parser.parse_args(argv)
    if args.cmd == "run-local":
        return run_local(Path(args.tar), args.variant, Path(args.out), args.replay_interval)
    if args.cmd == "dump-run":
        return dump_run(Path(args.out))
    if args.cmd == "run-modal":
        return run_modal(
            args.env,
            tar=args.tar,
            variant=args.variant,
            stubs=bool(args.stubs),
            hang_stage=args.hang_stage,
            kill_coordinator_after=args.kill_coordinator_after,
            fail_stage=args.fail_stage,
            allow_escalation=bool(args.allow_escalation),
        )
    if args.cmd == "spend":
        return print_spend()
    if args.cmd == "stop-apps":
        return _modal_unavailable("stop-apps")
    return 2


def run_local(tar_path: Path, variant: str, out: Path, replay_interval: float) -> int:
    if not tar_path.is_file():
        print(f"fixture tar not on disk: {tar_path}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    packet_root = out / "packet" / variant
    packet_root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path) as archive:
        archive.extractall(packet_root, filter="data")
    packet_path = _find_packet(packet_root, variant)
    data = packet_path.read_bytes()
    packet = json.loads(data)
    services = _services(out)
    capture_id = str(packet.get("captureId") or f"cap_{variant}")
    with bind(services):
        services.state.put_capture(  # type: ignore[attr-defined]
            CaptureRecord(
                capture_id=capture_id,
                packet_id=str(packet.get("packetId") or "packet-cli"),
                status="awaiting_files",
                tier="arkit",
                flow=str(packet.get("flow") or "guided"),  # type: ignore[arg-type]
                device_model="cli",
                owner_principal="cli",
                created_at=services.clock.iso(),
                finalize_by=services.clock.iso(),
            )
        )
        uri = f"captures/{capture_id}/packet.json"
        services.storage.put_bytes(uri, data, "application/json")  # type: ignore[attr-defined]
        uploaded = _upload_fixture_media(services, capture_id, packet, packet_path.parent)
        _replay(services, capture_id, packet, replay_interval, uploaded)
        run = start_run(capture_id, packet_sha256(data), uri)
        events = services.state.events_after(capture_id, 0, limit=500)  # type: ignore[attr-defined]
    (out / "run.json").write_text(run.model_dump_json(indent=2))
    lines = [event.model_dump(mode="json") for event in events]
    (out / "events.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))
    for line in lines:
        print(json.dumps(line))
    print(run.run_id)
    return 0


def dump_run(out: Path) -> int:
    run_path = out / "run.json"
    events_path = out / "events.jsonl"
    if not run_path.is_file():
        print(f"no run.json under {out}", file=sys.stderr)
        return 2
    run = json.loads(run_path.read_text())
    print(f"# run {run['run_id']} status={run['status']} failure={run.get('failure_code')}")
    stages = _load_named(out, "stages.json")
    print("\n## stages\n")
    print("| stage | status | attempt | gpu | error |")
    print("|---|---|---|---|---|")
    if isinstance(stages, list) and stages:
        for rec in stages:
            if not isinstance(rec, dict):
                continue
            print(
                f"| {rec.get('stage')} | {rec.get('status')} | {rec.get('attempt')} | "
                f"{rec.get('gpu') or ''} | {rec.get('error') or ''} |"
            )
    elif events_path.is_file():
        latest: dict[str, dict] = {}
        for line in events_path.read_text().splitlines():
            event = json.loads(line)
            if event.get("type") != "stage":
                continue
            data = event.get("data") or {}
            if data.get("stage"):
                latest[str(data["stage"])] = data
        for name, data in latest.items():
            print(f"| {name} | {data.get('status')} | {data.get('attempt')} | {data.get('gpu') or ''} | |")
    if events_path.is_file():
        print("\n## events\n")
        for line in events_path.read_text().splitlines():
            event = json.loads(line)
            print(f"- {event['seq']} {event['type']} {json.dumps(event['data'])}")
    timings = _load_named(out, "timings.json")
    print("\n## timings.json\n")
    if not isinstance(timings, dict):
        print("(not written)")
    else:
        print(json.dumps(timings, indent=2))
        print(f"merge_objects_s: {timings.get('merge_objects_s')}")
    for label in ("cost.json", "stages.json"):
        body = _load_named(out, label)
        print(f"\n## {label}\n")
        if body is None:
            print("(not written)")
            continue
        print(json.dumps(body, indent=2) if not isinstance(body, str) else body)
    return 0


def _load_named(out: Path, name: str) -> object | None:
    matches = sorted(out.rglob(name))
    if not matches:
        return None
    return json.loads(matches[0].read_text())


def print_spend() -> int:
    body = {
        "modal": "available" if modal_available() else "unavailable",
        "keep_warm_usd": KEEP_WARM_USD,
        "note": "Live Modal Dict spend is not queried when the SDK or token is absent.",
    }
    print(json.dumps(body))
    return 0


def run_modal(
    env: str,
    *,
    tar: str = "",
    variant: str = "",
    stubs: bool = False,
    hang_stage: str = "",
    kill_coordinator_after: str = "",
    fail_stage: str = "",
    allow_escalation: bool = False,
) -> int:
    """Drive one run on the deployed Modal app. No call is spawned without a real tar."""

    available = modal_available()
    if not available:
        return _modal_unavailable("run-modal")
    if not tar:
        print(
            json.dumps(
                {
                    "cmd": "run-modal",
                    "modal": "available",
                    "env": env,
                    "remote": "supported",
                    "spawned": False,
                    "reason": "a packet tar is required",
                }
            )
        )
        return 2
    tar_path = Path(tar)
    if not tar_path.is_file():
        print(
            json.dumps(
                {
                    "cmd": "run-modal",
                    "modal": "available",
                    "env": env,
                    "remote": "supported",
                    "spawned": False,
                    "reason": f"fixture tar not on disk: {tar}",
                }
            ),
            file=sys.stderr,
        )
        return 2
    packet = _packet_text(tar_path, variant)
    digest = hashlib.sha256(packet.encode()).hexdigest()[:12]
    capture_id = f"cap_modal_{digest}"
    hook = f"{hang_stage}|{kill_coordinator_after}|{fail_stage}"
    if hang_stage or kill_coordinator_after or fail_stage:
        capture_id = f"{capture_id}_{hashlib.sha256(hook.encode()).hexdigest()[:6]}"
    spec = {
        "packet": packet,
        "capture_id": capture_id,
        "stubs": stubs,
        "hang_stage": hang_stage,
        "kill_coordinator_after": kill_coordinator_after,
        "fail_stage": fail_stage,
        "allow_escalation": allow_escalation,
        "hard_stop_min": os.environ.get("HARD_STOP_MIN") or "",
    }
    app_name = os.environ.get("MODAL_APP_NAME", "house-scanning")
    function = modal.Function.from_name(app_name, "run_capture", environment_name=env)
    result = function.remote(spec)
    print(json.dumps({"cmd": "run-modal", "modal": "available", "env": env, "remote": "spawned", **result}))
    return 0


def _modal_unavailable(cmd: str) -> int:
    print(
        json.dumps(
            {
                "cmd": cmd,
                "modal": "unavailable",
                "reason": "modal SDK or server.modal_app is not on this machine; no GPU was spawned",
            }
        ),
        file=sys.stderr,
    )
    return 2


def _services(out: Path) -> Services:
    settings = Settings(
        STATE_BACKEND="memory",
        STORAGE_BACKEND="local",
        VISION_EXTERNAL_CALLS="off",
        VOLUME_ROOT=str(out),
        SMP_ENVIRONMENT="dev",
    )
    orch = load_orch()
    orch.stage_stubs = True
    return Services(
        settings=settings,
        orch=orch,
        state=MemoryState(),
        storage=LocalFsStorage(out / "store"),
        clock=Clock(),
        executor=InlineExecutor(config=orch, stubs=True),
        work_root=out / "work",
    )


def _packet_text(tar_path: Path, variant: str) -> str:
    with tempfile.TemporaryDirectory(prefix="hss-modal-") as tmp:
        root = Path(tmp)
        with tarfile.open(tar_path) as archive:
            archive.extractall(root, filter="data")
        return _find_packet(root, variant).read_text()


def _find_packet(root: Path, variant: str = "") -> Path:
    if variant:
        named = sorted(root.rglob(f"packet.{variant}.json"))
        if named:
            return named[0]
    found = sorted(root.rglob("packet.json"))
    if found:
        return found[0]
    raise FileNotFoundError(f"no packet.json under {root}")


def _upload_fixture_media(
    services: Services, capture_id: str, packet: dict, packet_dir: Path
) -> dict[str, tuple[str, int]]:
    """Store keyframe images, stills, and other packet files that are in the tar."""

    uploaded: dict[str, tuple[str, int]] = {}
    for rel in _media_paths(packet):
        src = _resolve_under(packet_dir, rel)
        if src is None:
            continue
        data = src.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        key = f"captures/{capture_id}/{rel}"
        services.storage.put_bytes(key, data, _content_type(rel))  # type: ignore[attr-defined]
        uploaded[rel] = (digest, len(data))
    return uploaded


def _media_paths(packet: dict) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        if not isinstance(value, str) or not value or value in seen:
            return
        seen.add(value)
        paths.append(value)

    for item in packet.get("files") or []:
        if isinstance(item, dict):
            add(item.get("path"))
    for frame in list(packet.get("keyframes") or []) + list(packet.get("stills") or []):
        if isinstance(frame, dict):
            add(frame.get("img"))
            add(frame.get("file"))
            add(frame.get("path"))
    return paths


def _resolve_under(root: Path, rel: str) -> Path | None:
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts:
        return None
    candidate = root / path
    return candidate if candidate.is_file() else None


def _content_type(rel: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".json": "application/json",
    }.get(Path(rel).suffix.lower(), "application/octet-stream")


def _stored_file(capture_id: str, item: dict, uploaded: dict[str, tuple[str, int]]) -> tuple[str, str, int, str]:
    path = str(item.get("id") or item.get("file") or item.get("img") or "file")
    image = None
    for candidate in (item.get("img"), item.get("file"), item.get("path")):
        if isinstance(candidate, str) and candidate in uploaded:
            image = candidate
            break
    if image is None:
        return path, "0" * 64, 1, f"captures/{capture_id}/{path}"
    digest, nbytes = uploaded[image]
    return path, digest, nbytes, f"captures/{capture_id}/{image}"


def _replay(
    services: Services,
    capture_id: str,
    packet: dict,
    interval: float,
    uploaded: dict[str, tuple[str, int]] | None = None,
) -> None:
    stored = uploaded or {}
    files: list[CommittedFile] = []
    for frame in packet.get("keyframes") or []:
        if not isinstance(frame, dict):
            continue
        path, digest, nbytes, key = _stored_file(capture_id, frame, stored)
        files.append(
            CommittedFile(
                path=path,
                role="keyframe",
                sha256=digest,
                bytes=nbytes,
                generation="1",
                key=key,
                meta=frame,
            )
        )
    for still in packet.get("stills") or []:
        if not isinstance(still, dict):
            continue
        path, digest, nbytes, key = _stored_file(capture_id, still, stored)
        files.append(
            CommittedFile(
                path=path,
                role="still",
                sha256=digest,
                bytes=nbytes,
                generation="1",
                key=key,
                meta=still,
            )
        )
    if not files:
        return
    if interval <= 0:
        dispatch_early(capture_id, files)
        return
    for index, item in enumerate(files):
        if index:
            time.sleep(interval)
        dispatch_early(capture_id, [item])
    flush_pending(services, capture_id)


if __name__ == "__main__":
    raise SystemExit(main())
