#!/usr/bin/env bash
# FOCUS installation script — macOS and Linux
# Usage: bash install.sh [--reinstall]

set -euo pipefail

REINSTALL=0
for arg in "$@"; do
    [[ "$arg" == "--reinstall" ]] && REINSTALL=1
done

# ── Helper: print coloured status messages ────────────────────────────────────
info()    { echo -e "\033[1;34m[INFO]\033[0m  $*"; }
success() { echo -e "\033[1;32m[OK]\033[0m    $*"; }
warn()    { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
error()   { echo -e "\033[1;31m[ERROR]\033[0m $*" >&2; }

# ── Resolve the directory this script lives in ────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 1. Verify conda is available ──────────────────────────────────────────────
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

# ── Helper: check whether a named conda env exists ────────────────────────────
conda_env_exists() {
    conda info --envs | awk '{print $1}' | grep -Fxq "$1"
}

# ── 2. Detect system CUDA and resolve the right PyTorch wheel index ───────────
#
# Problem: `pip install torch` (default PyPI) installs nvidia-cuda-*-cu12 packages
# as separate pip dependencies.  On HPC systems with a system-managed CUDA at a
# different version, these bundled libraries conflict at the dynamic-linker level
# and cause bus errors / segfaults on import.
#
# Solution: install torch from PyTorch's own wheel index
# (https://download.pytorch.org/whl/cuXXX).  Those wheels bundle CUDA inside the
# wheel's own lib directory and do NOT create separate nvidia-* pip packages, so
# they coexist safely with any system CUDA.
#
# Detection priority:
#   1. nvcc --version  — reports the actual CUDA *toolkit* version (most accurate)
#   2. nvidia-smi      — reports the driver's maximum supported CUDA version
#   3. No GPU found    — CPU-only wheel
#
# The detected version is mapped to the nearest PyTorch wheel that is ≤ that
# version, because CUDA drivers are backward-compatible (a driver for CUDA 13.x
# can run code built for CUDA 12.x).

detect_cuda_version() {
    # 1. nvcc in PATH — most accurate (toolkit version)
    if command -v nvcc &>/dev/null; then
        nvcc --version 2>/dev/null \
            | grep -oP 'release \K[0-9]+\.[0-9]+' \
            | head -1
        return
    fi

    # 2. CUDA_HOME / CUDA_PATH / CUDA_ROOT — set by Lmod when a cuda module is loaded.
    #    Try nvcc from there first, then fall back to version.txt / version.json.
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

    # 3. LOADEDMODULES — Lmod / Environment Modules sets this to a colon-separated
    #    list such as "gcc/12.2:cuda/12.8:...". Parse the cuda entry directly.
    if [[ -n "${LOADEDMODULES:-}" ]]; then
        local mod_ver
        mod_ver=$(echo "$LOADEDMODULES" | tr ':' '\n' \
            | grep -iP '^cuda/' | grep -oP '[0-9]+\.[0-9]+' | head -1)
        if [[ -n "$mod_ver" ]]; then
            echo "$mod_ver"
            return
        fi
    fi

    # 4. nvidia-smi — driver's maximum supported CUDA version (always present on GPU nodes)
    if command -v nvidia-smi &>/dev/null; then
        nvidia-smi 2>/dev/null \
            | grep -oP 'CUDA Version: \K[0-9]+\.[0-9]+' \
            | head -1
        return
    fi

    echo ""
}

# Returns a short token describing the HPC job scheduler in use, or empty string
# on a regular workstation/laptop.
detect_hpc_env() {
    [[ -n "${SLURM_JOB_ID:-}" ]]  && { echo "SLURM"; return; }
    [[ -n "${PBS_JOBID:-}" ]]      && { echo "PBS/Torque"; return; }
    [[ -n "${LSB_JOBID:-}" ]]      && { echo "LSF"; return; }
    [[ -n "${SGE_TASK_ID:-}" ]]    && { echo "SGE"; return; }
    # Lmod present even outside a job (login nodes)
    command -v module &>/dev/null  && { echo "Lmod"; return; }
    [[ -n "${LMOD_CMD:-}" ]]       && { echo "Lmod"; return; }
    echo ""
}

# Maps a CUDA version string (e.g. "12.4") to the URL of the closest PyTorch
# wheel index that is safe to use on that platform.
resolve_torch_index() {
    local cuda_ver="$1"

    if [[ -z "$cuda_ver" ]]; then
        echo "https://download.pytorch.org/whl/cpu"
        return
    fi

    local major minor
    major=$(echo "$cuda_ver" | cut -d. -f1)
    minor=$(echo "$cuda_ver" | cut -d. -f2)

    # Keep this table in sync with https://download.pytorch.org/whl/
    # Always pick the highest available wheel version that is ≤ system CUDA.
    if   [[ $major -ge 13 ]] || { [[ $major -eq 12 ]] && [[ $minor -ge 8 ]]; }; then
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
        warn "CUDA ${cuda_ver} predates PyTorch's oldest supported wheel — using CPU build."
        echo "https://download.pytorch.org/whl/cpu"
    fi
}

# Install torch, timm, and huggingface-hub into a conda env using the wheel
# index that matches the system CUDA.  These three packages are handled
# separately because:
#   - torch must come from pytorch.org's index to avoid bundled nvidia-* packages
#   - timm and huggingface-hub are installed from default PyPI afterward so
#     they always get the latest version
#
# When requirements.txt is later installed, pip sees torch already satisfied and
# skips it, so no conflicting re-download occurs.
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
            info "No CUDA detected — installing CPU-only PyTorch."
        fi
    else
        info "Detected CUDA ${cuda_ver} — using PyTorch wheel index: ${torch_index}"
        info "PyTorch wheels from this index bundle CUDA internally and do not install"
        info "separate nvidia-* pip packages, avoiding conflicts with the system CUDA."
        [[ -n "$hpc_env" ]] && info "HPC environment (${hpc_env}) confirmed — system CUDA libs will not be overridden."
    fi

    # Step 1: torch from the CUDA-matched pytorch.org index
    conda run --no-capture-output -n "$env_name" \
        pip install torch --index-url "$torch_index"

    # Step 2: timm and huggingface-hub from default PyPI
    conda run --no-capture-output -n "$env_name" \
        pip install timm huggingface-hub
}

