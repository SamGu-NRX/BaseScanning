"""Outcome lattice, AR placement, events, and the reviewer page."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pytest
from jsonschema import Draft202012Validator

from server.common.backends.state_memory import MemoryState
from server.contracts.jobs import CaptureRecord, RunRecord, StageContext
from server.contracts.result import ResultResponse
from server.result import ar, notify
from server.result.compose import (
    ComposeResult,
    ResultError,
    _deliver,
    _route,
    compose_result,
    emit_events,
    run,
)
from server.result.config import ResultConfig
from server.result.copy import FALLBACK, load_catalogue
from server.result.harness import chain as chain_mod
from server.result.harness import frame as frame_mod
from server.result.tests.support import DATA

_ROOT = Path(__file__).resolve().parents[3]
SCHEMA = json.loads((_ROOT / "server/contracts/schemas/result.v0.schema.json").read_text())
_SNAKE = re.compile(r'"[A-Za-z0-9]*_[A-Za-z0-9]+"\s*:')


def _catalogue():
    return load_catalogue(DATA / "prompts.en-US.json")


def _capture_run(name: str = "run.ok.json") -> tuple[CaptureRecord, RunRecord, list[str]]:
    raw = json.loads((DATA / "capture.sample.json").read_text())
    epochs = list(raw.pop("epochs"))
    return CaptureRecord.model_validate(raw), RunRecord.model_validate(json.loads((DATA / name).read_text())), epochs


def _go(tmp_path: Path, verdicts: Path, run_name: str = "run.ok.json", **kwargs):
    capture, run_rec, epochs = _capture_run(run_name)
    config = kwargs.pop("config", ResultConfig(catalogue_path=DATA / "prompts.en-US.json"))
    catalogue = kwargs.pop("catalogue", None)
    return compose_result(
        capture,
        run_rec,
        kwargs.pop("scene", DATA / "synthetic"),
        verdicts,
        tmp_path,
        reads=kwargs.pop("reads", DATA / "reads.auto_fill.json"),
        scale=kwargs.pop("scale", None),
        catalogue=_catalogue() if catalogue is None else catalogue,
        config=config,
        epochs=epochs,
    )


def _payload(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "result.json").read_text())


def test_synthetic_eligible_has_arkit_box_and_route(tmp_path):
    composed = _go(tmp_path, DATA / "synthetic" / "verdicts")
    body = _payload(tmp_path)
    Draft202012Validator(SCHEMA).validate(body)
    ResultResponse.model_validate(body)
    outcome = body["outcome"]
    assert outcome["kind"] == "eligible"
    assert body["status"] == "complete"
    assert outcome["arkitEpoch"] == "e1"
    placement = outcome["recommendedPlacement"]
    assert placement["boxArkitWorld"] is not None
    assert outcome["cableRoutePolylineArkit"]
    assert placement["startSM"] == -1.0
    assert body["viewsNeeded"] == outcome["viewsNeeded"]
    assert not _SNAKE.search(json.dumps(body))
    scene = json.loads((DATA / "synthetic" / "scene.json").read_text())
    source = frame_mod.mat4_from_list(scene["frame"]["from_source"])
    mapped = frame_mod.mat4_from_list(outcome["frameToArkit"])
    assert np.allclose(mapped @ source, np.eye(4), atol=1e-5)
    exact = ar.scene_to_arkit(scene["frame"])
    scale = frame_mod.scale_of(exact)
    box = composed.response.outcome.recommendedPlacement
    assert box is not None and box.boxArkitWorld is not None
    assert max(abs(a - b * scale) for a, b in zip(box.boxArkitWorld.size, box.boxSceneM.sizeM, strict=True)) < 1e-9
    center = np.array(placement["boxSceneM"]["centerM"])
    pose = frame_mod.mat4_from_list(placement["boxArkitWorld"]["pose"])
    back = frame_mod.apply(np.linalg.inv(mapped), pose[:3, 3])
    assert float(np.linalg.norm(back - center)) <= 0.01
    yaw = math.radians(placement["boxSceneM"]["yawDeg"])
    dx, dz = math.cos(yaw), -math.sin(yaw)
    nx, nz = -dz, dx
    w, h, d = placement["boxSceneM"]["sizeM"]
    corners = []
    for sx, sy, sz in (
        (-1, -1, -1),
        (1, -1, -1),
        (1, 1, -1),
        (-1, 1, -1),
        (-1, -1, 1),
        (1, -1, 1),
        (1, 1, 1),
        (-1, 1, 1),
    ):
        corners.append(
            center
            + np.array([dx, 0.0, dz]) * sx * w / 2
            + np.array([0.0, 1.0, 0.0]) * sy * h / 2
            + np.array([nx, 0.0, nz]) * sz * d / 2
        )
    restored = frame_mod.apply(np.linalg.inv(mapped), frame_mod.apply(mapped, np.vstack(corners)))
    assert float(np.linalg.norm(restored - np.vstack(corners), axis=1).max()) <= 0.01
    again = tmp_path / "again"
    _go(again, DATA / "synthetic" / "verdicts")
    assert (tmp_path / "result.json").read_bytes() == (again / "result.json").read_bytes()


def test_needs_more_photos_uses_crops_without_an_arkit_frame(tmp_path):
    composed = _go(tmp_path, DATA / "photo" / "verdicts", scene=DATA / "photo", reads=None)
    body = _payload(tmp_path)
    assert body["outcome"]["kind"] == "needs_more_photos"
    assert body["status"] == "needs_views"
    assert "no_arkit_frame" in composed.warnings
    assert body["outcome"]["frameToArkit"] is None
    ground = [view for view in body["outcome"]["viewsNeeded"] if view["band"] == "ground"]
    assert ground
    assert ground[0]["promptId"].startswith("vn.ground.")
    assert ground[0]["viewingAngleDeg"] == [20, 60]
    assert ground[0]["referenceCrop"] is not None
    assert ground[0]["suggestedCameraPoseArkit"] is None
    wall = next(view for view in body["viewsNeeded"] if view["band"] == "wall")
    assert wall["viewingAngleDeg"] == [0, 30]
    assert body["viewsNeeded"] == body["outcome"]["viewsNeeded"]


def test_hidden_reject_stays_off_the_device_payload(tmp_path):
    composed = _go(tmp_path, DATA / "reject" / "verdicts", reads=None)
    text = (tmp_path / "result.json").read_text()
    body = json.loads(text)
    assert body["outcome"]["kind"] == "manual_review"
    assert body["status"] == "manual_review"
    assert body["outcome"]["reasons"] == []
    assert body["site"]["reasons"] == []
    assert body["site"]["decision"] == "manual_review"
    assert body["verdictUrl"] is None
    assert "reject" not in text
    assert "not_eligible" not in text
    assert "drive_clearance" not in text
    assert "fail" not in text
    row = body["criteria"][0]
    assert row["id"] == "review"
    assert row["outcome"] == "unsure"
    assert row["measuredFt"] is None
    assert row["thresholdFt"] is None
    internal = json.loads((tmp_path / "result.internal.json").read_text())
    assert internal["decision"] == "reject"
    review = (tmp_path / "review.html").read_text()
    assert "reject" in review
    assert "drive_clearance" in review
    hook = notify.webhook_body(
        event_id="evt_test",
        capture_id="cap_test",
        order_ref="base-order-0",
        run_id=composed.run_id,
        decision=internal["decision"],
        verdict_url=None,
    )
    assert hook["decision"] == "reject"
    assert composed.response.outcome is not None


def test_shown_reject_names_drive_clearance(tmp_path):
    config = ResultConfig(show_reject=True, catalogue_path=DATA / "prompts.en-US.json")
    _go(tmp_path, DATA / "reject" / "verdicts", reads=None, config=config)
    body = _payload(tmp_path)
    assert body["outcome"]["kind"] == "not_eligible"
    assert body["status"] == "complete"
    reason = body["outcome"]["reasons"][0]
    assert reason["criterion"] == "drive_clearance"
    assert reason["measuredFt"] == 0.0
    assert reason["plusMinusFt"] == 0.3
    assert reason["thresholdFt"] == 3.0
    assert reason["text"]


def test_degraded_run_is_never_eligible(tmp_path):
    _go(tmp_path, DATA / "synthetic" / "verdicts", "run.degraded.json")
    body = _payload(tmp_path)
    assert body["outcome"]["kind"] == "manual_review"
    assert "degraded:objects" in body["site"]["reasons"]
    assert body["outcome"]["recommendedPlacement"] is None


def test_failed_run_without_a_verdict_uses_the_failure_code(tmp_path):
    empty = tmp_path / "verdicts"
    empty.mkdir()
    out = tmp_path / "out"
    _go(out, empty, "run.failed.json", reads=None)
    body = _payload(out)
    assert body["outcome"]["kind"] == "manual_review"
    assert body["site"]["reasons"] == ["hard_stop"]
    assert body["criteria"] == []


def test_verdict_missing(tmp_path):
    empty = tmp_path / "verdicts"
    empty.mkdir()
    with pytest.raises(ResultError) as caught:
        _go(tmp_path / "out", empty)
    assert caught.value.code == "verdict_missing"


def test_verdict_invalid(tmp_path):
    verdicts = tmp_path / "verdicts"
    verdicts.mkdir()
    (verdicts / "verdict.C.json").write_text("{}\n")
    with pytest.raises(ResultError) as caught:
        _go(tmp_path / "out", verdicts)
    assert caught.value.code == "verdict_invalid"


def test_scene_invalid(tmp_path):
    scene = tmp_path / "scene"
    scene.mkdir()
    (scene / "scene.json").write_text("{}\n")
    with pytest.raises(ResultError) as caught:
        _go(tmp_path / "out", DATA / "synthetic" / "verdicts", scene=scene)
    assert caught.value.code == "scene_invalid"


def test_payload_invalid_without_a_candidate(tmp_path):
    verdicts = tmp_path / "verdicts"
    verdicts.mkdir()
    doc = json.loads((DATA / "synthetic" / "verdicts" / "verdict.C.json").read_text())
    doc["candidates"] = []
    doc["site"]["chosen_candidate"] = None
    (verdicts / "verdict.C.json").write_text(json.dumps(doc))
    with pytest.raises(ResultError) as caught:
        _go(tmp_path / "out", verdicts)
    assert caught.value.code == "payload_invalid"


def test_catalogue_missing_uses_the_fallback(tmp_path):
    composed = _go(tmp_path, DATA / "synthetic" / "verdicts", catalogue=FALLBACK)
    assert "catalogue_missing" in composed.warnings
    assert composed.response.outcome is not None
    assert composed.response.outcome.message == "Your home looks eligible"


def test_events_once_and_retake_when_required(tmp_path):
    state = MemoryState()
    capture, run_rec, _epochs = _capture_run()
    state.put_capture(capture)
    state.put_run(run_rec)
    composed = _go(tmp_path, DATA / "synthetic" / "verdicts")
    assert [name for name, _data in composed.events] == ["verdict_ready"]
    emit_events(composed, state)
    emit_events(composed, state)
    ready = [ev for ev in state.events_after(capture.capture_id, 0) if ev.type == "verdict_ready"]
    assert len(ready) == 1
    photos = _go(tmp_path / "photos", DATA / "photo" / "verdicts", scene=DATA / "photo", reads=None)
    assert [name for name, _data in photos.events] == ["retake_request"]
    retake_state = MemoryState()
    retake_state.put_capture(capture)
    retake_state.put_run(run_rec)
    emit_events(photos, retake_state)
    emit_events(photos, retake_state)
    retakes = [ev for ev in retake_state.events_after(capture.capture_id, 0) if ev.type == "retake_request"]
    assert len(retakes) == 1
    assert not any(ev.type == "verdict_ready" for ev in retake_state.events_after(capture.capture_id, 0))
    scale = tmp_path / "scale.json"
    scale.write_text(json.dumps({"status": "unverified", "source": "none", "rel_bound_95": 0.5}))
    unverified = _go(tmp_path / "scale", DATA / "synthetic" / "verdicts", scale=scale)
    assert unverified.response.memberActions == ["retake_meter_closeups"]
    assert [name for name, _data in unverified.events] == ["retake_request"]
    known = {
        "status": "unverified",
        "conflict": True,
        "model": "demo-meter",
        "source": "meter_model",
        "rel_bound_95": 0.02,
        "estimates": [{"used": True, "kind": "meter_model"}],
    }
    (tmp_path / "known.json").write_text(json.dumps(known))
    scanned = _go(tmp_path / "scan", DATA / "synthetic" / "verdicts", scale=tmp_path / "known.json")
    assert scanned.response.memberActions == ["truedepth_meter_scan"]


def test_review_page_lists_profiles_criteria_and_offline_notice(tmp_path):
    _go(tmp_path, DATA / "synthetic" / "verdicts")
    html = (tmp_path / "review.html").read_text()
    assert html.count("<tr") >= 1
    for name in ("A", "B", "C", "S"):
        assert name in html
    assert "three.js" in html
    assert "offline" in html
    assert 'action="/v1/captures/cap_test/confirmations"' in html
    assert "TODO queue" in html
    assert ("SS" + "R") not in html


def test_package_has_no_ssr_token():
    token = "SS" + "R"
    root = Path(__file__).resolve().parents[1]
    for path in root.rglob("*"):
        if "tests" in path.parts or path.suffix not in {".py", ".html", ".css", ".md"}:
            continue
        assert token not in path.read_text()


def test_run_sets_status(tmp_path, monkeypatch):
    for name in ("WEBHOOK_HMAC_SECRET", "BASE_WEBHOOK_URL", "SHOW_REJECT_TO_HOMEOWNER", "HSS_RESULT_PROMPTS_PATH"):
        monkeypatch.delenv(name, raising=False)
    capture, run_rec, epochs = _capture_run()
    state = MemoryState()
    state.put_capture(capture)
    state.put_run(run_rec)

    class _Settings:
        RULES_PROFILE_DEFAULT = "C"
        SHOW_REJECT_TO_HOMEOWNER = False
        HSS_RESULT_MAX_VIEWS = 6
        HSS_RESULT_SIGNED_URL_TTL_S = 900
        HSS_RESULT_PREVIEW_MAX_POINTS = 200_000
        HSS_RESULT_METER_AUTOFILL = "barcode"
        HSS_RESULT_REVIEW_THREEJS_URL = "https://unpkg.com/three@0.160.0/build/three.module.js"
        HSS_RESULT_PROMPTS_PATH = str(DATA / "prompts.en-US.json")
        BASE_WEBHOOK_URL = None

    ctx = StageContext(
        capture_id=capture.capture_id,
        run_id=run_rec.run_id,
        stage="result",
        attempt=1,
        branch="lidar",
        flow="guided",
        tier="arkit",
        work_dir=tmp_path,
        inputs={
            "scene": str(DATA / "synthetic"),
            "verdicts": str(DATA / "synthetic" / "verdicts"),
            "reads": str(DATA / "reads.auto_fill.json"),
            "out": str(tmp_path / "out"),
            "epochs": ",".join(epochs),
        },
        deadline_at="2026-09-27T00:00:00Z",
        heartbeat=lambda _step: None,
        vlm_budget_usd=0,
        storage=None,
        state=state,
        settings=_Settings(),
    )
    result = run(ctx)
    assert state.get_capture(capture.capture_id).status == "complete"
    assert "result/result.json" in result.artifacts
    assert (tmp_path / "out" / "review.html").is_file()


def test_run_resolves_reads_and_scale_from_work_dir(tmp_path, monkeypatch):
    """Orchestrated runs hydrate reads/reads.json and scale/scale.json into work_dir without short keys."""

    class _Settings:
        RULES_PROFILE_DEFAULT = "C"
        SHOW_REJECT_TO_HOMEOWNER = False
        HSS_RESULT_MAX_VIEWS = 6
        HSS_RESULT_SIGNED_URL_TTL_S = 900
        HSS_RESULT_PREVIEW_MAX_POINTS = 200_000
        HSS_RESULT_METER_AUTOFILL = "barcode"
        HSS_RESULT_REVIEW_THREEJS_URL = "https://unpkg.com/three@0.160.0/build/three.module.js"
        HSS_RESULT_PROMPTS_PATH = str(DATA / "prompts.en-US.json")
        BASE_WEBHOOK_URL = None

    for name in ("WEBHOOK_HMAC_SECRET", "BASE_WEBHOOK_URL", "SHOW_REJECT_TO_HOMEOWNER", "HSS_RESULT_PROMPTS_PATH"):
        monkeypatch.delenv(name, raising=False)
    capture, run_rec, epochs = _capture_run()
    state = MemoryState()
    state.put_capture(capture)
    state.put_run(run_rec)
    (tmp_path / "reads").mkdir(parents=True)
    (tmp_path / "reads" / "reads.json").write_bytes((DATA / "reads.auto_fill.json").read_bytes())
    (tmp_path / "scale").mkdir()
    (tmp_path / "scale" / "scale.json").write_text(
        json.dumps({"source": "lidar", "status": "verified", "rel_bound_95": 0.1})
    )

    ctx = StageContext(
        capture_id=capture.capture_id,
        run_id=run_rec.run_id,
        stage="result",
        attempt=1,
        branch="lidar",
        flow="guided",
        tier="arkit",
        work_dir=tmp_path,
        inputs={
            "scene": str(DATA / "synthetic"),
            "verdicts": str(DATA / "synthetic" / "verdicts"),
            "out": str(tmp_path / "out"),
            "epochs": ",".join(epochs),
        },
        deadline_at="2026-09-27T00:00:00Z",
        heartbeat=lambda _step: None,
        vlm_budget_usd=0,
        storage=None,
        state=state,
        settings=_Settings(),
    )
    run(ctx)
    body = json.loads((tmp_path / "out" / "result.json").read_text())
    internal = json.loads((tmp_path / "out" / "result.internal.json").read_text())
    assert "reads_missing" not in internal["warnings"]
    assert body["scale"]["source"] == "lidar"
    assert body["meter"]["status"] == "auto_filled"


class _SigningStorage:
    """Storage double that signs any key, the way the deployed backends do."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def exists(self, key: str) -> bool:
        return key in self.objects

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        del content_type
        self.objects[key] = data

    def signed_get_url(self, key: str, ttl_s: int) -> str:
        return f"https://signed.test/{key}?ttl={ttl_s}"


