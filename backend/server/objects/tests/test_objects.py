"""Acceptance pinned to cached scores and cached model answers. No API keys."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from server.objects.cli import _select_cascade, cross_fit, offline_zero_scores
from server.objects.config import PLANNED_USD, Cluster, FrameRef, ObjectsConfig, ObjectsError
from server.objects.confirm import Budget, agreement
from server.objects.emit import to_objects, validate_object
from server.objects.prompts import WS08_PURPOSES
from server.objects.run import run, run_3d
from server.objects.select import select_house
from server.objects.vlm import (
    LiveBudgetExceeded,
    LiveSpend,
    VisionClient,
    live_max_usd,
    parse_boxes,
    parse_presence,
    request_kwargs,
)

ROOT = Path(__file__).resolve().parents[3]
FIX = ROOT / "data" / "fixtures" / "objects"


def _load(name: str):
    return json.loads((FIX / "b3" / name).read_text())


@pytest.mark.parametrize(
    ("gpt", "grok", "state", "conf"),
    [
        ("yes", "yes", "agree", 0.9),
        ("yes", "no", "disagree", 0.5),
        ("yes", "unsure", "disagree", 0.5),
        ("yes", "unavailable", "disagree", 0.5),
        ("unsure", "yes", "disagree", 0.5),
        ("no", "no", "drop", 0.0),
        ("yes", "off", "single", 0.75),
        ("no", "yes", "drop", 0.0),
    ],
)
def test_agreement_table(gpt, grok, state, conf):
    assert agreement(gpt, grok) == (state, conf)


def test_budget_skips_later_stages_and_never_hazard():
    budget = Budget(0.05)
    assert budget.allow("hazard", 0.04)
    budget.charge("gpt-5.5", 0.04)
    assert budget.allow("cheap", 0.02) is False
    assert budget.skipped_stages == ["cheap"]
    assert budget.allow("confirm", 0.02) is False
    assert budget.allow("second", 0.02) is False
    assert budget.allow("hazard", 1.0) is True


def test_budget_skip_order_is_second_then_confirm_then_cheap():
    """Spend order is hazard, cheap, confirm, second. The first stage that does not fit ends the rest."""
    order = ["hazard", "cheap", "confirm", "second"]
    planned = {"hazard": 0.02, "cheap": 0.02, "confirm": 0.02, "second": 0.05}
    budget = Budget(0.05)
    ran = []
    for stage in order:
        if budget.allow(stage, planned[stage]):
            budget.charge("m", planned[stage])
            ran.append(stage)
    assert ran == ["hazard", "cheap"]
    assert budget.skipped_stages == ["confirm", "second"]
    assert budget.allow("second", 0.01) is False


def test_location_unsure_sentinel_and_not_from_confidence():
    scene = {"frame": {"anchor": {"position_m": [1.0, 0.0, 2.0]}}, "objects": [{"id": "meter", "class": "meter"}]}
    low = Cluster("window", [0, 0, 0], [{"frame": "a", "score": 0.2, "confidence": 0.3}], 0.0, "location_unsure")
    high = Cluster("window", [0, 0, 0], [{"frame": "a", "score": 0.2, "confidence": 0.9}], 0.0, "location_unsure")
    cfg = ObjectsConfig(external_vision=False)
    objects_low, requests = to_objects([low], {}, "photo_only", scene, cfg)
    objects_high, _ = to_objects([high], {}, "photo_only", scene, cfg)
    assert objects_low[0]["plus_minus_m"] == 10.0
    assert objects_high[0]["plus_minus_m"] == objects_low[0]["plus_minus_m"]
    assert objects_low[0]["geometry"]["pos_m"] == [1.0, 1.0, 4.0]
    assert objects_low[0]["evidence"] == "inferred"
    assert objects_low[0]["id"] != "meter"
    assert requests and requests[0]["kind"] == "region"
    assert "photo of the window" in requests[0]["target"]


def test_object_records_validate_against_scene_schema():
    scene = {"objects": []}
    cluster = Cluster(
        "door",
        [0.2, 1.0, 3.0],
        [{"frame": "kf1", "score": 0.8, "confidence": 0.75, "box_px": [1, 2, 30, 40]}],
        0.4,
        "triangulate",
    )
    objects, _ = to_objects([cluster], {}, "arkit", scene, ObjectsConfig())
    assert objects[0]["class"] == "door"
    validate_object(objects[0])


def test_lidar_cluster_is_observed_with_measured_bound():
    cluster = Cluster(
        "window",
        [1, 1, 1],
        [{"frame": "a", "score": 0.9, "confidence": 0.9}, {"frame": "b", "score": 0.8, "confidence": 0.9}],
        0.1,
        "lidar",
    )
    objects, requests = to_objects([cluster], {}, "arkit_lidar", {"objects": []}, ObjectsConfig())
    assert objects[0]["evidence"] == "observed"
    assert objects[0]["plus_minus_m"] == pytest.approx(0.55)  # max(0.35, 0.1) + 0.2 opening
    assert requests == []


def test_cache_round_trip_makes_zero_requests(tmp_path: Path):
    img = tmp_path / "frame.jpg"
    _write_image(img)
    calls = {"n": 0}

    def transport(model, image, prompt):
        calls["n"] += 1
        body = json.dumps({"present": {"electric_meter": "yes"}, "quality": "good", "readable_equipment_text": "no"})
        return body, 10, 5, 0.01

    client = VisionClient(ObjectsConfig(), tmp_path / "cache", transport=transport)
    first = client.presence("gpt-5.4-mini", img)
    second = client.presence("gpt-5.4-mini", img)
    assert calls["n"] == 1
    assert client.requests == 1
    assert client.cache_hits == 1
    assert second.cached is True
    assert second.cost_usd == 0.0
    assert first.present["electric_meter"] == "yes"


def test_live_max_usd_defaults_to_three(monkeypatch):
    monkeypatch.delenv("HSS_OBJECTS_LIVE_MAX_USD", raising=False)
    assert live_max_usd() == 3.0
    monkeypatch.setenv("HSS_OBJECTS_LIVE_MAX_USD", "1.25")
    assert live_max_usd() == 1.25


def test_live_spend_refuses_the_next_vendor_call(tmp_path: Path):
    """GPT-5.5 is called while it fits. Grok 4.7 is refused once planned spend would meet the cap."""
    first = tmp_path / "a.jpg"
    second = tmp_path / "b.jpg"
    _write_image(first, (20, 40, 60))
    _write_image(second, (80, 10, 10))
    seen: list[str] = []

    def transport(model, image, prompt):
        seen.append(model)
        if model == "grok-4.7":
            body = json.dumps({"objects": [], "present": {"gas_meter": "yes"}, "ground_type": "grass"})
            return body, 20, 10, 0.029
        body = json.dumps({"present": {"electric_meter": "yes"}, "quality": "good", "readable_equipment_text": "no"})
        return body, 10, 5, 0.0167

    # Planned GPT-5.5 is $0.0167, under $0.02. Planned Grok is $0.029, which would pass it.
    tight = VisionClient(ObjectsConfig(), tmp_path / "tight", transport=transport, live_spend=LiveSpend(0.02))
    answer = tight.presence("gpt-5.5", first)
    assert answer.model == "gpt-5.5"
    assert tight.live_spend is not None
    assert tight.live_spend.spent == pytest.approx(0.0167)
    with pytest.raises(LiveBudgetExceeded, match="live_budget_exhausted:grok-4.7"):
        tight.boxes("grok-4.7", second)
    assert seen == ["gpt-5.5"]

    # Under the default $3 cap both vendors are called, and a cache hit adds nothing.
    roomy_spend = LiveSpend(3.0)
    roomy = VisionClient(ObjectsConfig(), tmp_path / "roomy", transport=transport, live_spend=roomy_spend)
    roomy.presence("gpt-5.5", first)
    roomy.boxes("grok-4.7", second)
    assert seen == ["gpt-5.5", "gpt-5.5", "grok-4.7"]
    spent = roomy_spend.spent
    again = roomy.presence("gpt-5.5", first)
    assert again.cached is True
    assert roomy_spend.spent == pytest.approx(spent)
    assert roomy_spend.cap == 3.0


def test_request_kwargs_omit_temperature_for_gpt55():
    assert request_kwargs("gpt-5.5", 0) == {"reasoning_effort": "low"}
    assert "temperature" not in request_kwargs("gpt-5.5", 0)
    assert request_kwargs("gpt-5.4-mini", 0)["temperature"] == 0
    assert request_kwargs("grok-4.7", 0)["seed"] == 0


def test_parse_presence_and_boxes():
    text = '{"present": {"electric_meter": "YES", "gas_meter": "maybe"}, "quality": "good", "readable_equipment_text": "no"}'
    parsed = parse_presence(text)
    assert parsed["present"]["electric_meter"] == "yes"
    assert parsed["present"]["gas_meter"] == "unsure"
    boxes = parse_boxes(
        '{"objects": [{"label": "window", "box_2d": [100, 200, 400, 500], "confidence": 0.8}], "present": {}, "ground_type": "grass"}'
    )
    assert boxes["objects"][0]["box"] == [0.2, 0.1, 0.5, 0.4]
    assert boxes["ground_type"] == "grass"
    named = parse_boxes('{"objects": [], "present": {}, "ground_type": "walkway"}')
    assert named["ground_type"] == "walkway"
    mapped = parse_boxes('{"objects": [], "present": {}, "ground_type": "lawn"}')
    assert mapped["ground_type"] is None


def test_prompts_name_only_openai_and_xai():
    banned = ("anth" + "ropic", "cla" + "ude", "s" + "k-", "xa" + "i-")
    blob = "\n".join(p.read_text() for p in (ROOT / "server" / "objects").rglob("*.py"))
    lowered = blob.lower()
    for word in banned:
        assert word not in lowered


def test_ws08_purposes_never_enter_selection():
    from server.objects.select import select_frames

    scores = {"clip": {"h/a": {"meter": 0.9, "panel": 0, "gas_meter": 0, "ac_condenser": 0, "pool": 0}}}
    # A still with a WS-08 purpose is not in `order`, so it cannot be selected.
    sel = select_frames(scores, ["h/a"], k=2, always=["s1"], purposes={"s1": "meter_close"})
    assert "s1" not in sel.frames
    assert "meter_close" in WS08_PURPOSES


def test_checklist_stills_are_kept():
    from server.objects.select import select_frames

    row = {"meter": 0.1, "panel": 0.1, "gas_meter": 0.1, "ac_condenser": 0.1, "pool": 0.1}
    frames = [f"h/{i}" for i in range(25)]
    scores = {"clip": {fid: row for fid in frames}, "siglip": {fid: row for fid in frames}}
    sel = select_frames(scores, frames, k=1, always=["h/24"], purposes={"h/24": "house_overview"}, max_selected=20)
    assert "h/24" in sel.frames


def test_select_frames_match_cached_union():
    images = _load("images.json")
    scores = _load("gate_scores_all.json")
    oracle = _load("selection_top2_union.json")
    mini = _jsonl(FIX / "b3/raw/presence/gpt-5.4-mini.jsonl")
    big = _jsonl(FIX / "b3/raw/presence/gpt-5.5.jsonl")
    walk: dict[str, list[str]] = {}
    for row in images:
        if row.get("set") == "walk" and row["id"] in mini and row["id"] in big:
            walk.setdefault(row["house"], []).append(row["id"])
    for house, frames in walk.items():
        union = set()
        for model in ("clip", "siglip"):
            got, _hz = select_house(frames, scores[model], k=2)
            union.update(got)
        assert [f for f in frames if f in union] == oracle[house]["frames"]


def test_cached_cascade_hits_published_house_recall():
    images = _load("images.json")
    scores = _load("gate_scores_all.json")
    gt = _load("gt.json")
    mini = _jsonl(FIX / "b3/raw/presence/gpt-5.4-mini.jsonl")
    big = _jsonl(FIX / "b3/raw/presence/gpt-5.5.jsonl")
    walk: dict[str, list[str]] = {}
    for row in images:
        if row.get("set") == "walk" and row["id"] in mini and row["id"] in big:
            walk.setdefault(row["house"], []).append(row["id"])
    strict = _select_cascade(walk, scores, mini, big, gt["houses"], strict=True)
    lenient = _select_cascade(walk, scores, mini, big, gt["houses"], strict=False)
    assert strict["house_recall"] == "11/11"
    assert strict["false_present"] == "2/31"
    assert strict["frames_sent"] == "166/1321"
    assert strict["filtered_frac"] >= 0.85
    assert lenient["house_recall"] == "14/18"
    assert strict["planned_usd_per_house"] <= 0.20


def test_gate_cross_fit_recall():
    images = _load("images.json")
    scores = _load("gate_scores_all.json")
    gt = _load("gt.json")["frames"]
    clip = cross_fit(scores["clip"], gt, images, 0.8)
    siglip = cross_fit(scores["siglip"], gt, images, 0.5)
    assert clip["recall"] == "239/239"
    kept, total = siglip["recall"].split("/")
    assert int(kept) >= 237 and int(total) == 239


def test_siglip_frame_cascade_matches_cached_row():
    from server.objects.tests.b1_eval import siglip_frame_recall

    recall = siglip_frame_recall(FIX / "b3")
    assert recall["gas_meter"] == "13/13"
    assert recall["ac_condenser"] == "9/9"
    assert recall["main_panel"] == "2/2"
    assert recall["pool"] == "3/4"
    assert recall["electric_meter"] == "3/5"


def test_pair_presence_recall_on_cached_b1_answers():
    from server.objects.tests.b1_eval import pair_presence_recall

    recalls = pair_presence_recall(FIX / "b1")
    assert recalls, "expected labelled classes"
    for cls, (hit, n) in recalls.items():
        if n >= 3:
            assert hit / n >= 0.8, cls


def test_external_vision_off_keeps_detector_confidence(tmp_path: Path):
    from server.objects.run import run_2d

    frame = FrameRef("h/0", tmp_path / "missing.jpg", 10, 10, "keyframe", house="h")
    row = {"meter": 1, "panel": 0, "gas_meter": 0, "ac_condenser": 0, "pool": 0}
    scores = {"clip": {"h/0": row}, "siglip": {"h/0": row}}
    dets = {"h/0": [{"label": "window", "score": 0.6, "box": [0.1, 0.1, 0.4, 0.5]}]}
    result = run_2d(
        [frame],
        {},
        tmp_path / "out",
        ObjectsConfig(external_vision=False),
        vlm_budget_usd=0.85,
        scores=scores,
        detections=dets,
    )
    assert "external_vision_disabled" in result.warnings
    doc = json.loads((tmp_path / "out" / "objects.json").read_text())
    assert doc["format"] == "hss-objects/v0"
    assert doc["objects"]
    assert all(obj["confidence"] <= 0.5 for obj in doc["objects"])
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert (
        "cheap" in report["skipped_stages"]
        and "confirm" in report["skipped_stages"]
        and "second" in report["skipped_stages"]
    )


def test_offline_zero_scores_include_ws08_stills():
    frames = [
        FrameRef("k0", Path("k.jpg"), 8, 8, "keyframe"),
        FrameRef("s1", Path("s1.jpg"), 8, 8, "still", purpose="meter_close"),
        FrameRef("s2", Path("s2.jpg"), 8, 8, "still", purpose="meter_oblique"),
    ]
    scores = offline_zero_scores(frames)
    assert set(scores["clip"]) == {"k0", "s1", "s2"}
    assert set(scores["siglip"]) == {"k0", "s1", "s2"}
    assert scores["clip"]["s1"]["meter"] == 0.0
    assert scores["clip"]["s1"] is not scores["siglip"]["s1"]


def test_mini_still_routing_writes_scores_for_every_frame(tmp_path: Path):
    from server.objects.run import run_2d

    frames = [
        FrameRef("k0", tmp_path / "k.jpg", 8, 8, "keyframe", house="m"),
        FrameRef("s1", tmp_path / "s1.jpg", 8, 8, "still", purpose="meter_close", house="m"),
        FrameRef("s2", tmp_path / "s2.jpg", 8, 8, "still", purpose="meter_oblique", house="m"),
        FrameRef("overview", tmp_path / "o.jpg", 8, 8, "still", purpose="house_overview", house="m"),
    ]
    row = {"meter": 0.2, "panel": 0, "gas_meter": 0, "ac_condenser": 0, "pool": 0}
    scores = {"clip": {f.id: row for f in frames}, "siglip": {f.id: row for f in frames}}
    run_2d(
        frames,
        {},
        tmp_path / "out",
        ObjectsConfig(external_vision=False),
        vlm_budget_usd=None,
        scores=scores,
        detections={},
    )
    saved = json.loads((tmp_path / "out" / "frame_scores.json").read_text())
    assert set(saved) >= {"k0", "s1", "s2", "overview"}
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert "s1" not in report["hazard_frames"]
    doc = json.loads((tmp_path / "out" / "presence.json").read_text())
    assert "class_summary" in doc


def test_meter_candidates_rank_by_clip_and_skip_when_tapped():
    from server.objects.early import meter_candidates

    packet = {"taps": [], "stills": [{"id": "s", "purpose": "meter_close"}]}
    dets = {
        "s": [{"label": "electric_meter", "score": 0.4, "box": [0, 0, 1, 1]}],
        "k": [
            {"label": "electric_meter", "score": 0.9, "box": [0, 0, 1, 1]},
            {"label": "door", "score": 0.99, "box": [0, 0, 1, 1]},
        ],
    }
    clip = {"s": {"meter": 0.2}, "k": {"meter": 0.8}}
    got = meter_candidates(packet, top_k=3, detections=dets, clip_scores=clip)
    assert [row["frame"] for row in got] == ["k", "s"]
    tapped = meter_candidates({"taps": [{"label": "meter"}]}, detections=dets, clip_scores=clip)
    assert tapped == []


def test_ground_vote_maps_b1_class_to_one_material():
    from server.objects.emit import ground_votes

    votes = ground_votes({"f": {"cheap": {"ground_type": "grass"}, "confirm": {"ground_type": "concrete_or_paving"}}})
    assert votes == [{"image": "f", "material": "concrete", "confidence": 0.9}]
    mini = ground_votes({"g": {"cheap": {"ground_type": "mulch_or_soil"}}})
    assert mini == [{"image": "g", "material": "mulch", "confidence": 0.5}]
    named = ground_votes({"h": {"cheap": {"ground_type": "pavers"}, "second": {"ground_type": "drive"}}})
    assert named == [{"image": "h", "material": "pavers", "confidence": 0.5}]
    other = ground_votes({"i": {"confirm": {"ground_type": "other"}}})
    assert other == [{"image": "i", "material": "unknown", "confidence": 0.9}]


def test_nms_and_iou():
    from server.objects.config import Detection
    from server.objects.proposals import iou, nms_same_class

    assert iou([0, 0, 1, 1], [0, 0, 1, 1]) == 1
    kept = nms_same_class(
        [
            Detection("window", 0.9, [0, 0, 1, 1]),
            Detection("window", 0.5, [0.05, 0.05, 0.95, 0.95]),
            Detection("door", 0.4, [0, 0, 1, 1]),
        ]
    )
    assert len(kept) == 2


def test_depth_percentile_uses_central_half():
    import numpy as np

    from server.objects.depth import box_depth

    depth = np.full((10, 10), 1.0, np.float32)
    depth[2:8, 2:8] = 2.0
    conf = np.ones((10, 10), np.uint8)
    # Box covers the whole image. The central half is the inner 50% (indices 2:8).
    z = box_depth(depth, conf, [0, 0, 10, 10])
    assert z == pytest.approx(2.0)


def test_wall_cast_hits_the_meter_wall():
    import numpy as np

    from server.objects.lift import cast_ray

    # Camera at z=5 looking toward -z would need a ray. Build a ray from (0, 1, 5) toward the wall x in [0,4] at z=0.
    origin = np.array([1.0, 1.0, 5.0])
    direction = np.array([0.0, 0.0, -1.0])
    wall = {"id": "w1", "baseline_m": [[0.0, 0.0], [4.0, 0.0]], "evidence": "observed", "plus_minus_m": 0.05}
    hit = cast_ray(origin, direction, wall)
    assert hit is not None
    point, s = hit
    assert point[2] == pytest.approx(0.0, abs=1e-6)
    assert s == pytest.approx(1.0, abs=1e-6)


def test_config_reads_hss_objects_env(monkeypatch):
    monkeypatch.setenv("HSS_OBJECTS_VLM_BUDGET_USD_PER_HOUSE", "0.4")
    monkeypatch.setenv("HSS_OBJECTS_SECOND_READER", "off")
    monkeypatch.setenv("VISION_EXTERNAL_CALLS", "off")
    cfg = ObjectsConfig.from_env()
    assert cfg.budget_usd == 0.4
    assert cfg.second_reader == "off"
    assert cfg.external_vision is False


def test_unset_vision_switch_stays_off(monkeypatch):
    """SD-67: unset is off in dev and in prod. A Settings fill-in of on is not authorization."""
    monkeypatch.delenv("VISION_EXTERNAL_CALLS", raising=False)
    monkeypatch.delenv("SMP_ENVIRONMENT", raising=False)
    assert ObjectsConfig.from_env().external_vision is False
    monkeypatch.setenv("SMP_ENVIRONMENT", "prod")
    assert ObjectsConfig.from_env().external_vision is False

    class Settings:
        VISION_EXTERNAL_CALLS = "on"

    assert ObjectsConfig.from_env(Settings()).external_vision is False
    monkeypatch.setenv("VISION_EXTERNAL_CALLS", "on")
    assert ObjectsConfig.from_env(Settings()).external_vision is True
    monkeypatch.setenv("VISION_EXTERNAL_CALLS", "off")
    assert ObjectsConfig.from_env().external_vision is False


def test_request_reads_only_standard_key_names(tmp_path: Path, monkeypatch):
    """Pooled and production ChatGraph names are present and must not become the API key."""
    banned = {
        "OpenAIChatGraphPooledProcApiKey": "proc-unused",
        "OpenAIChatGraphPooledProdApiKey": "prod-unused",
        "XAI_CHATGRAPH_PROD_API_KEY": "chatgraph-prod-unused",
    }
    for name, value in banned.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    created: list[dict] = []

    class FakeClient:
        def __init__(self, **kwargs):
            created.append(kwargs)
            self.chat = self

        class completions:
            @staticmethod
            def create(**kwargs):
                raise AssertionError("no network")

    monkeypatch.setattr("server.objects.vlm._openai", lambda: type("Mod", (), {"OpenAI": FakeClient}))
    client = VisionClient(ObjectsConfig(), tmp_path)
    image = tmp_path / "frame.jpg"
    _write_image(image)
    with pytest.raises(KeyError, match="OPENAI_API_KEY"):
        client._request("gpt-5.5", image, "prompt")
    with pytest.raises(KeyError, match="XAI_API_KEY"):
        client._request("grok-4.7", image, "prompt")
    assert created == []

    monkeypatch.setenv("OPENAI_API_KEY", "standard-openai")
    monkeypatch.setenv("XAI_API_KEY", "standardxai")
    with pytest.raises(AssertionError, match="no network"):
        client._request("gpt-5.5", image, "prompt")
    with pytest.raises(AssertionError, match="no network"):
        client._request("grok-4.7", image, "prompt")
    assert [row["api_key"] for row in created] == ["standard-openai", "standardxai"]
    assert created[1]["base_url"] == "https://api.x.ai/v1"
    assert all(row["api_key"] not in banned.values() for row in created)


def test_objects_sources_omit_pooled_key_names():
    other = "Anth" + "ropic"
    banned = (
        "OpenAIChatGraphPooledProcApiKey",
        "OpenAIChatGraphPooledProdApiKey",
        "XAI_CHATGRAPH_PROD_API_KEY",
        other + "ChatGraphPooledProcApiKey",
        other + "ChatGraphPooledProdApiKey",
        other.upper() + "_API_KEY",
    )
    root = Path(__file__).resolve().parents[1]
    files = [path for path in root.rglob("*.py") if "tests" not in path.parts and "__pycache__" not in path.parts]
    text = "\n".join(path.read_text() for path in files)
    for name in banned:
        assert name not in text


def test_effective_budget_takes_the_lower_cap():
    from server.objects.config import effective_budget

    assert effective_budget(ObjectsConfig(budget_usd=0.85), 0.2) == 0.2
    assert effective_budget(ObjectsConfig(budget_usd=0.85), None) == 0.85


def test_planned_prices_match_the_brief():
    assert PLANNED_USD["gpt-5.4-mini"] == 0.0011
    assert PLANNED_USD["gpt-5.5"] == 0.0167
    assert PLANNED_USD["grok-4.7"] == 0.029


def test_stage_names_export():
    from server.objects import run, run_3d

    assert callable(run) and callable(run_3d)


def test_missing_packet_fails_the_stage(tmp_path: Path):
    """A frames or normalized input is not how WS-03 provisions this stage."""

    class Ctx:
        work_dir = tmp_path
        inputs = {"frames": str(tmp_path / "frames.json"), "normalized": str(tmp_path / "normalized.json")}
        settings = None
        vlm_budget_usd = 0.85

    with pytest.raises(ObjectsError) as caught:
        run(Ctx())
    assert caught.value.code == "inputs_missing:packet"


def _stub_local_models(monkeypatch):
    """`server.objects.run` is the stage function. Patch the module loaded by name."""
    monkeypatch.delenv("VISION_EXTERNAL_CALLS", raising=False)
    run_mod = importlib.import_module("server.objects.run")

    def embed(frames, cfg):
        return {"clip": {frame.id: {} for frame in frames}, "siglip": {frame.id: {} for frame in frames}}

    monkeypatch.setattr(run_mod, "_embed_and_score", embed)
    monkeypatch.setattr(run_mod, "run_owlv2", lambda frames, **kwargs: {})
    monkeypatch.setattr(run_mod, "run_gdino", lambda frames, **kwargs: {})
    return run_mod


def test_run_reads_keyframes_from_hydrated_packet_dir(tmp_path: Path, monkeypatch):
    """Stub packet/normalized.json sits beside packet.json. Frames come from the packet and its files."""
    run_mod = _stub_local_models(monkeypatch)
    packet_dir = tmp_path / "packet"
    (packet_dir / "keyframes").mkdir(parents=True)
    image = packet_dir / "keyframes" / "k0.jpg"
    image.write_bytes(b"jpeg-bytes")
    (packet_dir / "normalized.json").write_text(json.dumps({"format": "house-capture-packet", "stub": True}))
    (packet_dir / "packet.json").write_text(
        json.dumps(
            {
                "capture_id": "cap",
                "keyframes": [{"id": "k0", "img": "keyframes/k0.jpg", "w": 8, "h": 8}],
                "stills": [{"id": "s1", "img": "stills/s1.jpg", "w": 4, "h": 4, "purpose": "meter_surround"}],
                "files": [
                    {"path": "keyframes/k0.jpg", "role": "keyframe"},
                    {"path": "features/k0.bin", "role": "features"},
                ],
            }
        )
    )
    (packet_dir / "stills").mkdir()
    (packet_dir / "stills" / "s1.jpg").write_bytes(b"still")
    work = tmp_path / "work"
    work.mkdir()

    class Ctx:
        work_dir = work
        inputs = {
            "packet_dir": str(packet_dir),
            "run_control": "run-control",
            "frames": str(tmp_path / "not-frames.json"),
            "normalized": str(packet_dir / "normalized.json"),
        }
        settings = None
        vlm_budget_usd = 0.85

    result = run_mod.run(Ctx())
    key = "objects/objects.json"
    assert result.artifacts[key] == key
    assert "objects.json" not in result.artifacts
    doc = json.loads((work / key).read_text())
    assert doc["format"] == "hss-objects/v0"
    assert (work / "objects" / "frame_scores.json").is_file()
    scores = json.loads((work / "objects" / "frame_scores.json").read_text())
    assert "k0" in scores
    assert "s1" in scores


def test_run_reads_committed_files_when_keyframes_are_absent(tmp_path: Path, monkeypatch):
    run_mod = _stub_local_models(monkeypatch)
    packet_dir = tmp_path / "packet"
    (packet_dir / "keyframes").mkdir(parents=True)
    (packet_dir / "keyframes" / "k00001.jpg").write_bytes(b"jpeg")
    (packet_dir / "packet.json").write_text(
        json.dumps(
            {
                "files": [
                    {"path": "features/k00001.bin", "role": "features"},
                    {"path": "keyframes/k00001.jpg", "role": "keyframe"},
                ]
            }
        )
    )
    work = tmp_path / "work"
    work.mkdir()

    class Ctx:
        work_dir = work
        inputs = {"packet_dir": str(packet_dir), "run_control": "run-control"}
        settings = None
        vlm_budget_usd = 0.85

    result = run_mod.run(Ctx())
    assert result.artifacts["objects/objects.json"] == "objects/objects.json"
    scores = json.loads((work / "objects" / "frame_scores.json").read_text())
    assert set(scores) == {"k00001"}


def test_run_3d_reads_published_artifacts_from_the_work_dir(tmp_path: Path):
    """WS-03 hydrates §3.9 keys into the work dir and does not set ctx.inputs scene/objects."""
    scene_dir = tmp_path / "scene"
    objects_dir = tmp_path / "objects"
    scene_dir.mkdir()
    objects_dir.mkdir()
    (scene_dir / "scene.json").write_text(json.dumps({"keyframes": [], "walls": [], "objects": []}))
    (objects_dir / "objects.json").write_text(
        json.dumps({"format": "hss-objects/v0", "objects": [{"id": "door-1", "class": "door", "views": []}]})
    )

    class Ctx:
        work_dir = tmp_path
        inputs = {"packet_dir": str(tmp_path), "run_control": "run-control"}
        settings = None
        tier = "arkit"
        vlm_budget_usd = 0.85

    result = run_3d(Ctx())
    written = json.loads((objects_dir / "objects_3d.json").read_text())
    assert written["format"] == "hss-objects-3d/v0"
    assert written["objects"]
    assert result.metrics["objects_emitted"] == 1.0
    objects_key = "objects/objects_3d.json"
    assert result.artifacts[objects_key] == objects_key
    assert "objects_3d.json" not in result.artifacts
    key = "objects/view_requests.json"
    assert result.artifacts[key] == key
    assert "view_requests.json" not in result.artifacts
    requests = json.loads((tmp_path / key).read_text())
    assert requests and requests[0]["object_id"] == written["objects"][0]["id"]
    assert requests[0]["why"] == "location_unsure"


def test_run_3d_missing_scene_artifact_names_the_file(tmp_path: Path):
    class Ctx:
        work_dir = tmp_path
        inputs = {"scene": str(tmp_path / "not-used.json"), "objects": str(tmp_path / "not-used.json")}
        settings = None
        tier = "arkit"

    with pytest.raises(ObjectsError) as caught:
        run_3d(Ctx())
    assert caught.value.code == "inputs_missing:scene"


def test_hss_objects_3d_format_and_check(tmp_path: Path):
    scene = {"objects": [{"id": "meter", "class": "meter"}], "frame": {"anchor": {"position_m": [0, 0, 0]}}}
    cluster = Cluster("pool", [0, 0, 0], [{"frame": "a", "confidence": 0.5}], 0, "location_unsure")
    objects, requests = to_objects([cluster], {}, "photo_only", scene, ObjectsConfig(external_vision=False))
    doc = {"format": "hss-objects-3d/v0", "objects": objects}
    path = tmp_path / "objects_3d.json"
    path.write_text(json.dumps(doc))
    scene_path = tmp_path / "scene.json"
    scene_path.write_text(json.dumps(scene))
    from server.objects.cli import check_cmd

    class Args:
        objects_3d = path
        scene = scene_path

    check_cmd(Args())
    assert objects[0]["plus_minus_m"] >= 0.8
    assert requests


def test_both_no_second_reader_drops_when_agreement_says_drop():
    assert agreement("no", "no")[0] == "drop"


def test_opening_vertical_bound_is_added_once():
    cluster = Cluster(
        "door",
        [0, 1, 1],
        [{"frame": "a", "confidence": 0.9}, {"frame": "b", "confidence": 0.9}],
        0.5,
        "lidar",
    )
    objects, _ = to_objects([cluster], {}, "arkit_lidar", {"objects": []}, ObjectsConfig())
    assert objects[0]["plus_minus_m"] == pytest.approx(max(0.35, 0.5) + 0.2)


def test_water_heater_uses_other_class():
    cluster = Cluster(
        "water_heater",
        [1, 1, 1],
        [{"frame": "a", "confidence": 0.75}, {"frame": "b", "confidence": 0.75}],
        0.2,
        "lidar",
    )
    objects, _ = to_objects([cluster], {}, "arkit_lidar", {"objects": []}, ObjectsConfig())
    assert objects[0]["class"] == "other"
    assert objects[0]["label"] == "water_heater"
    validate_object(objects[0])


def test_fence_is_not_an_object():
    cluster = Cluster("fence", [0, 0, 0], [{"frame": "a"}, {"frame": "b"}], 0.1, "lidar")
    objects, _ = to_objects([cluster], {}, "arkit_lidar", {"objects": []}, ObjectsConfig())
    assert objects == []


def test_gate_is_sidecar_and_movable_obstacles_are_emitted():
    """SD-54: bin, shrub and tree are geometry. Blocking by profile is WS-09's."""
    clusters = [
        Cluster("gate", [0, 0, 0], [{"frame": "a"}, {"frame": "b"}], 0.1, "lidar"),
        Cluster("bin", [1, 0, 1], [{"frame": "a", "confidence": 0.8}, {"frame": "b", "confidence": 0.8}], 0.2, "lidar"),
        Cluster(
            "shrub", [2, 0, 1], [{"frame": "a", "confidence": 0.8}, {"frame": "b", "confidence": 0.8}], 0.2, "lidar"
        ),
        Cluster(
            "tree", [3, 0, 1], [{"frame": "a", "confidence": 0.8}, {"frame": "b", "confidence": 0.8}], 0.2, "lidar"
        ),
    ]
    objects, _ = to_objects(clusters, {}, "arkit_lidar", {"objects": []}, ObjectsConfig())
    assert [obj["class"] for obj in objects] == ["bin", "shrub", "tree"]
    assert all("attrs" not in obj for obj in objects)


