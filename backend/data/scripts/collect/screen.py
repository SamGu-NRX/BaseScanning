#!/usr/bin/env python3
"""Build a labelled contact sheet per YouTube video from its storyboard images.

Usage: screen.py OUTDIR ID [ID ...]
Writes OUTDIR/<id>.json (yt-dlp info, trimmed) and OUTDIR/<id>.jpg (contact sheet).
"""
import concurrent.futures as cf
import io
import json
import os
import subprocess
import sys

import requests
from PIL import Image, ImageDraw

YTP = os.path.join(os.path.dirname(__file__), "ytp.sh")
N_THUMBS = 30
COLS = 6


def info(vid):
    r = subprocess.run([YTP, "-j", "--skip-download", f"https://www.youtube.com/watch?v={vid}"],
                       capture_output=True, text=True, timeout=600)
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError(f"{vid}: no info ({r.stdout[-300:]} {r.stderr[-300:]})")


def sheet(vid, outdir):
    d = info(vid)
    keep = {k: d.get(k) for k in ("id", "title", "channel", "channel_url", "uploader", "upload_date",
                                  "duration", "license", "description", "width", "height", "fps",
                                  "view_count", "webpage_url", "chapters", "tags")}
    keep["formats"] = [{k: f.get(k) for k in ("format_id", "ext", "width", "height", "fps", "vcodec", "filesize", "filesize_approx")}
                       for f in d.get("formats", []) if f.get("vcodec") not in (None, "none")]
    sbs = [f for f in d.get("formats", []) if str(f.get("format_id", "")).startswith("sb")]
    json.dump(keep, open(os.path.join(outdir, f"{vid}.json"), "w"), indent=1)
    if not sbs:
        return vid, "no storyboard"
    sb = max(sbs, key=lambda f: (f.get("width") or 0))
    rows, cols = sb.get("rows") or 1, sb.get("columns") or 1
    per = rows * cols
    frags = sb.get("fragments") or []
    thumbs = []  # (t_seconds, image)
    t0 = 0.0
    tile_dur = None
    for fr in frags:
        img = Image.open(io.BytesIO(requests.get(fr["url"], timeout=30).content)).convert("RGB")
        fdur = fr.get("duration") or 0
        tw, th = img.width // cols, img.height // rows
        tile_dur = fdur / per if per else 0
        for i in range(per):
            r_, c_ = divmod(i, cols)
            tile = img.crop((c_ * tw, r_ * th, (c_ + 1) * tw, (r_ + 1) * th))
            if tile.getbbox() is None:
                continue
            thumbs.append((t0 + i * tile_dur, tile))
        t0 += fdur
    dur = d.get("duration") or t0
    thumbs = [t for t in thumbs if t[0] <= dur]
    if not thumbs:
        return vid, "empty storyboard"
    step = max(1, len(thumbs) // N_THUMBS)
    pick = thumbs[::step][:N_THUMBS]
    tw, th = pick[0][1].size
    scale = max(1, 240 // tw)
    tw, th = tw * scale, th * scale
    rows_out = (len(pick) + COLS - 1) // COLS
    out = Image.new("RGB", (COLS * tw, rows_out * th + 24), "black")
    dr = ImageDraw.Draw(out)
    dr.text((4, 4), f"{vid} | {d.get('title','')[:90]} | {dur}s", fill="yellow")
    for k, (t, im) in enumerate(pick):
        r_, c_ = divmod(k, COLS)
        out.paste(im.resize((tw, th)), (c_ * tw, 24 + r_ * th))
        dr.rectangle((c_ * tw, 24 + r_ * th, c_ * tw + 44, 24 + r_ * th + 12), fill="black")
        dr.text((c_ * tw + 2, 24 + r_ * th), f"{int(t)//60}:{int(t)%60:02d}", fill="white")
    out.save(os.path.join(outdir, f"{vid}.jpg"), quality=85)
    return vid, f"ok {len(pick)} thumbs, sb {sb.get('width')}x{sb.get('height')}"


def main():
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    ids = sys.argv[2:]
    with cf.ThreadPoolExecutor(4) as ex:
        futs = {ex.submit(sheet, v, outdir): v for v in ids}
        for f in cf.as_completed(futs):
            try:
                print(*f.result(), flush=True)
            except Exception as e:  # noqa: BLE001
                print(futs[f], "ERR", str(e)[:200], flush=True)


if __name__ == "__main__":
    main()
