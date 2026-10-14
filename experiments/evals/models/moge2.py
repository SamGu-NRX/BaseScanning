"""MoGe-2 (ViT-L, normal head): one image to a metric point map, depth and intrinsics.

`MoGeModel.infer` (moge/model/v2.py at the pinned commit) already returns metric z-depth at the
resolution of the image it is given: it multiplies the affine point map by the predicted
`metric_scale` (v2.py:279-283) and resizes the heads' outputs back to the input size bilinearly
(v2.py:170). Its only camera input is `fov_x`; with it, the focal is fixed and only the depth shift
is solved (v2.py:259-264). The principal point is always the image centre and pixels are square.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator

import numpy as np

from models.common import (
    ImageResult,
    RunInputs,
    depth_to_input,
    intrinsics_to_input,
    intrinsics_to_network,
    load_rgb,
    longest_side_resample,
    to_network_image,
)

REPO = "Ruicheng/moge-2-vitl-normal"
FILENAME = "model.pt"
REVISION = "cb0e8bbd6b1e243589717c78e750b1ba4c093acf"
SHA256 = "280741fd09bc3f403ccff9967784c2a391b52d2c0742ae3efdb21d9f90cc1a01"  # LFS sha256 Hugging Face lists for REVISION
CODE = {
    "repo": "https://github.com/microsoft/MoGe",
    "commit": "07444410f1e33f402353b99d6ccd26bd31e469e8",
}
LICENSE = "weights unverified; code MIT"
RESOLUTION_LEVEL = 9  # MoGe's default: 3600 ViT tokens, the top of its 1200-3600 range.


def load(device):
    from moge.model.v2 import MoGeModel

    return MoGeModel.from_pretrained(REPO, revision=REVISION).to(device).eval()


def run(model, inputs: RunInputs) -> Iterator[ImageResult]:
    import torch

    for i, path in enumerate(inputs.images):
        rgb = load_rgb(path)
        h, w = rgb.shape[:2]
        # MoGe predicts at a fixed token budget whatever the input size; --max-side only bounds the
        # size of the image handed to it (and the output upsampling), which matters for 24 MP photos.
        r = longest_side_resample(w, h, inputs.max_side or max(w, h))
        net_rgb = to_network_image(rgb, r)
        fov_x = None
        if inputs.intrinsics_mode == "known":
            fx_net = intrinsics_to_network(inputs.intrinsics[i], r)[0]
            # Continuous-pixel width over the focal: MoGe's fov_x spans the full image edge to edge.
            fov_x = math.degrees(2 * math.atan(r.net_w / (2 * fx_net)))

        start = time.perf_counter()
        tensor = torch.from_numpy(net_rgb).to(device=model.device).permute(2, 0, 1).float() / 255
        out = model.infer(
            tensor,
            resolution_level=RESOLUTION_LEVEL,
            fov_x=fov_x,
            use_fp16=not inputs.fp32,
        )
        depth = out["depth"].float().cpu().numpy()
        mask = out["mask"].cpu().numpy().astype(bool)
        k_norm = out["intrinsics"].float().cpu().numpy()
        if inputs.device.startswith("mps"):
            torch.mps.synchronize()
        seconds = time.perf_counter() - start

        # utils3d normalised intrinsics: image spans [0, 1] edge to edge, so OpenCV = n * size - 0.5.
        k_net = np.array(
            [
                k_norm[0, 0] * r.net_w,
                k_norm[1, 1] * r.net_h,
                k_norm[0, 2] * r.net_w - 0.5,
                k_norm[1, 2] * r.net_h - 0.5,
            ]
        )
        depth_in, valid_in = depth_to_input(depth, mask, r)
        aspect = r.net_w / r.net_h
        num_tokens = int(
            model.num_tokens_range[0]
            + RESOLUTION_LEVEL / 9 * (model.num_tokens_range[1] - model.num_tokens_range[0])
        )
        grid_h, grid_w = round((num_tokens / aspect) ** 0.5), round((num_tokens * aspect) ** 0.5)
        yield ImageResult(
            path=path,
            depth=depth_in,
            valid=valid_in,
            intrinsics=intrinsics_to_input(k_net, r),
            seconds=seconds,
            resample=r,
            network_wh=(grid_w * 14, grid_h * 14),
            extra={"fov_x_deg": fov_x, "num_tokens": num_tokens},
        )
        del out, tensor
        if inputs.device.startswith("mps"):
            torch.mps.empty_cache()
