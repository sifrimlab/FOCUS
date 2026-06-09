#!/bin/bash
# ── FOCUS — SLURM batch submission script ─────────────────────────────────────
#
# Runs the FOCUS pipeline non-interactively (CLI mode) on an HPC cluster.
# By default it uses the Singularity/Apptainer container with GPU access — the
# recommended deployment on most clusters. A host-install (conda) variant is
# included at the bottom; comment one block and uncomment the other to switch.
#
# Submit with:
#   sbatch slurm/submit_focus.sbatch
#
# Override the config/SIF paths without editing this file:
#   sbatch --export=ALL,FOCUS_CONFIG=/scratch/$USER/proj/config.json,FOCUS_SIF=/scratch/$USER/focus.sif \
#          slurm/submit_focus.sbatch
#
# Dry-run the directives (syntax check, no scheduling):
#   sbatch --test-only slurm/submit_focus.sbatch
#
# ── Resource request ──────────────────────────────────────────────────────────
# Tune these to your data. See docs/deployment/hpc.md (Performance Tuning):
#   - cpus-per-task  → Raman preprocessing parallelism (config max_workers)
#   - mem            → MSI preprocessing peaks at ~40–100 GB per sample
#   - gres=gpu       → only needed for feature_extraction (GigaPath) registration
#SBATCH --job-name=focus_pipeline
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --gres=gpu:1                 # remove this line if NOT using feature_extraction
#SBATCH --time=24:00:00
#SBATCH --output=focus_%j.log        # %j = job ID

set -euo pipefail

# ── User-configurable paths (override via --export, see header) ───────────────
: "${FOCUS_CONFIG:=/scratch/$USER/myproject/focus_config.json}"
: "${FOCUS_SIF:=/scratch/$USER/focus.sif}"
# Top-level directory to bind-mount into the container (must contain your data
# and the config). Mounted at the same absolute path inside the container.
: "${FOCUS_WORKDIR:=/scratch/$USER}"

echo "[FOCUS] Job $SLURM_JOB_ID on $(hostname)"
echo "[FOCUS] Config:  $FOCUS_CONFIG"
echo "[FOCUS] SIF:     $FOCUS_SIF"
echo "[FOCUS] Workdir: $FOCUS_WORKDIR"

# ══════════════════════════════════════════════════════════════════════════════
# Option A — Singularity / Apptainer container (default, recommended)
# ══════════════════════════════════════════════════════════════════════════════
module load singularity 2>/dev/null || module load apptainer 2>/dev/null || true
module load cuda        2>/dev/null || true   # only needed for feature_extraction

RUNTIME="$(command -v singularity || command -v apptainer)"

# Drop --nv if this run does not use feature_extraction (and remove --gres above).
"$RUNTIME" run \
    --bind "$FOCUS_WORKDIR" \
    --nv \
    "$FOCUS_SIF" \
    --config "$FOCUS_CONFIG"

# ══════════════════════════════════════════════════════════════════════════════
# Option B — Host install (conda env created by install.sh). To use this path,
# comment out Option A above and uncomment the block below.
# ══════════════════════════════════════════════════════════════════════════════
# module load miniconda3 2>/dev/null || module load anaconda3 2>/dev/null || true
# module load cuda       2>/dev/null || true   # only needed for feature_extraction
#
# # 'conda activate' needs the shell hook in non-interactive shells:
# source "$(conda info --base)/etc/profile.d/conda.sh"
# conda activate FOCUS
# focus --config "$FOCUS_CONFIG"

echo "[FOCUS] Done. Pipeline log: ${FOCUS_WORKDIR}/.../focus.log (see <dataset_path>/focus.log)"
