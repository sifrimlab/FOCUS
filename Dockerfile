# ── FOCUS Dockerfile ──────────────────────────────────────────────────────────
# Builds a CPU-capable FOCUS image.
# For GPU (CUDA) support replace the base image with a CUDA-enabled variant,
# e.g.: pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime
#
# Build:
#   docker build -t focus .
#
# Run (GUI):
#   docker run --rm -p 5050:5050 -v /your/data:/your/data focus
#
# Run (CLI):
#   docker run --rm -v /your/data:/your/data focus --config /your/data/project/config.json

FROM python:3.11-slim

# ── System dependencies ───────────────────────────────────────────────────────
# libgl1 / libglib2.0-0  — OpenCV runtime
# libgomp1               — OpenMP (numpy/scipy parallelism)
# libsm6 / libxext6      — needed by some OpenCV headless builds
# curl                   — healthcheck / debugging
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libsm6 \
        libxext6 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ───────────────────────────────────────────────────────
WORKDIR /opt/focus

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Install the FOCUS package ─────────────────────────────────────────────────
# Copy only the package source and build metadata (not gui_src, notebooks, etc.)
COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir -e .

# ── Runtime configuration ─────────────────────────────────────────────────────
# GUI listens on 5050; expose it so -p 5050:5050 works out of the box
EXPOSE 5050

# Run as a non-root user for better security
RUN useradd -m -u 1000 focususer
USER focususer

ENTRYPOINT ["focus"]
# Default: launch GUI; pass --config /path/to/config.json for CLI mode
