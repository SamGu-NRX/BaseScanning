"""Replay client: the upload plan that stands in for the iOS app.

``plan_uploads`` is pure and tested. ``replay`` loads ``packet.json`` with
``server.contracts.packet.Packet``. An unreachable API exits 2. A document the contract
rejects exits 4. This branch does not invent a capture id.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import random
import sys
import tarfile
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests
from pydantic import ValidationError

from server.contracts.result import ResultResponse

try:
    from server.contracts.packet import Packet
except ImportError:
    Packet = None

_SESSION_LOCK = threading.Lock()
_STILL_FIRST = ("meter_close", "meter_oblique")
_META_KEY = {"keyframe": "keyframe", "still": "still", "truedepth": "trueDepthFrame"}


class PacketLayoutError(ValueError):
    """The packet path does not match ``--variant`` (SD-44)."""


class ReplayError(RuntimeError):
    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


@dataclass
class UploadItem:
    path: str
    role: str
    content_type: str
    meta: dict | None = None

    def register(self, data: bytes) -> dict:
        body = {
            "path": self.path,
            "role": self.role,
            "bytes": len(data),
            "md5": base64.b64encode(hashlib.md5(data).digest()).decode("ascii"),
            "sha256": hashlib.sha256(data).hexdigest(),
            "contentType": self.content_type,
        }
        if self.meta is not None:
            body["meta"] = self.meta
        return body


@dataclass
class UploadPlan:
    packet_id: str
    variant: str | None
    tier: str
    flow: str
    capture_body: dict
    files: list[UploadItem] = field(default_factory=list)
    batch_interval_s: float = 2.0
    finalize_name: str = "packet.json"

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["files"] = [asdict(item) for item in self.files]
        return payload


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


def batch_interval_s(rate: float) -> float:
    """Seconds between register batches. ``0`` means as fast as possible."""
    if rate <= 0:
        return 0.0
    return min(2.0, max(1.0, 50.0 / rate))


def content_type_for(path: str, role: str) -> str:
    lower = path.lower()
    if role == "still" and lower.endswith(".heic"):
        return "image/heic"
    if role in ("keyframe", "still") or lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if role == "stream" or lower.endswith(".csv.gz"):
        return "application/gzip"
    return "application/octet-stream"


def plan_uploads(packet: dict, *, variant: str | None = None, rate: float = 2.0) -> UploadPlan:
    """Order files the way Appendix D and the WS-11 brief require.

    A still whose image is also a keyframe is not registered twice (that would be
    ``path_duplicate``). The keyframe role is kept, because that is the pose input.
    """
    tier = (packet.get("session") or {}).get("tier") or "arkit"
    flow = "photo" if tier == "photo_only" else "guided"
    keyframes = list(packet.get("keyframes") or [])
    stills = list(packet.get("stills") or [])
    keyframe_images = {kf.get("img") for kf in keyframes if kf.get("img")}
    items: list[UploadItem] = []
    seen: set[str] = set()

    def add(path: str | None, role: str, record: dict | None) -> None:
        if not path or path in seen or path == "packet.json" or path.endswith("/packet.json"):
            return
        seen.add(path)
        meta = None
        key = _META_KEY.get(role)
        if key and record is not None:
            meta = {key: record}
        items.append(UploadItem(path, role, content_type_for(path, role), meta))

    for still in stills:
        if _still_first(still) and still.get("img") not in keyframe_images:
            add(still.get("img"), "still", still)
    for kf in sorted(keyframes, key=_keyframe_order(packet)):
        add(kf.get("img"), "keyframe", kf)
        add(_nested_file(kf.get("depth")), "depth", None)
        depth = kf.get("depth") or {}
        add(depth.get("confidenceFile"), "confidence", None)
        add(_nested_file(kf.get("features")), "features", None)
    for still in stills:
        if not _still_first(still):
            add(still.get("img"), "still", still)
    streams = packet.get("streams") or {}
    if isinstance(streams, dict):
        for spec in streams.values():
            path = spec.get("file") if isinstance(spec, dict) else None
            if path and str(path).endswith(".csv.gz"):
                add(path, "stream", None)
    geometry = packet.get("geometry") or {}
    mesh = geometry.get("mesh") if isinstance(geometry, dict) else None
    if isinstance(mesh, dict):
        mesh = mesh.get("file")
    add(mesh, "mesh", None)
    # Optional resources (including TrueDepth and compatibility files) still
    # belong to the accepted manifest even when not named by the traversal above.
    for entry in packet.get("files") or []:
        add(entry.get("path"), entry.get("role") or "other", None)
    body = {
        "packetId": packet.get("packetId"),
        "formatVersion": packet.get("formatVersion"),
        "tier": tier,
        "flow": flow,
        "device": {
            "model": (packet.get("session") or {}).get("deviceModel"),
            "systemVersion": (packet.get("session") or {}).get("systemVersion"),
            "appVersion": (packet.get("session") or {}).get("appVersion"),
        },
        "expected": {"keyframes": len(keyframes), "stills": len(stills)},
    }
    return UploadPlan(
        packet_id=str(packet.get("packetId") or ""),
        variant=variant,
        tier=tier,
        flow=flow,
        capture_body=body,
        files=items,
        batch_interval_s=batch_interval_s(rate),
    )


def resolve_packet_file(packet: Path, variant: str | None) -> tuple[Path, str | None]:
    """Return the packet JSON and the variant it came from.

    A folder (or file) that already has ``packet.json`` is variant-less: passing
    ``--variant`` is an error. Otherwise ``packet.<variant>.json`` is used, default
    ``arkit-nonlidar``.
    """
    path = Path(packet)
    if path.is_file() and path.name == "packet.json":
        if variant:
            raise PacketLayoutError("--variant on a variant-less packet.json is an error")
        return path, None
    if path.is_dir():
        plain = path / "packet.json"
        if plain.is_file():
            if variant:
                raise PacketLayoutError("--variant on a variant-less folder is an error")
            return plain, None
        chosen = variant or "arkit-nonlidar"
        alt = path / f"packet.{chosen}.json"
        if not alt.is_file():
            raise PacketLayoutError(f"missing {alt.name}")
        return alt, chosen
    if path.is_file() and ".tar" in path.name:
        return path, variant
    raise PacketLayoutError(f"unrecognised packet path {path}")


def replay(
    packet: Path,
    api: str,
    token: str,
    *,
    variant: str = "arkit-nonlidar",
    rate: float = 2.0,
    concurrency: int = 4,
    finalize_at: str = "last",
    until: str = "verdict",
    timeout_s: float = 900,
    out_dir: Path | None = None,
    session: requests.Session | None = None,
    sleeper=time.sleep,
) -> ReplayRun:
    """Upload ``packet`` and wait. Raises ``ReplayError`` with the brief's exit code."""
    if Packet is None:
        raise ReplayError(
            "server.contracts.packet.Packet is not importable (WS-00 is not on main); refusing to invent a captureId",
            2,
        )
    if not api:
        raise ReplayError("CAPTURE_API_URL is unset", 2)
    json_path, used = _materialise(Path(packet), None if variant == "arkit-nonlidar" else variant)
    document = json.loads(json_path.read_text())
    try:
        Packet.model_validate(document)
    except ValidationError as exc:
        raise ReplayError(f"packet failed contract validation: {exc}", 4) from exc
    plan = plan_uploads(document, variant=used, rate=rate)
    return _execute(
        plan,
        json_path,
        api.rstrip("/"),
        token,
        finalize_at,
        until,
        timeout_s,
        concurrency=concurrency,
        out_dir=out_dir,
        session=session or requests.Session(),
        sleeper=sleeper,
    )


