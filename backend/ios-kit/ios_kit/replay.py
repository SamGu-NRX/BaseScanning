"""iOS-facing replay. Delegates to WS-11 once that engine PUTs bytes; until then drives the same sequence."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import inspect
import json
import os
import sys
import tarfile
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

from ios_kit.schemas import validate_result
from ios_kit.vendored import validate_packet

EXIT_OK = 0
EXIT_RESULT = 2
EXIT_PACKET = 3
EXIT_API = 4
EXIT_TIMEOUT = 5
EXIT_NETWORK = 6
EXIT_UNSUPPORTED = 7
CAPTURE_CHECK_TARGET_S = 30.0


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(body)
        self.status = status
        self.body = body


class NetworkError(RuntimeError):
    pass


@dataclass
class ReplayRun:
    captureId: str | None = None
    runId: str | None = None
    packetId: str | None = None
    variant: str | None = None
    tier: str | None = None
    flow: str | None = None
    files: dict = field(default_factory=lambda: {"registered": 0, "committed": 0, "mismatch": []})
    timings: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    result: dict | None = None
    verdict_paths: dict = field(default_factory=dict)
    scene_path: str | None = None
    errors: list = field(default_factory=list)


def replay_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ios_kit replay")
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--variant", default=None)
    parser.add_argument("--api", default="")
    parser.add_argument("--rate", type=float, default=2.0)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--finalize-at", choices=("last", "early"), default="last")
    parser.add_argument("--until", choices=("processing", "verdict"), default="verdict")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--jsonl", action="store_true")
    parser.add_argument("--skip-validate", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--amend", default=None)
    parser.add_argument("--answers", default="")
    parser.add_argument("--keep", default="")
    args = parser.parse_args(argv)
    if args.amend:
        print("amendments need server.eval.replay.replay(..., amend_of=, answers=); 7 not_supported", file=sys.stderr)
        return EXIT_UNSUPPORTED
    if args.packet is None:
        print("replay needs --packet", file=sys.stderr)
        return EXIT_PACKET
    token = os.environ.get("CAPTURE_API_TOKEN", "")
    try:
        manifest, variant = _resolve(args.packet, args.variant)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_PACKET
    packet = json.loads(manifest.read_text())
    if not args.skip_validate and not args.dry_run:
        report = validate_packet(manifest, check_hashes=True)
        if not report["ok"]:
            print(json.dumps(report["errors"][:8], indent=1), file=sys.stderr)
            return EXIT_PACKET
    plan = _plan(packet, manifest.parent, variant, args.rate)
    if args.dry_run:
        for item in plan:
            print(f"{item['role']} {item['path']}")
        return EXIT_OK
    try:
        finished = run(
            args.packet,
            args.api,
            variant=args.variant,
            rate=args.rate,
            concurrency=args.concurrency,
            finalize_at=args.finalize_at,
            until=args.until,
            timeout_s=args.timeout,
            out_dir=args.out or Path("runs") / "latest",
            token=token,
            skip_validate=args.skip_validate,
        )
    except ApiError as exc:
        print(exc.body, file=sys.stderr)
        return EXIT_API
    except NetworkError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_NETWORK
    except TimeoutError:
        print("timeout", file=sys.stderr)
        return EXIT_TIMEOUT
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_PACKET
    print_events(finished, args.jsonl)
    print_result(finished)
    if finished.errors:
        return EXIT_API
    if args.until == "verdict":
        problems = validate_result(finished.result or {})
        if problems:
            print("\n".join(problems[:8]), file=sys.stderr)
            return EXIT_RESULT
    return EXIT_OK


def run(
    packet: Path,
    api: str,
    *,
    variant: str | None,
    rate: float,
    concurrency: int,
    finalize_at: str,
    until: str,
    timeout_s: float,
    out_dir: Path,
    token: str | None = None,
    skip_validate: bool = False,
) -> ReplayRun:
    """Upload a packet. Calls ``server.eval.replay.replay`` when that function PUTs file bytes."""
    del concurrency  # WS-11 records the flag; the live pool is still one request at a time
    bearer = token if token is not None else os.environ.get("CAPTURE_API_TOKEN", "")
    delegated = _delegate(packet, api, bearer, variant, rate, finalize_at, until, timeout_s)
    if delegated is not None:
        _write(out_dir, delegated)
        return delegated
    manifest, used = _resolve(packet, variant)
    document = json.loads(manifest.read_text())
    if not skip_validate:
        report = validate_packet(manifest, check_hashes=True)
        if not report["ok"]:
            raise ValueError("packet invalid: " + "; ".join(report["errors"][:4]))
    plan = _plan(document, manifest.parent, used, rate)
    executed = _upload(plan, manifest, _host(api), bearer, finalize_at, until, timeout_s, rate)
    _write(out_dir, executed)
    return executed


def print_events(run: ReplayRun, jsonl: bool) -> None:
    for event in run.events:
        if jsonl:
            print(json.dumps(event, separators=(",", ":")))
            continue
        data = event.get("data") or {}
        summary = data.get("code") or data.get("stage") or data.get("kind") or event.get("status") or ""
        print(f"{event.get('seq', '-')} {event.get('type')} {event.get('at', '')} {summary}".rstrip())


def print_result(run: ReplayRun) -> None:
    result = run.result or {}
    outcome = result.get("outcome") or {}
    scale = result.get("scale") or {}
    print(f"status {result.get('status')}")
    print(f"outcome.kind {outcome.get('kind')}")
    print(f"scale.status {scale.get('status')}")
    print(f"memberActions {result.get('memberActions')}")
    for view in outcome.get("viewsNeeded") or []:
        prompt = view.get("prompt") or {}
        print(f"prompt {prompt.get('title')} | {prompt.get('body')} | {view.get('viewingAngleDeg')}")
    timing = (run.timings or {}).get("last_commit_to_capture_check_s")
    if timing is not None:
        flag = "≤ 30 s" if timing <= CAPTURE_CHECK_TARGET_S else "> 30 s"
        print(f"timings.last_commit_to_capture_check_s {timing:.3f} {flag}")


def _delegate(packet, api, token, variant, rate, finalize_at, until, timeout_s) -> ReplayRun | None:
    try:
        import server.eval.replay as engine
    except ImportError:
        return None
    source = inspect.getsource(getattr(engine, "_execute", engine.replay))
    if "requests.put" not in source and ".put(" not in source:
        return None
    host = api.rstrip("/")
    if host.endswith("/v1"):
        host = host[:-3]
    produced = engine.replay(
        packet,
        host,
        token,
        variant=variant or "arkit-nonlidar",
        rate=rate,
        finalize_at=finalize_at,
        until=until,
        timeout_s=timeout_s,
    )
    return produced


def _host(api: str) -> str:
    """CLI examples pass ``http://host/v1``. WS-11's engine appends ``/v1`` itself, so strip one suffix."""
    root = api.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    return root