def test_remapped_scene_class_still_emits_and_triangulates(monkeypatch):
    """2D rows store the scene class. 3D must keep ac_unit and panel, and treat ac_unit as free-standing."""
    from server.objects.run import _clusters_from_2d

    seen = {"free": [], "wall": []}

    def wall(dets, cams, scene):
        for rows in dets.values():
            seen["wall"].extend(row["label"] for row in rows)
        return []

    def free(dets, cams):
        for rows in dets.values():
            seen["free"].extend(row["label"] for row in rows)
        return []

    monkeypatch.setattr("server.objects.lift.lift_wall_cast", wall)
    monkeypatch.setattr("server.objects.lift.lift_triangulate", free)
    doc = {
        "objects": [
            {
                "class": "ac_unit",
                "confidence": 0.8,
                "views": [
                    {"image": "a", "box_px": [1, 1, 4, 4], "score": 0.8},
                    {"image": "b", "box_px": [1, 1, 4, 4], "score": 0.8},
                ],
            },
            {"class": "panel", "confidence": 0.7, "views": [{"image": "a", "box_px": [1, 1, 3, 3], "score": 0.7}]},
        ]
    }
    pose = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    scene = {
        "walls": [{"id": "w0"}],
        "keyframes": [
            {"id": "a", "pose": pose, "intrinsics": [100, 0, 5, 0, 100, 5, 0, 0, 1], "w": 10, "h": 10},
            {"id": "b", "pose": pose, "intrinsics": [100, 0, 5, 0, 100, 5, 0, 0, 1], "w": 10, "h": 10},
        ],
        "objects": [],
    }
    clusters = _clusters_from_2d(doc, scene, "arkit")
    objects, _ = to_objects(clusters, {}, "arkit", scene, ObjectsConfig(external_vision=False))
    assert {obj["class"] for obj in objects} == {"ac_unit", "panel"}
    assert seen["free"] == ["ac_condenser", "ac_condenser"]
    assert seen["wall"] == ["main_panel"]


