"""Shared plumbing for the C3 VLM judge: photo packets, provider calls, JSON parsing, cost."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

STORE = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2")
DATA = STORE / "internal/data"
C3 = STORE / "internal/breadth/c3-vlm"
RESULTS = C3 / "results"
CACHE = Path.home() / "cache/c3"

MAX_EDGE = 1568
PACKET_SIZE = 16
TAG_ORDER = [
    "meter", "meter_can", "panel", "gas_meter", "ac_unit", "openings_near_meter", "corners",
    "ground_14ft", "pool", "driveway", "fence", "overhangs",
]
MAX_TAGGED = 10

# USD per million tokens (checked 2026-09-26: platform.claude.com pricing, OpenAI model pages).
PRICES = {
    "claude-sonnet-5": {"in": 2.0, "cache_write": 2.5, "cache_read": 0.2, "out": 10.0},
    "claude-opus-5-5": {"in": 4.0, "cache_write": 5.0, "cache_read": 0.2, "out": 20.0},
    "claude-haiku-4-5-20251001": {"in": 1.0, "cache_write": 1.25, "cache_read": 0.1, "out": 5.0},
    "gpt-5.5": {"in": 5.0, "cache_read": 0.5, "out": 30.0},
    "gpt-5.6-sol": {"in": 4.0, "cache_read": 0.4, "out": 20.0},
    "gpt-5.6-terra": {"in": 2.0, "cache_read": 0.2, "out": 12.0},
}


@dataclass
class Photo:
    index: int  # 1-based, as the model sees it
    frame: str
    path: Path
    width: int
    height: int
    scale: float  # packet pixels / original pixels


def load_manifest() -> dict:
    p = DATA / "manifest.json"
    return json.loads(p.read_text()) if p.exists() else {"houses": []}


def house_entry(house_id: str) -> dict:
    per_house = DATA / "houses" / house_id / "house.json"
    if per_house.exists():
        try:
            return json.loads(per_house.read_text())
        except json.JSONDecodeError:
            pass
    for h in load_manifest().get("houses", []):
        if h["id"] == house_id:
            return h
    return {"id": house_id}


def ready_houses() -> list[str]:
    ids = []
    for d in sorted((DATA / "houses").iterdir()):
        if (d / "frames").is_dir() and house_entry(d.name).get("status") == "ready":
            ids.append(d.name)
    return ids


def select_frames(house_id: str, n: int = PACKET_SIZE) -> list[str]:
    """Frames a Base-style packet would hold: tagged evidence frames first, then even coverage."""
    frames_dir = DATA / "houses" / house_id / "frames"
    all_frames = sorted(p.name for p in frames_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    have = set(all_frames)
    entry = house_entry(house_id)
    tagged: list[str] = []
    for tag in TAG_ORDER:
        info = (entry.get("visible") or {}).get(tag) or {}
        if info.get("state") in ("yes", "partial"):
            for f in info.get("frames") or []:
                if f in have and f not in tagged and len(tagged) < MAX_TAGGED:
                    tagged.append(f)
    chosen = list(tagged)
    k = n - len(chosen)
    if k > 0 and all_frames:
        step = len(all_frames) / k
        for i in range(k):
            f = all_frames[min(len(all_frames) - 1, int(i * step + step / 2))]
            if f not in chosen:
                chosen.append(f)
    order = {f: i for i, f in enumerate(all_frames)}
    return sorted(set(chosen), key=lambda f: order[f])[:n]


def build_packet(house_id: str, frames: list[str] | None = None) -> list[Photo]:
    frames = frames or select_frames(house_id)
    out_dir = CACHE / house_id / "packet"
    out_dir.mkdir(parents=True, exist_ok=True)
    photos = []
    for i, f in enumerate(frames, 1):
        src = DATA / "houses" / house_id / "frames" / f
        dst = out_dir / (Path(f).stem + ".jpg")
        with Image.open(src) as im:
            im = im.convert("RGB")
            w0, h0 = im.size
            s = min(1.0, MAX_EDGE / max(w0, h0))
            if not dst.exists():
                tmp = dst.with_suffix(f".tmp{os.getpid()}.jpg")
                im.resize((round(w0 * s), round(h0 * s)), Image.LANCZOS).save(tmp, quality=88)
                tmp.replace(dst)
        with Image.open(dst) as im2:
            w, h = im2.size
        photos.append(Photo(i, f, dst, w, h, w / w0))
    return photos


def b64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode()


def image_b64_png(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode()


def extract_json(text: str) -> dict | None:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
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


def cost_usd(model: str, usage: dict) -> float:
    p = PRICES[model]
    if model.startswith("claude"):
        return (
            usage.get("input_tokens", 0) * p["in"]
            + usage.get("cache_creation_input_tokens", 0) * p["cache_write"]
            + usage.get("cache_read_input_tokens", 0) * p["cache_read"]
            + usage.get("output_tokens", 0) * p["out"]
        ) / 1e6
    cached = usage.get("cached_tokens", 0)
    return ((usage.get("input_tokens", 0) - cached) * p["in"] + cached * p["cache_read"] + usage.get("output_tokens", 0) * p["out"]) / 1e6


_anthropic = None
_openai = None


def anthropic_client():
    global _anthropic
    if _anthropic is None:
        import anthropic

        key = os.environ.get("ANTHROPIC_API_KEY") or os.environ["AnthropicChatGraphPooledProdApiKey"]
        _anthropic = anthropic.Anthropic(api_key=key, max_retries=4)
    return _anthropic


def openai_client():
    global _openai
    if _openai is None:
        import openai

        key = os.environ.get("OPENAI_API_KEY") or os.environ["OpenAIChatGraphPooledProdApiKey"]
        _openai = openai.OpenAI(api_key=key, max_retries=4)
    return _openai


def photo_blocks_anthropic(photos: list[Photo], cache: bool = True) -> list[dict]:
    blocks: list[dict] = []
    for ph in photos:
        blocks.append({"type": "text", "text": f"Photo {ph.index}:"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64(ph.path)}})
    if cache:
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks


def photo_blocks_openai(photos: list[Photo]) -> list[dict]:
    blocks: list[dict] = []
    for ph in photos:
        blocks.append({"type": "input_text", "text": f"Photo {ph.index}:"})
        blocks.append({"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64(ph.path)}", "detail": "high"})
    return blocks


def call_photo_prompt(model: str, system: str, photos: list[Photo], prompt_text: str, max_tokens: int = 16000, cache_key: str | None = None,
                      prompt_first: bool = False) -> dict:
    """One single-turn call. Default: photos first (cacheable prefix), then the prompt text.
    prompt_first: the prompt text, then the photos, as Base's prompts describe; nothing is shared across prompts, so no caching."""
    t0 = time.time()
    if model.startswith("claude"):
        text_block = [{"type": "text", "text": prompt_text}]
        content = text_block + photo_blocks_anthropic(photos, cache=False) if prompt_first else photo_blocks_anthropic(photos) + text_block
        kw = {"system": system} if system else {}
        msg = anthropic_client().messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}],
            **kw,
        )
        text = "".join(b.text for b in msg.content if b.type == "text")
        u = msg.usage
        usage = {
            "input_tokens": u.input_tokens,
            "cache_creation_input_tokens": u.cache_creation_input_tokens or 0,
            "cache_read_input_tokens": u.cache_read_input_tokens or 0,
            "output_tokens": u.output_tokens,
        }
        stop = msg.stop_reason
    else:
        text_block = [{"type": "input_text", "text": prompt_text}]
        content = text_block + photo_blocks_openai(photos) if prompt_first else photo_blocks_openai(photos) + text_block
        resp = openai_client().responses.create(
            model=model,
            instructions=system or None,
            input=[{"role": "user", "content": content}],
            reasoning={"effort": "medium"},
            max_output_tokens=max_tokens,
            prompt_cache_key=cache_key or photos[0].path.parent.parent.name,
        )
        text = resp.output_text
        u = resp.usage
        usage = {
            "input_tokens": u.input_tokens,
            "cached_tokens": (u.input_tokens_details.cached_tokens if u.input_tokens_details else 0) or 0,
            "output_tokens": u.output_tokens,
            "reasoning_tokens": (u.output_tokens_details.reasoning_tokens if u.output_tokens_details else 0) or 0,
        }
        stop = resp.status
    return {
        "model": model,
        "text": text,
        "json": extract_json(text),
        "usage": usage,
        "cost_usd": cost_usd(model, usage),
        "latency_s": round(time.time() - t0, 2),
        "stop": stop,
    }
