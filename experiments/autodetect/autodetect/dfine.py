"""Fallback student: D-FINE small (ustc-community/dfine-small-coco, Apache-2.0, COCO-pretrained)
fine-tuned for window and door on the same 1,340 Open Images train images as the Create ML student,
in PyTorch on MPS. Used because the Create ML transfer-learning student scored poorly
(results/proposals.md).

Preprocessing is the checkpoint's: resize to 640 x 640 and scale to [0, 1], no normalization.
Training: horizontal flips, AdamW (backbone at a tenth of the base rate), linear warmup, gradient
clipping at 0.1, a fixed number of epochs set before any scoring, final weights kept. Each box
takes its best class; the shared post-processing (nms.py) then applies, as for OWLv2.

transformers' DFineIntegral calls F.linear with a one-dimensional weight, whose backward pass fails
on MPS ("mat2 must be a matrix"). _patch_integral swaps in the same weighted sum written as a
product and sum; tests/test_dfine.py checks the two agree.

Not trained in this run. `probe` measured 8.6 s a step (batch 4, 640 px, 3.56 GB of GPU memory)
on this shared Mac at a load average near 300, which is about 48 minutes an epoch. Timings from
`predict` would be PyTorch on the Mac (MPS, and CPU-only for OI eval); there is no Core ML export
here, so on-device speed would still be unmeasured.

Usage: python -m autodetect.dfine train <epochs> [batch]
       python -m autodetect.dfine probe              # time 20 training steps and report memory
       python -m autodetect.dfine predict
"""

from __future__ import annotations

import json
import random
import sys
import time

import numpy as np
from PIL import Image

from .nms import postprocess
from .paths import DATA, WEIGHTS
from .sets import SETS, ground_truth, image_path, save_preds

BASE = WEIGHTS / "dfine-small"
OUT = DATA / "student" / "dfine_small"
LABELS = ["window", "door"]
SIZE = 640
LR, BACKBONE_LR_SCALE, WEIGHT_DECAY, WARMUP, CLIP = 2e-4, 0.1, 1e-4, 100, 0.1
MPS_MEMORY_FRACTION = 0.2  # of the recommended working set, to keep GPU memory near 3 GB
SEED = 20260926


def _integral_forward(self, pred_corners, project):
    import torch.nn.functional as F

    batch_size, num_queries, _ = pred_corners.shape
    prob = F.softmax(pred_corners.reshape(-1, self.max_num_bins + 1), dim=1)
    corners = (prob * project.to(prob.device).reshape(1, -1)).sum(1).reshape(-1, 4)
    return corners.reshape(batch_size, num_queries, -1)


def _patch_integral() -> None:
    from transformers.models.d_fine import modeling_d_fine

    modeling_d_fine.DFineIntegral.forward = _integral_forward


def _device():
    import torch

    if not torch.backends.mps.is_available():
        raise RuntimeError("MPS not available")
    torch.mps.set_per_process_memory_fraction(MPS_MEMORY_FRACTION)
    return torch.device("mps")


