"""Server-side OCR engines, each returning PR #16's engine-agnostic result shape.

    {"lines": [{"text", "box": [x, y, w, h], "conf"}],   # box normalized, top-left origin
     "barcodes": [{"payload", "symbology", "box"}]}

so `meter_eval.locate.candidates` (vendored unchanged from PR #16) ranks every engine's output
the same way it ranked Apple Vision's.

    python engines.py --engine paddle --set meter [--ids m01,m02]

Raw output goes to raw/<set>/<engine>.jsonl next to this file (resumable).
"""

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("B2_DATA", "/tmp/b2data"))
# Runs append here; keep it off the synced store while writing (sync makes conflict copies).
RAW = Path(os.environ.get("B2_RAW", HERE / "raw"))
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


def barcodes(image: Image.Image) -> list[dict]:
    """zxing-cpp over the full image and a half-size copy, unique payloads kept."""
    import zxingcpp

    found: dict[str, dict] = {}
    w, h = image.size
    for scale in (1.0, 0.5):
        img = image if scale == 1.0 else image.resize((int(w * scale), int(h * scale)))
        for b in zxingcpp.read_barcodes(np.asarray(img.convert("L"))):
            if not b.text or b.text in found:
                continue
            p = b.position
            xs = [p.top_left.x, p.top_right.x, p.bottom_left.x, p.bottom_right.x]
            ys = [p.top_left.y, p.top_right.y, p.bottom_left.y, p.bottom_right.y]
            sw, sh = img.size
            found[b.text] = {
                "payload": b.text,
                "symbology": str(b.format).split(".")[-1],
                "box": [min(xs) / sw, min(ys) / sh, (max(xs) - min(xs)) / sw, (max(ys) - min(ys)) / sh],
            }
    return list(found.values())


class Tesseract:
    def __init__(self, psm: int) -> None:
        self.psm = psm

    def __call__(self, image: Image.Image) -> list[dict]:
        import pytesseract

        w, h = image.size
        d = pytesseract.image_to_data(
            image, config=f"--oem 1 --psm {self.psm}", output_type=pytesseract.Output.DICT
        )
        groups: dict[tuple, list[int]] = {}
        for i, text in enumerate(d["text"]):
            if text.strip():
                groups.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(i)
        lines = []
        for idx in groups.values():
            x0 = min(d["left"][i] for i in idx)
            y0 = min(d["top"][i] for i in idx)
            x1 = max(d["left"][i] + d["width"][i] for i in idx)
            y1 = max(d["top"][i] + d["height"][i] for i in idx)
            confs = [float(d["conf"][i]) for i in idx]
            lines.append(
                {
                    "text": " ".join(d["text"][i] for i in idx),
                    "box": [x0 / w, y0 / h, (x1 - x0) / w, (y1 - y0) / h],
                    "conf": round(sum(confs) / len(confs) / 100, 3),
                }
            )
        return lines


class Paddle:
    def __init__(self) -> None:
        from paddleocr import PaddleOCR

        self.ocr = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=True,
            enable_mkldnn=False,
        )

    def __call__(self, image: Image.Image) -> list[dict]:
        w, h = image.size
        r = self.ocr.predict(np.asarray(image)[:, :, ::-1].copy())[0]
        lines = []
        for text, score, box in zip(r["rec_texts"], r["rec_scores"], r["rec_boxes"]):
            x0, y0, x1, y1 = (float(v) for v in box)
            if text.strip():
                lines.append(
                    {
                        "text": text,
                        "box": [x0 / w, y0 / h, (x1 - x0) / w, (y1 - y0) / h],
                        "conf": round(float(score), 3),
                    }
                )
        return lines


class Doctr:
    def __init__(self) -> None:
        from doctr.models import ocr_predictor

        self.model = ocr_predictor(
            det_arch="db_resnet50",
            reco_arch="parseq",
            pretrained=True,
            assume_straight_pages=False,
            detect_orientation=False,
            export_as_straight_boxes=True,
        )

        self.straight = None

    def __call__(self, image: Image.Image) -> list[dict]:
        try:
            page = self.model([np.array(image)]).pages[0]
        except ValueError:
            # docTR 1.1's rotated-crop path hits a negative-stride tensor on some photos.
            if self.straight is None:
                from doctr.models import ocr_predictor

                self.straight = ocr_predictor(det_arch="db_resnet50", reco_arch="parseq", pretrained=True)
            page = self.straight([np.array(image)]).pages[0]
        lines = []
        for block in page.blocks:
            for line in block.lines:
                (x0, y0), (x1, y1) = line.geometry
                confs = [w.confidence for w in line.words]
                lines.append(
                    {
                        "text": " ".join(w.value for w in line.words),
                        "box": [float(x0), float(y0), float(x1 - x0), float(y1 - y0)],
                        "conf": round(float(sum(confs) / len(confs)), 3),
                    }
                )
        return lines


ENGINES = {
    "tesseract_psm11": lambda: Tesseract(11),
    "tesseract_psm3": lambda: Tesseract(3),
    "paddle": Paddle,
    "doctr": Doctr,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, choices=list(ENGINES))
    parser.add_argument("--set", required=True)
    parser.add_argument("--ids", default="")
    args = parser.parse_args()
    images = sorted((DATA / args.set / "images").glob("*.jpg"))
    if args.ids:
        wanted = set(args.ids.split(","))
        images = [p for p in images if p.stem in wanted]
    out = RAW / args.set / f"{args.engine}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        done = {json.loads(line)["id"] for line in out.read_text().splitlines() if line.strip()}
    engine = ENGINES[args.engine]()
    with out.open("a") as handle:
        for path in images:
            if path.stem in done:
                continue
            with Image.open(path) as im:
                image = im.convert("RGB")
            start = time.time()
            lines = engine(image)
            ocr_secs = time.time() - start
            codes = barcodes(image)
            record = {
                "id": path.stem,
                "engine": args.engine,
                "width": image.width,
                "height": image.height,
                "secs": round(ocr_secs, 2),
                "lines": lines,
                "barcodes": codes,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            print(f"{args.engine} {path.stem} {ocr_secs:.1f}s {len(lines)} lines", flush=True)


if __name__ == "__main__":
    main()