def test_review_page_signs_photos_and_raw_links(tmp_path):
    """The review page is served under /v1/captures/{id}/review, so bare names are dead links."""
    capture, run_rec, _epochs = _capture_run()
    storage = _SigningStorage()
    storage.put_bytes(f"captures/{capture.capture_id}/k01.jpg", b"jpeg-bytes", "image/jpeg")
    compose_result(
        capture,
        run_rec,
        DATA / "photo",
        DATA / "photo" / "verdicts",
        tmp_path,
        reads=DATA / "reads.auto_fill.json",
        scale=None,
        catalogue=_catalogue(),
        config=ResultConfig(catalogue_path=DATA / "prompts.en-US.json"),
        storage=storage,
    )
    html = (tmp_path / "review.html").read_text()
    assert f"https://signed.test/captures/{capture.capture_id}/k01.jpg" in html
    assert f"https://signed.test/captures/{capture.capture_id}/runs/{run_rec.run_id}/verdicts/verdict.C.json" in html
    assert f"https://signed.test/captures/{capture.capture_id}/runs/{run_rec.run_id}/result/result.json" in html
    assert 'href="verdict.C.json"' not in html


def test_deliver_signs_current_time_and_warns_on_failure(monkeypatch):
    stamped: dict[str, int] = {}

    def _capture_stamp(body, *, url, secret, timestamp, client=None, sent=None):
        stamped["t"] = timestamp
        return True

    monkeypatch.setattr("server.result.compose.time.time", lambda: 1_700_000_000.4)
    monkeypatch.setattr("server.result.compose.notify.post_webhook", _capture_stamp)
    monkeypatch.setenv("WEBHOOK_HMAC_SECRET", "test-secret")
    composed = ComposeResult(
        response=ResultResponse.model_construct(verdictUrl="verdict.C.json"),
        internal={"decision": "pass"},
        events=[],
        capture_id="cap_test",
        run_id="run_ok",
    )
    capture = CaptureRecord.model_construct(capture_id="cap_test", order_ref="base-order-0")
    config = ResultConfig(webhook_url="https://example.invalid/hook")
    _deliver(composed, capture, config)
    assert stamped["t"] == 1_700_000_000
    assert composed.warnings == []

    def _boom(body, *, url, secret, timestamp, client=None, sent=None):
        raise RuntimeError("webhook status 500")

    monkeypatch.setattr("server.result.compose.notify.post_webhook", _boom)
    _deliver(composed, capture, config)
    assert composed.warnings == ["webhook_failed"]


