"""Run an open-vocabulary detector over the evaluation images.

Writes one JSONL row per image to results/dets/<model>.jsonl with boxes normalised to [0, 1]
(x0, y0, x1, y1) in the upright original image, a low storage threshold so thresholds can be
swept later, and the wall-clock seconds per image on this CPU.

usage: python run_detectors.py <model> [--images eval_images.json] [--limit N]
models: gdino-tiny gdino-base mmgdino-tiny owlv2-base florence2-base florence2-large sam3
"""

import argparse
import difflib
import time
from pathlib import Path

import torch

from common import B1, CLASSES, PROMPT_TO_CLASS, load_image, read_image_list, write_jsonl, read_jsonl

torch.set_num_threads(4)
STORE_THRESHOLD = 0.08


def to_class(text):
    text = text.strip().lower()
    if text in PROMPT_TO_CLASS:
        return PROMPT_TO_CLASS[text]
    best = difflib.get_close_matches(text, list(PROMPT_TO_CLASS), n=1, cutoff=0.0)
    return PROMPT_TO_CLASS[best[0]] if best else None


def norm_box(b, w, h):
    x0, y0, x1, y1 = [float(v) for v in b]
    return [max(0.0, x0 / w), max(0.0, y0 / h), min(1.0, x1 / w), min(1.0, y1 / h)]


class GDino:
    """Grounding DINO family. Class score = mean token probability over that class's phrase span.

    The stock post-processing merges tokens of overlapping phrases ("electric meter" inside
    "electric meter socket box") into one label, so class scores are read from the logits here.
    """

    def __init__(self, ckpt):
        from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection

        self.proc = AutoProcessor.from_pretrained(ckpt)
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(ckpt).eval()
        self.text = ". ".join(CLASSES.values()) + "."
        enc = self.proc.tokenizer(self.text, return_offsets_mapping=True)
        self.spans = {}
        pos = 0
        for cls, prompt in CLASSES.items():
            start = self.text.index(prompt, pos)
            end = start + len(prompt)
            pos = end
            self.spans[cls] = [i for i, (a, b) in enumerate(enc["offset_mapping"]) if b > a and a >= start and b <= end]

    @torch.inference_mode()
    def __call__(self, img):
        inputs = self.proc(images=img, text=self.text, return_tensors="pt")
        out = self.model(**inputs)
        prob = out.logits[0].sigmoid()
        cls_scores = torch.stack([prob[:, idx].mean(dim=1) for idx in self.spans.values()], dim=1)
        score, arg = cls_scores.max(dim=1)
        cx, cy, bw, bh = out.pred_boxes[0].unbind(-1)
        boxes = torch.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], dim=-1).clamp(0, 1)
        names = list(self.spans)
        keep = (score >= STORE_THRESHOLD).nonzero().flatten()
        return [
            {"label": names[int(arg[q])], "score": float(score[q]), "box": [float(v) for v in boxes[q]]}
            for q in keep
        ]


class Owl:
    def __init__(self, ckpt):
        from transformers import Owlv2Processor, Owlv2ForObjectDetection

        self.proc = Owlv2Processor.from_pretrained(ckpt)
        self.model = Owlv2ForObjectDetection.from_pretrained(ckpt).eval()
        self.queries = list(CLASSES.values())

    @torch.inference_mode()
    def __call__(self, img):
        inputs = self.proc(text=[self.queries], images=img, return_tensors="pt")
        out = self.model(**inputs)
        side = max(img.width, img.height)  # OWLv2 pads to a square; boxes are in padded pixels
        res = self.proc.post_process_grounded_object_detection(
            out, threshold=STORE_THRESHOLD / 2, target_sizes=[(side, side)]
        )[0]
        dets = []
        for box, score, lab in zip(res["boxes"], res["scores"], res["labels"]):
            cls = PROMPT_TO_CLASS[self.queries[int(lab)]]
            dets.append({"label": cls, "score": float(score), "box": norm_box(box, img.width, img.height)})
        return dets


