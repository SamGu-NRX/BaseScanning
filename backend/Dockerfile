# GPU pipeline image. Application code is not copied here; Modal adds server/
# with add_local_python_source. COLMAP 4.2.0 matches pycolmap==4.2.0 (SD-34).
# linux/amd64 digest of nvidia/cuda:12.4.1-devel-ubuntu22.04.
FROM nvidia/cuda:12.4.1-devel-ubuntu22.04@sha256:5645fec64549cc35930eee9d85aafd2b0006c0c3f22632be5a1d85e2604e9749

ENV DEBIAN_FRONTEND=noninteractive \
    HF_HOME=/vol/hf \
    HUGGINGFACE_HUB_CACHE=/vol/hf/hub \
    TORCH_HOME=/vol/torch \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH

# Jammy's Ceres package is 1.14. COLMAP 4.2 needs Ceres >= 2.2, so that
# package is not installed; Ceres 2.2.0 is built below. OpenImageIO's Ubuntu
# CMake config looks for /usr/include/opencv4 even when OpenCV is unused.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        cmake \
        ninja-build \
        build-essential \
        libboost-program-options-dev \
        libboost-filesystem-dev \
        libboost-graph-dev \
        libboost-system-dev \
        libeigen3-dev \
        libopenimageio-dev \
        openimageio-tools \
        libmetis-dev \
        libgoogle-glog-dev \
        libgflags-dev \
        libsqlite3-dev \
        libsuitesparse-dev \
        libcurl4-openssl-dev \
        libssl-dev \
        libglew-dev \
        libgl1-mesa-dev \
        libglu1-mesa-dev \
        libcgal-dev \
        libopenblas-dev \
        liblapack-dev \
        ca-certificates \
    && mkdir -p /usr/include/opencv4 \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --branch 2.2.0 --depth 1 https://github.com/ceres-solver/ceres-solver.git /tmp/ceres \
    && cmake -S /tmp/ceres -B /tmp/ceres/build -GNinja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
        -DBUILD_TESTING=OFF \
        -DBUILD_EXAMPLES=OFF \
        -DBUILD_BENCHMARKS=OFF \
    && ninja -C /tmp/ceres/build -j3 \
    && ninja -C /tmp/ceres/build install \
    && rm -rf /tmp/ceres

# A 3.11.1 binary under a 4.x pycolmap wheel is forbidden. Tag 4.2.0 is the
# COLMAP release pycolmap 4.2.0 is built against. The PyPI pycolmap wheel is
# CPU-only (has_cuda is false). pycolmap-cuda12 is installed last: the Pi3,
# MoGe, and Depth Anything archives depend on pycolmap and would replace an
# earlier CUDA wheel with the CPU one. The NVIDIA CUDA 12.4 image is not
# Ubuntu's CUDA package, so the jammy GCC 10 workaround does not apply.
# OpenImageIO's CMake config requires the OpenEXR config, which is not pulled
# in by libopenimageio-dev alone.
# Jammy's CMake is 3.22. COLMAP 4.2 fetches faiss, which requires CMake >= 3.24.
RUN apt-get update && apt-get install -y --no-install-recommends curl libopenexr-dev \
    && curl -fsSL -o /tmp/cmake.tgz https://github.com/Kitware/CMake/releases/download/v3.31.8/cmake-3.31.8-linux-x86_64.tar.gz \
    && echo "630615d8e98ac33eba7fbe472626dff5c899c85af3c024585ae109166a6909d0  /tmp/cmake.tgz" | sha256sum -c - \
    && tar -C /usr/local --strip-components=1 -xzf /tmp/cmake.tgz \
    && rm -rf /tmp/cmake.tgz /var/lib/apt/lists/* \
    && git clone --branch 4.2.0 --depth 1 https://github.com/colmap/colmap.git /tmp/colmap \
    && cmake -S /tmp/colmap -B /tmp/colmap/build -GNinja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CUDA_ARCHITECTURES="80;86;89;90" \
        -DGUI_ENABLED=OFF \
        -DTESTS_ENABLED=OFF \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
    && ninja -C /tmp/colmap/build -j3 \
    && ninja -C /tmp/colmap/build install \
    && ldconfig \
    && (colmap -h > /tmp/colmap-help.txt || true) \
    && grep -q COLMAP /tmp/colmap-help.txt \
    && rm -rf /tmp/colmap /tmp/colmap-help.txt

# Tag 0.11.23, pinned by digest. Modal's builder rejects a reference that sets both.
COPY --from=ghcr.io/astral-sh/uv@sha256:d0a0a753ab981624b49c97abc98821c1c09f4ca69d1ef5cee69c501be3d88479 /uv /uvx /bin/

RUN uv python install 3.12

WORKDIR /app
COPY pyproject.toml uv.lock ./
# libsm6/libice6/libx11/libxext are NEEDED by the CUDA wheel. --no-deps keeps
# pip's cuda-toolkit meta-package off this image; CUDA 12.4 is already here.
RUN apt-get update && apt-get install -y --no-install-recommends libsm6 libice6 libx11-6 libxext6 \
    && rm -rf /var/lib/apt/lists/* \
    && uv sync --frozen --no-install-project --no-dev --extra gpu \
    && uv pip install --python /app/.venv/bin/python pip \
    && curl -fsSL -o /tmp/pi3.tgz "https://github.com/yyfz/Pi3/archive/9fa3ddb3f8d53041f8b2738df404f62223bbaa7b.tar.gz" \
    && echo "017c226430e69bd3508940f326bce3db6e3eb778d7efe088b7fe6374cd550431  /tmp/pi3.tgz" | sha256sum -c - \
    && curl -fsSL -o /tmp/moge.tgz "https://github.com/microsoft/MoGe/archive/925b8ed835a7a9cdb7578ba15c658a0afc969030.tar.gz" \
    && echo "c6d90b2564d643f0f3ae65b5ffe163abaea3b55d194c6e2ba9976d67f552bd26  /tmp/moge.tgz" | sha256sum -c - \
    && curl -fsSL -o /tmp/da3.tgz "https://github.com/ByteDance-Seed/Depth-Anything-3/archive/3d835ec1a5802d64a8b8b15f817a1ab54809bfe4.tar.gz" \
    && echo "98aa2dd53ab44b96cef5190ae4841c6ae51797d099b784e08e12a1a55bd3a69b  /tmp/da3.tgz" | sha256sum -c - \
    && uv pip install --python /app/.venv/bin/python /tmp/pi3.tgz /tmp/moge.tgz /tmp/da3.tgz \
    && rm -f /tmp/pi3.tgz /tmp/moge.tgz /tmp/da3.tgz \
    && uv pip install --python /app/.venv/bin/python \
        torch==2.5.1 torchvision==0.20.1 \
        --index-url https://download.pytorch.org/whl/cu124 \
    && uv pip uninstall --python /app/.venv/bin/python pycolmap pycolmap-cuda12 \
    && uv pip install --python /app/.venv/bin/python --no-deps "pycolmap-cuda12==4.2.0"

RUN python -c "import pycolmap; assert pycolmap.__version__.startswith('4.2'), pycolmap.__version__; assert pycolmap.has_cuda, pycolmap.has_cuda"
