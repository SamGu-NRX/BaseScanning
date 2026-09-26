#!/bin/bash
# A7 VM rebuild (CPU only). A2 layout (~/a2; a2ff hard-codes it), pycolmap 4.2.0 for A1, harness deps,
# Pi3 + DA3-Large weights only, ETH3D facade + electro (undistorted full-res DSLR, laser, occlusion).
set -ex
S=/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2
mkdir -p ~/a2/{repos,dl,data/eth3d,hsdata/evals,hf,preds} ~/a7 && ln -sfn ~/a2/data/eth3d ~/a2/hsdata/evals/eth3d
[ -d ~/a2/code ] || cp -r $S/internal/breadth/a2-feedforward-code ~/a2/code
sudo apt-get update -qq && sudo apt-get install -y -qq p7zip-full libegl1 libgl1 libgomp1
pip3 install --user --break-system-packages -q uv
export PATH=$HOME/.local/bin:$PATH
( cd ~/a2/dl; for s in facade electro; do for k in dslr_scan_eval dslr_occlusion dslr_undistorted; do f=${s}_${k}.7z
    [ -f done_$f ] && continue; curl -sSL --retry 5 -o $f https://www.eth3d.net/data/$f && 7z x -y -bso0 -bsp0 -o$HOME/a2/data/eth3d $f && rm $f && touch done_$f; done; done; echo ALL_DONE ) > ~/a2/dl.log 2>&1 &
cd ~/a2 && [ -d venv ] || uv venv -q --python 3.12 venv
source ~/a2/venv/bin/activate
uv pip install -q torch torchvision --index-url https://download.pytorch.org/whl/cpu
uv pip install -q "numpy<2" opencv-python-headless scipy einops safetensors huggingface_hub pillow trimesh plyfile omegaconf hydra-core python-box natsort orjson pillow-heif requests tqdm e3nn imageio "pycolmap==4.2.0" typer addict "moviepy==1.0.3" evo jsonschema pyyaml shapely pydantic pytest open3d
python - <<'PY' &
import os; os.environ["HF_HOME"]=os.path.expanduser("~/a2/hf")
from huggingface_hub import snapshot_download
for r in ["yyfz233/Pi3","depth-anything/DA3-LARGE-1.1"]: print(snapshot_download(r), flush=True)
print("WEIGHTS_DONE")
PY
cd ~/a2/repos
[ -d Depth-Anything-3 ] || { git clone -q https://github.com/ByteDance-Seed/Depth-Anything-3 && git -C Depth-Anything-3 checkout -q 3d835ec; }
[ -d Pi3 ] || { git clone -q https://github.com/yyfz/Pi3 && git -C Pi3 checkout -q 9fa3ddb; }
cd ~/a2 && uv pip install -q --no-deps -e repos/Depth-Anything-3 -e repos/Pi3
wait
echo SETUP_DONE
