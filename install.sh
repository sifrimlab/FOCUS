#!/usr/bin/env bash
# FOCUS installation script for macOS and Linux
# Usage: bash install.sh [--reinstall]

set -euo pipefail

REINSTALL=0
for arg in "$@"; do
    [[ "$arg" == "--reinstall" ]] && REINSTALL=1
done

# Pins torch + torchvision so that later pip install commands cannot replace
# them from PyPI.
TORCH_CONSTRAINTS=""

info()    { echo -e "\033[1;34m[INFO]\033[0m  $*"; }
success() { echo -e "\033[1;32m[OK]\033[0m    $*"; }
warn()    { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
error()   { echo -e "\033[1;31m[ERROR]\033[0m $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1. Verify conda is available
if ! command -v conda &>/dev/null; then
    error "conda not found in PATH."
    echo
    echo "  Please install Miniconda or Anaconda and make sure it is initialised"
    echo "  for your shell before running this script."
    echo
    echo "  Miniconda: https://docs.conda.io/en/latest/miniconda.html"
    echo "  Anaconda:  https://www.anaconda.com/download"
    exit 1
fi
success "conda found: $(conda --version)"

conda_env_exists() {
    conda info --envs | awk '{print $1}' | grep -Fxq "$1"
}

# 2. Detect system CUDA and resolve the right PyTorch wheel index
#
# Default PyPI torch installs nvidia-cuda-*-cu12 as separate pip packages. On
# systems with a system-managed CUDA at a different version these conflict at the
# dynamic-linker level and cause bus errors or segfaults on import. Wheels from
# https://download.pytorch.org/whl/cuXXX bundle CUDA in the wheel's own lib
# directory and create no separate nvidia-* packages, so they coexist with any
# system CUDA.
#
# The detected version maps to the highest wheel that is <= it, since CUDA drivers
# are backward-compatible: a driver for CUDA 13.x runs code built for CUDA 12.x.

detect_cuda_version() {
    # 1. nvcc in PATH: reports the CUDA toolkit version, the most accurate source
    if command -v nvcc &>/dev/null; then
        nvcc --version 2>/dev/null \
            | grep -oP 'release \K[0-9]+\.[0-9]+' \
            | head -1
        return
    fi

    # 2. CUDA_HOME / CUDA_PATH / CUDA_ROOT: set by Lmod when a cuda module is
    #    loaded. Try nvcc from there, then version.json / version.txt.
    for cuda_home_var in CUDA_HOME CUDA_PATH CUDA_ROOT; do
        local cuda_dir="${!cuda_home_var:-}"
        [[ -z "$cuda_dir" ]] && continue

        if [[ -x "$cuda_dir/bin/nvcc" ]]; then
            "$cuda_dir/bin/nvcc" --version 2>/dev/null \
                | grep -oP 'release \K[0-9]+\.[0-9]+' | head -1
            return
        fi
        if [[ -f "$cuda_dir/version.json" ]]; then
            python3 -c "
import json, sys
try:
    d = json.load(open('$cuda_dir/version.json'))
    v = d.get('cuda', d.get('CUDA', {})).get('version', '')
    print('.'.join(v.split('.')[:2]))
except: pass
" 2>/dev/null | grep -P '^[0-9]+\.[0-9]+$' && return
        fi
        if [[ -f "$cuda_dir/version.txt" ]]; then
            grep -oP 'CUDA Version \K[0-9]+\.[0-9]+' "$cuda_dir/version.txt" 2>/dev/null \
                | head -1 && return
        fi
    done

    # 3. LOADEDMODULES: Lmod and Environment Modules set this to a colon-separated
    #    list such as "gcc/12.2:cuda/12.8:...". Parse the cuda entry.
    if [[ -n "${LOADEDMODULES:-}" ]]; then
        local mod_ver
        mod_ver=$(echo "$LOADEDMODULES" | tr ':' '\n' \
            | grep -iP '^cuda/' | grep -oP '[0-9]+\.[0-9]+' | head -1)
        if [[ -n "$mod_ver" ]]; then
            echo "$mod_ver"
            return
        fi
    fi

    # 4. nvidia-smi: the driver's maximum supported CUDA version. Always present
    #    on GPU nodes.
    if command -v nvidia-smi &>/dev/null; then
        nvidia-smi 2>/dev/null \
            | grep -oP 'CUDA Version: \K[0-9]+\.[0-9]+' \
            | head -1
        return
    fi

    echo ""
}

# Returns a token naming the HPC job scheduler in use, or an empty string on a
# regular workstation.
detect_hpc_env() {
    [[ -n "${SLURM_JOB_ID:-}" ]]  && { echo "SLURM"; return; }
    [[ -n "${PBS_JOBID:-}" ]]      && { echo "PBS/Torque"; return; }
    [[ -n "${LSB_JOBID:-}" ]]      && { echo "LSF"; return; }
    [[ -n "${SGE_TASK_ID:-}" ]]    && { echo "SGE"; return; }
    # Lmod is present outside a job too, on login nodes
    command -v module &>/dev/null  && { echo "Lmod"; return; }
    [[ -n "${LMOD_CMD:-}" ]]       && { echo "Lmod"; return; }
    echo ""
}

# Maps a CUDA version string such as "12.4" to the URL of the closest PyTorch
# wheel index that is safe on that platform.
resolve_torch_index() {
    local cuda_ver="$1"

    if [[ -z "$cuda_ver" ]]; then
        echo "https://download.pytorch.org/whl/cpu"
        return
    fi

    local major minor
    major=$(echo "$cuda_ver" | cut -d. -f1)
    minor=$(echo "$cuda_ver" | cut -d. -f2)

    # Keep this table in sync with https://download.pytorch.org/whl/ and pick the
    # highest available wheel version that is <= system CUDA.
    #
    # Do not use plain `pip install torch` (default PyPI) even for CUDA 13+. PyPI
    # torch calls _preload_cuda_lib at import time, which dlopen()s the
    # nvidia-cuda-runtime pip package. That library's init code mmaps
    # /dev/nvidiactl, which does not exist on login or CPU nodes, and the process
    # dies with SIGBUS before any user code runs. The pytorch.org wheel index
    # bundles CUDA internally and loads it safely on nodes without GPU hardware.
    # CUDA 13.x drivers are backward-compatible with cu128 code.
    if [[ $major -ge 13 ]] || { [[ $major -eq 12 ]] && [[ $minor -ge 8 ]]; }; then
        echo "https://download.pytorch.org/whl/cu128"
    elif [[ $major -eq 12 && $minor -ge 6 ]]; then
        echo "https://download.pytorch.org/whl/cu126"
    elif [[ $major -eq 12 && $minor -ge 4 ]]; then
        echo "https://download.pytorch.org/whl/cu124"
    elif [[ $major -eq 12 ]]; then
        echo "https://download.pytorch.org/whl/cu121"
    elif [[ $major -eq 11 && $minor -ge 8 ]]; then
        echo "https://download.pytorch.org/whl/cu118"
    else
        warn "CUDA ${cuda_ver} predates PyTorch's oldest supported wheel. Using the CPU build."
        echo "https://download.pytorch.org/whl/cpu"
    fi
}

# Installs torch, torchvision, timm and huggingface-hub into a conda env using a
# CUDA-matched wheel index.
#
# None of these packages may appear in requirements.txt or in pyproject.toml
# [project.dependencies]. A later `pip install -r` or `pip install -e .` would
# resolve them against default PyPI and overwrite the CUDA-matched wheels
# installed here, through the timm -> torchvision -> torch version-lock chain. The
# pip constraints file below is an additional guard.
install_pytorch_packages() {
    local env_name="$1"

    local cuda_ver
    cuda_ver=$(detect_cuda_version)

    local torch_index
    torch_index=$(resolve_torch_index "$cuda_ver")

    local hpc_env
    hpc_env=$(detect_hpc_env)

    if [[ -z "$cuda_ver" ]]; then
        if [[ -n "$hpc_env" ]]; then
            warn "HPC environment detected (${hpc_env}) but no CUDA toolkit found."
            warn "If you need GPU support, load the CUDA module before running this script:"
            warn "  module load cuda && bash install.sh --reinstall"
            warn "Falling back to CPU-only PyTorch for now."
        else
            info "No CUDA detected. Installing CPU-only PyTorch."
        fi
    else
        info "Detected CUDA ${cuda_ver}. Using PyTorch wheel index: ${torch_index}"
        info "PyTorch wheels from this index bundle CUDA internally and do not install"
        info "separate nvidia-* pip packages, avoiding conflicts with the system CUDA."
        [[ -n "$hpc_env" ]] && info "HPC environment (${hpc_env}) confirmed. System CUDA libs will not be overridden."
    fi

    # Step 1: torch + torchvision from the pytorch.org wheel index. Both must come
    # from the same index because PyPI torchvision is version-locked to a specific
    # PyPI torch, for example torchvision 0.26.0 requires torch==2.11.0.
    #
    # TORCH_VERSION pins a version, for systems where the latest torch from the
    # index does not work.
    local index_url="${torch_index:-https://download.pytorch.org/whl/cpu}"
    local torch_spec="torch"
    if [[ -n "${TORCH_VERSION:-}" ]]; then
        torch_spec="torch==${TORCH_VERSION}"
        info "TORCH_VERSION is set. Installing ${torch_spec}"
    fi

    conda run --no-capture-output -n "$env_name" \
        pip install "$torch_spec" torchvision --index-url "$index_url"

    # Step 2: verify torch imports. Some versions crash with SIGBUS at import time
    # on HPC nodes due to incompatible CUDA library preloading.
    if ! conda run --no-capture-output -n "$env_name" \
            python -c "import torch" 2>/dev/null; then
        local bad_ver
        bad_ver=$(conda run --no-capture-output -n "$env_name" \
            pip show torch 2>/dev/null | grep "^Version:" | awk '{print $2}')
        error "torch ${bad_ver} installed but crashes on import (likely SIGBUS from CUDA preloading)."
        error "Specify a known-working version and re-run:"
        error "  TORCH_VERSION=<version> bash install.sh --reinstall"
        error ""
        error "To find a working version, test manually in a clean conda env:"
        error "  pip install torch==<version> --index-url ${index_url}"
        error "  python -c \"import torch; print(torch.__version__, torch.cuda.is_available())\""
        exit 1
    fi
    success "torch $(conda run --no-capture-output -n "$env_name" python -c "import torch; print(torch.__version__)")"

    # Step 3: pin torch + torchvision to the versions just installed. Every later
    # `pip install` uses `-c $TORCH_CONSTRAINTS` so pip's resolver cannot replace
    # them through transitive dependencies.
    #
    # Uses `pip show` rather than `import torch`, so it works even where the
    # installed torch cannot be imported.
    TORCH_CONSTRAINTS="$(mktemp "${TMPDIR:-/tmp}/torch-constraints.XXXXXX")"
    conda run --no-capture-output -n "$env_name" \
        pip show torch | grep "^Version:" | awk '{print "torch=="$2}' \
        >> "$TORCH_CONSTRAINTS"
    conda run --no-capture-output -n "$env_name" \
        pip show torchvision | grep "^Version:" | awk '{print "torchvision=="$2}' \
        >> "$TORCH_CONSTRAINTS"

    info "Pinned PyTorch constraints:"
    while IFS= read -r line; do info "  $line"; done < "$TORCH_CONSTRAINTS"

    # Step 4: timm and huggingface-hub from default PyPI, constrained so pip cannot
    # pull a different torch or torchvision through timm's dependencies.
    conda run --no-capture-output -n "$env_name" \
        pip install timm huggingface-hub -c "$TORCH_CONSTRAINTS"
}

setup_env() {
    local env_name="$1"
    local req_file="$2"
    local python_ver="${3:-3.11}"
    local install_torch="${4:-0}"   # pass "1" for envs that need PyTorch

    if conda_env_exists "$env_name" && [[ $REINSTALL -eq 0 ]]; then
        warn "Conda environment '$env_name' already exists. Skipping creation."
        warn "Run with --reinstall to recreate it from scratch."
        return
    fi

    if conda_env_exists "$env_name" && [[ $REINSTALL -eq 1 ]]; then
        info "Removing existing environment '$env_name' for reinstall..."
        conda env remove -y -n "$env_name"
    fi

    info "Creating conda environment '$env_name' (python=${python_ver})..."
    conda create -y -n "$env_name" python="$python_ver"

    # Install torch before the general requirements so the CUDA-matched wheel is
    # already present when pip processes requirements.txt, which makes pip skip
    # torch as already satisfied.
    if [[ "$install_torch" == "1" ]]; then
        install_pytorch_packages "$env_name"
    fi

    if [[ -f "$req_file" ]]; then
        info "Installing dependencies from $(basename "$req_file") into '$env_name'..."
        conda run --no-capture-output -n "$env_name" \
            pip install -r "$req_file" ${TORCH_CONSTRAINTS:+-c "$TORCH_CONSTRAINTS"}
    else
        warn "No requirements.txt found at $req_file. Skipping dependency install."
    fi
}

# 3. Main FOCUS environment
info "Setting up main FOCUS environment..."
setup_env "FOCUS" "$SCRIPT_DIR/requirements.txt" "3.11" "1"

# Editable install, so the 'focus' console script entry point is registered.
info "Installing FOCUS package into 'FOCUS' environment..."
conda run --no-capture-output -n FOCUS \
    pip install -e "$SCRIPT_DIR" ${TORCH_CONSTRAINTS:+-c "$TORCH_CONSTRAINTS"}
success "FOCUS package installed. You can now run 'focus [--config ...]' after activating the FOCUS env."

# 4. Optional tool environments (tools/<Name>/)
TOOLS_DIR="$SCRIPT_DIR/tools"
if [[ -d "$TOOLS_DIR" ]]; then
    for subfolder in "$TOOLS_DIR"/*/; do
        [[ -d "$subfolder" ]] || continue
        subfolder_name="$(basename "$subfolder")"
        env_name="FOCUS_${subfolder_name}"
        req_file="${subfolder}requirements.txt"

        info "Setting up tool environment '$env_name'..."
        setup_env "$env_name" "$req_file" "3.11"

        # OpenJDK is required by Java-dependent tools such as ASHLAR.
        if ! conda_env_exists "$env_name"; then
            : # env was skipped
        else
            info "Ensuring OpenJDK is present in '$env_name'..."
            conda install -y -n "$env_name" -c conda-forge openjdk 2>/dev/null || \
                warn "OpenJDK install skipped (may already be present or unavailable)."
        fi
    done
else
    info "No 'tools/' directory found. Skipping tool environments."
fi

[[ -n "$TORCH_CONSTRAINTS" && -f "$TORCH_CONSTRAINTS" ]] && rm -f "$TORCH_CONSTRAINTS"

echo
success "All environments are ready."
echo
echo "  To start FOCUS, activate the environment and run:"
echo "    conda activate FOCUS"
echo "    focus                   # launches the GUI"
echo "    focus --config /path/to/config.json   # CLI mode"
echo
