"""OpenAI and xAI vision calls, response cache, and parsers."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from server.objects.config import PLANNED_USD, TOKEN_PRICES, XAI_BASE_URL, ObjectsConfig
from server.objects.gate import load_image
from server.objects.prompts import (
    BOXES_SHA,
    GROUND_MATERIAL,
    PRESENCE_CLASSES,
    PRESENCE_SHA,
    PROMPT_BOXES_V1,
    PROMPT_PRESENCE_V1,
)

YES = ("yes", "no", "unsure")
LIVE_MAX_USD_ENV = "HSS_OBJECTS_LIVE_MAX_USD"
LIVE_MAX_USD_DEFAULT = 3.0
# SD-67: these two names only. No ChatGraph pooled or production key fallback.
OPENAI_KEY_ENV = "OPENAI_API_KEY"
XAI_KEY_ENV = "XAI_API_KEY"


class LiveBudgetExceeded(RuntimeError):
    """The next vendor call would meet or pass the live-test spend cap."""


@dataclass
class LiveSpend:
    """Session tally for the two `-m live` tests. Cache hits are not charged."""

    cap: float
    spent: float = 0.0

    def allow(self, model: str) -> bool:
        return self.spent + PLANNED_USD.get(model, 0.0) < self.cap


def live_max_usd() -> float:
    """`HSS_OBJECTS_LIVE_MAX_USD`, default 3. The live tests refuse the next vendor call at this cap."""
    raw = os.environ.get(LIVE_MAX_USD_ENV, "")
    if raw.strip() == "":
        return LIVE_MAX_USD_DEFAULT
    return float(raw)


def vendor_api_key(model: str) -> str:
    """Read `XAI_API_KEY` for Grok and `OPENAI_API_KEY` otherwise. Nothing else."""
    name = XAI_KEY_ENV if model.startswith("grok") else OPENAI_KEY_ENV
    return os.environ[name]


def cache_key(model: str, prompt_sha256: str, image_sha256: str, max_side: int, detail: str) -> str:
    raw = f"{model}|{prompt_sha256}|{image_sha256}|{max_side}|{detail}"
    return hashlib.sha256(raw.encode()).hexdigest()


def image_sha256(path: Path, max_side: int, *, transpose: bool = False) -> str:
    img = load_image(path, max_side, transpose=transpose)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return hashlib.sha256(buf.getvalue()).hexdigest()


def encode_jpeg(path: Path, max_side: int, *, transpose: bool = False) -> str:
    img = load_image(path, max_side, transpose=transpose)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def request_kwargs(model: str, seed: int) -> dict:
    """temperature 0 and seed for mini and Grok; gpt-5.5 omits temperature and sends reasoning_effort low."""
    if model == "gpt-5.5":
        return {"reasoning_effort": "low"}
    return {"temperature": 0, "seed": seed}


@dataclass
class PresenceAnswer:
    present: dict[str, str]
    ground_type: str | None = None
    quality: str | None = None
    readable_equipment_text: str | None = None
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cached: bool = False
    model: str = ""


@dataclass
class BoxAnswer:
    objects: list[dict]
    present: dict[str, str]
    ground_type: str | None = None
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cached: bool = False
    model: str = ""


def parse_presence(text: str) -> dict:
    data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    present = {k: str(data.get("present", {}).get(k, "no")).lower() for k in PRESENCE_CLASSES}
    for key, value in present.items():
        if value not in YES:
            present[key] = "unsure"
    return {
        "present": present,
        "readable_equipment_text": data.get("readable_equipment_text"),
        "quality": data.get("quality"),
        "ground_type": data.get("ground_type"),
    }


def parse_boxes(text: str) -> dict:
    data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    dets = []
    known = set(PRESENCE_CLASSES)
    for obj in data.get("objects", []):
        lab = obj.get("label")
        box = obj.get("box_2d")
        if lab not in known or not box or len(box) != 4:
            continue
        y0, x0, y1, x1 = (min(1.0, max(0.0, float(v) / 1000)) for v in box)
        if x1 <= x0 or y1 <= y0:
            continue
        dets.append({"label": lab, "score": float(obj.get("confidence", 1.0) or 1.0), "box": [x0, y0, x1, y1]})
    present = {c: str(data.get("present", {}).get(c, "no")).lower() for c in PRESENCE_CLASSES}
    ground = data.get("ground_type")
    if ground not in GROUND_MATERIAL:
        ground = None
    return {"objects": dets, "present": present, "ground_type": ground}


@dataclass
class VisionClient:
    """OpenAI SDK for both vendors. Tests pass `transport` to avoid the network."""

    cfg: ObjectsConfig
    cache_dir: Path
    transport: object | None = None
    live_spend: LiveSpend | None = None
    requests: int = 0
    cache_hits: int = 0
    _mem: dict = field(default_factory=dict)

    def presence(
        self, model: str, image: Path, prompt: str = PROMPT_PRESENCE_V1, *, transpose: bool = False
    ) -> PresenceAnswer:
        sha = prompt_sha(prompt, PRESENCE_SHA if prompt == PROMPT_PRESENCE_V1 else None)
        payload = self._call(model, image, prompt, sha, "high", transpose=transpose)
        parsed = payload["parsed"]
        return PresenceAnswer(
            present=parsed["present"],
            ground_type=parsed.get("ground_type"),
            quality=parsed.get("quality"),
            readable_equipment_text=parsed.get("readable_equipment_text"),
            cost_usd=float(payload["cost_usd"]),
            tokens_in=int(payload["tokens_in"]),
            tokens_out=int(payload["tokens_out"]),
            cached=bool(payload["cached"]),
            model=model,
        )

    def boxes(self, model: str, image: Path, prompt: str = PROMPT_BOXES_V1, *, transpose: bool = False) -> BoxAnswer:
        sha = prompt_sha(prompt, BOXES_SHA if prompt == PROMPT_BOXES_V1 else None)
        payload = self._call(model, image, prompt, sha, "high", kind="boxes", transpose=transpose)
        parsed = payload["parsed"]
        return BoxAnswer(
            objects=parsed["objects"],
            present=parsed["present"],
            ground_type=parsed.get("ground_type"),
            cost_usd=float(payload["cost_usd"]),
            tokens_in=int(payload["tokens_in"]),
            tokens_out=int(payload["tokens_out"]),
            cached=bool(payload["cached"]),
            model=model,
        )

    def _call(
        self,
        model: str,
        image: Path,
        prompt: str,
        prompt_digest: str,
        detail: str,
        kind: str = "presence",
        transpose: bool = False,
    ) -> dict:
        img_sha = image_sha256(image, self.cfg.max_side_px, transpose=transpose)
        key = cache_key(model, prompt_digest, img_sha, self.cfg.max_side_px, detail)
        hit = self._read(key)
        if hit is not None:
            self.cache_hits += 1
            hit["cached"] = True
            hit["cost_usd"] = 0.0
            return hit
        last = "vlm_unavailable"
        for attempt in range(3):
            try:
                self._require_live_spend(model)
                text, tin, tout, cost = self._request(model, image, prompt, transpose=transpose)
                parsed = parse_boxes(text) if kind == "boxes" else parse_presence(text)
                if cost is None:
                    pin, pout = TOKEN_PRICES.get(model, (0.0, 0.0))
                    cost = tin / 1e6 * pin + tout / 1e6 * pout
                rec = {
                    "parsed": parsed,
                    "cost_usd": cost,
                    "tokens_in": tin,
                    "tokens_out": tout,
                    "cached": False,
                    "model": model,
                }
                self._write(key, rec)
                _log_call(model, img_sha, tin, tout, cost)
                self._note_live_spend(float(cost))
                return rec
            except LiveBudgetExceeded:
                raise
            except Exception as exc:  # noqa: BLE001 — vendor and parse failures retry, then degrade
                last = repr(exc)[:300]
                if attempt < 2:
                    time.sleep(3 * (attempt + 1))
        raise RuntimeError(last)

    def _require_live_spend(self, model: str) -> None:
        """Refuse the HTTP call when planned cost would meet or pass the live cap. Cache hits never reach here."""
        if self.live_spend is None or self.live_spend.allow(model):
            return
        raise LiveBudgetExceeded(f"live_budget_exhausted:{model}")

    def _note_live_spend(self, amount: float) -> None:
        if self.live_spend is not None:
            self.live_spend.spent += amount

    def _request(
        self, model: str, image: Path, prompt: str, *, transpose: bool = False
    ) -> tuple[str, int, int, float | None]:
        self.requests += 1
        if self.transport is not None:
            return self.transport(model, image, prompt)  # type: ignore[operator]
        openai = _openai()
        timeout = self.cfg.grok_timeout_s if model.startswith("grok") else self.cfg.openai_timeout_s
        api_key = vendor_api_key(model)
        if model.startswith("grok"):
            client = openai.OpenAI(api_key=api_key, base_url=XAI_BASE_URL, timeout=timeout, max_retries=0)
        else:
            client = openai.OpenAI(api_key=api_key, timeout=timeout, max_retries=0)
        b64 = encode_jpeg(image, self.cfg.max_side_px, transpose=transpose)
        kwargs = request_kwargs(model, self.cfg.seed)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            **kwargs,
        )
        ticks = getattr(resp.usage, "cost_in_usd_ticks", None)
        cost = ticks / 1e10 if ticks is not None else None
        return resp.choices[0].message.content or "", resp.usage.prompt_tokens, resp.usage.completion_tokens, cost

    def _read(self, key: str) -> dict | None:
        if key in self._mem:
            return json.loads(json.dumps(self._mem[key]))
        path = self.cache_dir / f"{key}.json"
        if path.is_file():
            return json.loads(path.read_text())
        return None

    def _write(self, key: str, rec: dict) -> None:
        self._mem[key] = rec
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / f"{key}.json").write_text(json.dumps(rec))


def prompt_sha(text: str, known: str | None) -> str:
    if known and text:
        return known
    return hashlib.sha256(text.encode()).hexdigest()


def _openai():
    """The OpenAI SDK is an optional extra. Import it only when a live call is made."""
    import openai

    return openai


def _log_call(model: str, image_sha: str, tin: int, tout: int, cost: float) -> None:
    provider = "xai" if model.startswith("grok") else "openai"
    line = {
        "event": "vlm_call",
        "vlm_provider": provider,
        "vlm_model": model,
        "tokens_in": tin,
        "tokens_out": tout,
        "vlm_cost_usd": round(float(cost), 6),
        "image_sha256": image_sha,
    }
    print(json.dumps(line), flush=True)