def _execute(
    plan,
    json_path,
    api,
    token,
    finalize_at,
    until,
    timeout_s,
    *,
    concurrency: int,
    out_dir: Path | None,
    session: requests.Session,
    sleeper,
) -> ReplayRun:
    """PUT, commit, retry, poll events, and download the result."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    run = ReplayRun(packetId=plan.packet_id, variant=plan.variant, tier=plan.tier, flow=plan.flow)
    deadline = time.monotonic() + timeout_s
    created = _request(
        session,
        "POST",
        f"{api}/v1/captures",
        headers={**headers, "Idempotency-Key": plan.packet_id},
        json=plan.capture_body,
        timeout=60,
        sleeper=sleeper,
        deadline=deadline,
    )
    _raise_for_status(created, out_dir)
    body = created.json()
    run.captureId = body.get("captureId")
    batch_size = min(50, (body.get("upload") or {}).get("maxBatch", 50))
    root = json_path.parent
    committed: list[str] = []
    last_keyframe_commit: float | None = None
    groups = _commit_groups(plan.files, finalize_at)
    if created.status_code == 200:
        current = _request(
            session,
            "GET",
            f"{api}/v1/captures/{run.captureId}",
            headers=headers,
            timeout=60,
            sleeper=sleeper,
            deadline=deadline,
        )
        _raise_for_status(current, out_dir)
        status = current.json()
        if status.get("status") in {"failed", "expired"}:
            raise ReplayError(f"capture cannot resume from {status['status']}", 3)
        if status.get("status") in {"processing", "complete", "needs_views", "manual_review"}:
            # Create replays its original response, whose status is still
            # uploading. The fresh status prevents forbidden re-registration.
            groups = []
            run.files["registered"] = status.get("filesRegistered", 0)
            run.files["committed"] = status.get("filesCommitted", 0)
    for index, batch_group in enumerate(groups):
        for batch in _chunks(batch_group, batch_size):
            ok, bad, keyframe_commit = _register_commit(
                session, api, run.captureId, headers, root, batch, concurrency, sleeper, deadline
            )
            committed.extend(ok)
            if keyframe_commit is not None:
                last_keyframe_commit = keyframe_commit
            for path in bad:
                run.errors.append(f"mismatch:{path}")
                run.files["mismatch"].append(path)
            run.files["registered"] = int(run.files.get("registered", 0)) + len(batch)
            run.files["committed"] = len(committed)
        if finalize_at == "early" and index == 0 and len(groups) > 1:
            early = _finalize(session, api, run, plan, json_path, headers, out_dir, sleeper, deadline)
            if early.get("status") != "awaiting_files" or not early.get("missing"):
                run.errors.append(
                    f"early finalize status was {early.get('status')!r}, expected awaiting_files with missing files"
                )
        if index + 1 < len(groups) and plan.batch_interval_s:
            sleeper(plan.batch_interval_s)
    final = _finalize(session, api, run, plan, json_path, headers, out_dir, sleeper, deadline)
    if finalize_at == "last":
        if final.get("status") != "processing" or final.get("missing") not in ([], None):
            run.errors.append(
                f"finalize status was {final.get('status')!r} missing={final.get('missing')!r}, expected processing"
            )
    if until == "processing":
        return run
    _poll_events(session, api, run, headers, last_keyframe_commit, sleeper, deadline)
    _download_result(session, api, run, headers, out_dir, sleeper, deadline)
    return run


def _commit_groups(files: list, finalize_at: str) -> list[list]:
    """Early finalize commits the first half, then the rest. Last commits everything."""
    if not files:
        return []
    if finalize_at != "early" or len(files) < 2:
        return [files]
    mid = len(files) // 2
    return [files[:mid], files[mid:]]


def _register_commit(session, api, capture_id, headers, root, batch, concurrency, sleeper, deadline):
    payloads = []
    blobs = {}
    for item in batch:
        data = (root / item.path).read_bytes()
        blobs[item.path] = data
        payloads.append(item.register(data))
    registered = _request(
        session,
        "POST",
        f"{api}/v1/captures/{capture_id}/files",
        headers=headers,
        json={"files": payloads},
        timeout=60,
        sleeper=sleeper,
        deadline=deadline,
    )
    _raise_for_status(registered, None)
    targets = {row["path"]: row for row in registered.json().get("files") or []}
    workers = max(1, concurrency)

    def put_one(item):
        _put_target(session, targets.get(item.path) or {}, blobs[item.path], headers, sleeper, deadline)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(put_one, batch))
    committed_at = time.time()
    commit_files = [{"path": item["path"], "sha256": item["sha256"]} for item in payloads]
    by_path = {item["path"]: item for item in commit_files}
    committed, mismatch = _commit_paths(session, api, capture_id, headers, commit_files, sleeper, deadline)
    still_bad = []
    for path in mismatch:
        _put_target(session, targets.get(path) or {}, blobs[path], headers, sleeper, deadline)
        again, left = _commit_paths(session, api, capture_id, headers, [by_path[path]], sleeper, deadline)
        committed.extend(again)
        if left:
            still_bad.append(path)
    keyframe_commit = committed_at if any(item.role == "keyframe" for item in batch) else None
    return committed, still_bad, keyframe_commit


def _commit_paths(session, api, capture_id, headers, files, sleeper, deadline):
    response = _request(
        session,
        "POST",
        f"{api}/v1/captures/{capture_id}/files:commit",
        headers=headers,
        json={"files": files},
        timeout=60,
        sleeper=sleeper,
        deadline=deadline,
    )
    _raise_for_status(response, None)
    body = response.json()
    return list(body.get("committed") or []), list(body.get("mismatch") or [])


def _put_target(session, spec, data, headers, sleeper, deadline):
    if spec.get("state") == "committed":
        return
    upload = spec.get("upload") or {}
    method = upload.get("method") or "PUT"
    if method == "MULTIPART":
        part_bytes = int(upload.get("partBytes") or len(data) or 1)
        parts = upload.get("parts") or []
        for index, part in enumerate(parts):
            start = index * part_bytes
            chunk = data[start : start + part_bytes]
            _request(
                session,
                "PUT",
                part["url"],
                headers=part.get("headers") or {},
                data=chunk,
                timeout=120,
                sleeper=sleeper,
                deadline=deadline,
            )
        complete = upload.get("completeUrl")
        if complete:
            done = _request(
                session,
                "POST",
                complete,
                headers=headers,
                json={"parts": len(parts)},
                timeout=60,
                sleeper=sleeper,
                deadline=deadline,
            )
            _raise_for_status(done, None)
        return
    url = upload.get("url")
    if not url:
        raise ReplayError(f"register response has no upload url for {spec.get('path')}", 2)
    put_headers = upload.get("headers") or {}
    response = _request(
        session, "PUT", url, headers=put_headers, data=data, timeout=120, sleeper=sleeper, deadline=deadline
    )
    if response.status_code == 403:
        raise ReplayError("storage signature expired", 2)
    if response.status_code == 400 and "BadDigest" in response.text:
        raise ReplayError(f"BadDigest for {spec.get('path')}", 2)
    if response.status_code >= 400:
        raise ReplayError(f"PUT failed: {response.status_code}", 2)


def _finalize(session, api, run, plan, json_path, headers, out_dir, sleeper, deadline) -> dict:
    raw = json_path.read_bytes()
    gz = gzip.compress(raw)
    digest = hashlib.sha256(raw).hexdigest()
    response = _request(
        session,
        "POST",
        f"{api}/v1/captures/{run.captureId}/finalize",
        headers={
            **headers,
            "Content-Encoding": "gzip",
            "Content-Type": "application/json",
            "Idempotency-Key": plan.packet_id,
            "X-Packet-Sha256": digest,
        },
        data=gz,
        timeout=120,
        sleeper=sleeper,
        deadline=deadline,
    )
    if response.status_code == 422:
        dest = Path(str(out_dir) + ".422.json") if out_dir else Path("replay.422.json")
        dest.write_text(response.text)
        raise ReplayError(response.text, 4)
    _raise_for_status(response, out_dir, ok=(202,))
    body = response.json()
    run.runId = body.get("runId") or run.runId
    return body


def _poll_events(session, api, run, headers, last_keyframe_commit, sleeper, deadline):
    seq = 0
    while time.monotonic() < deadline:
        response = _request(
            session,
            "GET",
            f"{api}/v1/captures/{run.captureId}/events",
            headers=headers,
            params={"after": seq, "wait": 25},
            timeout=30,
            sleeper=sleeper,
            deadline=deadline,
        )
        _raise_for_status(response, None)
        payload = response.json()
        for event in payload.get("events") or []:
            seq = max(seq, int(event.get("seq") or 0))
            record = dict(event)
            record["wall_s"] = time.time()
            run.events.append(record)
            if event.get("type") == "capture_check" and last_keyframe_commit is not None:
                run.timings["last_commit_to_capture_check_s"] = record["wall_s"] - last_keyframe_commit
            if event.get("type") in ("verdict_ready", "retake_request", "failed"):
                return
        if not payload.get("events"):
            sleeper(0)
    raise ReplayError("timed out waiting for verdict_ready", 5)


def _download_result(session, api, run, headers, out_dir, sleeper, deadline):
    response = _request(
        session,
        "GET",
        f"{api}/v1/captures/{run.captureId}/result",
        headers=headers,
        timeout=60,
        sleeper=sleeper,
        deadline=deadline,
    )
    _raise_for_status(response, None)
    result = response.json()
    try:
        ResultResponse.model_validate(result)
    except ValidationError as exc:
        raise ReplayError("result failed contract validation", 4) from exc
    run.result = result
    if out_dir is None:
        return
    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    if url := result.get("verdictUrl"):
        fetched = _request(session, "GET", url, headers={}, timeout=60, sleeper=sleeper, deadline=deadline)
        _raise_for_status(fetched, None)
        verdict = fetched.json()
        profile = (verdict.get("evaluator") or {}).get("rules_profile") or "default"
        path = dest / "verdict.json"
        path.write_bytes(fetched.content)
        run.verdict_paths[profile] = str(path)


def _request(session, method, url, *, headers, sleeper, deadline, timeout, **kwargs):
    delay = 1.0
    last = None
    for attempt in range(8):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _deadline_error(session)
        try:
            with _SESSION_LOCK:
                response = session.request(method, url, headers=headers, timeout=min(timeout, remaining), **kwargs)
        except requests.RequestException as exc:
            last = exc
            if attempt == 7 or time.monotonic() >= deadline:
                if getattr(session, "hss_api_connected", False):
                    raise ReplayError("timed out waiting for the capture API", 5) from exc
                raise ReplayError(f"capture API unreachable: {exc}", 2) from exc
            pause = min(_backoff(delay, response=None), max(0.0, deadline - time.monotonic()))
            sleeper(pause)
            delay = min(60.0, delay * 2)
            continue
        session.hss_api_connected = True
        if response.status_code in (408, 429) or response.status_code >= 500:
            last = response
            if attempt == 7:
                raise ReplayError(f"{method} {url} failed: {response.status_code}", 2)
            retry_after = response.headers.get("Retry-After")
            pause = float(retry_after) if retry_after else _backoff(delay, response)
            sleeper(pause)
            delay = min(60.0, delay * 2)
            continue
        if response.status_code == 409:
            raise ReplayError(response.text, 3)
        return response
    raise ReplayError(f"{method} {url} failed: {last}", 2)


def _deadline_error(session) -> ReplayError:
    """Exit 2 when nothing ever connected. Exit 5 only after the API has answered."""
    if getattr(session, "hss_api_connected", False):
        return ReplayError("timed out waiting for the capture API", 5)
    return ReplayError("capture API unreachable", 2)


def _backoff(delay: float, response) -> float:
    del response
    return random.uniform(0, min(60.0, delay))


def _raise_for_status(response, out_dir, ok=(200, 201, 202)):
    del out_dir
    if response.status_code in ok:
        return
    if response.status_code == 409:
        raise ReplayError(response.text, 3)
    if response.status_code == 422:
        raise ReplayError(response.text, 4)
    raise ReplayError(f"capture API failed: {response.status_code} {response.text}", 2)


def _chunks(items, size):
    return [items[i : i + size] for i in range(0, len(items), size)] or [[]]


def _materialise(packet: Path, variant: str | None) -> tuple[Path, str | None]:
    if packet.is_file() and (".tar" in packet.suffixes or packet.name.endswith(".tar")):
        dest = Path(tempfile.mkdtemp(prefix="hss-replay-"))
        with tarfile.open(packet) as archive:
            archive.extractall(dest, filter="data")
        return resolve_packet_file(_packet_root(dest, variant), variant)
    return resolve_packet_file(packet, variant)


def _packet_root(dest: Path, variant: str | None) -> Path:
    """Fixture tars nest the packet under ``<site>/``."""
    try:
        resolve_packet_file(dest, variant)
        return dest
    except PacketLayoutError:
        pass
    for child in sorted(p for p in dest.iterdir() if p.is_dir()):
        try:
            resolve_packet_file(child, variant)
            return child
        except PacketLayoutError:
            continue
    return dest


def _still_first(still: dict) -> bool:
    purpose = still.get("purpose") or ""
    return purpose in _STILL_FIRST or purpose.startswith("panel_")


def _nested_file(value) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        path = value.get("file")
        if isinstance(path, str) and path:
            return path
    return None


def _keyframe_order(packet: dict):
    epochs = [e.get("id") for e in packet.get("epochs") or []]

    def key(kf: dict):
        try:
            index = epochs.index(kf.get("epoch"))
        except ValueError:
            index = len(epochs)
        return (index, float(kf.get("timestamp") or 0))

    return key


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="server.eval.replay")
    ap.add_argument("--packet", type=Path, required=True)
    ap.add_argument("--variant", default=None)
    ap.add_argument("--api", default=os.environ.get("CAPTURE_API_URL", ""))
    ap.add_argument("--token", default=None)
    ap.add_argument("--token-env", default="CAPTURE_API_TOKEN")
    ap.add_argument("--rate", type=float, default=2.0)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--finalize-at", choices=["last", "early"], default="last")
    ap.add_argument("--until", choices=["verdict", "processing"], default="verdict")
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        json_path, used = _materialise(args.packet, args.variant)
        document = json.loads(json_path.read_text())
        if args.dry_run:
            plan = plan_uploads(document, variant=used, rate=args.rate)
            text = json.dumps(plan.to_dict(), indent=2)
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(text + "\n")
            print(text)
            return 0
        token = args.token if args.token is not None else os.environ.get(args.token_env, "")
        if args.token is not None:
            print("pass the token with --token-env; a literal --token is not logged", file=sys.stderr)
        run = replay(
            args.packet,
            args.api,
            token,
            variant=args.variant or "arkit-nonlidar",
            rate=args.rate,
            concurrency=args.concurrency,
            finalize_at=args.finalize_at,
            until=args.until,
            timeout_s=args.timeout,
            out_dir=args.out.parent if args.out else None,
        )
    except PacketLayoutError as exc:
        print(exc, file=sys.stderr)
        return 2
    except ReplayError as exc:
        print(exc, file=sys.stderr)
        return exc.code
    payload = asdict(run)
    text = json.dumps(payload, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
