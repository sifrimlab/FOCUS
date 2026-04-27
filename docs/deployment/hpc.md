# HPC and Headless Servers

FOCUS runs fully on headless Linux servers and HPC clusters. There are two deployment strategies: a **host installation** (via `install.sh` inside a conda environment) and a **container deployment** (Singularity/Apptainer, the standard on most HPC systems). Both support CLI-only (non-interactive) operation and SLURM batch job submission.

---

## Host Installation on HPC

If your cluster has conda (or Miniconda) available as a module, you can install FOCUS directly:

```bash
module load miniconda3   # or: module load anaconda3
git clone https://github.com/sifrimlab/FOCUS.git
cd FOCUS
bash install.sh
```

To activate and run:

```bash
conda activate FOCUS
focus --config /scratch/$USER/myproject/focus_config.json
```

!!! warning "Load CUDA before running the install script"
    If your pipeline will use `feature_extraction` (GPU-based registration), load the CUDA module **before** running `install.sh` so the script can detect the correct PyTorch wheel index:

    ```bash
    module load cuda
    bash install.sh
    ```

    See [Local Installation — Troubleshooting PyTorch / CUDA on HPC](local.md#troubleshooting-pytorch-cuda-on-hpc) for full details on CUDA detection and the `TORCH_VERSION` escape hatch.

---

## Singularity / Apptainer (Recommended for HPC)

Singularity (and its successor Apptainer) is the container runtime of choice on HPC clusters, as it does not require root at runtime and integrates with the host filesystem via `--bind`.

### Building the SIF image

Build on a machine where you have root or fakeroot access, then copy the resulting `.sif` to the cluster:

```bash
# From the repository root:
singularity build focus.sif focus.def
# or
apptainer build focus.sif focus.def
```

!!! tip "Fakeroot build (no root required)"
    ```bash
    singularity build --fakeroot focus.sif focus.def
    apptainer build --fakeroot focus.sif focus.def
    ```

The `focus.def` definition bootstraps from `python:3.11-slim`, installs system libraries and Python dependencies, and places the FOCUS package in a dedicated venv at `/opt/focus-env/`.

### Running manually on the cluster

**CLI mode (recommended for HPC):**

```bash
singularity run --bind /scratch/mylab focus.sif --config /scratch/mylab/project/focus_config.json
```

`--bind /path` maps the host directory to the **same absolute path** inside the container. All paths in your config file stay unchanged.

**With GPU:**

```bash
singularity run --bind /scratch/mylab --nv focus.sif --config /scratch/mylab/project/focus_config.json
```

`--nv` passes NVIDIA GPU access (and the required CUDA libraries from the host) into the Singularity container.

**Using `focus-container.sh` on the cluster:**

```bash
bash focus-container.sh --runtime singularity --mount /scratch/mylab -- --config /scratch/mylab/project/focus_config.json
bash focus-container.sh --runtime singularity --gpu --mount /scratch/mylab -- --config /scratch/mylab/project/focus_config.json
```

---

## SLURM Batch Script Example

Save the following as `submit_focus.sh` and submit with `sbatch submit_focus.sh`:

```bash
#!/bin/bash
#SBATCH --job-name=focus_pipeline
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --gres=gpu:1          # remove this line if not using feature_extraction
#SBATCH --time=24:00:00
#SBATCH --output=focus_%j.log

# Load required modules
module load singularity        # or: apptainer
module load cuda               # only needed if using feature_extraction

# Run the pipeline in CLI mode
singularity run \
    --bind /scratch/$USER \
    --nv \
    /scratch/$USER/focus.sif \
    --config /scratch/$USER/myproject/focus_config.json
```

!!! note "Removing GPU allocation"
    If your pipeline does not use `feature_extraction` registration, remove `#SBATCH --gres=gpu:1` and the `--nv` flag. All other pipeline stages (preprocessing, alignment, interpolation-based registration, compilation) run on CPU.

### Host-install variant (no container)

```bash
#!/bin/bash
#SBATCH --job-name=focus_pipeline
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --gres=gpu:1
#SBATCH --time=24:00:00
#SBATCH --output=focus_%j.log

module load miniconda3
module load cuda

conda activate FOCUS
focus --config /scratch/$USER/myproject/focus_config.json
```

---

## GUI on Headless Servers (SSH Port Forwarding)

The alignment GUI requires a web browser. On a headless server, forward the port over SSH to your local machine:

**Step 1 — On your local machine, open an SSH tunnel:**

```bash
ssh -L 5050:localhost:5050 -L 8000:localhost:8000 user@hpc-cluster.example.org
```

**Step 2 — On the server, start FOCUS in GUI mode:**

=== "Host install"

    ```bash
    conda activate FOCUS
    focus
    ```

=== "Singularity container"

    ```bash
    singularity run --bind /scratch/$USER focus.sif
    ```

    !!! tip
        Singularity inherits the host network namespace — no `-p` port mapping is needed. Port 5050 on the container is the same as port 5050 on the cluster node.

**Step 3 — Open `http://localhost:5050`** in your local browser. The tunnel forwards the connection transparently to the cluster.

!!! warning "HPC warning from the launcher script"
    When running Singularity in GUI mode, `focus-container.sh` automatically prints:

    ```
    [WARN]  On HPC: use 'ssh -L 5050:localhost:5050 <host>' to access the GUI from your local machine.
    ```

---

## Disabling the Alignment GUI (Fully Automated Runs)

For pipelines where manual alignment is not needed or has already been done, disable the interactive alignment step entirely by setting `"alignment_strategy": "pre_aligned"` for all non-reference modalities in your config, or by turning off alignment and registration stages:

```json
{
  "perform_alignment": false,
  "perform_registration": false
}
```

This allows FOCUS to run from start to finish in a batch job with no human interaction.

---

## Performance Tuning

| Stage | Notes |
|---|---|
| **Raman preprocessing** | Parallelised across workers. Increase `max_workers` in the config (default: 8) to match available CPUs. |
| **MSI preprocessing** | Peak RAM scales with a single sample. Typical tissue samples use 40–50 GB; large tissue sections may require up to 100 GB. Request memory accordingly when submitting batch jobs. |
| **Feature extraction** | Requires an NVIDIA GPU. Ensure `--nv` (Singularity) or `--gpus all` (Docker/Podman) is set, and that `nvidia-smi` returns the expected device inside the container. |
| **Compilation** | CPU-bound and I/O-bound. Fast NVMe storage for `dataset_path` significantly reduces runtime for large datasets. |

### Log files

FOCUS writes a log to `<dataset_path>/focus.log`. Inspect this file when diagnosing pipeline failures in batch jobs:

```bash
tail -f /scratch/$USER/myproject/focus.log
```

---

## Platform Compatibility Summary

| Feature | Linux (desktop) | Linux (headless / HPC) |
|---|:---:|:---:|
| Host install (`install.sh`) | Yes | Yes |
| GUI mode | Yes | via SSH tunnel |
| CLI mode | Yes | Yes |
| Docker / Podman | Yes | Yes |
| Singularity / Apptainer | Yes | Yes |
| GPU acceleration (`feature_extraction`) | Yes | Yes |