def _ws09_verdict(src: Path, dest: Path, *, chosen: str) -> None:
    """PR 39 stamps the chosen spot ``candidate_id: "best"`` and ``chosen_candidate: runN``."""
    verdict = json.loads(src.read_text())
    verdict["site"]["chosen_candidate"] = chosen
    verdict["candidates"] = [
        {
            "id": chosen,
            "wall_id": "w1",
            "start_s_m": [0.45, 1.24],
            "outcome": verdict["candidates"][0]["outcome"],
        }
    ]
    for item in verdict["criteria"]:
        if item.get("scope") == "candidate":
            item["candidate_id"] = "best"
            if item.get("margin_over_uncertainty") is not None:
                item["margin_over_uncertainty"] = 1.5
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "verdict.C.json").write_text(json.dumps(verdict))
    (dest / "placement.C.json").write_text(
        json.dumps(
            {
                "profile": "C",
                "chosen_candidate": chosen,
                "start_s_m": 0.4516,
                "box_scene_m": {
                    "center_m": [0.1, 0.5, 0.2],
                    "size_m": [0.7874, 1.0033, 0.5588],
                    "yaw_deg": 0.0,
                },
                "route_polyline_scene_m": [[0.0, 0.3048, 0.02]],
                "checks_at_spot": [{"id": "wall_backing", "outcome": "pass", "margin_over_uncertainty": None}],
            }
        )
    )


