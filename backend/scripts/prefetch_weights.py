"""Fill the house-capture-pipeline volume at /vol/hf. Dispatch with `modal run`."""

from __future__ import annotations

import os

REPOS = (
    "yyfz/Pi3",
    "microsoft/MoGe",
    "depth-anything/Depth-Anything-V2",
)


def main() -> int:
    if not os.environ.get("MODAL_TOKEN_ID") or not os.environ.get("MODAL_TOKEN_SECRET"):
        print("MODAL_TOKEN_ID or MODAL_TOKEN_SECRET is unset; weights were not prefetched.")
        return 0
    import modal

    from server.modal_app import GPU_IMAGE, volume

    app = modal.App("house-scanning-prefetch")

    @app.function(image=GPU_IMAGE, volumes={"/vol": volume}, timeout=1800, cpu=2)
    def prefetch() -> list[str]:
        from huggingface_hub import snapshot_download

        done = []
        for repo in REPOS:
            snapshot_download(repo, cache_dir="/vol/hf/hub")
            done.append(repo)
        volume.commit()
        return done

    with app.run():
        print(prefetch.remote())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
