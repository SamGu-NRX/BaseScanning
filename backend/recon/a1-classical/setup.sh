#!/usr/bin/env bash
# Rebuild the A1 working environment on a fresh Ubuntu 24.04 cloud VM (CPU only, 4 cores).
# Everything lands in ~/a1 (off-store: large downloads, builds, work dirs). About 15 min total.
set -euo pipefail
A1=$HOME/a1
mkdir -p "$A1"/{dl,eth3d,logs,work}

# 1. System packages (OpenMVS v2.3.0 + ETH3D evaluator deps, 7z, venv).
sudo apt-get update -qq
sudo apt-get install -y -qq p7zip-full cmake build-essential git python3.12-venv \
  libeigen3-dev libboost-all-dev libopencv-dev libcgal-dev libglew-dev libpng-dev libjpeg-dev \
  libtiff-dev libglfw3-dev libatlas-base-dev libsuitesparse-dev libceres-dev libjxl-dev libpcl-dev

# 2. Python venv. pycolmap 4.2.0 = COLMAP 4.2 CPU wheels, includes GLOMAP's global mapper and pose-prior BA.
python3 -m venv "$A1/venv"
. "$A1/venv/bin/activate"
pip install -q --upgrade pip
pip install -q pycolmap==4.2.0 numpy scipy open3d opencv-python-headless plyfile pillow

# 3. ETH3D laser scans (eval format) + undistorted DSLR images for electro and delivery_area.
#    Facade images come from the store (internal/data/houses/eth3d-facade/frames, 2048 px).
cd "$A1/dl"
for f in facade_dslr_scan_eval.7z electro_dslr_scan_eval.7z delivery_area_dslr_scan_eval.7z \
         electro_dslr_undistorted.7z delivery_area_dslr_undistorted.7z; do
  [ -f "$f" ] || curl -sSL -o "$f" "https://www.eth3d.net/data/$f"
done
cd "$A1/eth3d"
for f in "$A1"/dl/*.7z; do 7z x -y -bso0 -bsp0 "$f"; done

# 4. OpenMVS v2.3.0, CPU only. Must use gcc: /usr/bin/c++ is clang-18 here and fails to link libstdc++.
#    Do not use OpenMVS develop/v2.4+: they need vcpkg-only deps (PoseLib, TinyEXIF, halfmesh).
cd "$A1"
[ -d vcglib ] || git clone -q --depth 1 https://github.com/cdcseacave/VCG.git vcglib
[ -d openMVS ] || git clone -q --depth 1 --branch v2.3.0 --recurse-submodules https://github.com/cdcseacave/openMVS.git openMVS
# Util::formatTime passes 128 instead of 128-len to snprintf; glibc fortify aborts DensifyPointCloud.
sed -i '601,628s/_sntprintf(buf+len, 128,/_sntprintf(buf+len, 128-len,/' openMVS/libs/Common/Util.h
mkdir -p openMVS_build && cd openMVS_build
CC=gcc CXX=g++ cmake ../openMVS -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++ \
  -DVCG_ROOT="$A1/vcglib" -DOpenMVS_USE_CUDA=OFF -DOpenMVS_USE_PYTHON=OFF -DOpenMVS_USE_BREAKPAD=OFF \
  -DCMAKE_INSTALL_PREFIX="$A1/openmvs_install" > cmake.log 2>&1
make -j3 > make.log 2>&1 && make install > install.log 2>&1
# Binaries: $A1/openmvs_install/bin/OpenMVS/{InterfaceCOLMAP,DensifyPointCloud,ReconstructMesh,...}

# 5. Official ETH3D multi-view evaluator (accuracy / completeness / F1 at tolerances). Needs C++17 for PCL 1.14.
cd "$A1"
[ -d multi-view-evaluation ] || git clone -q --depth 1 https://github.com/ETH3D/multi-view-evaluation.git
sed -i 's/-std=c++11/-std=c++17/' multi-view-evaluation/CMakeLists.txt
mkdir -p multi-view-evaluation/build && cd multi-view-evaluation/build
CC=gcc CXX=g++ cmake .. -DCMAKE_BUILD_TYPE=Release > cmake.log 2>&1 && make -j4 > make.log 2>&1
# Usage: ./ETH3DMultiViewEvaluation --tolerances 0.01,0.02,0.05,0.1,0.2,0.5 \
#   --reconstruction_ply_path rec.ply --ground_truth_mlp_path $A1/eth3d/<scene>/dslr_scan_eval/scan_alignment.mlp

echo "A1 setup done"