def test_bare_keyframe_ids_share_one_house_and_are_filtered(tmp_path: Path):
    from server.objects.run import run_2d

    frames = [FrameRef(f"kf{i}", tmp_path / f"{i}.jpg", 8, 8, "keyframe") for i in range(21)]
    scores = {"clip": {}, "siglip": {}}
    for index, frame in enumerate(frames):
        row = {"meter": 1.0 if index < 2 else 0.0, "panel": 0.0, "gas_meter": 0.0, "ac_condenser": 0.0, "pool": 0.0}
        scores["clip"][frame.id] = row
        scores["siglip"][frame.id] = row
    run_2d(
        frames,
        {},
        tmp_path / "out",
        ObjectsConfig(external_vision=False),
        vlm_budget_usd=0.85,
        scores=scores,
        detections={},
    )
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert report["frames_total"] == 21
    assert report["frames_selected"] < 21
    assert report["frames_selected"] <= 20


def test_small_house_records_hazard_frames():
    from server.objects.select import select_frames

    frames = [f"h/{i}" for i in range(5)]
    scores = {"clip": {}, "siglip": {}}
    for index, fid in enumerate(frames):
        row = {
            "meter": 0.1,
            "panel": 0.1,
            "gas_meter": 1.0 if index == 2 else 0.0,
            "ac_condenser": 0.1,
            "pool": 0.0,
        }
        scores["clip"][fid] = row
        scores["siglip"][fid] = row
    sel = select_frames(scores, frames, k=2, max_selected=20, houses={fid: "h" for fid in frames})
    assert sel.frames == frames
    assert "h/2" in sel.hazard_frames


