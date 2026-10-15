"""Fill model weights on the pipeline volume. Public Hugging Face repos only.

``make weights`` runs this on Modal. No token is read or sent.

Volume ``house-capture-pipeline`` is mounted at ``/vol``. Exact layout:

    /vol/hf/<org>--<repo>/                              local-dir snapshot
    /vol/hf/hub/models--<org>--<repo>/snapshots/<rev>/  Hugging Face hub cache
    /vol/hf/torch/                                      torch hub

Dense repos are written both ways so either loader works:

- ``HSS_DENSE_WEIGHTS=/vol/hf`` (SD-59) reads ``<org>--<repo>``
  (``yyfz233--Pi3``, ``depth-anything--DA3-LARGE-1.1``,
  ``Ruicheng--moge-2-vits-normal``).
- ``from_pretrained("yyfz233/Pi3")`` and the same for DA3 and MoGe resolve
  ``models--<org>--<repo>`` when ``HF_HOME=/vol/hf`` and
  ``HUGGINGFACE_HUB_CACHE=/vol/hf/hub``. ``HOUSE_WEIGHTS=/vol/hf`` is that
  same root (``prepare_cache`` points the hub cache there).

Objects (WS-07) uses the hub cache only: CLIP, SigLIP2, OWLv2, Grounding DINO.
"""

from __future__ import annotations

import os
from pathlib import Path

VOLUME_NAME = "house-capture-pipeline"
WEIGHTS_ROOT = "/vol/hf"

# WS-06 local snapshots. Loaders never call snapshot_download themselves.
DENSE_REPOS = (
    "yyfz233/Pi3",
    "depth-anything/DA3-LARGE-1.1",
    "Ruicheng/moge-2-vits-normal",
)
# WS-07 gate and proposer. Grounding DINO is on by default (HSS_OBJECTS_GDINO_OPENINGS).
HUB_REPOS = (
    "laion/CLIP-ViT-B-32-laion2B-s34B-b79K",
    "google/siglip2-base-patch16-256",
    "google/owlv2-base-patch16-ensemble",
    "IDEA-Research/grounding-dino-tiny",
)

try:
    import modal
except ImportError:
    modal = None


def dense_snapshot_dir(root: str | Path, repo: str) -> Path:
    """Local-dir tree: ``yyfz233/Pi3`` -> ``/vol/hf/yyfz233--Pi3``."""
    return Path(root) / repo.replace("/", "--")


def hub_model_dir(root: str | Path, repo: str) -> Path:
    """Hub cache: ``yyfz233/Pi3`` -> ``/vol/hf/hub/models--yyfz233--Pi3``."""
    return Path(root) / "hub" / ("models--" + repo.replace("/", "--"))


def fill_weights(root: str = WEIGHTS_ROOT) -> dict[str, str]:
    """Download the public repos under ``root``. Returns repo -> directory.

    ``huggingface_hub`` is installed on the Modal image, not required to import
    this module.
    """
    from huggingface_hub import snapshot_download

    os.environ["HF_HOME"] = root
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(Path(root) / "hub")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    written: dict[str, str] = {}
    for repo in DENSE_REPOS:
        dest = dense_snapshot_dir(root, repo)
        dest.mkdir(parents=True, exist_ok=True)
        snapshot_download(repo, local_dir=str(dest))
        written[repo] = str(dest)
    hub = Path(root) / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    # Dense repos go into the hub cache as well, so from_pretrained(repo_id)
    # finds models--<org>--<repo> beside the local-dir trees above.
    for repo in (*DENSE_REPOS, *HUB_REPOS):
        snapshot_download(repo, cache_dir=str(hub))
        written[f"hub:{repo}"] = str(hub_model_dir(root, repo))
    return written


app = modal.App("house-scanning-weights") if modal is not None else None
_volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True) if modal is not None else None
_image = (
    modal.Image.debian_slim(python_version="3.12").pip_install("huggingface_hub>=0.26") if modal is not None else None
)


if modal is not None and app is not None and _volume is not None and _image is not None:

    @app.function(image=_image, volumes={"/vol": _volume}, timeout=3600, cpu=2, memory=8192)
    def fill() -> dict[str, str]:
        written = fill_weights(WEIGHTS_ROOT)
        _volume.commit()
        return written

    @app.local_entrypoint()
    def run() -> None:
        result = fill.remote()
        for repo, path in result.items():
            print(f"{repo} -> {path}")