def test_ws09_best_candidate_id_is_the_chosen_spot(tmp_path):
    verdicts = tmp_path / "verdicts"
    _ws09_verdict(DATA / "synthetic" / "verdicts" / "verdict.C.json", verdicts, chosen="run7")
    composed = _go(tmp_path / "out", verdicts)
    body = composed.response
    assert body.criteria is not None
    assert "wall_backing" in [row.id for row in body.criteria]
    place = body.outcome.recommendedPlacement
    assert place is not None
    assert place.startSM == 0.4516
    assert place.confidence == pytest.approx(0.25)


def test_ws09_best_id_reject_names_drive_clearance(tmp_path):
    verdicts = tmp_path / "verdicts"
    _ws09_verdict(DATA / "reject" / "verdicts" / "verdict.C.json", verdicts, chosen="run3")
    config = ResultConfig(show_reject=True, catalogue_path=DATA / "prompts.en-US.json")
    _go(tmp_path / "out", verdicts, reads=None, config=config)
    body = _payload(tmp_path / "out")
    assert body["outcome"]["reasons"][0]["criterion"] == "drive_clearance"


def test_shown_reject_names_unmatched_candidate_fails(tmp_path):
    src = _ROOT / "data/fixtures/verdicts/eth3d-delivery/gt.C1.g80.json"
    dest = tmp_path / "verdicts"
    dest.mkdir()
    (dest / "verdict.C.json").write_text(src.read_text())
    config = ResultConfig(show_reject=True, catalogue_path=DATA / "prompts.en-US.json")
    _go(tmp_path / "out", dest, reads=None, config=config)
    reasons = _payload(tmp_path / "out")["outcome"]["reasons"]
    assert [row["criterion"] for row in reasons] == [
        "ground_surface",
        "drive_clearance",
        "opening_clearance",
    ]


