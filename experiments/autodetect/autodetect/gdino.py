"""Grounding DINO tiny (IDEA-Research/grounding-dino-tiny, Apache-2.0) zero-shot detection through
the ONNX export in onnx-community/grounding-dino-tiny-ONNX, on onnxruntime's CPU provider with the
fp16 graph upcast to fp32 in memory (owl.upcast_fp16).

The export takes a fixed 800 x 800 image. Each image is resized so its long side is 800, padded
bottom and right, and pixel_mask marks the real pixels; predicted boxes are (cx, cy, w, h)
relative to the real pixels, as in DETR-style models. The caption joins the phrases in config,
each ending in " ."; a box's score for a phrase is the highest token probability within that
phrase's tokens, and the box takes its best phrase.

Not scored in this run: one electro photo took 15 to 31 s on this shared Mac at a load average
near 130, about 5 hours for the 836 images. The torch route used for OWLv2 does not carry over:
294 of the 1,046 parameters of transformers 5.17's GroundingDinoForObjectDetection have no
same-named weight in the export (the Swin backbone is named differently).

Usage: python -m autodetect.gdino [set ...]    # default: every set, then electro
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import config
from .nms import postprocess
from .owl import upcast_fp16
from .paths import WEIGHTS
from .sets import SETS, ground_truth, image_path, save_preds

DIR = WEIGHTS / "gdino"  # ONNX weights and tokenizer.json, outside git
MODEL = DIR / "model_fp16.onnx"
URL = "https://huggingface.co/onnx-community/grounding-dino-tiny-ONNX/resolve/main/onnx/model_fp16.onnx"  # fetch MODEL from here
SIZE = 800  # fixed graph input; the long side resizes to this, then pads to 800 x 800
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)  # ImageNet channel means
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)  # ImageNet channel stds


class GDino:
    """Zero-shot Grounding DINO detector over one fixed caption.

    __init__ loads the ONNX graph and tokenizer and builds the caption and its token spans
    once; every image shares them. detect() runs one image through preprocess, the session
    and nms.postprocess.
    """

    def __init__(self) -> None:
        """Load the graph and tokenizer, then build the caption and each phrase's token spans.

        The caption joins config.GDINO_PHRASES values, each ending in " .", and is encoded
        once for every image. Raises FileNotFoundError when MODEL is missing and ValueError
        when a phrase ends up with no tokens.
        """
        import onnxruntime as ort
        from tokenizers import Tokenizer

        if not MODEL.exists():
            raise FileNotFoundError(f"{MODEL} missing; download it from {URL}")
        self.session = ort.InferenceSession(upcast_fp16(MODEL), providers=["CPUExecutionProvider"])
        tok = Tokenizer.from_file(str(DIR / "tokenizer.json"))
        self.labels = list(config.GDINO_PHRASES)
        caption = " ".join(f"{p} ." for p in config.GDINO_PHRASES.values())
        enc = tok.encode(caption)
        self.ids = np.array([enc.ids], dtype=np.int64)
        self.mask = np.ones_like(self.ids)
        self.types = np.zeros_like(self.ids)
        # token positions of each phrase, from character offsets in the caption
        spans, pos = [], 0
        for p in config.GDINO_PHRASES.values():
            start = caption.index(p, pos)
            spans.append((start, start + len(p)))
            pos = start + len(p)
        self.token_sets = []
        for a, b in spans:
            toks = [k for k, (s, e) in enumerate(enc.offsets) if e > s and s >= a and e <= b]
            if not toks:
                raise ValueError(f"phrase at {a}:{b} has no tokens")
            self.token_sets.append(toks)

    @staticmethod
    def preprocess(im: Image.Image) -> tuple[np.ndarray, np.ndarray]:
        """Resize so the long side is SIZE, pad bottom and right to SIZE x SIZE, normalize.

        Returns (pixel_values, pixel_mask): pixel_values is float32, shape (1, 3, SIZE, SIZE),
        with MEAN and STD applied; pixel_mask is int64, shape (1, SIZE, SIZE), 1 on real
        pixels and 0 on padding.

        >>> from PIL import Image
        >>> from autodetect.gdino import GDino
        >>> im = Image.new("RGB", (200, 100), (127, 127, 127))
        >>> pixels, mask = GDino.preprocess(im)
        >>> pixels.shape
        (1, 3, 800, 800)
        >>> mask.shape, mask.dtype
        ((1, 800, 800), dtype('int64'))
        >>> int(mask.sum())  # an 800 x 400 strip of real pixels
        320000
        >>> round(float(pixels[0, 0, 0, 0]), 4)  # (127 / 255 - MEAN[0]) / STD[0]
        0.0569
        >>> float(pixels[0, 0, 700, 0])  # padding is exactly 0
        0.0
        """
        w, h = im.size
        scale = SIZE / max(w, h)
        nw, nh = round(w * scale), round(h * scale)
        x = np.zeros((SIZE, SIZE, 3), dtype=np.float32)
        x[:nh, :nw] = (np.asarray(im.resize((nw, nh), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0 - MEAN) / STD
        mask = np.zeros((1, SIZE, SIZE), dtype=np.int64)
        mask[0, :nh, :nw] = 1
        return x.transpose(2, 0, 1)[None], mask

    def detect(self, path: Path) -> tuple[list[dict], float, float]:
        """Detections for one image; preprocess ms; inference ms.

        A box's score for a phrase is the highest sigmoid logit over that phrase's tokens,
        and the box takes its best phrase. Boxes come out in [x0, y0, x1, y1] normalized to
        the real pixels: the graph unpads them through pixel_mask, so no rescale runs here,
        unlike owl.detect. Returns the nms.postprocess dicts: {"label", "score", "box"}.
        """
        im = Image.open(path).convert("RGB")
        t0 = time.perf_counter()
        pixels, pmask = self.preprocess(im)
        t1 = time.perf_counter()
        logits, boxes = self.session.run(
            ["logits", "pred_boxes"],
            {"pixel_values": pixels, "input_ids": self.ids, "token_type_ids": self.types, "attention_mask": self.mask, "pixel_mask": pmask},
        )
        t2 = time.perf_counter()
        prob = 1 / (1 + np.exp(-logits[0]))  # (900, 256)
        per_phrase = np.stack([prob[:, toks].max(1) for toks in self.token_sets], 1)
        label = per_phrase.argmax(1)
        score = per_phrase.max(1)
        cx, cy, bw, bh = boxes[0].T
        xyxy = np.clip(np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], 1), 0, 1)
        return postprocess(xyxy, score, [self.labels[i] for i in label]), 1000 * (t1 - t0), 1000 * (t2 - t1)


def meta() -> dict:
    """Description of this candidate for save_preds: model, phrases, license, size, runtime.

    Reads MODEL's size from disk, so the weights file must exist.
    """
    return {
        "model": "Grounding DINO tiny, ONNX fp16 upcast to fp32",
        "phrases": config.GDINO_PHRASES,
        "license": "Apache-2.0",
        "size": f"{MODEL.stat().st_size / 1e6:.0f} MB (fp16 ONNX)",
        "runtime": "onnxruntime CPU, Mac, fp32",
        "input": "800 x 800 (long side 800, padded)",
        "command": "uv run python -m autodetect.gdino",
    }


def run(model: GDino, name: str) -> None:
    """Detect every image of set name in sorted id order and save PREDS/gdino/<name>.json.

    Prints progress to stderr every 50 images.
    """
    images = {}
    for k, i in enumerate(sorted(ground_truth(name))):
        dets, pre_ms, ms = model.detect(image_path(name, i))
        images[i] = {"elapsed_ms": ms, "preprocess_ms": pre_ms, "dets": dets}
        if k % 50 == 0:
            print(f"{name}: {k} images, last {ms:.0f} ms", file=sys.stderr)
    save_preds("gdino", name, meta(), images)


if __name__ == "__main__":
    g = GDino()
    for s in sys.argv[1:] or (*SETS, "electro"):
        run(g, s)
