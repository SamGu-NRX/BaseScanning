"""Weight path names. Downloads stay in ``make weights``."""

from pathlib import Path

from server.weights.fetch import DENSE_REPOS, HUB_REPOS, dense_snapshot_dir, hub_model_dir


def test_dense_snapshots_match_ws06_layout() -> None:
    assert dense_snapshot_dir("/vol/hf", "yyfz233/Pi3") == dense_snapshot_dir("/vol/hf", DENSE_REPOS[0])
    assert str(dense_snapshot_dir("/vol/hf", "yyfz233/Pi3")).endswith("yyfz233--Pi3")
    assert "depth-anything/DA3-LARGE-1.1" in DENSE_REPOS
    assert "Ruicheng/moge-2-vits-normal" in DENSE_REPOS


def test_objects_gate_repos_are_public_ids() -> None:
    assert "laion/CLIP-ViT-B-32-laion2B-s34B-b79K" in HUB_REPOS
    assert "google/siglip2-base-patch16-256" in HUB_REPOS
    assert "google/owlv2-base-patch16-ensemble" in HUB_REPOS


def test_dense_repos_also_have_hub_cache_paths() -> None:
    root = Path("/vol/hf")
    assert hub_model_dir(root, "yyfz233/Pi3") == root / "hub" / "models--yyfz233--Pi3"
    assert hub_model_dir(root, "depth-anything/DA3-LARGE-1.1") == (
        root / "hub" / "models--depth-anything--DA3-LARGE-1.1"
    )
    assert hub_model_dir(root, "Ruicheng/moge-2-vits-normal") == (root / "hub" / "models--Ruicheng--moge-2-vits-normal")
