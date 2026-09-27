"""OpenAI and xAI chat calls. Answers are never logged."""

from __future__ import annotations

import json
import logging
import os
import re
import time

from server.reads.config import ReadsConfig
from server.reads.types import ReaderAnswer

try:
    from openai import OpenAI
except ImportError:  # the reads extra is optional for the default CI install
    OpenAI = None

log = logging.getLogger("server.reads")

# USD per million tokens (input, output). Grok completion tokens may omit reasoning.
PRICES = {
    "gpt-5.5": (5.0, 30.0),
    "gpt-5.4-mini": (0.75, 4.5),
    "grok-4.7": (2.0, 6.0),
}


def cost_usd(model: str, usage: dict) -> float:
    pin, pout = PRICES[model]
    return usage.get("in", 0) / 1e6 * pin + usage.get("out", 0) / 1e6 * pout


def parse_answer(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _api_key(model: str) -> str | None:
    """SD-67: only the named keys. Pooled and production aliases are not read."""
    name = "XAI_API_KEY" if model.startswith("grok") else "OPENAI_API_KEY"
    value = os.environ.get(name)
    return value or None


def _provider(model: str) -> str:
    return "xai" if model.startswith("grok") else "openai"


def log_call(
    *,
    model: str,
    prompt_id: str,
    sha256: str,
    usage: dict,
    cost: float,
    duration_ms: float,
    error_code: str | None,
    capture_id: str | None = None,
) -> None:
    """One JSON line. The sha256 prefix is the image digest, never a meter number."""
    record = {
        "level": "info" if error_code is None else "warning",
        "event": "vlm_call",
        "msg": "reader call",
        "vlm_provider": _provider(model),
        "vlm_model": model,
        "tokens_in": usage.get("in", 0),
        "tokens_out": usage.get("out", 0),
        "vlm_cost_usd": round(cost, 6),
        "duration_ms": round(duration_ms, 1),
        "prompt_id": prompt_id,
        "sha256_prefix": sha256[:12],
        "capture_id": capture_id,
        "error_code": error_code,
    }
    log.info("%s", json.dumps({k: v for k, v in record.items() if v is not None}))


def _require_openai() -> None:
    if OpenAI is None:
        log.error("openai is not installed; install the reads extra")
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        raise RuntimeError("openai is not installed")


def complete_chat(model: str, prompt: str, data_url: str, timeout_s: float) -> tuple[str, dict]:
    """One chat completion. Tests monkeypatch this; it is the only network entry."""
    _require_openai()
    client_cls = OpenAI
    if client_cls is None:
        raise RuntimeError("openai is not installed")
    key = _api_key(model)
    if not key:
        raise RuntimeError("provider_error")
    kwargs = {"api_key": key, "timeout": timeout_s}
    if model.startswith("grok"):
        kwargs["base_url"] = "https://api.x.ai/v1"
    client = client_cls(**kwargs)
    response = client.chat.completions.create(
        model=model,
        max_completion_tokens=8000,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                ],
            }
        ],
    )
    text = response.choices[0].message.content or ""
    usage = response.usage
    return text, {
        "in": getattr(usage, "prompt_tokens", 0) or 0,
        "out": getattr(usage, "completion_tokens", 0) or 0,
    }


def _error_code(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    if status in {402, 429}:
        return "provider_quota"
    name = type(exc).__name__.lower()
    if "timeout" in name or "timeout" in str(exc).lower():
        return "vlm_timeout"
    return "provider_error"


def call_reader(
    model: str,
    prompt_id: str,
    prompt: str,
    data_url: str,
    sha256: str,
    cfg: ReadsConfig,
    *,
    capture_id: str | None = None,
) -> ReaderAnswer:
    if not cfg.external_vision:
        return ReaderAnswer(model=model, prompt_id=prompt_id, error="disabled")
    _require_openai()
    if OpenAI is None:
        return ReaderAnswer(model=model, prompt_id=prompt_id, error="disabled")
    last_error = "provider_error"
    for attempt in range(cfg.attempts):
        started = time.time()
        try:
            text, usage = complete_chat(model, prompt, data_url, cfg.timeout_for(model))
        except Exception as exc:  # network and provider failures stay inside the attempt loop
            last_error = _error_code(exc)
            log_call(
                model=model,
                prompt_id=prompt_id,
                sha256=sha256,
                usage={},
                cost=0.0,
                duration_ms=(time.time() - started) * 1000,
                error_code=last_error,
                capture_id=capture_id,
            )
            if attempt + 1 < cfg.attempts:
                time.sleep(10 * (attempt + 1))
            continue
        parsed = parse_answer(text)
        spent = cost_usd(model, usage)
        secs = time.time() - started
        error = None if parsed is not None else "vlm_parse_error"
        log_call(
            model=model,
            prompt_id=prompt_id,
            sha256=sha256,
            usage=usage,
            cost=spent,
            duration_ms=secs * 1000,
            error_code=error,
            capture_id=capture_id,
        )
        return ReaderAnswer(
            model=model,
            prompt_id=prompt_id,
            answer=parsed,
            raw_text="" if parsed is not None else text,
            secs=round(secs, 2),
            usage=usage,
            cost_usd=spent,
            error=error,
        )
    return ReaderAnswer(model=model, prompt_id=prompt_id, error=last_error)