def test_presence_prompt_asks_for_b1_ground_type():
    from server.objects.prompts import GROUND_TYPES, PROMPT_PRESENCE_V1

    assert "ground_type" in PROMPT_PRESENCE_V1
    for name in GROUND_TYPES:
        assert name in PROMPT_PRESENCE_V1
    assert "pavers" not in PROMPT_PRESENCE_V1
    assert "walkway" not in PROMPT_PRESENCE_V1
    parsed = parse_presence(
        '{"present": {}, "ground_type": "concrete_or_paving", "quality": "good", "readable_equipment_text": "no"}'
    )
    assert parsed["ground_type"] == "concrete_or_paving"


def test_cascade_receives_gate_scores_and_escalates(tmp_path: Path, monkeypatch):
    from server.objects.confirm import _needs_confirm
    from server.objects.run import run_2d

    class Sel:
        hazard_frames: list[str] = []

    mini = {"present": {"gas_meter": "no", "ac_condenser": "no"}}
    scores = {"siglip": {"f1": {"gas_meter": 0.0, "ac_condenser": 0.5, "meter": 0, "panel": 0, "pool": 0}}}
    assert _needs_confirm("f1", mini, Sel(), scores, {"ac_condenser": 0.01}) is True
    assert _needs_confirm("f1", mini, Sel(), {}, {"ac_condenser": 0.01}) is False

    names = ["f0", "f1", "f2"]
    frames = []
    for index, name in enumerate(names):
        path = tmp_path / f"{name}.jpg"
        _write_image(path, color=(20 + index, 40, 60))
        frames.append(FrameRef(name, path, 8, 8, "keyframe", house="h"))
    gate = {"clip": {}, "siglip": {}}
    rows = {
        "f0": {"meter": 0.0, "panel": 0.0, "gas_meter": 1.0, "ac_condenser": 0.0, "pool": 0.0},
        "f1": {"meter": 0.0, "panel": 0.0, "gas_meter": 0.0, "ac_condenser": 1.0, "pool": 0.0},
        "f2": {"meter": 0.2, "panel": 0.0, "gas_meter": 0.0, "ac_condenser": 0.0, "pool": 0.0},
    }
    for model in gate:
        gate[model] = rows
    seen: list[tuple[str, str]] = []

    def transport(model, image, prompt):
        seen.append((model, Path(image).name))
        body = json.dumps({"present": {}, "quality": "good", "readable_equipment_text": "no", "ground_type": "grass"})
        return body, 10, 5, 0.0

    import importlib

    run_mod = importlib.import_module("server.objects.run")
    real = run_mod.VisionClient

    def patched(cfg, cache_dir, live_spend=None):
        return real(cfg, cache_dir, transport=transport, live_spend=live_spend)

    monkeypatch.setattr(run_mod, "VisionClient", patched)
    run_2d(
        frames,
        {},
        tmp_path / "out",
        ObjectsConfig(external_vision=True, top_k_per_class=1, second_reader="off"),
        vlm_budget_usd=0.85,
        scores=gate,
        detections={},
    )
    assert ("gpt-5.5", "f1.jpg") in seen
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert report["gate_thresholds"]["ac_condenser"] > 0


