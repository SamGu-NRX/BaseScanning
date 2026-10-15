"""Interim manifest checks until server.orchestrator.validate_manifest exists."""

from __future__ import annotations

import numpy as np

from server.common.geometry import from_column_major, pixel_ray_arkit
from server.contracts.packet import Packet
from server.contracts.validation import ValidationIssue, check_packet


def _issue(code: str, pointer: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, level="error", pointer=pointer, message=message)


def manifest_errors(packet: Packet) -> list[ValidationIssue]:
    """WS-02's validate_manifest when it is implemented, otherwise the interim checks."""
    try:
        import server.orchestrator as orchestrator

        if not getattr(orchestrator, "STUB", False):
            report = orchestrator.validate_manifest(packet.model_dump(mode="json", exclude_none=True))
            return list(report.errors)
    except NotImplementedError:
        pass
    report = check_packet(packet)
    errors = [_issue("schema", item.pointer, item.message) if item.code != "schema" else item for item in report.errors]
    by_path = {entry.path: (index, entry) for index, entry in enumerate(packet.files)}
    for index, tap in enumerate(packet.taps or []):
        keyframe = next((item for item in packet.keyframes if item.id == tap.keyframe), None)
        if keyframe is None:
            continue
        origin, direction = pixel_ray_arkit(
            from_column_major(keyframe.pose),
            keyframe.intrinsics,
            np.asarray(tap.pixel, dtype=float),
        )
        origin_err = float(np.max(np.abs(origin - np.asarray(tap.rayOrigin, dtype=float))))
        direction_err = float(np.max(np.abs(direction - np.asarray(tap.rayDirection, dtype=float))))
        if origin_err > 1e-3 or direction_err > 1e-3:
            errors.append(_issue("tap_replay", f"/taps/{index}", "tap ray does not match the keyframe"))
    for keyframe in packet.keyframes:
        depth = keyframe.depth
        if depth is None:
            continue
        listed = by_path.get(depth.file)
        if listed is not None and listed[1].bytes != depth.w * depth.h * 4:
            errors.append(_issue("depth_size", f"/files/{listed[0]}/bytes", "depth byte count is not w*h*4"))
        if depth.confidenceFile:
            conf = by_path.get(depth.confidenceFile)
            if conf is not None and conf[1].bytes != depth.w * depth.h:
                errors.append(_issue("confidence_size", f"/files/{conf[0]}/bytes", "confidence byte count is not w*h"))
        if keyframe.features is not None:
            feats = by_path.get(keyframe.features.file)
            if feats is not None and feats[1].bytes != 20 * keyframe.features.count:
                errors.append(
                    _issue("features_size", f"/files/{feats[0]}/bytes", "features byte count is not 20*count")
                )
    return errors