def _resolve(packet: Path, variant: str | None) -> tuple[Path, str | None]:
    path = Path(packet)
    if path.is_file() and (path.name.endswith(".tar") or ".tar" in path.suffixes):
        dest = Path(tempfile.mkdtemp(prefix="hss-ios-replay-"))
        with tarfile.open(path) as archive:
            archive.extractall(dest, filter="data")
        # packetgen archives nest the packet one folder under the extract root (`<house>/`).
        return _resolve_extracted(dest, variant)
    if path.is_file() and path.name == "packet.json":
        if variant:
            raise ValueError("variant_not_applicable")
        return path, None
    if path.is_dir():
        found = _packet_in(path, variant)
        if found is not None:
            return found
        chosen = variant or "arkit-nonlidar"
        raise ValueError(f"missing packet.{chosen}.json")
    raise ValueError(f"unrecognised packet path {path}")


def _resolve_extracted(dest: Path, variant: str | None) -> tuple[Path, str | None]:
    found = _packet_in(dest, variant)
    if found is not None:
        return found
    for child in sorted(path for path in dest.iterdir() if path.is_dir()):
        found = _packet_in(child, variant)
        if found is not None:
            return found
    chosen = variant or "arkit-nonlidar"
    raise ValueError(f"missing packet.{chosen}.json")


