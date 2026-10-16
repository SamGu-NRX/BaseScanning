"""Tool-augmented variant: the same SSR prompts, plus measurement and render tools over a metric reconstruction.

Usage: python run_tools.py --house eth3d-facade [--models claude-sonnet-5] [--prompts p06,p07,p08,p12]
Prompts: verbatim (ssr_prompts.py), prompt first, then the same meter-marked photos as meter_hint.py.
Results: results/<house>/tools_<geometry>_verbatim/<model>/<prompt_id>.json
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import ImageDraw, ImageFont

import geometry as G
import meter_hint as MH
import ssr_prompts as S
import vlm

TOOL_PREAMBLE = """You also have tools backed by a metric 3D reconstruction of this site ({source}). Each photo has a known camera pose, so a pixel you point at can be located in 3D.
- Point at things with normalised pixel coordinates: x from 0 (left) to 1000 (right), y from 0 (top) to 1000 (bottom) of the photo.
- Use view_photo to zoom in with a coordinate grid before pointing, so your coordinates land on the exact edge or object you mean.
- Use measure / locate / facing_clearance for every distance that decides a check, instead of estimating from the image. Report measured values in your JSON.
- A pixel with no reconstructed geometry is unobserved. Generated or missing geometry is never evidence that an area is clear; unseen means unsure.
{anchors}When you are done, answer with the single JSON object the task asks for."""

TOOLS = [
    {"name": "scene_info", "description": "Geometry source, units, camera position/heading for each photo, and any capture anchors (e.g. the homeowner's meter tap).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "view_photo", "description": "Return a zoomed crop of a photo with a labelled 0-1000 coordinate grid (and projected anchors as red crosses). Use it to find exact pixel coordinates.",
     "input_schema": {"type": "object", "properties": {"photo": {"type": "integer"}, "x0": {"type": "number"}, "y0": {"type": "number"}, "x1": {"type": "number"}, "y1": {"type": "number"}},
                      "required": ["photo"]}},
    {"name": "locate", "description": "3D location of the surface at a pixel: height above ground (ft), plan position (ft), horizontal distance to the meter anchor, range from camera. Optionally save it under a name for measure().",
     "input_schema": {"type": "object", "properties": {"photo": {"type": "integer"}, "x": {"type": "number"}, "y": {"type": "number"}, "name": {"type": "string"}}, "required": ["photo", "x", "y"]}},
    {"name": "measure", "description": "Distance between two points, each given as {photo, x, y} or {mark: name} (a saved name or an anchor such as electric_meter). Returns 3D, horizontal and vertical distance in feet.",
     "input_schema": {"type": "object", "properties": {"a": {"type": "object"}, "b": {"type": "object"}}, "required": ["a", "b"]}},
    {"name": "facing_clearance", "description": "From a point on a wall, the open horizontal distance straight out from the wall to the first obstruction 1-6 ft high (fence, hedge, wall), measured over a battery-wide corridor.",
     "input_schema": {"type": "object", "properties": {"photo": {"type": "integer"}, "x": {"type": "number"}, "y": {"type": "number"}}, "required": ["photo", "x", "y"]}},
    {"name": "top_down", "description": "Render a top-down plan of the reconstruction (walls/objects, ground, cameras with photo numbers, anchors and saved marks, 10 ft scale bar). Optional center {photo,x,y} or {mark} and radius_ft.",
     "input_schema": {"type": "object", "properties": {"center": {"type": "object"}, "radius_ft": {"type": "number"}}}},
    {"name": "render_battery", "description": "Draw a 31 x 22 x 36 in battery flush against the wall at a pixel (point at the wall base where the battery would stand), with 3 ft and 5 ft ground offsets, into that photo. Also returns the facing clearance there.",
     "input_schema": {"type": "object", "properties": {"photo": {"type": "integer"}, "x": {"type": "number"}, "y": {"type": "number"}}, "required": ["photo", "x", "y"]}},
]


def view_photo(tools: G.SceneTools, photo: int, x0=0, y0=0, x1=1000, y1=1000):
    ph = tools.photos[photo]
    from PIL import Image

    img = Image.open(ph.path).convert("RGB")
    W, H = img.size
    dr = ImageDraw.Draw(img)
    cam = tools.geo.cameras.get(ph.frame)
    if cam is not None:
        for name, X in list(tools.geo.anchors.items()) + list(tools.marks.items()):
            Xc = cam.R @ X + cam.t
            if Xc[2] > 0.1:
                u = (cam.K[0, 0] * Xc[0] / Xc[2] + cam.K[0, 2]) * ph.scale
                v = (cam.K[1, 1] * Xc[1] / Xc[2] + cam.K[1, 2]) * ph.scale
                dr.line([u - 12, v, u + 12, v], fill=(255, 0, 0), width=3)
                dr.line([u, v - 12, u, v + 12], fill=(255, 0, 0), width=3)
                dr.text((u + 8, v + 8), name, fill=(255, 0, 0))
    x0, x1 = sorted((max(0, x0), min(1000, x1)))
    y0, y1 = sorted((max(0, y0), min(1000, y1)))
    if x1 - x0 < 20:
        x1 = min(1000, x0 + 20)
    if y1 - y0 < 20:
        y1 = min(1000, y0 + 20)
    crop = img.crop((int(x0 / 1000 * W), int(y0 / 1000 * H), int(x1 / 1000 * W), int(y1 / 1000 * H)))
    s = 1200 / max(crop.size)
    crop = crop.resize((max(1, int(crop.width * s)), max(1, int(crop.height * s))))
    dr = ImageDraw.Draw(crop)
    font = ImageFont.load_default()
    span = max(x1 - x0, y1 - y0)
    step = next(st for st in (5, 10, 25, 50, 100, 200) if span / st <= 12)
    for gx in np.arange(np.ceil(x0 / step) * step, x1 + 1e-6, step):
        px = (gx - x0) / (x1 - x0) * crop.width
        dr.line([px, 0, px, crop.height], fill=(0, 255, 255), width=1)
        dr.text((px + 2, 2), f"{gx:.0f}", fill=(0, 255, 255), font=font)
    for gy in np.arange(np.ceil(y0 / step) * step, y1 + 1e-6, step):
        py = (gy - y0) / (y1 - y0) * crop.height
        dr.line([0, py, crop.width, py], fill=(255, 0, 255), width=1)
        dr.text((2, py + 2), f"{gy:.0f}", fill=(255, 0, 255), font=font)
    return crop


def dispatch(tools: G.SceneTools, name: str, args: dict) -> tuple[str, object | None]:
    try:
        if name == "scene_info":
            return json.dumps(tools.scene_info()), None
        if name == "view_photo":
            return json.dumps({"ok": True, "grid": "cyan = x, magenta = y (0-1000 photo coordinates)"}), view_photo(tools, int(args["photo"]), args.get("x0", 0), args.get("y0", 0), args.get("x1", 1000), args.get("y1", 1000))
        if name == "locate":
            return json.dumps(tools.locate(int(args["photo"]), float(args["x"]), float(args["y"]), args.get("name"))), None
        if name == "measure":
            return json.dumps(tools.measure(args["a"], args["b"])), None
        if name == "facing_clearance":
            return json.dumps(tools.facing_clearance(int(args["photo"]), float(args["x"]), float(args["y"]))), None
        if name == "top_down":
            return json.dumps({"ok": True}), tools.top_down(args.get("center"), float(args.get("radius_ft", 40)))
        if name == "render_battery":
            img, info = tools.render_battery(int(args["photo"]), float(args["x"]), float(args["y"]))
            return json.dumps(info), img
    except Exception as e:
        return json.dumps({"ok": False, "reason": f"tool error: {e!r}"[:300]}), None
    return json.dumps({"ok": False, "reason": f"unknown tool {name}"}), None


def anchors_note(geo: G.Geometry) -> str:
    if "electric_meter" in geo.anchors:
        return ("- Capture anchor: during capture the homeowner tapped the electric meter's location; it is available as mark "
                "'electric_meter' (5 ft above ground on the meter wall) and is drawn as a red cross in view_photo and top_down.\n")
    return ""


def run_tool_prompt(model: str, geo: G.Geometry, photos: list[vlm.Photo], prompt: S.SSRPrompt, max_turns: int = 30) -> dict:
    if not model.startswith("claude"):
        return run_tool_prompt_openai(model, geo, photos, prompt, max_turns)
    tools = G.SceneTools(geo, photos)
    client = vlm.anthropic_client()
    system = TOOL_PREAMBLE.format(source=geo.source.replace("_", " "), anchors=anchors_note(geo))
    messages = [{"role": "user", "content": [{"type": "text", "text": MH.NOTE + prompt.text}] + vlm.photo_blocks_anthropic(photos)}]
    usage = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0}
    calls = []
    t0 = time.time()
    text = ""
    for turn in range(max_turns + 1):
        kwargs = dict(model=model, max_tokens=16000, system=system, messages=messages, tools=TOOLS)
        if turn == max_turns:
            kwargs["tool_choice"] = {"type": "none"}
        msg = client.messages.create(**kwargs)
        for k in usage:
            usage[k] += getattr(msg.usage, k, 0) or 0
        messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in msg.content]})
        uses = [b for b in msg.content if b.type == "tool_use"]
        text = "".join(b.text for b in msg.content if b.type == "text")
        if msg.stop_reason != "tool_use" or not uses:
            break
        results = []
        for u in uses:
            out, img = dispatch(tools, u.name, u.input)
            calls.append({"tool": u.name, "input": u.input, "output": json.loads(out)})
            content = [{"type": "text", "text": out}]
            if img is not None:
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": vlm.image_b64_png(img)}})
            results.append({"type": "tool_result", "tool_use_id": u.id, "content": content})
        messages.append({"role": "user", "content": results})
    return {"model": model, "text": text, "json": vlm.extract_json(text), "usage": usage, "cost_usd": vlm.cost_usd(model, usage),
            "latency_s": round(time.time() - t0, 1), "tool_calls": calls, "n_tool_calls": len(calls)}


def run_tool_prompt_openai(model: str, geo: G.Geometry, photos: list[vlm.Photo], prompt: S.SSRPrompt, max_turns: int = 30) -> dict:
    """Same loop over the Responses API; tool images go back inside function_call_output."""
    tools = G.SceneTools(geo, photos)
    client = vlm.openai_client()
    fn_tools = [{"type": "function", "name": t["name"], "description": t["description"], "parameters": t["input_schema"]} for t in TOOLS]
    system = TOOL_PREAMBLE.format(source=geo.source.replace("_", " "), anchors=anchors_note(geo))
    items: list = [{"role": "user", "content": [{"type": "input_text", "text": MH.NOTE + prompt.text}] + vlm.photo_blocks_openai(photos)}]
    usage = {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
    calls = []
    t0 = time.time()
    prev = None
    text = ""
    for turn in range(max_turns + 1):
        kw = dict(model=model, instructions=system, input=items, tools=fn_tools, reasoning={"effort": "medium"}, max_output_tokens=16000)
        if prev:
            kw["previous_response_id"] = prev
        if turn == max_turns:
            kw["tool_choice"] = "none"
        resp = client.responses.create(**kw)
        u = resp.usage
        usage["input_tokens"] += u.input_tokens
        usage["cached_tokens"] += (u.input_tokens_details.cached_tokens if u.input_tokens_details else 0) or 0
        usage["output_tokens"] += u.output_tokens
        usage["reasoning_tokens"] += (u.output_tokens_details.reasoning_tokens if u.output_tokens_details else 0) or 0
        prev = resp.id
        text = resp.output_text or ""
        fcs = [o for o in resp.output if o.type == "function_call"]
        if not fcs:
            break
        items = []
        for fc in fcs:
            args = json.loads(fc.arguments or "{}")
            out, img = dispatch(tools, fc.name, args)
            calls.append({"tool": fc.name, "input": args, "output": json.loads(out)})
            output: object = out
            if img is not None:
                output = [{"type": "input_text", "text": out}, {"type": "input_image", "image_url": f"data:image/png;base64,{vlm.image_b64_png(img)}", "detail": "high"}]
            items.append({"type": "function_call_output", "call_id": fc.call_id, "output": output})
    return {"model": model, "text": text, "json": vlm.extract_json(text), "usage": usage, "cost_usd": vlm.cost_usd(model, usage),
            "latency_s": round(time.time() - t0, 1), "tool_calls": calls, "n_tool_calls": len(calls)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--house", default="eth3d-facade")
    ap.add_argument("--models", default="claude-sonnet-5,claude-opus-5-5")
    ap.add_argument("--prompts", default="p06,p07,p08,p12")
    a = ap.parse_args()
    if a.house != "eth3d-facade":
        raise SystemExit("only the laser-scan geometry is wired up so far")
    geo = G.load_eth3d_facade()
    photos = MH.marked_packet(geo)
    variant = "tools_laser_gt_verbatim"
    prompts = [p for p in S.PROMPTS if any(p.id.startswith(w) for w in a.prompts.split(","))]

    def job(model, prompt):
        out = vlm.RESULTS / a.house / variant / model / f"{prompt.id}.json"
        if out.exists():
            return
        r = run_tool_prompt(model, geo, photos, prompt)
        r.update(prompt_id=prompt.id, house=a.house, variant=variant, verdicts=S.normalize(prompt.id, r["json"]) if r.get("json") else {})
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(r, indent=1, default=str))
        print(a.house, model, prompt.id, r["verdicts"], f"${r['cost_usd']:.3f}", r["n_tool_calls"], "calls", flush=True)

    jobs = [(m, p) for m in a.models.split(",") for p in prompts]
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(lambda mp: job(*mp), jobs))


if __name__ == "__main__":
    main()
