"""OpenAI-compatible chat client. GPT-5.5 and Grok 4.7 only.

SD-67: the only key names this module reads are OPENAI_API_KEY and XAI_API_KEY.
"""

from __future__ import annotations

import json
import os
import time

PRICES = {
    "gpt-5.5": {"in": 1.25, "out": 10.0, "cache_read": 0.125},
    "grok-4.7": {"in": 2.0, "out": 10.0, "cache_read": 0.2},
    "gpt-5.4-mini": {"in": 0.4, "out": 1.6, "cache_read": 0.04},
}


def extract_json(text: str) -> dict | None:
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


class HouseSpend:
    """Running C3 spend for one house. A call at the cap is refused; one that lands over it is kept."""

    def __init__(self, limit_usd: float):
        self.limit_usd = float(limit_usd)
        self.spent = 0.0

    def blocked(self) -> bool:
        return self.spent >= self.limit_usd - 1e-9

    def charge(self, cost) -> float:
        try:
            amount = float(cost or 0.0)
        except (TypeError, ValueError):
            amount = 0.0
        if amount < 0:
            amount = 0.0
        self.spent += amount
        return amount


def parsed_answer(answer) -> tuple[dict | None, bool]:
    """Model JSON and whether the client envelope failed to parse.

    `chat` returns `{text, json, usage, cost_usd, model}`. A bare flag or fact dict,
    which the tests pass, is the model JSON itself.
    """
    if not isinstance(answer, dict):
        return None, False
    if any(key in answer for key in ("json", "cost_usd", "usage", "text")):
        payload = answer.get("json")
        return (payload if isinstance(payload, dict) else None), not isinstance(payload, dict)
    return answer, False


def answer_cost(answer) -> float:
    if not isinstance(answer, dict) or "cost_usd" not in answer:
        return 0.0
    try:
        amount = float(answer.get("cost_usd") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return amount if amount > 0 else 0.0


def answer_usage(answer) -> dict:
    if isinstance(answer, dict) and isinstance(answer.get("usage"), dict):
        return dict(answer["usage"])
    return {}


def cost_usd(model: str, usage: dict) -> float:
    p = PRICES.get(model, PRICES["gpt-5.5"])
    cached = usage.get("cached_tokens", 0)
    inp = usage.get("input_tokens", 0) - cached
    return (inp * p["in"] + cached * p["cache_read"] + usage.get("output_tokens", 0) * p["out"]) / 1e6


def api_key_for(model: str) -> str:
    """Return the key for this model. No other environment name is consulted."""
    name = "XAI_API_KEY" if model.startswith("grok") else "OPENAI_API_KEY"
    return os.environ[name]


def chat(model: str, prompt: str, images: list[dict] | None = None, timeout_s: float = 90.0) -> dict:
    """One JSON answer. `images` are data-url dicts. Raises on transport failure."""
    # openai is an optional extra. Importing it here keeps the criteria package
    # importable when that extra is not installed.
    from openai import OpenAI

    key = api_key_for(model)
    if model.startswith("grok"):
        client = OpenAI(api_key=key, base_url="https://api.x.ai/v1", timeout=timeout_s)
    else:
        client = OpenAI(api_key=key, timeout=timeout_s)
    content: list[dict] = []
    for img in images or []:
        content.append({"type": "image_url", "image_url": {"url": img["url"], "detail": "high"}})
    content.append({"type": "text", "text": prompt})
    last = None
    for attempt in range(3):
        try:
            msg = client.chat.completions.create(
                model=model,
                temperature=0,
                messages=[{"role": "user", "content": content}],
                timeout=timeout_s,
            )
            text = msg.choices[0].message.content or ""
            usage = {
                "input_tokens": getattr(msg.usage, "prompt_tokens", 0) or 0,
                "output_tokens": getattr(msg.usage, "completion_tokens", 0) or 0,
            }
            return {
                "text": text,
                "json": extract_json(text),
                "usage": usage,
                "cost_usd": cost_usd(model, usage),
                "model": model,
            }
        except Exception as exc:  # noqa: BLE001 — retry then surface
            last = exc
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(str(last))