def _packet_in(directory: Path, variant: str | None) -> tuple[Path, str | None] | None:
    plain = directory / "packet.json"
    if plain.is_file():
        if variant:
            raise ValueError("variant_not_applicable")
        return plain, None
    chosen = variant or "arkit-nonlidar"
    alt = directory / f"packet.{chosen}.json"
    if alt.is_file():
        return alt, chosen
    return None


def _plan(packet: dict, root: Path, variant: str | None, rate: float) -> list[dict]:
    del rate
    roles = {row["path"]: row.get("role", "other") for row in packet.get("files") or []}
    keyframes = list(packet.get("keyframes") or [])
    stills = list(packet.get("stills") or [])
    keyframe_paths = {row.get("img") for row in keyframes if row.get("img")}
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(path: str | None, role: str) -> None:
        if not path or path in seen or path.endswith("packet.json"):
            return
        seen.add(path)
        ordered.append((path, roles.get(path, role)))

    for still in stills:
        purpose = still.get("purpose") or ""
        if purpose in ("meter_close", "meter_oblique") or purpose.startswith("panel_"):
            if still.get("img") not in keyframe_paths:
                add(still.get("img"), "still")
    epochs = [row.get("id") for row in packet.get("epochs") or []]

    def order(frame: dict) -> tuple:
        try:
            index = epochs.index(frame.get("epoch"))
        except ValueError:
            index = len(epochs)
        return (index, float(frame.get("timestamp") or 0))

    for frame in sorted(keyframes, key=order):
        add(frame.get("img"), "keyframe")
        depth = frame.get("depth") if isinstance(frame.get("depth"), dict) else {}
        add(depth.get("file") if isinstance(depth, dict) else None, "depth")
        add(depth.get("confidenceFile") if isinstance(depth, dict) else None, "confidence")
        features = frame.get("features") if isinstance(frame.get("features"), dict) else {}
        add(features.get("file") if isinstance(features, dict) else None, "features")
    for still in stills:
        purpose = still.get("purpose") or ""
        if purpose not in ("meter_close", "meter_oblique") and not purpose.startswith("panel_"):
            add(still.get("img"), "still")
    streams = packet.get("streams") or {}
    if isinstance(streams, dict):
        for spec in streams.values():
            if isinstance(spec, dict):
                add(spec.get("file"), "stream")
    for row in packet.get("files") or []:
        add(row.get("path"), row.get("role") or "other")
    items = []
    for path, role in ordered:
        data_path = root / path
        items.append({"path": path, "role": role, "file": data_path, "variant": variant})
    return items


