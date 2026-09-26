"""Annotation viewer: crop of a frame with a labelled grid in original pixel coordinates.

    python view.py SCENE STEM [x0 y0 x1 y1] [--step 50] [--out /tmp/a8view.jpg] [--pts x,y x,y ...]
"""

import argparse

import cv2
import numpy as np

from common import image_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scene")
    ap.add_argument("stem")
    ap.add_argument("box", nargs="*", type=int)
    ap.add_argument("--step", type=int, default=0)
    ap.add_argument("--width", type=int, default=1000)
    ap.add_argument("--out", default="/tmp/a8view.jpg")
    ap.add_argument("--pts", nargs="*", default=[])
    a = ap.parse_args()
    img = cv2.imread(str(image_path(a.scene, a.stem)))
    H, W = img.shape[:2]
    x0, y0, x1, y1 = a.box if a.box else (0, 0, W, H)
    crop = img[y0:y1, x0:x1]
    s = a.width / crop.shape[1]
    crop = cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)
    step = a.step or max(10, int(round((x1 - x0) / 10 / 10)) * 10)
    for x in range((x0 // step + 1) * step, x1, step):
        X = int((x - x0) * s)
        cv2.line(crop, (X, 0), (X, crop.shape[0]), (0, 255, 255), 1)
        cv2.putText(crop, str(x), (X + 2, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
    for y in range((y0 // step + 1) * step, y1, step):
        Y = int((y - y0) * s)
        cv2.line(crop, (0, Y), (crop.shape[1], Y), (0, 255, 255), 1)
        cv2.putText(crop, str(y), (2, Y - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
    for p in a.pts:
        u, v = map(float, p.split(","))
        cv2.circle(crop, (int((u - x0) * s), int((v - y0) * s)), 4, (0, 0, 255), 1)
    cv2.imwrite(a.out, crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
    print(a.out, crop.shape, "step", step)


if __name__ == "__main__":
    main()
