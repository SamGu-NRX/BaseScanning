"""CPU gate features for every image in images.json.

    python gate_features.py quality|clip|siglip|owlv2|dbnet [--threads 2] [--sets walk,meter]

Writes $B3_FEAT/<model>.jsonl (resumable; default /tmp/b3/feat, copied into results/feat/ when
done because store sync makes conflict copies of files appended while syncing). clip and siglip
also write <model>_emb.npz (L2-normalised image embeddings) so prompts can be changed offline.
All images are loaded upright with the long side capped at 1024 px (B1's detector setting).
"""

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
FEAT = Path(os.environ.get("B3_FEAT", "/tmp/b3/feat"))
MAX_SIDE = 1024

OWL_QUERIES = [
    "electric meter", "meter nameplate with printed text", "electrical breaker panel", "electrical panel label",
    "circuit breakers", "gas meter", "air conditioner condenser unit", "water heater", "downspout",
    "outdoor water faucet", "vent on a wall", "utility box on a wall", "window", "door", "swimming pool",
]


def load(path, max_side=MAX_SIDE):
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    s = min(1.0, max_side / max(img.size))
    if s < 1.0:
        img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    return img


def quality(img):
    import cv2

    g = np.asarray(img.convert("L"), dtype=np.float32)
    lap = cv2.Laplacian(g, cv2.CV_32F)
    h, w = g.shape
    centre = lap[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
    return {"lap_var": float(lap.var()), "lap_var_centre": float(centre.var()), "brightness": float(g.mean()),
            "w": img.width, "h": img.height}


class Clip:
    def __init__(self, arch="ViT-B-32", tag="laion2b_s34b_b79k"):
        import open_clip
        import torch

        self.torch = torch
        self.model, _, self.pre = open_clip.create_model_and_transforms(arch, pretrained=tag)
        self.model.eval()

    def __call__(self, img):
        with self.torch.no_grad():
            e = self.model.encode_image(self.pre(img).unsqueeze(0))
            e = e / e.norm(dim=-1, keepdim=True)
        return e[0].numpy()


class Siglip:
    def __init__(self, name="google/siglip2-base-patch16-256"):
        import torch
        from transformers import AutoModel, AutoProcessor

        self.torch = torch
        self.model = AutoModel.from_pretrained(name).eval()
        self.proc = AutoProcessor.from_pretrained(name)

    def __call__(self, img):
        with self.torch.no_grad():
            inp = self.proc(images=img, return_tensors="pt")
            e = self.model.get_image_features(**inp)
            if not isinstance(e, self.torch.Tensor):
                e = e.pooler_output
            e = e / e.norm(dim=-1, keepdim=True)
        return e[0].numpy()


class Owl:
    def __init__(self, name="google/owlv2-base-patch16-ensemble"):
        import torch
        from transformers import Owlv2ForObjectDetection, Owlv2Processor

        self.torch = torch
        self.proc = Owlv2Processor.from_pretrained(name)
        self.model = Owlv2ForObjectDetection.from_pretrained(name).eval()

    def __call__(self, img):
        with self.torch.no_grad():
            inp = self.proc(text=[OWL_QUERIES], images=img, return_tensors="pt")
            out = self.model(**inp)
        # OWLv2 pads to a square; boxes are relative to the padded square.
        side = max(img.size)
        post = getattr(self.proc, "post_process_object_detection", None) or self.proc.post_process_grounded_object_detection
        res = post(out, threshold=0.05, target_sizes=[(side, side)])[0]
        dets = {}
        for score, label, box in zip(res["scores"].tolist(), res["labels"].tolist(), res["boxes"].tolist()):
            x0, y0, x1, y1 = box
            nb = [max(0, x0 / img.width), max(0, y0 / img.height), min(1, x1 / img.width), min(1, y1 / img.height)]
            dets.setdefault(OWL_QUERIES[label], []).append([round(score, 4)] + [round(v, 4) for v in nb])
        for q in dets:
            dets[q] = sorted(dets[q], reverse=True)[:5]
        return {"dets": dets, "max": {q: (dets[q][0][0] if q in dets else 0.0) for q in OWL_QUERIES}}


class DBNet:
    def __init__(self):
        from doctr.models import detection_predictor

        self.det = detection_predictor("db_resnet50", pretrained=True, assume_straight_pages=True)

    def __call__(self, img):
        out = self.det([np.asarray(img)])[0]
        words = out["words"] if isinstance(out, dict) else next(iter(out.values()))
        boxes = [[round(float(v), 4) for v in w[:5]] for w in np.asarray(words)]
        area = sum((b[2] - b[0]) * (b[3] - b[1]) for b in boxes)
        return {"n": len(boxes), "area": round(area, 5), "boxes": boxes[:200]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("model")
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--sets", default="")
    p.add_argument("--ids-file", default="", help="subset (OWLv2 and DBNet run on every 3rd walkaround frame; see subset.txt)")
    args = p.parse_args()
    import torch

    torch.set_num_threads(args.threads)
    images = json.loads((HERE / "images.json").read_text())
    if args.sets:
        keep = set(args.sets.split(","))
        images = [r for r in images if r["set"] in keep]
    if args.ids_file:
        keep = set(Path(args.ids_file).read_text().split())
        images = [r for r in images if r["id"] in keep]
    FEAT.mkdir(parents=True, exist_ok=True)
    out_path = FEAT / f"{args.model}.jsonl"
    done = set()
    if out_path.exists():
        done = {json.loads(line)["id"] for line in out_path.read_text().splitlines() if line.strip()}
    emb_path = FEAT / f"{args.model}_emb.npz"
    embs = dict(np.load(emb_path)) if emb_path.exists() else {}
    fn = {"quality": lambda: quality, "clip": Clip, "clipL": lambda: Clip("ViT-L-14", "datacomp_xl_s13b_b90k"),
          "siglip": Siglip, "owlv2": Owl, "dbnet": DBNet}[args.model]()
    todo = [r for r in images if r["id"] not in done]
    print(f"{args.model}: {len(todo)} to do", flush=True)
    with out_path.open("a") as h:
        for k, r in enumerate(todo):
            img = load(r["path"])
            t = time.time()
            res = fn(img)
            secs = round(time.time() - t, 3)
            if isinstance(res, np.ndarray):
                embs[r["id"].replace("/", "__")] = res.astype(np.float16)
                res = {}
                if k % 50 == 0 or k == len(todo) - 1:
                    np.savez(emb_path, **embs)
            h.write(json.dumps({"id": r["id"], "secs": secs, **res}) + "\n")
            h.flush()
            if k % 25 == 0:
                print(args.model, k, r["id"], secs, flush=True)
    if embs:
        np.savez(emb_path, **embs)


if __name__ == "__main__":
    main()
