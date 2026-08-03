# Installation Guide

FOCUS is distributed as a **source repository**, not a PyPI package. You install it
by cloning the repository and running the provided installer, which creates a conda
environment named `FOCUS` and registers a single `focus` command. There is no
`pip install focus` from PyPI.

This page is an overview. For full, platform-specific detail see:

- [Host Machine Install](deployment/local.md): the recommended path for most users
- [Containers (Docker / Podman / Singularity)](deployment/containers.md)
- [HPC & Headless Servers](deployment/hpc.md)

---

## System Requirements

| Component | Requirement | Notes |
|-----------|-------------|-------|
| **Operating System** | Windows 10/11, macOS 12+, Linux (Ubuntu 20.04+) | |
| **Python** | 3.11 | Created and managed for you by conda |
| **Conda** | Miniconda or Anaconda | Required: the installer builds a conda env |
| **RAM** | 64 GB recommended; up to ~100 GB for large tissue samples | Peak usage scales with a single sample, not the whole dataset |
| **Storage** | 20 GB+ free | For the conda environments and intermediate outputs |

### GPU (optional)

GPU acceleration is required **only** for the `feature_extraction` registration type,
which downloads and runs the Prov-GigaPath deep-learning model. All other pipeline
stages run on CPU.

| Component | Requirement | Notes |
|-----------|-------------|-------|
| **GPU** | NVIDIA with CUDA 11.8+ | AMD/Intel GPUs and Apple Silicon (no CUDA/MPS) are not supported for this stage |
| **Driver** | Recent NVIDIA driver | The installer auto-detects the CUDA version and picks the matching PyTorch wheel |

---

## Method 1: Host Machine Installation (recommended)

### Step 1: Install conda

