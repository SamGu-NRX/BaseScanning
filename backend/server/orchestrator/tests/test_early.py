"""Early workers: claim-once dispatch, vision off, QA thresholds, frusta codes."""

from __future__ import annotations

import json
from typing import get_args

import cv2
import numpy as np

from server.contracts.capture_check import CaptureCheckCode, HintCode
from server.contracts.jobs import CommittedFile
from server.contracts.packet import FileRole
from server.orchestrator.api import dispatch_early
from server.orchestrator.early.capture_check import frusta_missing
from server.orchestrator.early.qa import classify, score_image
from server.orchestrator.services import bind
from server.orchestrator.stateio import get_json
from server.orchestrator.tests.conftest import build_services


def _file(path: str, role: FileRole, purpose: str) -> CommittedFile:
    return CommittedFile(
        path=path,
        role=role,
        sha256="ab" * 32,
        bytes=8,
        generation="1",
        key=f"uploads/{path}",
        meta={"purpose": purpose},
    )


def test_second_dispatch_spawns_no_second_worker(tmp_path) -> None:
    services, _uri, _digest = build_services(tmp_path, "memory")
    item = _file("s1", "still", "meter_close")
    with bind(services):
        dispatch_early("cap_test", [item])
        first = services.state.list_blobs("early:cap_test:")  # type: ignore[attr-defined]
        dispatch_early("cap_test", [item])
        second = services.state.list_blobs("early:cap_test:")  # type: ignore[attr-defined]
    assert first == second
    assert services.state.get_kv("early-worker:cap_test:s1")  # type: ignore[attr-defined]


def test_vision_off_skips_early_reads(tmp_path) -> None:
    services, _uri, _digest = build_services(tmp_path, "modal_dict")
    assert services.settings.VISION_EXTERNAL_CALLS == "off"
    item = _file("s1", "still", "meter_close")
    with bind(services):
        dispatch_early("cap_test", [item])
    keys = services.state.list_blobs("early:cap_test:reads:")  # type: ignore[attr-defined]
    assert keys == []


def test_qa_thresholds() -> None:
    assert "frames_blurry" in classify({"blur_var": 59, "mean_lum": 0.5, "clipped_share": 0.0})
    assert "frames_blurry" not in classify({"blur_var": 60, "mean_lum": 0.5, "clipped_share": 0.0})
    assert "frames_dark" in classify({"blur_var": 100, "mean_lum": 39 / 255, "clipped_share": 0.0})
    assert "frames_overexposed" in classify({"blur_var": 100, "mean_lum": 216 / 255, "clipped_share": 0.0})
    assert "frames_overexposed" in classify({"blur_var": 100, "mean_lum": 0.5, "clipped_share": 0.16})
    assert classify({"blur_var": 100, "mean_lum": 0.5, "clipped_share": 0.15}) == []


def test_score_image_on_flat_frame() -> None:
    dark = np.zeros((32, 32, 3), dtype=np.uint8)
    stats = score_image(dark)
    assert stats["mean_lum"] == 0.0
    assert "frames_dark" in classify(stats)


def test_frusta_empty_without_poses() -> None:
    assert frusta_missing([{"purpose": "meter_close"}]) == []


def test_frusta_codes_are_closed() -> None:
    pose = [0.0] * 16
    pose[0] = pose[5] = pose[10] = 1.0
    codes = frusta_missing([{"purpose": "meter_close", "pose": pose, "w": 100, "h": 100}])
    for code in codes:
        assert code in get_args(CaptureCheckCode) or code in get_args(HintCode)
    assert "ground_downward_w1" not in codes
    assert "too_few_keyframes" not in codes


def test_blurry_still_emits_hint(tmp_path) -> None:
    services, _uri, _digest = build_services(tmp_path, "memory")
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    key = "uploads/s1.png"
    services.storage.put_bytes(key, encoded.tobytes(), "image/png")  # type: ignore[attr-defined]
    item = CommittedFile(
        path="s1.png",
        role="still",
        sha256="cd" * 32,
        bytes=len(encoded),
        generation="1",
        key=key,
        meta={"purpose": "meter_close"},
    )
    with bind(services):
        dispatch_early("cap_test", [item])
    events = services.state.events_after("cap_test", 0)  # type: ignore[attr-defined]
    hints = [event for event in events if event.type == "hint"]
    assert hints
    assert hints[0].data["code"] == "frames_blurry"
    blob = get_json(services.state, "early:cap_test:qa:s1.png")
    assert blob["codes"]
    json.dumps(blob)
