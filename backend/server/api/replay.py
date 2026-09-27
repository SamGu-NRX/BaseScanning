"""Interim replay client. WS-11 moves this to server/eval/replay.py. Same flags (SD-36)."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx


def resolve_packet(path: Path, variant: str | None) -> Path:
    """SD-44: packet.json wins. Passing --variant against that folder is an error."""
    if path.is_file():
        return path
    plain = path / "packet.json"
    chosen = variant or "arkit-nonlidar"
    named = path / f"packet.{chosen}.json"
    if plain.exists():
        if variant is not None:
            raise SystemExit(f"{path} is variant-less (packet.json); do not pass --variant")
        if named.exists():
            raise SystemExit(f"{path} holds both packet.json and {named.name}")
        return plain
    if not named.exists():
        raise SystemExit(f"no packet at {plain} or {named}")
    return named


def _order(packet: dict) -> list[dict]:
    still_purpose = {item.get("img"): item.get("purpose") or "" for item in packet.get("stills") or []}
    groups: list[str] = []
    for frame in packet.get("keyframes") or []:
        groups.append(frame["img"])
        depth = frame.get("depth") or {}
        if depth.get("file"):
            groups.append(depth["file"])
        if depth.get("confidenceFile"):
            groups.append(depth["confidenceFile"])
        features = frame.get("features") or {}
        if features.get("file"):
            groups.append(features["file"])
    rank = {path: index for index, path in enumerate(groups)}
    meter, frames, stills, streams, mesh, rest = [], [], [], [], [], []
    for entry in packet["files"]:
        purpose = still_purpose.get(entry["path"], "")
        if entry["role"] == "still" and ("meter" in purpose or "panel" in purpose):
            meter.append(entry)
        elif entry["path"] in rank:
            frames.append(entry)
        elif entry["role"] == "still":
            stills.append(entry)
        elif entry["role"] == "stream":
            streams.append(entry)
        elif entry["role"] == "mesh":
            mesh.append(entry)
        else:
            rest.append(entry)
    frames.sort(key=lambda item: rank[item["path"]])
    return meter + frames + stills + streams + mesh + rest


def _meta(packet: dict, entry: dict) -> dict | None:
    """Meta follows the file role. A still may reuse a keyframe image."""
    path = entry["path"]
    role = entry.get("role")
    if role == "still":
        for still in packet.get("stills") or []:
            if still.get("img") == path:
                return {"still": still}
        return None
    if role == "keyframe":
        for frame in packet.get("keyframes") or []:
            if frame.get("img") == path:
                return {"keyframe": frame}
        return None
    return None


def _content_type(role: str, tier: str) -> str:
    if role in {"keyframe", "still"}:
        return "image/jpeg"
    if role == "stream":
        return "application/gzip"
    if role in {"compat", "other"}:
        return "application/octet-stream"
    return "application/octet-stream"


def run_replay(
    *,
    packet_path: Path,
    api: str,
    token: str | None = None,
    variant: str | None = None,
    finalize_at: str = "last",
    until: str = "processing",
    timeout_s: float = 120,
    out: Path | None = None,
    client: httpx.Client | None = None,
) -> dict:
    packet_file = resolve_packet(packet_path, variant)
    packet = json.loads(packet_file.read_text())
    root = packet_file.parent
    ordered = _order(packet)
    own_client = client is None
    http = client or httpx.Client(timeout=60)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    started = time.perf_counter()
    try:
        created = http.post(
            f"{api}/captures",
            json={
                "packetId": packet["packetId"],
                "formatVersion": packet["formatVersion"],
                "tier": packet["session"]["tier"],
                "flow": packet["session"].get("flow")
                or ("photo" if packet["session"]["tier"] == "photo_only" else "guided"),
                "device": {
                    "model": packet["session"].get("deviceModel") or "iPhone",
                    "systemVersion": packet["session"].get("systemVersion") or "18",
                    "appVersion": packet["session"].get("appVersion") or "0",
                },
            },
            headers={**headers, "Idempotency-Key": packet["packetId"]},
        )
        if created.status_code == 409:
            return _finish(3, created.json(), out, started)
        if created.status_code not in {200, 201}:
            return _finish(
                4 if created.status_code == 422 else 1,
                {"status": created.status_code, "body": created.text},
                out,
                started,
            )
        capture_id = created.json()["captureId"]
        if created.status_code == 200:
            current = http.get(f"{api}/captures/{capture_id}", headers=headers)
            current.raise_for_status()
            body = current.json()
            if body.get("status") not in {"uploading", "awaiting_files"}:
                return _finish(
                    0,
                    {
                        "captureId": capture_id,
                        "runId": body.get("runId"),
                        "status": body.get("status"),
                        "committed": body.get("filesCommitted"),
                        "listed": len(packet["files"]),
                        "earlyStatus": None,
                        "replayed": True,
                    },
                    out,
                    started,
                )
        early_cut = 1
        if finalize_at == "early":
            meter = [item for item in ordered if item["role"] == "still"]
            early_cut = max(1, len(meter) and 1)
        chunks = [ordered[:early_cut], ordered[early_cut:]] if finalize_at == "early" else [ordered]
        committed = 0
        early_status = None
        for index, chunk in enumerate(chunks):
            committed += _upload(http, api, headers, capture_id, root, packet, chunk)
            if finalize_at == "early" and index == 0:
                early = _finalize(http, api, headers, capture_id, packet, packet_file)
                if isinstance(early, int):
                    return _finish(early, {"captureId": capture_id}, out, started)
                early_status = early.get("status")
        if finalize_at != "early":
            final = _finalize(http, api, headers, capture_id, packet, packet_file)
            if isinstance(final, int):
                return _finish(final, {"captureId": capture_id, "committed": committed}, out, started)
        status = _poll(http, api, headers, capture_id, until, timeout_s - (time.perf_counter() - started))
        if status is None:
            return _finish(5, {"captureId": capture_id, "committed": committed}, out, started)
        summary = {
            "captureId": capture_id,
            "runId": status.get("runId"),
            "status": status.get("status"),
            "committed": committed,
            "listed": len(packet["files"]),
            "earlyStatus": early_status,
            "seconds": round(time.perf_counter() - started, 3),
        }
        return _finish(0, summary, out, started)
    finally:
        if own_client:
            http.close()


def _upload(http, api, headers, capture_id, root, packet, entries) -> int:
    committed = 0
    pending: list[tuple[str, str]] = []
    last = time.perf_counter()
    for start in range(0, len(entries), 50):
        batch = entries[start : start + 50]
        body = []
        payloads = []
        for entry in batch:
            data = (root / entry["path"]).read_bytes()
            payloads.append((entry["path"], data, entry["sha256"]))
            item = {
                "path": entry["path"],
                "bytes": entry["bytes"],
                "sha256": entry["sha256"],
                "md5": _md5(data),
                "role": entry["role"],
                "contentType": _content_type(entry["role"], packet["session"]["tier"]),
            }
            meta = _meta(packet, entry)
            if meta:
                item["meta"] = meta
            body.append(item)
        registered = http.post(f"{api}/captures/{capture_id}/files", json={"files": body}, headers=headers)
        registered.raise_for_status()
        for spec, item in zip(payloads, registered.json()["files"], strict=True):
            path, data, digest = spec
            if item["state"] == "pending":
                target = item["upload"]
                put = http.put(target["url"], content=data, headers=target.get("headers") or {})
                put.raise_for_status()
            pending.append((path, digest))
            if len(pending) >= 10 or time.perf_counter() - last >= 2:
                committed += _commit(http, api, headers, capture_id, pending)
                pending = []
                last = time.perf_counter()
    if pending:
        committed += _commit(http, api, headers, capture_id, pending)
    return committed


def _commit(http, api, headers, capture_id, pending) -> int:
    response = http.post(
        f"{api}/captures/{capture_id}/files:commit",
        json={"files": [{"path": path, "sha256": digest} for path, digest in pending]},
        headers=headers,
    )
    if response.status_code == 409:
        raise SystemExit(3)
    response.raise_for_status()
    return len(response.json().get("committed") or [])


def _finalize(http, api, headers, capture_id, packet, packet_file):
    raw = packet_file.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    response = http.post(
        f"{api}/captures/{capture_id}/finalize",
        content=gzip.compress(raw),
        headers={
            **headers,
            "Idempotency-Key": packet["packetId"],
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "X-Packet-Sha256": digest,
        },
    )
    if response.status_code == 409:
        return 3
    if response.status_code == 422:
        return 4
    response.raise_for_status()
    return response.json()


def _poll(http, api, headers, capture_id, until, timeout_s):
    deadline = time.perf_counter() + max(timeout_s, 1)
    after = 0
    while time.perf_counter() < deadline:
        status = http.get(f"{api}/captures/{capture_id}", headers=headers)
        status.raise_for_status()
        body = status.json()
        if until == "processing" and body["status"] in {
            "processing",
            "complete",
            "needs_views",
            "manual_review",
            "failed",
        }:
            return body
        events = http.get(f"{api}/captures/{capture_id}/events", params={"after": after, "wait": 1}, headers=headers)
        events.raise_for_status()
        payload = events.json()
        after = payload["next"]
        if until == "verdict" and any(item["type"] in {"verdict_ready", "failed"} for item in payload["events"]):
            return body
        if body["status"] == "failed":
            return body
    return None


def _md5(data: bytes) -> str:
    import base64

    return base64.b64encode(hashlib.md5(data).digest()).decode()


def _finish(code: int, summary: dict, out: Path | None, started: float) -> dict:
    summary.setdefault("seconds", round(time.perf_counter() - started, 3))
    summary["exit"] = code
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", required=True)
    parser.add_argument("--variant", default=None)
    parser.add_argument("--api", required=True)
    parser.add_argument("--token", default=None)
    parser.add_argument("--token-env", default=None)
    parser.add_argument("--rate", type=float, default=0)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--finalize-at", choices=["last", "early"], default="last")
    parser.add_argument("--until", choices=["processing", "verdict"], default="processing")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    token = args.token or os.environ.get(args.token_env or "") or None
    summary = run_replay(
        packet_path=Path(args.packet),
        api=args.api.rstrip("/"),
        token=token,
        variant=args.variant,
        finalize_at=args.finalize_at,
        until=args.until,
        timeout_s=args.timeout,
        out=Path(args.out) if args.out else None,
    )
    return int(summary["exit"])


if __name__ == "__main__":
    sys.exit(main())