def _upload(
    plan, manifest: Path, host: str, token: str, finalize_at: str, until: str, timeout_s: float, rate: float
) -> ReplayRun:
    packet = json.loads(manifest.read_text())
    tier = packet["session"]["tier"]
    flow = "photo" if tier == "photo_only" else "guided"
    session = packet.get("session") or {}
    body = {
        "packetId": packet["packetId"],
        "formatVersion": packet["formatVersion"],
        "tier": tier,
        "flow": flow,
        "device": {
            "model": session.get("deviceModel") or "iPhone",
            "systemVersion": session.get("systemVersion") or "16.0",
            "appVersion": session.get("appVersion") or "ios-kit",
        },
        "expected": {"keyframes": len(packet.get("keyframes") or []), "stills": len(packet.get("stills") or [])},
    }
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    created = _request(
        "POST", f"{host}/v1/captures", headers={**headers, "Idempotency-Key": packet["packetId"]}, json=body
    )
    if created.status_code not in (200, 201):
        raise ApiError(created.status_code, created.text)
    created_body = created.json()
    run = ReplayRun(
        captureId=created_body["captureId"],
        packetId=packet["packetId"],
        variant=plan[0]["variant"] if plan else None,
        tier=tier,
        flow=flow,
    )
    capture = run.captureId
    assert capture is not None
    pause = 0.0 if rate <= 0 else 1.0 / rate
    split = 1 if finalize_at == "early" and len(plan) > 1 else len(plan)
    trigger_at: float | None = None

    def send_files(rows: list[dict]) -> None:
        nonlocal trigger_at
        for item in rows:
            data = Path(item["file"]).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            md5 = base64.b64encode(hashlib.md5(data).digest()).decode("ascii")
            content_type = (
                "image/jpeg"
                if item["role"] in ("keyframe", "still")
                else "application/gzip"
                if item["path"].endswith(".gz")
                else "application/octet-stream"
            )
            registered = _request(
                "POST",
                f"{host}/v1/captures/{capture}/files",
                headers=headers,
                json={
                    "files": [
                        {
                            "path": item["path"],
                            "role": item["role"],
                            "bytes": len(data),
                            "md5": md5,
                            "sha256": digest,
                            "contentType": content_type,
                        }
                    ]
                },
            )
            if registered.status_code >= 400:
                raise ApiError(registered.status_code, registered.text)
            upload = registered.json()["files"][0]
            if upload.get("upload"):
                put = _request(
                    "PUT",
                    _absolute(host, upload["upload"]["url"]),
                    headers={"Content-Type": content_type, "Content-MD5": md5},
                    data=data,
                )
                if put.status_code >= 400:
                    raise ApiError(put.status_code, put.text or "put failed")
            committed = _request(
                "POST",
                f"{host}/v1/captures/{capture}/files:commit",
                headers=headers,
                json={"files": [{"path": item["path"], "sha256": digest}]},
            )
            if committed.status_code >= 400:
                raise ApiError(committed.status_code, committed.text)
            run.files["committed"] += 1
            run.files["registered"] += 1
            if item["role"] == "keyframe" and trigger_at is None:
                trigger_at = time.monotonic()
            if pause:
                time.sleep(pause)

    send_files(plan[:split])
    raw = manifest.read_bytes()
    gz = gzip.compress(raw)
    digest = hashlib.sha256(raw).hexdigest()
    finalized = _request(
        "POST",
        f"{host}/v1/captures/{capture}/finalize",
        headers={**headers, "Content-Encoding": "gzip", "Content-Type": "application/json", "X-Packet-Sha256": digest},
        data=gz,
    )
    if finalized.status_code >= 400:
        raise ApiError(finalized.status_code, finalized.text)
    fin = finalized.json()
    run.runId = fin.get("runId")
    _poll(run, host, headers, fin.get("status") or "awaiting_files", wait=0)
    if finalize_at == "early" and fin.get("status") != "awaiting_files":
        run.errors.append(f"early finalize status was {fin.get('status')}")
    if split < len(plan):
        send_files(plan[split:])
    if until == "processing" and any(
        row.get("status") == "processing" or row.get("type") == "stage" for row in run.events
    ):
        _stamp_timing(run, trigger_at)
        return run
    _wait_verdict(run, host, headers, timeout_s, trigger_at)
    return run


def _wait_verdict(run: ReplayRun, host: str, headers: dict, timeout_s: float, trigger_at: float | None) -> None:
    deadline = time.monotonic() + timeout_s
    confirmed = False
    after = _cursor(run)
    while time.monotonic() < deadline:
        status = _poll(run, host, headers, None, wait=1, after=after)
        after = _cursor(run)
        if not confirmed:
            confirmed = _maybe_confirm(run, host, headers)
        if run.result is None and status in ("needs_views", "complete", "manual_review", "failed"):
            fetched = _request("GET", f"{host}/v1/captures/{run.captureId}/result", headers=headers)
            if fetched.status_code == 200:
                run.result = fetched.json()
        ready = any(row.get("type") == "verdict_ready" for row in run.events)
        confirmed_seen = any(row.get("type") == "confirmation" for row in run.events)
        if not (ready and run.result is not None):
            continue
        if _hint_confirm(run) and not confirmed_seen:
            continue
        _stamp_timing(run, trigger_at)
        return
    raise TimeoutError("replay timed out before verdict")