def test_fallback_box_on_a_negative_run_narrower_than_the_battery(tmp_path):
    verdict = json.loads((DATA / "synthetic" / "verdicts" / "verdict.C.json").read_text())
    span = [-1.3, -1.2]
    width = 0.7874
    assert span[1] - span[0] < width
    verdict["candidates"][0]["start_s_m"] = span
    dest = tmp_path / "verdicts"
    dest.mkdir()
    (dest / "verdict.C.json").write_text(json.dumps(verdict))
    composed = _go(tmp_path / "out", dest)
    place = composed.response.outcome.recommendedPlacement
    assert place is not None
    assert place.startSM == pytest.approx(span[1])
    assert place.boxSceneM.centerM[0] == pytest.approx(span[1] + width / 2)


def test_missing_scene_cloud_skips_preview(tmp_path):
    scene_dir = tmp_path / "scene"
    scene_dir.mkdir()
    scene = json.loads((DATA / "synthetic" / "scene.json").read_text())
    scene["assets"][0]["path"] = "missing-laser.ply"
    (scene_dir / "scene.json").write_text(json.dumps(scene))
    composed = _go(tmp_path / "out", DATA / "synthetic" / "verdicts", scene=scene_dir)
    assert "preview_skipped" in composed.warnings
    assert composed.response.previewUrl is None
    assert not (tmp_path / "out" / "preview.ply").is_file()


