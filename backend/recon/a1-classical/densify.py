"""OpenMVS v2.3 (CPU) dense point cloud for one sparse run.

    python densify.py <scene> <run> [--res-level 1]

Workspace: WORK/<scene>/mvs/<run>/ with images/ (symlinks) and sparse/ (COLMAP text model of the
run). Output: scene_dense.ply there, plus <run>.json with timings.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import time

from common import OPENMVS_BIN, WORK, dump_json


def densify(scene: str, run: str, res_level: int = 1, threads: int = 4) -> dict:
    d = WORK / scene
    ws = d / "mvs" / f"{run}_r{res_level}"
    shutil.rmtree(ws, ignore_errors=True)
    (ws / "sparse").mkdir(parents=True)
    src = d / "sparse" / run
    for f in ("cameras.txt", "images.txt", "points3D.txt"):
        shutil.copy(src / f, ws / "sparse" / f)
    (ws / "images").symlink_to((d / "images").resolve())
    log = open(ws / "openmvs.log", "w")
    t0 = time.time()
    subprocess.run([str(OPENMVS_BIN / "InterfaceCOLMAP"), "-i", str(ws), "-o", str(ws / "scene.mvs"),
                    "--image-folder", str(ws / "images"), "-w", str(ws)],
                   check=True, stdout=log, stderr=subprocess.STDOUT)
    t1 = time.time()
    subprocess.run([str(OPENMVS_BIN / "DensifyPointCloud"), str(ws / "scene.mvs"), "-w", str(ws),
                    "--resolution-level", str(res_level), "--max-threads", str(threads)],
                   check=True, stdout=log, stderr=subprocess.STDOUT)
    t2 = time.time()
    for f in ws.glob("depth*.dmap"):
        f.unlink()
    rep = {"run": run, "res_level": res_level, "interface_s": t1 - t0, "densify_s": t2 - t1,
           "ply": str(ws / "scene_dense.ply")}
    dump_json(d / "mvs" / f"{run}_r{res_level}.json", rep)
    return rep


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("scene")
    ap.add_argument("run")
    ap.add_argument("--res-level", type=int, default=1)
    a = ap.parse_args()
    print(densify(a.scene, a.run, a.res_level))
