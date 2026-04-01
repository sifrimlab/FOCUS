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

# ── Helper: create (or skip) a conda env and install from requirements.txt ───
setup_env() {
    local env_name="$1"
    local req_file="$2"
    local python_ver="${3:-3.11}"

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

    if [[ -f "$req_file" ]]; then
        info "Installing dependencies from $(basename "$req_file") into '$env_name'..."
        conda run --no-capture-output -n "$env_name" pip install -r "$req_file"
    else
        warn "No requirements.txt found at $req_file — skipping dependency install."
    fi
}

# ── 2. Main FOCUS environment ─────────────────────────────────────────────────
info "Setting up main FOCUS environment..."
setup_env "FOCUS" "$SCRIPT_DIR/requirements.txt" "3.11"

# Install the FOCUS package itself as an editable install so that
# the 'focus' console script entry point is registered.
info "Installing FOCUS package into 'FOCUS' environment..."
conda run --no-capture-output -n FOCUS pip install -e "$SCRIPT_DIR"
success "FOCUS package installed. You can now run 'focus [--config ...]' after activating the FOCUS env."

# ── 3. Optional tool environments (tools/<Name>/) ─────────────────────────────
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