# ── Helper: create (or skip) a conda env and install from requirements.txt ───
setup_env() {
    local env_name="$1"
    local req_file="$2"
    local python_ver="${3:-3.11}"
    local install_torch="${4:-0}"   # pass "1" for envs that need PyTorch

    if conda_env_exists "$env_name" && [[ $REINSTALL -eq 0 ]]; then
        warn "Conda environment '$env_name' already exists — skipping creation."
        warn "Run with --reinstall to recreate it from scratch."
        return
    fi

    if conda_env_exists "$env_name" && [[ $REINSTALL -eq 1 ]]; then
        info "Removing existing environment '$env_name' for reinstall..."
        conda env remove -y -n "$env_name"
    fi

    info "Creating conda environment '$env_name' (python=${python_ver})..."
    conda create -y -n "$env_name" python="$python_ver"

    # Install torch/timm/huggingface-hub before the general requirements so
    # that the CUDA-matched torch wheel is already present when pip processes
    # requirements.txt (pip will then skip torch as already satisfied).
    if [[ "$install_torch" == "1" ]]; then
        install_pytorch_packages "$env_name"
    fi

    if [[ -f "$req_file" ]]; then
        info "Installing dependencies from $(basename "$req_file") into '$env_name'..."
        conda run --no-capture-output -n "$env_name" pip install -r "$req_file"
    else
        warn "No requirements.txt found at $req_file — skipping dependency install."
    fi
}

# ── 3. Main FOCUS environment ─────────────────────────────────────────────────
info "Setting up main FOCUS environment..."
setup_env "FOCUS" "$SCRIPT_DIR/requirements.txt" "3.11" "1"

# Install the FOCUS package itself as an editable install so that
# the 'focus' console script entry point is registered.
info "Installing FOCUS package into 'FOCUS' environment..."
conda run --no-capture-output -n FOCUS pip install -e "$SCRIPT_DIR"
success "FOCUS package installed. You can now run 'focus [--config ...]' after activating the FOCUS env."

# ── 4. Optional tool environments (tools/<Name>/) ─────────────────────────────
TOOLS_DIR="$SCRIPT_DIR/tools"
if [[ -d "$TOOLS_DIR" ]]; then
    for subfolder in "$TOOLS_DIR"/*/; do
        [[ -d "$subfolder" ]] || continue
        subfolder_name="$(basename "$subfolder")"
        env_name="FOCUS_${subfolder_name}"
        req_file="${subfolder}requirements.txt"

        info "Setting up tool environment '$env_name'..."
        setup_env "$env_name" "$req_file" "3.11"

        # Install OpenJDK for Java-dependent tools (e.g. ASHLAR)
        if ! conda_env_exists "$env_name"; then
            : # env was skipped
        else
            info "Ensuring OpenJDK is present in '$env_name'..."
            conda install -y -n "$env_name" -c conda-forge openjdk 2>/dev/null || \
                warn "OpenJDK install skipped (may already be present or unavailable)."
        fi
    done
else
    info "No 'tools/' directory found — skipping tool environments."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo
success "All environments are ready."
echo
echo "  To start FOCUS, activate the environment and run:"
echo "    conda activate FOCUS"
echo "    focus                   # launches the GUI"
echo "    focus --config /path/to/config.json   # CLI mode"
echo
