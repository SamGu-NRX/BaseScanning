"""CLIP and SigLIP2 frame embeddings and zero-shot class scores.

Port of B3 gate_features.{Clip,Siglip,load} and gate_eval.{PROMPTS,text_emb,embedding_scores}.
Torch and open_clip load inside the call so `server.objects` imports without the extra.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from server.objects.device import gate_batch, move_batch, release_cuda, resolve_device, use_half
from server.objects.prompts import GATE_CLASSES, GATE_PROMPTS

MAX_SIDE = 1024


def frame_needs_transpose(frame) -> bool:
    """Packet keyframes are already upright. Stills, and frames with no kind, follow EXIF."""
    return getattr(frame, "kind", "still") != "keyframe"


def load_image(path: Path | str, max_side: int = MAX_SIDE, *, transpose: bool = False) -> Image.Image:
    """Keyframes stay upright. Pass transpose=True for stills that carry an EXIF orientation."""
    img = Image.open(path)
    if transpose:
        img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    scale = min(1.0, max_side / max(img.size))
    if scale < 1.0:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    return img


def embed_frames(
    frames: list,
    model: str,
    *,
    max_side: int = 1024,
    device: str | None = None,
    timing: dict[str, float] | None = None,
    beat=None,
) -> dict[str, np.ndarray]:
    import torch

    torch.set_num_threads(2)
    dev = resolve_device(device)
    bundle = _gate(model, dev)
    size = gate_batch(dev)
    out: dict[str, np.ndarray] = {}
    chunk: list[tuple[object, Image.Image, float]] = []

    def flush() -> None:
        if not chunk:
            return
        if beat:
            beat(f"objects:{model}")
        images = [img for _, img, _ in chunk]
        started = time.perf_counter()
        vectors = _encode_images(bundle, images)
        share = (time.perf_counter() - started) / len(chunk)
        for (frame, _, prep), vec in zip(chunk, vectors, strict=True):
            vec = np.asarray(vec, np.float32)
            vec = vec / max(float(np.linalg.norm(vec)), 1e-12)
            out[frame.id] = vec
            if timing is not None:
                timing[frame.id] = prep + share
        chunk.clear()

    for frame in frames:
        started = time.perf_counter()
        img = load_image(frame.image, max_side, transpose=frame_needs_transpose(frame))
        prep = time.perf_counter() - started
        chunk.append((frame, img, prep))
        if len(chunk) >= size:
            flush()
    flush()
    return out


def text_bank(model: str, prompts: dict[str, list[str]] | None = None, *, device: str | None = None) -> dict:
    """Text embeddings, logit scale and bias, and the class that owns each prompt."""
    prompts = prompts or GATE_PROMPTS
    flat: list[str] = []
    owner: list[str] = []
    for cls, lines in prompts.items():
        flat.extend(lines)
        owner.extend([cls] * len(lines))
    emb, scale, bias = _text_emb(model, flat, device=device)
    return {"emb": emb.astype(np.float32), "scale": float(scale), "bias": float(bias), "owner": owner}


def class_scores(
    emb: dict[str, np.ndarray],
    model: str,
    prompts: dict[str, list[str]] | None = None,
    *,
    bank: dict | None = None,
    device: str | None = None,
) -> dict[str, dict[str, float]]:
    """Frame id -> class -> score. CLIP sums softmax mass; SigLIP takes the max sigmoid."""
    bank = bank or text_bank(model, prompts, device=device)
    return score_with_bank(emb, model, bank)


def score_with_bank(emb: dict[str, np.ndarray], model: str, bank: dict) -> dict[str, dict[str, float]]:
    owner = np.array(bank["owner"])
    text = np.asarray(bank["emb"], np.float32)
    scale = float(bank["scale"])
    bias = float(bank["bias"])
    classes = list(dict.fromkeys(bank["owner"]))
    # Keep B3's reported classes even if a prompt set adds extras.
    wanted = [c for c in GATE_CLASSES if c in classes] or classes
    out: dict[str, dict[str, float]] = {}
    for frame_id, vec in emb.items():
        sims = np.asarray(vec, np.float32) @ text.T
        if model.startswith("clip"):
            shifted = scale * sims
            shifted = shifted - shifted.max()
            prob = np.exp(shifted)
            prob /= prob.sum()
            out[frame_id] = {c: float(prob[owner == c].sum()) for c in wanted}
        else:
            prob = 1 / (1 + np.exp(-(scale * sims + bias)))
            out[frame_id] = {c: float(prob[owner == c].max()) for c in wanted}
    return out


_GATE: dict[str, dict] = {}


def release_gates() -> None:
    """Drop a loaded tower before the next model. Peak memory is one tower, not three."""
    _GATE.clear()
    release_cuda()


def _gate(model: str, device: str) -> dict:
    key = f"{model}:{device}"
    cached = _GATE.get(key)
    if cached is not None:
        return cached
    if _GATE:
        release_gates()
    loaded = _load_gate(model, device)
    _GATE[key] = loaded
    return loaded


def _load_gate(model: str, device: str) -> dict:
    half = use_half(device)
    if model == "clip":
        import open_clip

        net, _, pre = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k")
        net = net.eval().to(device)
        if half:
            net = net.half()
        return {
            "kind": "clip",
            "net": net,
            "pre": pre,
            "tok": open_clip.get_tokenizer("ViT-B-32"),
            "device": device,
            "half": half,
        }
    if model == "siglip":
        from transformers import AutoModel, AutoProcessor

        name = "google/siglip2-base-patch16-256"
        net = AutoModel.from_pretrained(name).eval().to(device)
        if half:
            net = net.half()
        return {
            "kind": "siglip",
            "net": net,
            "proc": AutoProcessor.from_pretrained(name),
            "device": device,
            "half": half,
        }
    raise ValueError(f"unknown gate model {model}")


def _image_vector(vec, torch) -> object:
    if not isinstance(vec, torch.Tensor):
        vec = vec.pooler_output
    vec = vec / vec.norm(dim=-1, keepdim=True)
    return vec.float().cpu().numpy()


def _encode_images(bundle: dict, images: list[Image.Image]):
    import torch

    try:
        return _encode_images_once(bundle, images)
    except torch.cuda.OutOfMemoryError:
        if len(images) == 1 or bundle["device"] != "cuda":
            raise
    # Leave the except suite first. Its traceback still owns the failed batch,
    # so empty_cache() inside the handler cannot free those tensors.
    release_cuda()
    rows = [_encode_images_once(bundle, [img]) for img in images]
    return np.concatenate(rows, axis=0)


def _encode_images_once(bundle: dict, images: list[Image.Image]):
    import torch

    net = bundle["net"]
    with torch.inference_mode():
        if bundle["kind"] == "clip":
            batch = torch.stack([bundle["pre"](img) for img in images]).to(bundle["device"])
            if bundle["half"]:
                batch = batch.half()
            vec = net.encode_image(batch)
            return _image_vector(vec, torch)
        if bundle["kind"] == "siglip":
            rows = [bundle["proc"](images=img, return_tensors="pt") for img in images]
            inputs = move_batch(_cat_inputs(rows), bundle["device"], bundle["half"])
            vec = net.get_image_features(**inputs)
            return _image_vector(vec, torch)
    raise ValueError(f"unknown gate model {bundle['kind']}")


def _cat_inputs(rows: list[dict]) -> dict:
    import torch

    merged: dict = {}
    for key in rows[0]:
        values = [row[key] for row in rows]
        if torch.is_tensor(values[0]):
            merged[key] = torch.cat(values, 0)
        else:
            merged[key] = values
    return merged


def _text_emb(model: str, prompts: list[str], *, device: str | None = None) -> tuple[np.ndarray, float, float]:
    import torch

    dev = resolve_device(device)
    if model.startswith("clip") and model != "clip":
        import open_clip

        net, _, _ = open_clip.create_model_and_transforms("ViT-L-14", pretrained="datacomp_xl_s13b_b90k")
        net = net.eval().to(dev)
        if use_half(dev):
            net = net.half()
        tokens = open_clip.get_tokenizer("ViT-L-14")(prompts).to(dev)
        with torch.inference_mode():
            emb = net.encode_text(tokens)
        scale, bias = float(net.logit_scale.detach().float().exp()), 0.0
    else:
        bundle = _gate("clip" if model == "clip" else "siglip", dev)
        net = bundle["net"]
        with torch.inference_mode():
            if bundle["kind"] == "clip":
                emb = net.encode_text(bundle["tok"](prompts).to(dev))
                scale, bias = float(net.logit_scale.detach().float().exp()), 0.0
            else:
                inp = bundle["proc"](text=prompts, padding="max_length", max_length=64, return_tensors="pt")
                emb = net.get_text_features(**move_batch(inp, dev, bundle["half"]))
                if not isinstance(emb, torch.Tensor):
                    emb = emb.pooler_output
                scale = float(net.logit_scale.detach().float().exp())
                bias = float(net.logit_bias.detach().float())
    emb = emb.float()
    emb = emb / emb.norm(dim=-1, keepdim=True)
    return emb.cpu().numpy(), scale, bias


def load_cached_embeddings(path: Path) -> dict[str, np.ndarray]:
    """npz written by B3: keys are frame ids with '/' replaced by '__'."""
    packed = np.load(path)
    out = {}
    for key in packed.files:
        out[key.replace("__", "/")] = packed[key].astype(np.float32)
    return out