def test_mini_arkit_packet_path_is_explicit():
    from server.objects.cli import MINI_ARKIT_PACKET

    assert MINI_ARKIT_PACKET.as_posix() == "data/fixtures/packets/mini-arkit/packet.json"
    assert "*" not in MINI_ARKIT_PACKET.as_posix()


def test_cascade_eval_without_images_or_gt_uses_b3(tmp_path: Path):
    """Brief §7 omits --images and --gt. Those files live next to the b3 presence cache."""
    from server.objects.cli import main

    out = tmp_path / "cascade"
    main(
        [
            "cascade-eval",
            "--presence-cache",
            str(FIX / "b3" / "raw" / "presence"),
            "--scores",
            str(FIX / "b3" / "gate_scores_all.json"),
            "--out",
            str(out),
        ]
    )
    summary = json.loads((out / "cascade.json").read_text())
    assert summary["strict"]["house_recall"] == "11/11"
    assert summary["lenient"]["house_recall"] == "14/18"


def test_keyframes_skip_exif_and_stills_follow_it(tmp_path: Path):
    from PIL import Image

    from server.objects.gate import frame_needs_transpose, load_image

    img = Image.new("RGB", (2, 4), (255, 0, 0))
    exif = img.getexif()
    exif[274] = 6
    path = tmp_path / "turned.jpg"
    img.save(path, exif=exif)
    assert frame_needs_transpose(FrameRef("k", path, 2, 4, "keyframe")) is False
    assert frame_needs_transpose(FrameRef("s", path, 2, 4, "still")) is True
    assert load_image(path, transpose=False).size == (2, 4)
    assert load_image(path, transpose=True).size == (4, 2)


