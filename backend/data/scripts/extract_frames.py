#!/usr/bin/env python3
"""Extract frames from a local video into houses/<id>/frames/ as t<seconds>.jpg.

  extract_frames.py preview VIDEO OUT.jpg [every_s=4]        dense timestamped contact sheet
  extract_frames.py extract VIDEO OUT_DIR FPS START-END [START-END ...] [--max-edge 2048]

Times are seconds or m:ss. Frame names encode the source timestamp, e.g. t0083.50.jpg.
"""
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw


def secs(t):
    if ":" in t:
        m, s = t.split(":")
        return int(m) * 60 + float(s)
    return float(t)


def ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def preview(video, out, every):
    with tempfile.TemporaryDirectory() as td:
        ff("-i", video, "-vf", f"fps=1/{every},scale=320:-2", "-q:v", "4", os.path.join(td, "%05d.jpg"))
        files = sorted(os.listdir(td))
        first = Image.open(os.path.join(td, files[0]))
        tw, th = first.size
        cols = 8
        rows = (len(files) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tw, rows * th), "black")
        dr = ImageDraw.Draw(sheet)
        for k, f in enumerate(files):
            r, c = divmod(k, cols)
            sheet.paste(Image.open(os.path.join(td, f)), (c * tw, r * th))
            t = k * every
            dr.rectangle((c * tw, r * th, c * tw + 40, r * th + 12), fill="black")
            dr.text((c * tw + 2, r * th), f"{int(t)//60}:{int(t)%60:02d}", fill="yellow")
        sheet.save(out, quality=80)
        print(out, len(files), "tiles")


def extract(video, out_dir, fps, segments, max_edge):
    os.makedirs(out_dir, exist_ok=True)
    total = 0
    for seg in segments:
        a, b = (secs(x) for x in seg.split("-"))
        with tempfile.TemporaryDirectory() as td:
            scale = f"scale='if(gt(iw,ih),min({max_edge},iw),-2)':'if(gt(iw,ih),-2,min({max_edge},ih))'"
            ff("-ss", f"{a}", "-to", f"{b}", "-i", video, "-vf", f"fps={fps},{scale}", "-q:v", "2",
               os.path.join(td, "%05d.jpg"))
            for k, f in enumerate(sorted(os.listdir(td))):
                t = a + k / fps
                shutil.move(os.path.join(td, f), os.path.join(out_dir, f"t{t:07.2f}.jpg"))
                total += 1
    print(f"{total} frames -> {out_dir}")


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "preview":
        preview(sys.argv[2], sys.argv[3], float(sys.argv[4]) if len(sys.argv) > 4 else 4)
    elif mode == "extract":
        args = sys.argv[2:]
        me = 2048
        if "--max-edge" in args:
            i = args.index("--max-edge")
            me = int(args[i + 1])
            del args[i:i + 2]
        extract(args[0], args[1], float(args[2]), args[3:], me)
    else:
        sys.exit(__doc__)