class Florence:
    def __init__(self, ckpt):
        from transformers import AutoProcessor, Florence2ForConditionalGeneration

        self.proc = AutoProcessor.from_pretrained(ckpt)
        self.model = Florence2ForConditionalGeneration.from_pretrained(ckpt, dtype=torch.float32).eval()

    @torch.inference_mode()
    def run(self, img, task, text=""):
        inputs = self.proc(text=task + text, images=img, return_tensors="pt")
        ids = self.model.generate(**inputs, max_new_tokens=256, num_beams=3, do_sample=False)
        raw = self.proc.batch_decode(ids, skip_special_tokens=False)[0]
        return self.proc.post_process_generation(raw, task=task, image_size=(img.width, img.height))[task]

    def __call__(self, img):
        dets = []
        for cls, prompt in CLASSES.items():
            r = self.run(img, "<OPEN_VOCABULARY_DETECTION>", prompt)
            for box in r.get("bboxes", []):
                # Florence-2 gives no scores; 1.0 marks "returned".
                dets.append({"label": cls, "score": 1.0, "box": norm_box(box, img.width, img.height)})
        return dets


class Sam3:
    def __init__(self, ckpt):
        from transformers import Sam3Processor, Sam3Model

        self.proc = Sam3Processor.from_pretrained(ckpt)
        self.model = Sam3Model.from_pretrained(ckpt).eval()

    @torch.inference_mode()
    def __call__(self, img):
        dets = []
        for cls, prompt in CLASSES.items():
            inputs = self.proc(images=img, text=prompt, return_tensors="pt")
            out = self.model(**inputs)
            res = self.proc.post_process_instance_segmentation(
                out, threshold=STORE_THRESHOLD, mask_threshold=0.5, target_sizes=inputs.get("original_sizes").tolist()
            )[0]
            for box, score in zip(res["boxes"], res["scores"]):
                dets.append({"label": cls, "score": float(score), "box": norm_box(box, img.width, img.height)})
        return dets


def build(name):
    if name == "gdino-tiny":
        return GDino("IDEA-Research/grounding-dino-tiny")
    if name == "gdino-base":
        return GDino("IDEA-Research/grounding-dino-base")
    if name == "mmgdino-tiny":
        return GDino("openmmlab-community/mm_grounding_dino_tiny_o365v1_goldg_v3det")
    if name == "owlv2-base":
        return Owl("google/owlv2-base-patch16-ensemble")
    if name == "florence2-base":
        return Florence("florence-community/Florence-2-base")
    if name == "florence2-large":
        return Florence("florence-community/Florence-2-large")
    if name == "sam3":
        return Sam3("facebook/sam3")
    raise SystemExit(f"unknown model {name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--images", default=str(B1 / "eval_images.json"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    items = read_image_list(args.images)
    if args.limit:
        items = items[: args.limit]
    out = Path(args.out or B1 / f"results/dets/{args.model}.jsonl")
    done = {}
    if out.exists():
        done = {r["id"]: r for r in read_jsonl(out)}
    t0 = time.time()
    det = build(args.model)
    print(f"loaded {args.model} in {time.time() - t0:.1f}s", flush=True)
    rows = []
    for i, it in enumerate(items):
        if it["id"] in done:
            rows.append(done[it["id"]])
            continue
        if not Path(it["path"]).exists():
            print(f"[{i + 1}/{len(items)}] {it['id']} missing, skipped", flush=True)
            continue
        img, _ = load_image(it["path"])
        t = time.time()
        dets = det(img)
        dt = time.time() - t
        rows.append({"id": it["id"], "model": args.model, "seconds": dt, "size": [img.width, img.height], "dets": dets})
        print(f"[{i + 1}/{len(items)}] {it['id']} {len(dets)} dets {dt:.1f}s", flush=True)
        if (i + 1) % 10 == 0:
            write_jsonl(out, rows)
    write_jsonl(out, rows)


if __name__ == "__main__":
    main()