def test_select_house_skips_frames_without_scores():
    frames = ["a", "b", "c"]
    row = {"meter": 0.2, "panel": 0.0, "gas_meter": 0.0, "ac_condenser": 0.0, "pool": 0.0}
    score = {"a": {**row, "meter": 0.9}, "c": row}
    got, _hz = select_house(frames, score, k=2, min_gap=0)
    assert got == ["a", "c"]


def test_two_boxes_on_one_frame_both_emit(tmp_path: Path):
    from server.objects.run import run_2d

    frame = FrameRef("h/0", tmp_path / "missing.jpg", 10, 10, "keyframe", house="h")
    row = {"meter": 1, "panel": 0, "gas_meter": 0, "ac_condenser": 0, "pool": 0}
    scores = {"clip": {"h/0": row}, "siglip": {"h/0": row}}
    dets = {
        "h/0": [
            {"label": "window", "score": 0.6, "box": [0.1, 0.1, 0.2, 0.2]},
            {"label": "window", "score": 0.4, "box": [0.5, 0.5, 0.8, 0.8]},
        ]
    }
    run_2d(
        [frame],
        {},
        tmp_path / "out",
        ObjectsConfig(external_vision=False),
        vlm_budget_usd=0.85,
        scores=scores,
        detections=dets,
    )
    doc = json.loads((tmp_path / "out" / "objects.json").read_text())
    views = doc["objects"][0]["views"]
    assert len(views) == 2
    assert views[0]["box_px"] != views[1]["box_px"]


