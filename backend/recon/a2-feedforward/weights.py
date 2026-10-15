import os, time
os.environ["HF_HOME"] = os.path.expanduser("~/a2/hf")
from huggingface_hub import hf_hub_download, snapshot_download
todo = [
 ("facebook/map-anything-apache", None),
 ("yyfz233/Pi3", None),
 ("depth-anything/DA3-LARGE-1.1", None),
 ("facebook/VGGT-1B", ["model.safetensors", "config.json", "*.md"]),
 ("depth-anything/DA3METRIC-LARGE", None),
 ("naver/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric", None),
 ("depth-anything/DA3NESTED-GIANT-LARGE-1.1", None),
]
for repo, pats in todo:
    t = time.time()
    p = snapshot_download(repo, allow_patterns=pats)
    print(f"{time.strftime('%T')} {repo} -> {p} ({time.time()-t:.0f}s)", flush=True)
print("WEIGHTS_DONE")
