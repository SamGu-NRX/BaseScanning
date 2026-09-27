"""OWLv2 and Grounding-DINO proposals.

Port of B1 run_detectors.{Owl,GDino,norm_box} and evaluate.nms_same_class.
Florence, SAM and GDINO-base are not ported.
"""

from __future__ import annotations

from server.objects.config import Detection
from server.objects.gate import frame_needs_transpose, load_image
from server.objects.prompts import B1_CLASSES, DETECTOR_QUERIES

STORE_THRESHOLD = 0.08
NMS_IOU = 0.7


def iou(a: list[float], b: list[float]) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / area if area > 0 else 0.0


def nms_same_class(dets: list[Detection], thr: float = NMS_IOU) -> list[Detection]:
    out: list[Detection] = []
    for det in sorted(dets, key=lambda d: -d.score):
        if all(other.label != det.label or iou(other.box, det.box) < thr for other in out):
            out.append(det)
    return out


def norm_box(box, width: int, height: int) -> list[float]:
    x0, y0, x1, y1 = (float(v) for v in box)
    return [
        max(0.0, x0 / width),
        max(0.0, y0 / height),
        min(1.0, x1 / width),
        min(1.0, y1 / height),
    ]


def run_owlv2(
    frames: list, *, queries: dict[str, str] | None = None, store_thr: float = STORE_THRESHOLD
) -> dict[str, list[Detection]]:
    import torch
    from transformers import Owlv2ForObjectDetection, Owlv2Processor

    torch.set_num_threads(2)
    torch.manual_seed(0)
    queries = queries or DETECTOR_QUERIES
    names = list(queries)
    texts = [queries[name] for name in names]
    proc = Owlv2Processor.from_pretrained("google/owlv2-base-patch16-ensemble")
    model = Owlv2ForObjectDetection.from_pretrained("google/owlv2-base-patch16-ensemble").eval()
    out: dict[str, list[Detection]] = {}
    for frame in frames:
        img = load_image(frame.image, transpose=frame_needs_transpose(frame))
        with torch.inference_mode():
            inputs = proc(text=[texts], images=img, return_tensors="pt")
            pred = model(**inputs)
        side = max(img.width, img.height)
        res = proc.post_process_grounded_object_detection(pred, threshold=store_thr / 2, target_sizes=[(side, side)])[0]
        dets: list[Detection] = []
        for box, score, lab in zip(res["boxes"], res["scores"], res["labels"], strict=True):
            if float(score) < store_thr:
                continue
            dets.append(Detection(names[int(lab)], float(score), norm_box(box, img.width, img.height)))
        out[frame.id] = nms_same_class(dets)
    return out


def run_gdino(
    frames: list, *, classes: tuple[str, ...] = ("window", "door"), thr: float = 0.35
) -> dict[str, list[Detection]]:
    """GDINO-tiny. Class score is the mean token probability over that phrase, not the stock merger."""
    import torch
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

    torch.set_num_threads(2)
    # Openings use the B1 phrase list so token spans match the measured boxes; other classes
    # are appended and scored the same way.
    query_map = {name: B1_CLASSES[name] for name in classes if name in B1_CLASSES}
    for name in classes:
        query_map.setdefault(name, DETECTOR_QUERIES[name])
    text = ". ".join(query_map.values()) + "."
    proc = AutoProcessor.from_pretrained("IDEA-Research/grounding-dino-tiny")
    model = AutoModelForZeroShotObjectDetection.from_pretrained("IDEA-Research/grounding-dino-tiny").eval()
    enc = proc.tokenizer(text, return_offsets_mapping=True)
    spans: dict[str, list[int]] = {}
    pos = 0
    for cls, prompt in query_map.items():
        start = text.index(prompt, pos)
        end = start + len(prompt)
        pos = end
        spans[cls] = [i for i, (a, b) in enumerate(enc["offset_mapping"]) if b > a and a >= start and b <= end]
    names = list(spans)
    out: dict[str, list[Detection]] = {}
    for frame in frames:
        img = load_image(frame.image, transpose=frame_needs_transpose(frame))
        with torch.inference_mode():
            inputs = proc(images=img, text=text, return_tensors="pt")
            pred = model(**inputs)
            prob = pred.logits[0].sigmoid()
            cls_scores = torch.stack([prob[:, idx].mean(dim=1) for idx in spans.values()], dim=1)
            score, arg = cls_scores.max(dim=1)
            cx, cy, bw, bh = pred.pred_boxes[0].unbind(-1)
            boxes = torch.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], dim=-1).clamp(0, 1)
        dets: list[Detection] = []
        keep = (score >= thr).nonzero().flatten()
        for q in keep.tolist():
            box = [float(v) for v in boxes[q]]
            dets.append(Detection(names[int(arg[q])], float(score[q]), box))
        out[frame.id] = nms_same_class(dets)
    return out