def test_grok_box_fills_a_class_with_no_seed(tmp_path: Path):
    from server.objects.config import FrameSelection
    from server.objects.confirm import run_cascade
    from server.objects.vlm import BoxAnswer, PresenceAnswer

    class Stub:
        def presence(self, model, image, prompt="x", *, transpose=False):
            present = {name: "no" for name in ("gas_meter",)}
            present["gas_meter"] = "yes"
            return PresenceAnswer(present=present, cost_usd=0.0, cached=True)

        def boxes(self, model, image, prompt="x", *, transpose=False):
            return BoxAnswer(
                objects=[{"label": "gas_meter", "box": [0.2, 0.2, 0.5, 0.6], "score": 1.0}],
                present={"gas_meter": "yes"},
                cost_usd=0.0,
                cached=True,
            )

    frame = FrameRef("f", tmp_path / "f.jpg", 100, 80, "keyframe")
    sel = FrameSelection(frames=["f"], hazard_frames=[], filtered_frac=0.0)
    result = run_cascade(
        sel,
        {"f": frame},
        Stub(),
        Budget(0.85),
        ObjectsConfig(),
        {},
        gate_scores={"siglip": {"f": {}}},
        gate_thresholds={},
    )
    cand = next(c for c in result.candidates if c.cls == "gas_meter")
    assert cand.boxes["f"] == [0.2, 0.2, 0.5, 0.6]
    assert cand.grok_box is None
    assert "second_boxes_rejected" not in result.presence.get("f", {})


def test_grok_box_far_from_the_seed_is_rejected(tmp_path: Path):
    from server.objects.config import FrameSelection
    from server.objects.confirm import run_cascade
    from server.objects.vlm import BoxAnswer, PresenceAnswer

    class Stub:
        def presence(self, model, image, prompt="x", *, transpose=False):
            return PresenceAnswer(present={"gas_meter": "yes"}, cost_usd=0.0, cached=True)

        def boxes(self, model, image, prompt="x", *, transpose=False):
            return BoxAnswer(
                objects=[{"label": "gas_meter", "box": [0.7, 0.7, 0.9, 0.9], "score": 1.0}],
                present={"gas_meter": "yes"},
                cost_usd=0.0,
                cached=True,
            )

    frame = FrameRef("f", tmp_path / "f.jpg", 100, 80, "keyframe")
    sel = FrameSelection(frames=["f"], hazard_frames=[], filtered_frac=0.0)
    dets = {"f": [{"label": "gas_meter", "score": 0.8, "box": [0.1, 0.1, 0.2, 0.2]}]}
    result = run_cascade(
        sel,
        {"f": frame},
        Stub(),
        Budget(0.85),
        ObjectsConfig(),
        dets,
        gate_scores={"siglip": {"f": {}}},
        gate_thresholds={},
    )
    cand = next(c for c in result.candidates if c.cls == "gas_meter")
    assert cand.grok_box is None
    assert result.presence["f"]["second_boxes_rejected"][0]["label"] == "gas_meter"


def test_low_registration_still_places(monkeypatch):
    from server.objects.run import _clusters_from_2d

    scene = {
        "_low_registration": True,
        "walls": [{"id": "w", "baseline_m": [[0, 0], [1, 0]]}],
        "keyframes": [
            {
                "id": "a",
                "pose": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                "intrinsics": [1, 1, 0, 0],
                "w": 10,
                "h": 10,
            },
            {
                "id": "b",
                "pose": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 1, 0, 0, 1],
                "intrinsics": [1, 1, 0, 0],
                "w": 10,
                "h": 10,
            },
        ],
    }
    doc = {
        "objects": [
            {
                "class": "window",
                "label": "window",
                "confidence": 0.8,
                "views": [
                    {"image": "a", "box_px": [1, 1, 4, 4], "score": 0.8},
                    {"image": "b", "box_px": [1, 1, 4, 4], "score": 0.8},
                ],
            }
        ]
    }

    def wall(dets, cams, scene_arg, **kwargs):
        return [
            Cluster(
                "window",
                [1, 1, 1],
                [
                    {"frame": "a", "box": [0.1, 0.1, 0.4, 0.4], "score": 0.8},
                    {"frame": "b", "box": [0.1, 0.1, 0.4, 0.4], "score": 0.8},
                ],
                0.1,
                "wall_cast",
                wall_id="w",
                s_m=[0.0, 1.0],
                bottom_m=0.0,
                top_m=1.0,
            )
        ]

    monkeypatch.setattr("server.objects.lift.lift_wall_cast", wall)
    clusters = _clusters_from_2d(doc, scene, "arkit")
    assert any(cluster.method == "wall_cast" for cluster in clusters)
    objects, _requests = to_objects(clusters, {}, "arkit", scene, ObjectsConfig(external_vision=False))
    placed = [obj for obj in objects if obj["plus_minus_m"] < 10]
    assert placed
    assert placed[0]["evidence"] == "inferred"
    assert placed[0]["plus_minus_m"] >= 1