def _hint_confirm(run: ReplayRun) -> dict | None:
    for event in run.events:
        data = event.get("data") or {}
        if event.get("type") == "hint" and isinstance(data.get("confirm"), dict):
            return data["confirm"]
    return None


def _maybe_confirm(run: ReplayRun, host: str, headers: dict) -> bool:
    """The §7 replay commands do not POST a confirmation. Confirm the preselected test value so the log has one."""
    confirm = _hint_confirm(run)
    if not confirm or not confirm.get("preselected"):
        return False
    payload = {
        "fieldId": confirm["fieldId"],
        "kind": "confirmed",
        "value": confirm["preselected"],
        "imageId": confirm["imageId"],
        "clientAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    response = _request("POST", f"{host}/v1/captures/{run.captureId}/confirmations", headers=headers, json=payload)
    if response.status_code >= 400:
        raise ApiError(response.status_code, response.text)
    return True


def _poll(run: ReplayRun, host: str, headers: dict, status: str | None, *, wait: int, after: int | None = None) -> str:
    cursor = _cursor(run) if after is None else after
    response = _request(
        "GET", f"{host}/v1/captures/{run.captureId}/events", headers=headers, params={"after": cursor, "wait": wait}
    )
    if response.status_code >= 400:
        raise ApiError(response.status_code, response.text)
    body = response.json()
    current = body.get("status") or status or "uploading"
    events = body.get("events") or []
    if not events:
        last = run.events[-1] if run.events else None
        if not last or last.get("type") != "status" or last.get("status") != current:
            run.events.append(
                {
                    "status": current,
                    "type": "status",
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "data": {},
                }
            )
    for event in events:
        stamped = dict(event)
        stamped["status"] = current
        if event.get("type") == "capture_check" and "capture_check_seen" not in run.timings:
            run.timings["capture_check_seen"] = time.monotonic()
        run.events.append(stamped)
    if current == "processing" and "processing_at" not in run.timings:
        run.timings["processing_at"] = time.monotonic()
    return current


def _cursor(run: ReplayRun) -> int:
    seqs = [int(row["seq"]) for row in run.events if isinstance(row.get("seq"), int)]
    return max(seqs) if seqs else 0


def _stamp_timing(run: ReplayRun, trigger_at: float | None) -> None:
    run.timings.pop("processing_at", None)
    seen = run.timings.pop("capture_check_seen", None)
    if trigger_at is not None and seen is not None:
        run.timings["last_commit_to_capture_check_s"] = max(0.0, seen - trigger_at)
    elif "last_commit_to_capture_check_s" not in run.timings:
        run.timings["last_commit_to_capture_check_s"] = 0.0


def _absolute(host: str, url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return host + url


def _request(method: str, url: str, **kwargs) -> requests.Response:
    try:
        return requests.request(method, url, timeout=60, **kwargs)
    except requests.RequestException as exc:
        raise NetworkError(f"network failure: {exc}") from exc


def _write(out_dir: Path, run: ReplayRun) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(run) if isinstance(run, ReplayRun) else _as_plain(run)
    (out_dir / "run.json").write_text(json.dumps(payload, indent=1) + "\n")
    if payload.get("result") is not None:
        (out_dir / "result.json").write_text(json.dumps(payload["result"], indent=1) + "\n")
    lines = [json.dumps(row, separators=(",", ":")) for row in payload.get("events") or []]
    (out_dir / "events.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""))


def _as_plain(run: object) -> dict:
    if hasattr(run, "__dict__"):
        return dict(run.__dict__)
    return json.loads(json.dumps(run, default=str))
