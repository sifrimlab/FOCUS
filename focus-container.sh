#!/usr/bin/env bash
# FOCUS container launcher for macOS, Linux and WSL2
#
# Runs FOCUS inside Docker, Podman or Singularity/Apptainer.
# The mount directory is mounted at the *same absolute path* inside the
# container, so all paths in your config file require no translation.
#
# Usage:
#   focus-container.sh [OPTIONS] [-- FOCUS_ARGS...]
#
# Options:
#   -r, --runtime  docker|podman|singularity|apptainer
#                  (default: first available in that order)
#   -m, --mount    DIR   host directory to bind-mount (repeatable)
#                        default: current working directory
#   -i, --image    IMAGE docker/podman image name (default: focus)
#   -s, --sif      FILE  singularity .sif file     (default: ./focus.sif)
#   --gpu                pass GPU flags (--gpus all / --nv) at run time; with
#                        --build it also bakes a CUDA PyTorch build into the image
#   --build              build the image/sif before running (CPU torch by
#                        default; CUDA when --gpu is also set; override the
#                        baked wheel index with the TORCH_INDEX env var)
#   -h, --help           print this help
#
# Examples:
#   # GUI mode: open http://localhost:5050 in your browser
#   focus-container.sh --mount /data/mylab
#
#   # CLI mode
#   focus-container.sh --mount /data/mylab -- --config /data/mylab/project/config.json
#
#   # Use podman explicitly
#   focus-container.sh --runtime podman --mount /data/mylab -- --config /data/mylab/project/config.json
#
#   # Singularity on HPC
#   focus-container.sh --runtime singularity --mount /scratch/mylab -- --config /scratch/mylab/project/config.json

set -euo pipefail

# Defaults
RUNTIME=""
MOUNTS=()
IMAGE="focus"
SIF="$(dirname "$0")/focus.sif"
GPU=0
BUILD=0
FOCUS_ARGS=()
PORT=5050

# Colours
info()  { echo -e "\033[1;34m[INFO]\033[0m  $*"; }
ok()    { echo -e "\033[1;32m[OK]\033[0m    $*"; }
warn()  { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
die()   { echo -e "\033[1;31m[ERROR]\033[0m $*" >&2; exit 1; }

usage() {
    # Prints the header comment block: from line 3 to the first non-comment line.
    sed -n '3,${/^[^#]/q;p;}' "$0" | sed 's/^# \{0,2\}//'
    exit 0
}

# Argument parsing
while [[ $# -gt 0 ]]; do
    case "$1" in
        -r|--runtime)   RUNTIME="$2";        shift 2 ;;
        -m|--mount)     MOUNTS+=("$2");      shift 2 ;;
        -i|--image)     IMAGE="$2";          shift 2 ;;
        -s|--sif)       SIF="$2";            shift 2 ;;
        --gpu)          GPU=1;               shift   ;;
        --build)        BUILD=1;             shift   ;;
        -h|--help)      usage                        ;;
        --)             shift; FOCUS_ARGS=("$@"); break ;;
        *)              die "Unknown option: $1. Use -- before FOCUS arguments." ;;
    esac
done

