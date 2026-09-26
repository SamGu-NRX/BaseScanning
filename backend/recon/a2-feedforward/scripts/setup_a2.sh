#!/bin/bash
set -ex
mkdir -p ~/a2/{repos,dl,data/eth3d,hsdata/evals,hf,preds} && ln -sfn ~/a2/data/eth3d ~/a2/hsdata/evals/eth3d
[ -d ~/a2/code ] || cp -r /cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/breadth/a2-feedforward-code ~/a2/code
pip3 install --user --break-system-packages uv py7zr
export PATH=$HOME/.local/bin:$PATH
# ETH3D download in background (needs only py7zr)
cp ~/a2/code/dl.sh ~/a2/dl/ && (bash ~/a2/dl/dl.sh > ~/a2/dl.log 2>&1 &)
cd ~/a2 && [ -d venv ] || uv venv --python 3.12 venv
source ~/a2/venv/bin/activate
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
uv pip install "numpy<2" opencv-python-headless scipy einops safetensors huggingface_hub pillow trimesh plyfile omegaconf hydra-core python-box natsort orjson pillow-heif requests tqdm "uniception==0.1.7" e3nn imageio pycolmap py7zr typer addict "moviepy==1.0.3" evo
(python ~/a2/code/weights.py > ~/a2/weights.log 2>&1 &)
cd ~/a2/repos
[ -d map-anything ] || { git clone https://github.com/facebookresearch/map-anything && git -C map-anything checkout 3d10cf7; }
[ -d vggt ] || { git clone https://github.com/facebookresearch/vggt && git -C vggt checkout a288dd0; }
[ -d Depth-Anything-3 ] || { git clone https://github.com/ByteDance-Seed/Depth-Anything-3 && git -C Depth-Anything-3 checkout 3d835ec; }
[ -d Pi3 ] || { git clone https://github.com/yyfz/Pi3 && git -C Pi3 checkout 9fa3ddb; }
cd ~/a2 && uv pip install --no-deps -e repos/map-anything -e repos/vggt -e repos/Depth-Anything-3 -e repos/Pi3
echo SETUP_DONE