def test_unused_view_stays_location_unsure(monkeypatch):
    from server.objects.run import _clusters_from_2d

    scene = {
        "walls": [{"id": "w", "baseline_m": [[0, 0], [1, 0]]}],
        "keyframes": [
            {
                "id": "a",
                "pose": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                "intrinsics": [1, 1, 0, 0],
                "w": 10,
                "h": 10,
            },
            {
                "id": "b",
                "pose": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 1, 0, 0, 1],
                "intrinsics": [1, 1, 0, 0],
                "w": 10,
                "h": 10,
            },
        ],
    }
    doc = {
        "objects": [
            {
                "class": "window",
                "confidence": 0.8,
                "views": [
                    {"image": "a", "box_px": [1, 1, 4, 4], "score": 0.8},
                    {"image": "b", "box_px": [6, 6, 9, 9], "score": 0.4},
                ],
            }
        ]
    }

    def wall(dets, cams, scene_arg, **kwargs):
        return [
            Cluster(
                "window",
                [1, 1, 1],
                [{"frame": "a", "box": [0.1, 0.1, 0.4, 0.4], "score": 0.8}],
                0.1,
                "wall_cast",
                wall_id="w",
                s_m=[0.0, 1.0],
                bottom_m=0.0,
                top_m=1.0,
            )
        ]

    monkeypatch.setattr("server.objects.lift.lift_wall_cast", wall)
    monkeypatch.setattr("server.objects.lift.lift_triangulate", lambda *args, **kwargs: [])
    clusters = _clusters_from_2d(doc, scene, "arkit")
    unsure = [cluster for cluster in clusters if cluster.method == "location_unsure"]
    assert len(unsure) == 1
    assert unsure[0].views[0]["frame"] == "b"


def test_missing_detections_run_proposers(monkeypatch, tmp_path: Path):
    import importlib

    from server.objects.config import Detection, ObjectsError

    # `server.objects.run` is the stage function. The module is loaded by name.
    run_mod = importlib.import_module("server.objects.run")

    frame = FrameRef("h/0", tmp_path / "missing.jpg", 10, 10, "keyframe", house="h")
    row = {"meter": 1, "panel": 0, "gas_meter": 0, "ac_condenser": 0, "pool": 0}
    scores = {"clip": {"h/0": row}, "siglip": {"h/0": row}}

    def owl(frames, **kwargs):
        return {frames[0].id: [Detection("vent", 0.4, [0.1, 0.1, 0.2, 0.2])]}

    def gdino(frames, **kwargs):
        return {frames[0].id: [Detection("door", 0.5, [0.3, 0.3, 0.6, 0.7])]}

    monkeypatch.setattr(run_mod, "run_owlv2", owl)
    monkeypatch.setattr(run_mod, "run_gdino", gdino)
    run_mod.run_2d(
        [frame],
        {},
        tmp_path / "out",
        ObjectsConfig(external_vision=False),
        vlm_budget_usd=0.85,
        scores=scores,
        detections=None,
    )
    labels = {obj["label"] for obj in json.loads((tmp_path / "out" / "objects.json").read_text())["objects"]}
    assert "door" in labels
    assert "vent" in labels

    def boom(*args, **kwargs):
        raise ImportError("torch")

    monkeypatch.setattr(run_mod, "run_owlv2", boom)
    with pytest.raises(ObjectsError, match="proposer_model_missing"):
        run_mod.run_2d(
            [frame],
            {},
            tmp_path / "out2",
            ObjectsConfig(external_vision=False),
            vlm_budget_usd=0.85,
            scores=scores,
            detections=None,
        )


def test_alignment_inverts_packet_point():
    import numpy as np

    from server.objects.lift import alignment_to_source

    alignment = {
        "R_align_rows_src": [[0.0, -1.0, 0.0], [0.0, 0.0, 1.0], [-1.0, 0.0, 0.0]],
        "origin_src": [1.0, 2.0, 3.0],
        "scale": 2.0,
    }
    source = np.array([4.0, 5.0, 6.0])
    rot = np.array(alignment["R_align_rows_src"])
    packet = alignment["scale"] * (source - np.array(alignment["origin_src"])) @ rot.T
    assert np.allclose(alignment_to_source(packet, alignment), source)


def test_lift_view_records_pixel_box():
    import numpy as np

    from server.objects.lift import lift_depth

    depth = np.full((40, 40), 1.5, np.float32)
    conf = np.ones((40, 40), np.uint8)
    pose = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    cam = {"pose": pose, "intrinsics": [20.0, 20.0, 20.0, 20.0], "w": 40, "h": 40}
    box = [0.2, 0.2, 0.8, 0.8]
    dets = {
        "a": [{"label": "window", "score": 0.9, "box": box}],
        "b": [{"label": "window", "score": 0.8, "box": box}],
    }
    cams = {"a": cam, "b": {**cam, "pose": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0.4, 0, 0, 1]}}
    clusters = lift_depth(dets, cams, {"a": (depth, conf), "b": (depth, conf)})
    assert clusters
    assert clusters[0].views[0]["box_px"] == [8.0, 8.0, 32.0, 32.0]


def test_clean_packet_cameras_match_the_scene():
    import numpy as np

    from server.objects.lift import alignment_to_source, pose_matrix

    root = ROOT / ".fixtures" / "packets" / "eth3d-facade"
    truth_path = root / "_truth" / "truth.json"
    scene_path = ROOT / "data" / "fixtures" / "scenes" / "eth3d-facade" / "gt-laser" / "scene.json"
    if not truth_path.is_file():
        pytest.skip("facade packet is not fetched")
    truth = json.loads(truth_path.read_text())
    scene = json.loads(scene_path.read_text())
    transform = pose_matrix(scene["frame"]["from_source"])
    scene_kf = {row["id"]: row for row in scene["keyframes"]}
    errs = []
    for frame in truth["variants"]["arkit-lidar"]["frames"]:
        centre = pose_matrix(frame["pose_true"])[:3, 3]
        predicted = alignment_to_source(centre, truth["alignment"]) @ transform[:3, :3].T + transform[:3, 3]
        stem = Path(frame["source_image"]).stem
        actual = pose_matrix(scene_kf[stem]["pose"])[:3, 3]
        errs.append(float(np.linalg.norm(predicted - actual)))
    assert max(errs) < 1e-3


def _jsonl(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            if "error" not in row:
                out[row["id"]] = row
    return out


def _write_image(path: Path, color: tuple[int, int, int] = (20, 40, 60)) -> None:
    from PIL import Image

    Image.new("RGB", (8, 8), color).save(path)
