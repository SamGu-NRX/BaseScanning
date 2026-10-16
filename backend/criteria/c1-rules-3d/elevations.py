"""Render each wall of a scene3d as a laser-scan elevation (u along the wall, height up), coloured by
offset from the wall line, with a 1 ft grid, so openings can be read off in wall coordinates.

    python elevations.py annotations/facade.json /home/ubuntu/c1out/scenes/facade-M1.json
"""

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, "/home/ubuntu/work/verif/verification/scenes/eth3d-facade")
import build_scene as pr13  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scantools  # noqa: E402

ETH3D = Path("/home/ubuntu/eth3d")


def main():
    ann = json.loads(Path(sys.argv[1]).read_text())
    sc = json.loads(Path(sys.argv[2]).read_text())
    out = Path(__file__).resolve().parent / "outputs" / "views"
    rng = np.random.default_rng(7)
    data = ETH3D / ann["dataset"]
    if ann["dataset"] == "facade":
        P = pr13.load_scan_crop(data / "dslr_scan_eval", ann["roi_scan_xy_m"])
    else:
        P = scantools.load_scan(ann["dataset"], ann["roi_scan_xy_m"], voxel_m=0.02)
    frame = pr13.Frame(np.array(sc["meta"]["frame_A"]), np.array(sc["meta"]["frame_origin_scan_m"]))
    Ps = frame.pts(P)
    plan, h = Ps[:, [0, 2]], Ps[:, 1]
    res = 0.1
    for w in sc["walls"]:
        a, b = np.array(w["a"]), np.array(w["b"])
        L = np.linalg.norm(b - a)
        al = (b - a) / L
        n = np.array(w["outward"])
        lat, off = (plan - a) @ al, (plan - a) @ n
        hmax = min(24.0, w["top_y"])
        m = (lat > -4) & (lat < L + 4) & (np.abs(off) < 2.5) & (h > -1) & (h < hmax)
        W, H = int((L + 8) / res) + 2, int((hmax + 1) / res) + 2
        best = np.full((H, W), np.inf)
        iu = ((lat[m] + 4) / res).astype(int)
        iv = (H - 1 - (h[m] + 1) / res).astype(int)
        o = off[m]
        # keep the point nearest the wall face per pixel
        np.minimum.at(best, (iv, iu), np.abs(o) + 10 * (o < 0))
        img = np.zeros((H, W, 3), np.uint8)
        have = np.isfinite(best)
        v = np.where(best >= 10, -(best - 10), best)  # signed offset again
        t = np.clip(v / 1.0, -1, 1)
        img[..., 0] = np.where(have, np.where(t > 0, 255 * t, 0) + 60 * (np.abs(t) < 0.15), 0)
        img[..., 1] = np.where(have, 200 * (np.abs(t) < 0.15) + 40, 0)
        img[..., 2] = np.where(have, np.where(t < 0, -255 * t, 0) + 60 * (np.abs(t) < 0.15), 0)
        im = Image.fromarray(img.astype(np.uint8)).resize((W * 2, H * 2), Image.NEAREST)
        d = ImageDraw.Draw(im)
        for u in range(-4, int(L) + 5):
            x = int((u + 4) / res) * 2
            d.line([(x, H * 2 - 8), (x, H * 2)], fill=(255, 255, 0))
            if u % 5 == 0:
                d.line([(x, 0), (x, H * 2)], fill=(90, 90, 0))
                d.text((x + 2, H * 2 - 20), f"u{u}", fill=(255, 255, 0))
        for y in range(0, int(hmax) + 1, 1):
            yy = int(H - 1 - (y + 1) / res) * 2
            d.line([(0, yy), (10, yy)], fill=(0, 255, 255))
            if y % 5 == 0:
                d.line([(0, yy), (W * 2, yy)], fill=(0, 90, 90))
                d.text((12, yy - 12), f"{y}ft", fill=(0, 255, 255))
        for x in (4, 4 + L):
            d.line([(int(x / res) * 2, 0), (int(x / res) * 2, H * 2)], fill=(255, 0, 255), width=2)
        for ob in sc["objects"]:
            if ob.get("wall_id") == w["id"] and ob.get("span_u"):
                u0, u1 = ob["span_u"]
                d.rectangle([int((u0 + 4) / res) * 2, int(H - 1 - (ob["y1"] + 1) / res) * 2,
                             int((u1 + 4) / res) * 2, int(H - 1 - (ob["y0"] + 1) / res) * 2],
                            outline=(255, 255, 255), width=2)
        im.save(out / f"{ann['scene']}_elev_{w['id']}.png")
        print(w["id"], L, im.size)


if __name__ == "__main__":
    main()
