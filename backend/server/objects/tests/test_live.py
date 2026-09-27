"""§8 live calls. Frames stay out of the public repo.

Skipped unless `VISION_EXTERNAL_CALLS` is explicitly on and both `OPENAI_API_KEY` and
`XAI_API_KEY` are set (SD-67). ChatGraph pooled names are not read. When those three
are set the cascade calls GPT-5.5 and Grok 4.7. Further vendor calls stop once planned
spend would meet `HSS_OBJECTS_LIVE_MAX_USD` (default 3). Cache hits are free.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from server.objects.config import FrameRef, ObjectsConfig
from server.objects.run import run_2d
from server.objects.vlm import LiveSpend, live_max_usd

HOUSE = "yt-5-M-7f35Tyg"


def _frames_dir() -> Path | None:
    """House JPEGs live in the agent store, never in the repo. Mac and cloud layouts both count."""
    override = os.environ.get("HSS_OBJECTS_LIVE_FRAMES", "").strip()
    if override:
        path = Path(override)
        if path.is_dir() and any(path.glob("*.jpg")):
            return path
    roots = (
        Path.home() / "Library/Application Support/Cursor/AgentStores/cursor_agent_stores",
        Path("/cursor/stores"),
    )
    relatives = (
        Path("files") / "internal" / "data" / "houses" / HOUSE / "frames",
        Path("internal") / "data" / "houses" / HOUSE / "frames",
    )
    for root in roots:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            for rel in relatives:
                frames = child / rel
                if frames.is_dir() and any(frames.glob("*.jpg")):
                    return frames
    return None


def _require_keys() -> None:
    switch = os.environ.get("VISION_EXTERNAL_CALLS", "").strip().lower()
    if switch not in {"1", "true", "on", "yes"}:
        pytest.skip("VISION_EXTERNAL_CALLS must be explicitly on")
    if not os.environ.get("OPENAI_API_KEY") or not os.environ.get("XAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY and XAI_API_KEY are required")


def _frames(house: Path) -> list[FrameRef]:
    frames = []
    for path in sorted((house / "frames").glob("*.jpg")):
        frames.append(FrameRef(path.name, path, 360, 480, "keyframe"))
    return frames


@pytest.fixture(scope="module")
def live_house(tmp_path_factory):
    _require_keys()
    frames_dir = _frames_dir()
    if frames_dir is None:
        pytest.skip(f"{HOUSE} frames are not on this machine")
    frames = _frames(frames_dir.parent)
    assert len(frames) == 150
    pytest.importorskip("torch")
    from server.objects.proposals import run_owlv2

    out = tmp_path_factory.mktemp("live-house")
    detections = run_owlv2(frames)
    cfg = ObjectsConfig.from_env()
    # One tally for both tests. Default cap is $3; a repeat run must hit the cache and add $0.
    spend = LiveSpend(live_max_usd())
    run_2d(
        frames,
        {"id": HOUSE},
        out,
        cfg,
        vlm_budget_usd=0.85,
        detections=detections,
        live_spend=spend,
    )
    report = json.loads((out / "report.json").read_text())
    presence = json.loads((out / "presence.json").read_text())
    return {
        "frames": frames,
        "out": out,
        "cfg": cfg,
        "detections": detections,
        "spend": spend,
        "report": report,
        "presence": presence,
    }


@pytest.mark.live
def test_live_house_finds_gas_and_electric_meter(live_house):
    presence = live_house["presence"]
    summary = presence["class_summary"]
    assert summary.get("gas_meter", {}).get("state") == "present"
    assert summary.get("electric_meter", {}).get("state") == "present"
    report = live_house["report"]
    assert report["calls"].get("gpt-5.5", 0) >= 1
    assert report["calls"].get("grok-4.7", 0) >= 1
    assert report["cost_usd"].get("gpt-5.5", 0) > 0
    assert report["cost_usd"].get("grok-4.7", 0) > 0
    assert report["cost_usd"]["total"] <= 0.85
    assert report["cost_usd"]["total"] <= live_max_usd()
    assert live_house["spend"].spent == pytest.approx(report["cost_usd"]["total"])
    assert report["frames_selected"] <= 20


@pytest.mark.live
def test_live_repeat_uses_only_the_cache(live_house):
    before = live_house["spend"].spent
    run_2d(
        live_house["frames"],
        {"id": HOUSE},
        live_house["out"],
        live_house["cfg"],
        vlm_budget_usd=0.85,
        detections=live_house["detections"],
        live_spend=live_house["spend"],
    )
    report = json.loads((live_house["out"] / "report.json").read_text())
    assert report["cost_usd"]["total"] == 0
    assert report["cache_hits"] == sum(report["calls"].values())
    assert report["cache_hits"] > 0
    assert live_house["spend"].spent == pytest.approx(before)
