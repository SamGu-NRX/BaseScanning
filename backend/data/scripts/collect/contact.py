#!/usr/bin/env python3
"""Contact sheet of N evenly spaced frames, each labelled with its filename.

Usage: contact.py FRAMES_DIR OUT.jpg [N=24] [COLS=6] [TILE_W=320]
"""
import os
import sys

from PIL import Image, ImageDraw

d, out = sys.argv[1], sys.argv[2]
n = int(sys.argv[3]) if len(sys.argv) > 3 else 24
cols = int(sys.argv[4]) if len(sys.argv) > 4 else 6
tw = int(sys.argv[5]) if len(sys.argv) > 5 else 320
files = sorted(f for f in os.listdir(d) if f.lower().endswith((".jpg", ".jpeg", ".png")))
if not files:
    sys.exit(f"no frames in {d}")
step = max(1, len(files) / n)
pick = [files[min(len(files) - 1, int(i * step))] for i in range(min(n, len(files)))]
first = Image.open(os.path.join(d, pick[0]))
th = round(tw * first.height / first.width)
rows = (len(pick) + cols - 1) // cols
sheet = Image.new("RGB", (cols * tw, rows * th), "black")
dr = ImageDraw.Draw(sheet)
for k, f in enumerate(pick):
    im = Image.open(os.path.join(d, f)).convert("RGB")
    im.thumbnail((tw, th))
    r, c = divmod(k, cols)
    sheet.paste(im, (c * tw, r * th))
    dr.rectangle((c * tw, r * th, c * tw + 7 * len(f) + 4, r * th + 12), fill="black")
    dr.text((c * tw + 2, r * th), f, fill="yellow")
sheet.save(out, quality=82)
print(out, len(pick), "of", len(files))
