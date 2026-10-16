"""Sensitivity of the depth-free triangulation lift to its association parameters (clean poses).

Tuned on the one scene it is scored on, so the best row is optimistic; the pre-set default is
max_gap=6, epi_px=25, vote_min=3, min_angle_deg=0.

usage: python sweep_triangulate.py ../results/facade76/gdino-tiny.jsonl 0.35
"""

import itertools
import json
import sys

from common import B1
from eth3d import load_poses
from lift3d import lift_triangulate, load_dets, score, wall_segments


def main():
    path, thr = sys.argv[1], float(sys.argv[2])
    poses = load_poses()
    names = sorted(poses)
    gt = json.loads((B1 / "results/eth3d_gt.json").read_text())
    walls = wall_segments()
    dets = load_dets(path, thr)
    rows = []
    for gap, epi, vote, ang in itertools.product([6, 12], [15, 25], [3, 5], [0.0, 3.0, 6.0]):
        s = score(lift_triangulate(dets, poses, names, max_gap=gap, epi_px=epi, vote_min=vote, min_angle_deg=ang), gt, walls)
        rows.append({"max_gap": gap, "epi_px": epi, "vote_min": vote, "min_angle_deg": ang,
                     **{k: v for k, v in s.items() if k != "roi_objects"}})
        print(rows[-1])
    out = B1 / f"results/sweep_triangulate_{path.split('/')[-1].split('.')[0]}.json"
    out.write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
