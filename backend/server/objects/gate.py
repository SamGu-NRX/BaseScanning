"""CLIP and SigLIP2 frame embeddings and zero-shot class scores.

Port of B3 gate_features.{Clip,Siglip,load} and gate_eval.{PROMPTS,text_emb,embedding_scores}.
Torch and open_clip load inside the call so `server.objects` imports without the extra.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

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


def embed_frames(frames: list, model: str, *, max_side: int = 1024) -> dict[str, np.ndarray]:
    import torch

    torch.set_num_threads(2)
    encode = _encoder(model)
    out: dict[str, np.ndarray] = {}
    for frame in frames:
        img = load_image(frame.image, max_side, transpose=frame_needs_transpose(frame))
        vec = encode(img).astype(np.float32)
        vec = vec / max(np.linalg.norm(vec), 1e-12)
        out[frame.id] = vec
    return out


def text_bank(model: str, prompts: dict[str, list[str]] | None = None) -> dict:
    """Text embeddings, logit scale and bias, and the class that owns each prompt."""
    prompts = prompts or GATE_PROMPTS
    flat: list[str] = []
    owner: list[str] = []
    for cls, lines in prompts.items():
        flat.extend(lines)
        owner.extend([cls] * len(lines))
    emb, scale, bias = _text_emb(model, flat)
    return {"emb": emb.astype(np.float32), "scale": float(scale), "bias": float(bias), "owner": owner}


def class_scores(
    emb: dict[str, np.ndarray],
    model: str,
    prompts: dict[str, list[str]] | None = None,
    *,
    bank: dict | None = None,
) -> dict[str, dict[str, float]]:
    """Frame id -> class -> score. CLIP sums softmax mass; SigLIP takes the max sigmoid."""
    bank = bank or text_bank(model, prompts)
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


def _encoder(model: str):
    import torch

    if model == "clip":
        import open_clip

        net, _, pre = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k")
        net.eval()

        def encode(img: Image.Image) -> np.ndarray:
            with torch.no_grad():
                vec = net.encode_image(pre(img).unsqueeze(0))
                vec = vec / vec.norm(dim=-1, keepdim=True)
            return vec[0].numpy()

        return encode
    if model == "siglip":
        from transformers import AutoModel, AutoProcessor

        name = "google/siglip2-base-patch16-256"
        net = AutoModel.from_pretrained(name).eval()
        proc = AutoProcessor.from_pretrained(name)

        def encode(img: Image.Image) -> np.ndarray:
            with torch.no_grad():
                inp = proc(images=img, return_tensors="pt")
                vec = net.get_image_features(**inp)
                if not isinstance(vec, torch.Tensor):
                    vec = vec.pooler_output
                vec = vec / vec.norm(dim=-1, keepdim=True)
            return vec[0].numpy()

        return encode
    raise ValueError(f"unknown gate model {model}")


def _text_emb(model: str, prompts: list[str]) -> tuple[np.ndarray, float, float]:
    import torch

    if model.startswith("clip"):
        import open_clip

        arch, tag = ("ViT-B-32", "laion2b_s34b_b79k") if model == "clip" else ("ViT-L-14", "datacomp_xl_s13b_b90k")
        net, _, _ = open_clip.create_model_and_transforms(arch, pretrained=tag)
        tok = open_clip.get_tokenizer(arch)
        with torch.no_grad():
            emb = net.encode_text(tok(prompts))
        scale, bias = float(net.logit_scale.exp()), 0.0
    else:
        from transformers import AutoModel, AutoProcessor

        name = "google/siglip2-base-patch16-256"
        net = AutoModel.from_pretrained(name).eval()
        proc = AutoProcessor.from_pretrained(name)
        with torch.no_grad():
            inp = proc(text=prompts, padding="max_length", max_length=64, return_tensors="pt")
            emb = net.get_text_features(**inp)
            if not isinstance(emb, torch.Tensor):
                emb = emb.pooler_output
        scale, bias = float(net.logit_scale.exp()), float(net.logit_bias)
    emb = emb / emb.norm(dim=-1, keepdim=True)
    return emb.numpy(), scale, bias


def load_cached_embeddings(path: Path) -> dict[str, np.ndarray]:
    """npz written by B3: keys are frame ids with '/' replaced by '__'."""
    packed = np.load(path)
    out = {}
    for key in packed.files:
        out[key.replace("__", "/")] = packed[key].astype(np.float32)
    return out