# Default mount: current directory
[[ ${#MOUNTS[@]} -eq 0 ]] && MOUNTS+=("$(pwd)")

# Resolve all mounts to absolute paths
for i in "${!MOUNTS[@]}"; do
    MOUNTS[$i]="$(cd "${MOUNTS[$i]}" && pwd)"
done

# Runtime detection
detect_runtime() {
    for rt in docker podman singularity apptainer; do
        command -v "$rt" &>/dev/null && { echo "$rt"; return; }
    done
    die "No container runtime found.\n\n  Install one of: Docker, Podman, Singularity/Apptainer\n\n  Docker:    https://docs.docker.com/get-docker/\n  Podman:    https://podman.io/docs/installation\n  Apptainer: https://apptainer.org/docs/user/latest/quick_start.html"
}

[[ -z "$RUNTIME" ]] && RUNTIME="$(detect_runtime)"
ok "Using runtime: $RUNTIME"

# Detect GUI vs CLI mode
GUI_MODE=1
for arg in "${FOCUS_ARGS[@]}"; do
    [[ "$arg" == "--config" || "$arg" == "-c" ]] && GUI_MODE=0 && break
done

# Build
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ $BUILD -eq 1 ]]; then
    # PyTorch wheel index baked into the image: CPU by default, CUDA when
    # --gpu is requested at build time. Override explicitly with TORCH_INDEX.
    # (See install.sh resolve_torch_index for the cuXXX → CUDA-version mapping.)
    if [[ -n "${TORCH_INDEX:-}" ]]; then
        BUILD_TORCH_INDEX="$TORCH_INDEX"
    elif [[ $GPU -eq 1 ]]; then
        BUILD_TORCH_INDEX="https://download.pytorch.org/whl/cu128"
    else
        BUILD_TORCH_INDEX="https://download.pytorch.org/whl/cpu"
    fi

    case "$RUNTIME" in
        docker|podman)
            info "Building image '$IMAGE' (TORCH_INDEX=$BUILD_TORCH_INDEX)..."
            "$RUNTIME" build --build-arg "TORCH_INDEX=$BUILD_TORCH_INDEX" -t "$IMAGE" "$SCRIPT_DIR"
            ;;
        singularity|apptainer)
            info "Building SIF '$SIF' (TORCH_INDEX=$BUILD_TORCH_INDEX)..."
            # --build-arg requires Apptainer >= 1.1 / SingularityCE >= 3.11.
            "$RUNTIME" build --build-arg "TORCH_INDEX=$BUILD_TORCH_INDEX" "$SIF" "$SCRIPT_DIR/focus.def"
            ;;
    esac
fi

# Mount flags
# For Docker/Podman: -v /abs/path:/abs/path  (same path both sides)
# For Singularity:   --bind /abs/path        (Singularity maps to same path by default)

build_docker_mounts() {
    local flags=()
    for m in "${MOUNTS[@]}"; do
        flags+=("-v" "${m}:${m}")
    done
    echo "${flags[@]}"
}

build_singularity_binds() {
    local binds=""
    for m in "${MOUNTS[@]}"; do
        binds="${binds:+${binds},}${m}"
    done
    echo "$binds"
}

# Run
case "$RUNTIME" in

    docker|podman)
        # Verify image exists (unless --build was used)
        if ! "$RUNTIME" image inspect "$IMAGE" &>/dev/null; then
            die "Image '$IMAGE' not found.\n  Build it first:\n    $RUNTIME build -t $IMAGE $SCRIPT_DIR\n  Or pass --build to build automatically."
        fi

        DOCKER_ARGS=(
            "--rm"
            "--interactive"
            "--tty"
        )

        # Mount flags (same path on both sides)
        while IFS= read -r -d '' flag; do
            DOCKER_ARGS+=("$flag")
        done < <(printf '%s\0' $(build_docker_mounts))

        # GUI: expose port
        [[ $GUI_MODE -eq 1 ]] && DOCKER_ARGS+=("-p" "${PORT}:${PORT}")

        # GPU
        [[ $GPU -eq 1 ]] && DOCKER_ARGS+=("--gpus" "all")

        info "Mounted directories (same path inside container):"
        for m in "${MOUNTS[@]}"; do info "  $m"; done
        [[ $GUI_MODE -eq 1 ]] && info "GUI mode: open http://localhost:${PORT} in your browser"

        "$RUNTIME" run "${DOCKER_ARGS[@]}" "$IMAGE" "${FOCUS_ARGS[@]}"
        ;;

    singularity|apptainer)
        [[ ! -f "$SIF" ]] && die "SIF file not found: $SIF\n  Build it first:\n    $RUNTIME build $SIF $SCRIPT_DIR/focus.def\n  Or pass --build to build automatically."

        SING_ARGS=("--bind" "$(build_singularity_binds)")

        # GPU
        [[ $GPU -eq 1 ]] && SING_ARGS+=("--nv")

        info "Mounted directories (same path inside container):"
        for m in "${MOUNTS[@]}"; do info "  $m"; done
        [[ $GUI_MODE -eq 1 ]] && info "GUI mode: open http://localhost:${PORT} in your browser"
        [[ $GUI_MODE -eq 1 ]] && warn "On HPC: use 'ssh -L ${PORT}:localhost:${PORT} <host>' to access the GUI from your local machine."

        "$RUNTIME" run "${SING_ARGS[@]}" "$SIF" "${FOCUS_ARGS[@]}"
        ;;

    *)
        die "Unsupported runtime: $RUNTIME"
        ;;
esac