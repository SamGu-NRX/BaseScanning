"""Build an amendment packet: a normal v0.4 packet that points at its parent."""

from __future__ import annotations

import json
import uuid


def subset_packet(
    pk: dict,
    keep_keyframe_ids: set[str],
    keep_still_ids: set[str],
    parent_packet_id: str,
) -> dict:
    out = json_copy(pk)
    out["packetId"] = str(uuid.uuid4())
    out["parentPacketId"] = parent_packet_id
    out["formatVersion"] = pk.get("formatVersion")
    keyframes = [row for row in pk.get("keyframes") or [] if row.get("id") in keep_keyframe_ids]
    stills = [row for row in pk.get("stills") or [] if row.get("id") in keep_still_ids]
    taps = [row for row in pk.get("taps") or [] if row.get("keyframe") in keep_keyframe_ids]
    out["keyframes"] = keyframes
    out["stills"] = stills
    out["taps"] = taps
    scale = out.get("scaleReference")
    if isinstance(scale, dict):
        ext = scale.get("ext")
        if isinstance(ext, dict) and isinstance(ext.get("doorViews"), list):
            ext["doorViews"] = [
                row for row in ext["doorViews"] if row.get("frame") in keep_keyframe_ids | keep_still_ids
            ]
    kept_paths = _referenced_paths(out)
    out["files"] = [row for row in pk.get("files") or [] if row.get("path") in kept_paths]
    out["files"].sort(key=lambda row: row["path"])
    return out


def json_copy(value: dict) -> dict:
    return json.loads(json.dumps(value))


def _referenced_paths(pk: dict) -> set[str]:
    paths: set[str] = set()
    for frame in pk.get("keyframes") or []:
        _add(paths, frame.get("img"))
        depth = frame.get("depth") or {}
        if isinstance(depth, dict):
            _add(paths, depth.get("file"))
            _add(paths, depth.get("confidenceFile"))
        features = frame.get("features") or {}
        if isinstance(features, dict):
            _add(paths, features.get("file"))
    for still in pk.get("stills") or []:
        _add(paths, still.get("img"))
    streams = pk.get("streams") or {}
    if isinstance(streams, dict):
        for spec in streams.values():
            if isinstance(spec, dict):
                _add(paths, spec.get("file"))
    return paths


def _add(paths: set[str], value: object) -> None:
    if isinstance(value, str) and value:
        paths.add(value)
