"""MoGe-2 metric depth for a list of upright photos, one process, one model load.

Input: a JSON manifest, a list of {"image", "fx", "out"}: an upright RGB image, its focal length in
pixels (MoGe takes only the horizontal field of view) and the .npz to write. Output per image:
`depth` (float32 meters, z along the camera axis, NaN where MoGe's mask is off), at the image's
own size. Images should be at most about 640 px on the long side: MoGe-2 peaked at 4.07 GB on
24-megapixel photos and 2.97 GB at 640 x 480 on this Mac, and the worker must stay under 4 GB.

The checkpoint is pinned by revision; the caches live under $HOUSE_SCANNING_DATA/evals, shared
with the evals, so a machine that ran them does not download it again.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from pathlib import Path

DATA = Path(os.environ.get("HOUSE_SCANNING_DATA", Path.home() / "house-scanning-data"))
os.environ.setdefault("HF_HOME", str(DATA / "evals" / "hf-cache"))
os.environ.setdefault("TORCH_HOME", str(DATA / "evals" / "torch-cache"))

REPO = "Ruicheng/moge-2-vitl-normal"
REVISION = "cb0e8bbd6b1e243589717c78e750b1ba4c093acf"
RESOLUTION_LEVEL = 9
MPS_CAP_GB = 3.6


def write_depth(target: Path, depth) -> None:
    import numpy as np

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".npz", delete=False) as f:
            temporary = Path(f.name)
            np.savez(f, depth=depth.astype(np.float32))
        # A killed writer cannot leave a partially written archive at the reusable cache path.
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(manifest: Path) -> None:
    import cv2
    import numpy as np
    import torch
    from moge.model.v2 import MoGeModel

    device = (
        "cuda"
        if torch.cuda.is_available()
        else "mps"
        if torch.backends.mps.is_available()
        else "cpu"
    )
    if device == "mps":
        # A cap turns runaway growth into an error instead of swapping the shared machine.
        torch.mps.set_per_process_memory_fraction(
            MPS_CAP_GB * 1e9 / torch.mps.recommended_max_memory()
        )
    model = MoGeModel.from_pretrained(REPO, revision=REVISION).to(device).eval()
    for item in json.loads(manifest.read_text()):
        bgr = cv2.imread(item["image"], cv2.IMREAD_COLOR)
        if bgr is None:
            raise FileNotFoundError(item["image"])
        w = bgr.shape[1]
        rgb = torch.from_numpy(bgr[..., ::-1].copy()).to(device).permute(2, 0, 1).float() / 255
        fov_x = math.degrees(2 * math.atan(w / (2 * item["fx"])))
        with torch.no_grad():
            out = model.infer(rgb, resolution_level=RESOLUTION_LEVEL, fov_x=fov_x, use_fp16=True)
        depth = out["depth"].float().cpu().numpy()
        depth[~out["mask"].cpu().numpy().astype(bool)] = np.nan
        write_depth(Path(item["out"]), depth)
        print(f"{Path(item['image']).name}: median {np.nanmedian(depth):.2f} m", file=sys.stderr)
        del out, rgb
        if device == "mps":
            torch.mps.empty_cache()


if __name__ == "__main__":
    main(Path(sys.argv[1]))
