"""Minimal World Labs (Marble) World API client: multi-image world, poll, PLY splat export.

Key from env BaseHackathonWorldLabsAPIKey. Never prints it.
  python wlclient.py credits
  python wlclient.py generate --name eth3d-facade --out DIR --images img1.jpg ... (<= 8, same size)
  python wlclient.py poll --out DIR            (reads DIR/operation.json)
  python wlclient.py export --out DIR          (free PLY splat export + SPZ + collider GLB download)
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://api.worldlabs.ai/marble/v1"


def _req(method, path, body=None, timeout=120):
    key = os.environ.get("BaseHackathonWorldLabsAPIKey")
    if not key:
        sys.exit("BaseHackathonWorldLabsAPIKey missing")
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"WLT-Api-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:2000]}")


def credits():
    return _req("GET", "/credits")["remaining_credits"]


def download(url, dest):
    urllib.request.urlretrieve(url, dest)
    return dest


def generate(name, images, out, model, text_prompt=None):
    out.mkdir(parents=True, exist_ok=True)
    items = []
    for p in images:
        b = base64.b64encode(Path(p).read_bytes()).decode()
        items.append({"content": {"source": "data_base64", "data_base64": b, "extension": Path(p).suffix[1:].lower()}})
    prompt = {"type": "multi-image", "multi_image_prompt": items, "reconstruct_images": True}
    if text_prompt:
        prompt["text_prompt"] = text_prompt
    body = {"display_name": name[:64], "model": model, "world_prompt": prompt,
            "permission": {"public": False}, "tags": ["base-hackathon", "a5"]}
    before = credits()
    t0 = time.time()
    op = _req("POST", "/worlds:generate", body, timeout=600)
    rec = {"name": name, "model": model, "images": [str(p) for p in images], "credits_before": before,
           "submitted_unix": t0, "operation": op}
    (out / "operation.json").write_text(json.dumps(rec, indent=1))
    print(json.dumps(op)[:500])


def poll(out, every=20):
    rec = json.loads((out / "operation.json").read_text())
    oid = rec["operation"]["operation_id"]
    while True:
        op = _req("GET", f"/operations/{oid}")
        if op.get("done"):
            break
        print("…", json.dumps(op.get("metadata"))[:200], flush=True)
        time.sleep(every)
    rec["result"] = op
    rec["done_unix"] = time.time()
    rec["runtime_s"] = rec["done_unix"] - rec["submitted_unix"]
    rec["credits_after"] = credits()
    (out / "operation.json").write_text(json.dumps(rec, indent=1))
    if op.get("error"):
        sys.exit(f"generation failed: {op['error']}")
    w = op["response"]
    (out / "world.json").write_text(json.dumps(w, indent=1))
    print("world", w["world_id"], "cost", json.dumps(op.get("cost")), "runtime_s", round(rec["runtime_s"]))
    print("semantics", json.dumps(w["assets"]["splats"].get("semantics_metadata")))


def export(out):
    w = json.loads((out / "world.json").read_text())
    wid = w["world_id"]
    before = credits()
    r = _req("POST", f"/worlds/{wid}:export", {"asset_type": "splats", "format": "ply", "resolution": "full_res"}, timeout=900)
    if not r.get("done"):
        oid = r["operation_id"]
        while not r.get("done"):
            time.sleep(10)
            r = _req("GET", f"/operations/{oid}")
    (out / "export.json").write_text(json.dumps(r, indent=1))
    url = r["response"]["url"]
    download(url, out / "splats_full_res.ply")
    a = w["assets"]
    for k, u in (a["splats"].get("spz_urls") or {}).items():
        if k == "full_res":
            download(u, out / f"splats_{k}.spz")
    if a.get("mesh") and a["mesh"].get("collider_mesh_url"):
        download(a["mesh"]["collider_mesh_url"], out / "collider.glb")
    im = a.get("imagery") or {}
    if im.get("pano_url"):
        download(im["pano_url"], out / "pano.jpg")
    if a.get("thumbnail_url"):
        download(a["thumbnail_url"], out / "thumbnail.jpg")
    print("export credits", before, "->", credits())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["credits", "generate", "poll", "export"])
    ap.add_argument("--images", nargs="*", default=[])
    ap.add_argument("--name")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--model", default="marble-1.1")
    ap.add_argument("--text")
    a = ap.parse_args()
    if a.cmd == "credits":
        print(credits())
    elif a.cmd == "generate":
        assert 1 <= len(a.images) <= 8
        generate(a.name, a.images, a.out, a.model, a.text)
    elif a.cmd == "poll":
        poll(a.out)
    else:
        export(a.out)