def test_fallback_placement_uses_the_near_edge(tmp_path):
    verdict = json.loads((DATA / "synthetic" / "verdicts" / "verdict.C.json").read_text())
    verdict["candidates"][0]["start_s_m"] = [0.4516, 4.7696]
    dest = tmp_path / "verdicts"
    dest.mkdir()
    (dest / "verdict.C.json").write_text(json.dumps(verdict))
    composed = _go(tmp_path / "out", dest)
    place = composed.response.outcome.recommendedPlacement
    assert place is not None
    assert place.startSM == pytest.approx(0.4516)


def test_route_skips_the_duplicate_point_when_the_spot_starts_at_the_meter():
    scene = json.loads((DATA / "synthetic" / "scene.json").read_text())
    pts = _route(chain_mod.Chain(scene), scene, 0.0, [0.4, 1.0, 0.3], 1.0033)
    assert pts is not None and len(pts) >= 2
    for index in range(len(pts) - 1):
        assert pts[index] != pts[index + 1]


def test_a_correction_republishes_the_final_event_and_a_retry_does_not(tmp_path, monkeypatch):
    """A late correction re-runs the result stage under the same run id. A retry of
    the stage must not publish a second final event, but a recomposition after an
    accepted confirmation must, or clients following the cursor never see the
    corrected outcome."""
    for name in ("WEBHOOK_HMAC_SECRET", "BASE_WEBHOOK_URL", "SHOW_REJECT_TO_HOMEOWNER", "HSS_RESULT_PROMPTS_PATH"):
        monkeypatch.delenv(name, raising=False)
    capture, run_rec, epochs = _capture_run()
    state = MemoryState()
    state.put_capture(capture)
    state.put_run(run_rec)

    class _Settings:
        RULES_PROFILE_DEFAULT = "C"
        SHOW_REJECT_TO_HOMEOWNER = False
        HSS_RESULT_MAX_VIEWS = 6
        HSS_RESULT_SIGNED_URL_TTL_S = 900
        HSS_RESULT_PREVIEW_MAX_POINTS = 200_000
        HSS_RESULT_METER_AUTOFILL = "barcode"
        HSS_RESULT_REVIEW_THREEJS_URL = "https://unpkg.com/three@0.160.0/build/three.module.js"
        HSS_RESULT_PROMPTS_PATH = str(DATA / "prompts.en-US.json")
        BASE_WEBHOOK_URL = None

    ctx = StageContext(
        capture_id=capture.capture_id,
        run_id=run_rec.run_id,
        stage="result",
        attempt=1,
        branch="lidar",
        flow="guided",
        tier="arkit",
        work_dir=tmp_path,
        inputs={
            "scene": str(DATA / "synthetic"),
            "verdicts": str(DATA / "synthetic" / "verdicts"),
            "reads": str(DATA / "reads.auto_fill.json"),
            "out": str(tmp_path / "out"),
            "epochs": ",".join(epochs),
        },
        deadline_at="2026-09-27T00:00:00Z",
        heartbeat=lambda _step: None,
        vlm_budget_usd=0,
        storage=None,
        state=state,
        settings=_Settings(),
    )

    def finals() -> list[int]:
        return [
            ev.seq
            for ev in state.events_after(capture.capture_id, 0)
            if ev.type in {"verdict_ready", "retake_request"} and ev.run_id == run_rec.run_id
        ]

    run(ctx)
    run(ctx)  # a retry of the same composition
    first = finals()
    assert len(first) == 1

    state.append_event(
        capture.capture_id,
        "confirmation",
        {"confirmationId": "cfm_1", "fieldId": "meter_number", "imageId": "s1", "kind": "corrected", "by": "device"},
    )
    run(ctx)  # the recomposition the correction triggers
    run(ctx)  # and a retry of that recomposition
    second = finals()
    assert len(second) == 2, "the correction must publish its own final event, once"
    assert second[0] == first[0]
