"""Vision-language models via API as equipment readers.

    python vlm.py --model gpt-5.5 --set meter [--ids m01,m02] [--workers 6]

One structured JSON answer per photo, written to raw/<set>/vlm_<model>.jsonl (resumable), with
token usage and wall time. Photos are sent with the long side capped at 2048 px (JPEG q90);
providers may downscale further.
"""

import argparse
import base64
import io
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("B2_DATA", "/tmp/b2data"))
# Runs append here; keep it off the synced store while writing (sync makes conflict copies).
RAW = Path(os.environ.get("B2_RAW", HERE / "raw"))
MAX_SIDE = 2048

METER_PROMPT = """This is a photo of an electricity meter, taken for a utility site survey.
Transcribe only what is printed and visible. Never guess characters you cannot see; if part of a
string is unreadable, give null rather than a guess.

Return one JSON object and nothing else, with these keys:
- "meter_number": the meter's identifying number exactly as printed. When the plate carries
  several identifiers, prefer the utility's meter number (often large print, next to a barcode or
  after "No."/"Nr."/"#") over the manufacturer's serial, model or catalogue codes. null if not legible.
- "number_candidates": up to 5 distinct identifier strings printed on the meter (meter numbers,
  serials, barcode numbers), most likely meter number first. Exclude ratings, voltages, Kh, form,
  register readings and dates.
- "class_label": the ANSI class marking exactly as printed (for example "CL200", "CL 320",
  "CL20"); for meters without a CL marking, the current rating as printed (for example
  "10(60)A", "30(200)A"). null if none is legible.
- "manufacturer": maker name as printed, or null.
- "form": ANSI form as printed (for example "FM 2S"), or null.
- "meter_can": {"visible": true|false, "type": one of "ringless_socket", "ring_type_socket",
  "meter_main_combo", "multi_meter_bank", "pole_or_pedestal", "cabinet_or_panel_mounted",
  "no_socket_bolt_in_meter", "not_visible", "other"; "notes": short}
- "condition": {"rust": "none"|"light"|"heavy"|"cannot_tell", "physical_damage": true|false,
  "notes": short}
- "legibility": "good"|"partial"|"poor"
"""

PANEL_PROMPT = """This photo was taken for a residential battery-install site survey. It may show an
electrical panel (breaker box, load center, fuse box), its door label, a main breaker, a meter-main
combination, or something else.
Report only what is visible. Never guess text you cannot read; use null when unsure.

Return one JSON object and nothing else, with these keys:
- "equipment": one of "breaker_panel", "fuse_box", "meter_main_combo", "main_disconnect",
  "panel_label_only", "single_breaker", "industrial_or_non_residential", "not_electrical", "other"
- "manufacturer": the panel maker if visible as text or badge (for example Square D, Federal
  Pacific / FPE, Zinsco, Sylvania, GTE, Challenger, GE, Siemens, ITE, Eaton, Cutler-Hammer,
  Westinghouse, Murray, Crouse-Hinds, Bryant, Wadsworth, Pushmatic/Bulldog), or null.
- "brand_evidence": "printed_text"|"logo_badge"|"breaker_style_only"|"none"
- "product_line": series name as printed (for example "Stab-Lok", "QO", "Homeline", "Magnetrip",
  "Pushmatic"), or null.
- "main_breaker_amps": the main breaker's rating as an integer if its marking is legible, else null.
- "panel_rating_amps": the panel or bus rating from the label as an integer if legible, else null.
- "hazard_line": one of "federal_pacific_stab_lok", "zinsco_sylvania_magnetrip", "challenger",
  "pushmatic", "none_identified", "cannot_tell"
- "condition": {"rust": "none"|"light"|"heavy"|"cannot_tell", "damage_or_scorching": true|false,
  "notes": short}
- "text_seen": up to 10 short strings you can read that identify the maker, line, or ratings.
- "legibility": "good"|"partial"|"poor"
"""

PROMPTS = {"meter": METER_PROMPT, "panel": PANEL_PROMPT, "extra": PANEL_PROMPT}


def encode(path: Path) -> str:
    with Image.open(path) as im:
        image = im.convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def parse(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def call(model: str, prompt: str, b64: str) -> tuple[str, dict]:
    if model.startswith("claude"):
        import anthropic

        client = anthropic.Anthropic(api_key=os.environ["AnthropicChatGraphPooledProdApiKey"])
        r = client.messages.create(
            model=model,
            max_tokens=4000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        text = "".join(block.text for block in r.content if getattr(block, "type", "") == "text")
        return text, {"in": r.usage.input_tokens, "out": r.usage.output_tokens}
    from openai import OpenAI

    if model.startswith("grok"):
        client = OpenAI(api_key=os.environ["XAI_CHATGRAPH_PROD_API_KEY"], base_url="https://api.x.ai/v1")
    else:
        client = OpenAI(api_key=os.environ["OpenAIChatGraphPooledProdApiKey"])
    r = client.chat.completions.create(
        model=model,
        max_completion_tokens=8000,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + b64, "detail": "high"},
                    },
                ],
            }
        ],
    )
    return r.choices[0].message.content or "", {
        "in": r.usage.prompt_tokens,
        "out": r.usage.completion_tokens,
    }


def run_one(model: str, set_name: str, path: Path) -> dict:
    prompt = PROMPTS[set_name]
    b64 = encode(path)
    for attempt in range(4):
        start = time.time()
        try:
            text, usage = call(model, prompt, b64)
            return {
                "id": path.stem,
                "engine": f"vlm_{model}",
                "secs": round(time.time() - start, 2),
                "usage": usage,
                "answer": parse(text),
                "raw_text": text if parse(text) is None else "",
            }
        except Exception as error:  # noqa: BLE001
            if attempt == 3:
                return {"id": path.stem, "engine": f"vlm_{model}", "error": str(error)[:300]}
            time.sleep(10 * (attempt + 1))
    raise AssertionError("unreachable")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--set", required=True)
    parser.add_argument("--ids", default="")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    images = sorted((DATA / args.set / "images").glob("*.jpg"))
    if args.ids:
        wanted = set(args.ids.split(","))
        images = [p for p in images if p.stem in wanted]
    out = RAW / args.set / f"vlm_{args.model}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            record = json.loads(line)
            if "error" not in record:
                done.add(record["id"])
    todo = [p for p in images if p.stem not in done]
    with out.open("a") as handle, ThreadPoolExecutor(args.workers) as pool:
        for record in pool.map(lambda p: run_one(args.model, args.set, p), todo):
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            print(args.model, record["id"], record.get("secs"), "ERR" if "error" in record else "", flush=True)


if __name__ == "__main__":
    main()