def pixels(im: Image.Image) -> np.ndarray:
    x = np.asarray(im.convert("RGB").resize((SIZE, SIZE), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
    return x.transpose(2, 0, 1)


def _batches(gt: dict, batch: int, rng: random.Random):
    ids = sorted(gt)
    rng.shuffle(ids)
    for k in range(0, len(ids) - batch + 1, batch):
        xs, ys = [], []
        for i in ids[k : k + batch]:
            x = pixels(Image.open(image_path("oi_train", i)))
            b = np.array([bb["box"] for bb in gt[i]["boxes"]], dtype=np.float32)
            c = np.array([LABELS.index(bb["label"]) for bb in gt[i]["boxes"]])
            if rng.random() < 0.5:
                x = x[:, :, ::-1].copy()
                b = np.stack([1 - b[:, 2], b[:, 1], 1 - b[:, 0], b[:, 3]], 1)
            cxcywh = np.stack([(b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2, b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]], 1)
            xs.append(x)
            ys.append((c, cxcywh))
        yield np.stack(xs), ys


def _model(path, device):
    from transformers import DFineForObjectDetection

    _patch_integral()
    kw = {}
    if path == BASE:
        kw = dict(num_labels=2, id2label=dict(enumerate(LABELS)), label2id={n: k for k, n in enumerate(LABELS)}, ignore_mismatched_sizes=True)
    return DFineForObjectDetection.from_pretrained(str(path), **kw).to(device)


def train(epochs: int, batch: int = 4, max_steps: int | None = None) -> None:
    import torch

    torch.manual_seed(SEED)
    dev = _device()
    model = _model(BASE, dev).train()
    backbone = [p for n, p in model.named_parameters() if "backbone" in n]
    rest = [p for n, p in model.named_parameters() if "backbone" not in n]
    opt = torch.optim.AdamW(
        [{"params": backbone, "lr": LR * BACKBONE_LR_SCALE}, {"params": rest, "lr": LR}], weight_decay=WEIGHT_DECAY
    )
    base_lrs = [g["lr"] for g in opt.param_groups]
    gt = ground_truth("oi_train")
    rng = random.Random(SEED)
    step, t0 = 0, time.perf_counter()
    (DATA / "student").mkdir(parents=True, exist_ok=True)
    log = open(DATA / "student" / "dfine_small_train.log", "a")
    for epoch in range(epochs):
        losses = []
        for x, ys in _batches(gt, batch, rng):
            labels = [{"class_labels": torch.from_numpy(c).to(dev), "boxes": torch.from_numpy(b).to(dev)} for c, b in ys]
            out = model(pixel_values=torch.from_numpy(x).to(dev), labels=labels)
            opt.zero_grad()
            out.loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), CLIP)
            warm = min(1.0, (step + 1) / WARMUP)
            for g, lr in zip(opt.param_groups, base_lrs):
                g["lr"] = lr * warm
            opt.step()
            losses.append(out.loss.item())
            step += 1
            if step % 20 == 0:
                msg = f"epoch {epoch} step {step} loss {np.mean(losses[-20:]):.3f} {time.perf_counter() - t0:.0f}s mps {torch.mps.driver_allocated_memory() / 1e9:.2f} GB"
                print(msg, file=sys.stderr)
                log.write(msg + "\n")
                log.flush()
            if max_steps and step >= max_steps:
                return
    OUT.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(OUT))
    (OUT / "train.json").write_text(json.dumps({"epochs": epochs, "batch": batch, "steps": step, "seconds": time.perf_counter() - t0}))


def detect(model, dev, path) -> tuple[list[dict], float]:
    import torch

    x = torch.from_numpy(pixels(Image.open(path))[None]).to(dev)
    t0 = time.perf_counter()
    with torch.inference_mode():
        out = model(pixel_values=x)
        logits = out.logits[0].float().cpu().numpy()
        boxes = out.pred_boxes[0].float().cpu().numpy()
    ms = 1000 * (time.perf_counter() - t0)
    prob = 1 / (1 + np.exp(-logits))
    cx, cy, w, h = boxes.T
    xyxy = np.clip(np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], 1), 0, 1)
    return postprocess(xyxy, prob.max(1), [LABELS[k] for k in prob.argmax(1)]), ms


def predict() -> None:
    import torch

    size = sum(p.stat().st_size for p in OUT.glob("*.safetensors"))
    meta = {
        "model": "D-FINE small, fine-tuned for window and door",
        "license": "Apache-2.0 base; fine-tuned on Open Images V7 train (CC BY 2.0 images, CC BY 4.0 boxes)",
        "size": f"{size / 1e6:.0f} MB (safetensors fp32)",
        "runtime": "PyTorch MPS (GPU), Mac, fp32",
        "input": "640 x 640 (stretched)",
        "train": json.loads((OUT / "train.json").read_text()),
        "command": "uv run python -m autodetect.dfine predict",
    }
    dev = _device()
    model = _model(OUT, dev).eval()
    for s in (*SETS, "electro"):
        images = {}
        for i in sorted(ground_truth(s)):
            dets, ms = detect(model, dev, image_path(s, i))
            images[i] = {"elapsed_ms": ms, "dets": dets}
        save_preds("student_dfine", s, meta, images)
        print(f"{s}: {len(images)} images", file=sys.stderr)
    cpu = torch.device("cpu")
    model = model.to(cpu)
    images = {}
    for i in sorted(ground_truth("oi_eval"))[:61]:  # 60 timed after one warm-up
        dets, ms = detect(model, cpu, image_path("oi_eval", i))
        images[i] = {"elapsed_ms": ms, "dets": dets}
    save_preds("student_dfine_cpu", "oi_eval", dict(meta, runtime="PyTorch CPU, Mac, fp32 (first 61 OI eval images)"), images)


if __name__ == "__main__":
    step = sys.argv[1]
    if step == "train":
        train(int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 4)
    elif step == "probe":
        train(1, 4, max_steps=20)
    elif step == "predict":
        predict()
    else:
        raise SystemExit(f"unknown step {step!r}")
