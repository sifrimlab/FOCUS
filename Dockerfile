# FOCUS Dockerfile
# Builds a FOCUS image. CPU-only PyTorch is baked in by default; pass the
# TORCH_INDEX build-arg to get a CUDA build instead.
#
# The PyTorch wheels at download.pytorch.org bundle the CUDA runtime *inside*
# the wheel, so a cuXXX build runs on this plain python:3.11-slim base as long
# as the host GPU driver is exposed at runtime (`--gpus all`, via the NVIDIA
# Container Toolkit). No CUDA base image is required. This mirrors the wheel
# selection logic in install.sh.
#
# Build (CPU, the default):
#   docker build -t focus .
#
# Build (GPU): pick the index matching your target CUDA, e.g. CUDA 12.8+:
#   docker build --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu128 -t focus:gpu .
#   (other indices: cu126, cu124, cu121, cu118; see install.sh resolve_torch_index)
#
# Run (GUI):
#   docker run --rm -p 5050:5050 -v /your/data:/your/data focus
#
# Run (CLI):
#   docker run --rm -v /your/data:/your/data focus --config /your/data/project/config.json
#
# Run (GPU):
#   docker run --rm --gpus all -v /your/data:/your/data focus:gpu --config /your/data/project/config.json

FROM python:3.11-slim

# PyTorch wheel index. CPU by default; override at build time for a CUDA build,
# e.g. --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu128
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu

# System dependencies
# libgl1 / libglib2.0-0:  OpenCV runtime
# libgomp1:               OpenMP (numpy/scipy parallelism)
# libsm6 / libxext6:      needed by some OpenCV headless builds
# curl:                   healthcheck / debugging
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libsm6 \
        libxext6 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
WORKDIR /opt/focus

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# PyTorch ecosystem (torch, torchvision, timm, huggingface-hub)
# Installed separately from the wheel index selected by TORCH_INDEX, NOT from
# requirements.txt or pyproject.toml. Same strategy as install.sh. torch +
# torchvision come from the index first; timm + huggingface-hub then install
# from PyPI constrained to those exact versions so pip cannot pull a different
# torch/torchvision through timm's dependency chain.
RUN pip install --no-cache-dir torch torchvision --index-url "${TORCH_INDEX}" \
 && { pip show torch        | awk '/^Version:/ {print "torch=="$2}'        >  /tmp/torch-constraints.txt; \
      pip show torchvision  | awk '/^Version:/ {print "torchvision=="$2}'  >> /tmp/torch-constraints.txt; } \
 && pip install --no-cache-dir timm huggingface-hub -c /tmp/torch-constraints.txt \
 && rm -f /tmp/torch-constraints.txt \
 && python -c "import torch, torchvision, timm; print('torch', torch.__version__)"

# Install the FOCUS package
# src/ and the build metadata are what get installed. gui_src/ is copied too,
# but is NOT built here: focus/GUI already contains the compiled bundles. It is
# present because the image redistributes those minified bundles, and under GNU
# GPL v3 section 1 their Corresponding Source is the Vue/TypeScript source plus
# build config. Shipping it in the image discharges that duty directly, which
# matters because the upstream repository is private and so the section 6(d)
# "public network server" route is unavailable.
# .dockerignore already keeps node_modules/, dist/, .vite/ and *.tsbuildinfo
# out of the build context, so this stays small.
COPY pyproject.toml .
COPY LICENSE .
COPY src/ src/
COPY gui_src/ gui_src/

RUN pip install --no-cache-dir -e .

# Runtime configuration
# GUI listens on 5050; expose it so -p 5050:5050 works out of the box
EXPOSE 5050

# Run as a non-root user for better security
RUN useradd -m -u 1000 focususer
USER focususer

ENTRYPOINT ["focus"]
# Default: launch GUI; pass --config /path/to/config.json for CLI mode
