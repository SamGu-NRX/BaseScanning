"""Live-gate budget and refusal. No provider call, and no real meter number."""

from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path

import pytest
from PIL import Image

from server.reads.types import ReaderAnswer

ROOT = Path(__file__).resolve().parents[3]


def _gate():
    spec = importlib.util.spec_from_file_location("reads_gate_live", ROOT / "scripts" / "reads_gate.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _jpeg(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (20, 40, 60)).save(buf, format="JPEG")
    path.write_bytes(buf.getvalue())


def _panel_store(tmp_path: Path) -> None:
    raw = tmp_path / "internal" / "breadth" / "b2" / "raw" / "panel"
    raw.mkdir(parents=True)
    (raw / "vlm_gpt-5.4-mini.jsonl").write_text('{"id":"p01"}\n')
    (tmp_path / "internal" / "breadth" / "b2" / "inputs").mkdir(parents=True)


def _silence_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    monkeypatch.setenv("XAI_API_KEY", "unused")
    monkeypatch.setenv("OpenAIChatGraphPooledProdApiKey", "unused")
    monkeypatch.setenv("XAI_CHATGRAPH_PROD_API_KEY", "unused")
    monkeypatch.setenv("VISION_EXTERNAL_CALLS", "on")


def test_max_usd_defaults_to_12() -> None:
    args = _gate().build_parser().parse_args(["--live"])
    assert args.max_usd == 12
    assert args.workers == 12
    assert args.live is True


def test_hold_is_the_token_ceiling() -> None:
    gate = _gate()
    assert gate.hold_usd("gpt-5.5") == gate.cost_usd(
        "gpt-5.5", {"in": gate.HOLD_INPUT_TOKENS, "out": gate.HOLD_OUTPUT_TOKENS}
    )
    assert gate.hold_usd("gpt-5.5") > 0.2


def test_settle_releases_the_hold() -> None:
    gate = _gate()
    budget = gate.LiveBudget(0.30)
    hold = gate.hold_usd("gpt-5.5")
    assert budget.reserve(hold)
    assert budget.reserve(hold) is False
    budget.settle(hold, 0.01)
    assert budget.reserve(hold)
    budget.settle(hold, 0.02)
    assert budget.spent == pytest.approx(0.03)
    assert budget.held == pytest.approx(0)
    assert budget.skipped == 1


@pytest.mark.parametrize("raw", ["off", "yes", "true", "1"])
def test_live_refuses_unless_vision_is_exactly_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raw: str) -> None:
    gate = _gate()
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    monkeypatch.setenv("XAI_API_KEY", "unused")
    monkeypatch.setenv("VISION_EXTERNAL_CALLS", raw)

    def boom(*_args: object, **_kwargs: object) -> ReaderAnswer:
        raise AssertionError("provider call")

    monkeypatch.setattr(gate, "call_reader", boom)
    code = gate.main(["--live", "--set", "panel", "--out", str(tmp_path / "out")])
    assert code == 2


def test_live_authorization_allows_unset_or_on(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _gate()
    cfg = gate.ReadsConfig()
    monkeypatch.delenv("VISION_EXTERNAL_CALLS", raising=False)
    assert gate._authorize_live(cfg) is not None
    monkeypatch.setenv("VISION_EXTERNAL_CALLS", "on")
    assert gate._authorize_live(cfg) is not None
    for raw in ("off", "yes", "true", "1", ""):
        monkeypatch.setenv("VISION_EXTERNAL_CALLS", raw)
        assert gate._authorize_live(cfg) is None


def test_live_help_warns_not_to_commit_meter_numbers() -> None:
    text = _gate().build_parser().format_help()
    assert "Never commit or upload" in text


def test_live_refuses_without_the_named_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    gate = _gate()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("OpenAIChatGraphPooledProdApiKey", "unused")
    monkeypatch.setenv("XAI_CHATGRAPH_PROD_API_KEY", "unused")

    def boom(*_args: object, **_kwargs: object) -> ReaderAnswer:
        raise AssertionError("provider call")

    monkeypatch.setattr(gate, "call_reader", boom)
    code = gate.main(["--live", "--set", "panel", "--out", str(tmp_path / "out")])
    assert code == 2


def test_live_does_not_call_when_the_hold_exceeds_the_cap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    gate = _gate()
    _silence_providers(monkeypatch)
    _panel_store(tmp_path)
    _jpeg(tmp_path / "b2" / "panel" / "images" / "p01.jpg")
    monkeypatch.setenv("STORE", str(tmp_path))
    monkeypatch.setenv("B2_DATA", str(tmp_path / "b2"))
    called = {"n": 0}

    def boom(*_args: object, **_kwargs: object) -> ReaderAnswer:
        called["n"] += 1
        raise AssertionError("provider call")

    monkeypatch.setattr(gate, "call_reader", boom)
    code = gate.main(
        ["--live", "--set", "panel", "--max-usd", "0.01", "--workers", "2", "--out", str(tmp_path / "out")]
    )
    assert called["n"] == 0
    assert code == 1
    report = (tmp_path / "out" / "gate.json").read_text()
    assert '"skipped": 1' in report
    assert "000000" not in report


def test_live_settles_a_mocked_call_and_keeps_numbers_out_of_gate_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gate = _gate()
    _silence_providers(monkeypatch)
    _panel_store(tmp_path)
    _jpeg(tmp_path / "b2" / "panel" / "images" / "p01.jpg")
    monkeypatch.setenv("STORE", str(tmp_path))
    monkeypatch.setenv("B2_DATA", str(tmp_path / "b2"))

    def fake(*_args: object, **_kwargs: object) -> ReaderAnswer:
        return ReaderAnswer(
            model="gpt-5.4-mini",
            prompt_id="panel/v0",
            answer={"legibility": "poor", "hazard_line": "none_identified", "brand_evidence": "none"},
            secs=0.2,
            usage={"in": 10, "out": 10},
            cost_usd=0.01,
        )

    monkeypatch.setattr(gate, "call_reader", fake)
    code = gate.main(["--live", "--set", "panel", "--max-usd", "12", "--workers", "2", "--out", str(tmp_path / "out")])
    assert code == 1
    gate_text = (tmp_path / "out" / "gate.json").read_text()
    assert '"spent_usd": 0.01' in gate_text
    assert '"skipped": 0' in gate_text
    assert "000000" not in gate_text
    raw = (tmp_path / "out" / "raw" / "panel" / "vlm_gpt-5.4-mini.jsonl").read_text()
    assert "none_identified" in raw
    assert os.environ["OPENAI_API_KEY"] == "unused"