If conda is not already available, install [Miniconda](https://docs.conda.io/en/latest/miniconda.html)
or [Anaconda](https://www.anaconda.com/download).

!!! tip "Windows users"
    Use the **Anaconda Prompt** (or a PowerShell session with conda initialised) for
    every step below. The plain Command Prompt does not have conda on its PATH.

### Step 2: Clone the repository

```bash
git clone https://github.com/sifrimlab/FOCUS.git
cd FOCUS
```

### Step 3: Run the install script

=== "macOS / Linux"

    ```bash
    bash install.sh
    ```

=== "Windows"

    Open an **Anaconda Prompt (PowerShell)** and run:

    ```powershell
    .\install.ps1
    ```

    `install.ps1` is the full Windows installer and does everything `install.sh`
    does, including CUDA detection and the CUDA-matched PyTorch install. A
    `install.bat` shim that forwards to it is also provided, so `install.bat`
    works from a classic Command Prompt.

The script (`install.sh` / `install.ps1`):

1. Verifies conda is available.
2. Detects your system CUDA version (via `nvcc`, `nvidia-smi`, or Lmod module
   variables) and installs a matching PyTorch build from `download.pytorch.org/whl/`.
   PyTorch is **not** listed in `requirements.txt`; it is installed separately so the
   CUDA-bundled wheels do not conflict with the system CUDA on HPC nodes.
3. Creates a `FOCUS` conda environment (Python 3.11) and installs the dependencies
   from `requirements.txt`.
4. Installs the `focus` package in editable mode (`pip install -e .`), registering the
   `focus` command.
5. Scans the `tools/` directory and creates one auxiliary environment per subfolder:
   currently `FOCUS_ASHLAR` and `FOCUS_BaSiCpy` (both Python 3.11), used for Raman
   spectroscopy preprocessing. OpenJDK is installed into `FOCUS_ASHLAR` automatically.

These auxiliary environments are created by the default installer invocation; no extra
flag is needed.

### Step 4: Activate the environment

```bash
conda activate FOCUS
```

### Step 5: Verify

```bash
focus --help
```

You should see the FOCUS usage message listing the `--config` and `--debug` options.

---

## Method 2: Container Deployment

FOCUS ships a `Dockerfile`, a Singularity/Apptainer definition (`focus.def`), and
launcher scripts (`focus-container.sh` for macOS/Linux/WSL2, `focus-container.ps1`
for Windows PowerShell). Containers are ideal for reproducibility, machines without
conda, and HPC.

```bash
# Build and run the GUI (opens http://localhost:5050)
bash focus-container.sh --build --mount /path/to/your/data

# Build a GPU image (bakes in a CUDA PyTorch build) and run with GPU access
bash focus-container.sh --build --gpu --mount /path/to/your/data -- --config /path/to/your/data/project/focus_config.json

# CLI mode: everything after `--` is passed to the focus command inside the container
bash focus-container.sh --mount /path/to/your/data -- --config /path/to/your/data/project/focus_config.json
```

The images bundle PyTorch: CPU by default, or a CUDA build when you pass `--gpu`
to `--build` (or set the `TORCH_INDEX` build arg directly). See
[Containers (Docker / Podman / Singularity)](deployment/containers.md) for the full
flag reference, GPU options, Windows/PowerShell usage, and Singularity/Apptainer builds.

---

## Reinstalling and Updating

To wipe and recreate all environments from scratch:

=== "macOS / Linux"

    ```bash
    bash install.sh --reinstall
    ```

=== "Windows"

    ```powershell
    .\install.ps1 -Reinstall
    # or, from a Command Prompt: install.bat --reinstall
    ```

`--reinstall` (`-Reinstall` in PowerShell) is the only flag the install scripts
accept. To update an existing checkout:

```bash
cd FOCUS
git pull origin main
bash install.sh --reinstall          # macOS / Linux
# Windows: .\install.ps1 -Reinstall  (or install.bat --reinstall)
```

---

## GPU and PyTorch on HPC

The installer auto-detects CUDA and installs a matching PyTorch build. On HPC clusters
you may need to `module load cuda` before running it, or pin a known-good PyTorch
version via the `TORCH_VERSION` environment variable:

```bash
module load cuda
TORCH_VERSION=2.9.0 bash install.sh --reinstall
# Windows (PowerShell): $env:TORCH_VERSION='2.9.0'; .\install.ps1 -Reinstall
```

`TORCH_VERSION` is the only FOCUS-specific environment variable, and it is only read at
install time (both `install.sh` and `install.ps1` honour it). See [Local Installation: Troubleshooting PyTorch / CUDA on HPC](deployment/local.md#troubleshooting-pytorch-cuda-on-hpc)
for CUDA detection details and the SIGBUS workaround.

A HuggingFace token is required the first time the `feature_extraction` registration is
used, to download the Prov-GigaPath model weights. Provide it via the `huggingface_token`
field in your config, or in the GUI configuration panel.

---

## Developer (manual) installs

The install scripts already perform an editable install (`pip install -e .`), so a
separate manual procedure is rarely needed. If you do install by hand into your own
environment, note that **PyTorch, torchvision, timm, and huggingface-hub are not in
`requirements.txt`**. They are installed by `install.sh` / `install.ps1` (and by the
container images) from the CUDA-matched wheel index. A bare
`pip install -r requirements.txt && pip install -e .` will leave the
`feature_extraction` stage non-functional until you install a matching PyTorch build
yourself (see the [PyTorch / CUDA notes](deployment/local.md#troubleshooting-pytorch-cuda-on-hpc)).

---

## Preparing Your Data

FOCUS expects a two-level directory layout under your `dataset_path`: each first-level
subdirectory is a sample, and each second-level subdirectory must be named exactly
after a modality `name` in your config (case-sensitive).

```
<dataset_path>/
├── sample_001/
│   ├── microscopy/      # .tiff / .tif / .ome.tiff / .ome.tif / .qptiff / .czi
│   ├── msi/             # pos/ (and optionally neg/), each with .imzML + .ibd
│   ├── raman/           # .lif
│   └── st/              # .h5ad
└── sample_002/
    └── ...
```

FOCUS writes its outputs (including the final `merged/multimodal_dataset.h5mu`) back
into `dataset_path`; you do not create output directories yourself. See
[Preparing Your Data](user_guide/data_preparation.md) for the full per-modality file
requirements.

---

## Uninstallation

```bash
conda deactivate
conda env remove -n FOCUS
conda env remove -n FOCUS_ASHLAR
conda env remove -n FOCUS_BaSiCpy
rm -rf FOCUS
```

For containers, remove the image (`docker rmi focus` / `podman rmi focus`) and any
`focus.sif` file.

---

## Troubleshooting

**`conda: command not found`**: restart your terminal or `source ~/.bashrc` (Linux/macOS);
on Windows use the Anaconda Prompt.

**Install script fails or the env is broken**: re-run with `bash install.sh --reinstall`
(`.\install.ps1 -Reinstall`, or `install.bat --reinstall`, on Windows).

**`focus: command not found`**: make sure you ran `conda activate FOCUS`; if the package
step failed, re-run the installer with `--reinstall`.

**PyTorch crashes on import / CUDA not detected on HPC**: see
[Local Installation: Troubleshooting PyTorch / CUDA on HPC](deployment/local.md#troubleshooting-pytorch-cuda-on-hpc).

See the [Troubleshooting Guide](troubleshooting.md) for more.

---

## Next Steps

1. **Try the GUI**: run `focus` to start the interactive interface at `http://localhost:5050`.
2. **Use the CLI**: see the [CLI Usage Guide](quick_start/cli_usage.md) and
   [CLI Reference](user_guide/cli_reference.md).
3. **Prepare your data**: see [Preparing Your Data](user_guide/data_preparation.md).
4. **Learn configuration**: read [Config Structure](configuration/config_structure.md).

FOCUS is released under the MIT License.
