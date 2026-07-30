# FOCUS: end-to-end preprocessing, alignment and resolution-matched integration of spatial multi-omics data

FOCUS is an end-to-end preprocessing, alignment, and registration pipeline for **spatial multiomics** datasets. It integrates data acquired from different imaging and omics modalities on the same tissue section — such as microscopy images, mass spectrometry imaging (MSI/lipidomics), Raman spectroscopy, and spatial transcriptomics — into a single, analysis-ready multimodal dataset. The output is structured as [MuData](https://mudata.readthedocs.io/) (`.h5mu`), making it immediately compatible with established single-cell and spatial omics frameworks such as [scanpy](https://scanpy.readthedocs.io/), [squidpy](https://squidpy.readthedocs.io/), and [AnnData](https://anndata.readthedocs.io/).

No programming is required to use FOCUS. The entire pipeline is driven by a JSON configuration file that can be built interactively through a web-based GUI.

📖 **Full documentation: [sifrimlab.github.io/FOCUS](https://sifrimlab.github.io/FOCUS/)** — installation, user guide, per-modality docs, scientific methods, and deployment guides.

---

## Table of Contents

- [Documentation](https://sifrimlab.github.io/FOCUS/)

- [Supported Modalities](#supported-modalities)
- [Pipeline Overview](#pipeline-overview)
- [Requirements](#requirements)
- [Installation](#installation)
  - [macOS and Linux](#macos-and-linux)
  - [Windows](#windows)
  - [Troubleshooting PyTorch / CUDA on HPC](#troubleshooting-pytorch--cuda-on-hpc)
- [Dataset Structure](#dataset-structure)
- [Usage on the Host Machine](#usage-on-the-host-machine)
  - [GUI Mode](#gui-mode)
  - [CLI Mode](#cli-mode)
- [Usage with Containers](#usage-with-containers)
  - [Building the Image](#building-the-image)
  - [macOS and Linux (Docker · Podman · Singularity)](#macos-and-linux-docker--podman--singularity)
  - [Windows (Docker Desktop · Podman Desktop)](#windows-docker-desktop--podman-desktop)
  - [HPC / Headless Servers (Singularity · Apptainer)](#hpc--headless-servers-singularity--apptainer)
- [Platform Compatibility](#platform-compatibility)

---

## Supported Modalities

| Modality | Key `"type"` | Input Format | Output Format |
|---|---|---|---|
| Fluorescence / brightfield microscopy | `microscopy_image` | `.tiff`, `.tif`, `.ome.tiff`, `.ome.tif`, `.qptiff`, `.czi` | OME-TIFF pyramid |
| Mass Spectrometry Imaging (MSI / lipidomics) | `msi` | `.imzML` + `.ibd` | AnnData `.h5ad` |
| Raman spectroscopy | `raman` | `.lif` | OME-TIFF (hyperspectral) |
| Spatial transcriptomics | `st` | AnnData `.h5ad` | AnnData `.h5ad` |

---

## Pipeline Overview

```
Raw Data  ──►  Preprocessing  ──►  Alignment  ──►  Registration  ──►  MuData dataset
               (per modality)      (interactive      (feature-based     (.h5mu)
                                    web GUI)          or interpolation)
```

1. **Preprocessing** — modality-specific quality control, normalisation, background removal, and storage in a standardised format.
2. **Alignment** — an interactive web GUI lets you visually overlay the reference modality onto each target modality (translate, rotate, scale, flip, and corner-distort) to record their spatial correspondence. Each sample is handled individually.
3. **Registration** — computationally maps features from one modality onto the coordinate space of another using either deep-learning patch embeddings (requires GPU) or Gaussian-weighted spot interpolation.
4. **Compilation** — all aligned and registered modalities are merged into a single MuData (`.h5mu`) file.

---

## Requirements

| Requirement | Notes |
|---|---|
| **[Conda](https://docs.conda.io/en/latest/miniconda.html)** (Miniconda or Anaconda) | Required for environment management |
| **Python 3.11** | Managed automatically by the install script |
| **NVIDIA GPU + CUDA** | *Optional.* Required only for the `feature_extraction` registration type (uses the [Prov-GigaPath](https://huggingface.co/prov-gigapath/prov-gigapath) model via HuggingFace). All other pipeline stages run on CPU. |
| **HuggingFace token** | *Optional.* Required only when `feature_extraction` registration is enabled and the model has not been cached locally. |

FOCUS runs on **Windows 10/11**, **macOS**, and **Linux** (both desktop and headless servers).

---

## Installation

Clone the repository and run the install script for your platform. The script will:

- Check that conda is available and guide you to install it if not.
- Create a `FOCUS` conda environment with all dependencies.
- Register the `focus` command so you can run the software from any directory after activating the environment.
- Create auxiliary `FOCUS_ASHLAR` and `FOCUS_BaSiCpy` environments (one per subfolder in `tools/`) for Raman spectroscopy processing. These are built by default — no extra flag is needed.

```bash
git clone https://github.com/sifrimlab/FOCUS.git
cd FOCUS
```

### macOS and Linux

```bash
bash install.sh
```

To wipe and recreate all environments (e.g. after a dependency update):

```bash
bash install.sh --reinstall
```

### Windows

Open an **Anaconda Prompt (PowerShell)** and run the PowerShell installer:

```powershell
.\install.ps1
```

Or with the reinstall flag:

```powershell
.\install.ps1 -Reinstall
```

`install.ps1` mirrors `install.sh`: it detects your CUDA version and installs the matching PyTorch wheel. A `install.bat` shim that forwards to it is also provided, so from a classic Command Prompt `install.bat` / `install.bat --reinstall` work too.

> **Tip:** If `conda` is not found, install [Miniconda for Windows](https://docs.conda.io/en/latest/miniconda.html), then open a new **Anaconda Prompt** and retry.

### Troubleshooting PyTorch / CUDA on HPC

The install script automatically detects your system's CUDA version (via `nvcc`, `nvidia-smi`, or environment module variables) and installs a matching PyTorch build. On most systems this works without any intervention. On **HPC clusters**, however, two classes of problems can occur.

#### 1. CUDA not detected (CPU-only fallback)

If the install script prints:

```
[WARN]  HPC environment detected (SLURM) but no CUDA toolkit found.
```

it means none of the CUDA detection methods found a version. **Load the CUDA module before running the script:**

```bash
module load cuda          # use whatever CUDA module your cluster provides
bash install.sh --reinstall
```

#### 2. PyTorch crashes on import (Bus error / SIGBUS)

Some PyTorch versions crash with a `Bus error (core dumped)` at import time on certain HPC systems. This happens inside PyTorch's CUDA library preloading (`_preload_cuda_lib`), before any user code runs. Symptoms:

```
Fatal Python error: Bus error
  File ".../torch/__init__.py", line 324 in _preload_cuda_lib
```

The install script verifies that PyTorch imports correctly after installation. If the check fails, you will see:

```
[ERROR] torch X.Y.Z installed but crashes on import (likely SIGBUS from CUDA preloading).
[ERROR] Specify a known-working version and re-run:
[ERROR]   TORCH_VERSION=<version> bash install.sh --reinstall
```

**To fix this**, find a PyTorch version that works on your cluster and pass it via the `TORCH_VERSION` environment variable:

```bash
# Step 1: Find a working version.
# Create a temporary environment and test different versions:
conda create -y -n torch_test python=3.11
conda run -n torch_test pip install torch==2.9.0 --index-url https://download.pytorch.org/whl/cu128
conda run -n torch_test python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
# Expected output: 2.9.0+cu128 True
conda env remove -y -n torch_test

# Step 2: Reinstall FOCUS with the working version.
TORCH_VERSION=2.9.0 bash install.sh --reinstall
```

> **Note:** Replace `cu128` with the CUDA wheel index that matches your system (e.g. `cu126`, `cu124`, `cu121`, `cu118`). The install script prints which index it selects during installation.

#### Why does this happen?

Recent PyTorch versions (e.g. 2.11+) unconditionally call `_preload_cuda_lib` at import time, which uses `ctypes.CDLL` to load CUDA shared libraries installed as separate pip packages (`nvidia-cuda-runtime-*`, `cuda-bindings`, etc.). On some HPC configurations, the initialization code inside these shared libraries fails when attempting to interface with the CUDA kernel driver, resulting in a SIGBUS signal that terminates the process. Older PyTorch versions (e.g. 2.9.0) use a different loading strategy that does not trigger this behavior.

---

## Dataset Structure

FOCUS expects your data to follow a simple two-level directory hierarchy:

```
dataset_path/
├── sample_001/
│   ├── <modality_name>/          ← raw input files for this modality
│   └── <modality_name>/          ← raw input files for a second modality
├── sample_002/
│   ├── <modality_name>/
│   └── <modality_name>/
└── ...
```

- **`dataset_path`** is the root directory you provide in your config.
- Each **first-level subdirectory** is treated as a sample. Directory names become the sample identifiers.
- Each **second-level subdirectory** must match the modality `"name"` defined in your config (case-sensitive).
- Input file names within a modality folder are not significant; FOCUS selects files by extension.

FOCUS automatically creates the following output directories inside `dataset_path` as the pipeline progresses:

```
dataset_path/
├── sample_001/
│   ├── preprocessing/<modality>/   ← preprocessed files
│   ├── alignment/                  ← aligned files
│   └── registration/<modality>/    ← registered files
├── ...
└── merged/
    ├── preprocessing/
    ├── alignment/
    ├── registration/
    └── multimodal_dataset.h5mu     ← final output
```

---

## Usage on the Host Machine

Activate the FOCUS conda environment first:

```bash
conda activate FOCUS
```

### GUI Mode

Running `focus` without arguments starts a local web server and opens the interactive interface:

```bash
focus
```

```
FOCUS GUI started. Open http://localhost:5050 in your browser.
```

The GUI guides you through four stages:

1. **Setup** — enter your `dataset_path` or load an existing configuration file.
2. **Configuration** — define your modalities, processing settings, and registration options. The configuration is auto-saved as `focus_config.json` inside your `dataset_path`.
3. **Running** — monitor pipeline progress. When the alignment stage is reached, a button appears to open the interactive alignment tool (served separately at `http://localhost:8000`).
4. **Complete** — review the list of generated output files.

### CLI Mode

If you already have a configuration file, you can run the full pipeline non-interactively:

```bash
focus --config /path/to/your/dataset/focus_config.json
```

The configuration file must be a valid JSON file. Its path can be anywhere on the filesystem as long as it is readable. Use the GUI as an interactive config builder, or see the [configuration documentation](docs/configuration/config_structure.md) for the expected structure.

---

## Usage with Containers

FOCUS provides a `Dockerfile`, a Singularity definition file (`focus.def`), and launcher scripts for all three major container runtimes. The key design principle is **same-path mounting**: the directory you choose to mount is mapped to the *identical absolute path* inside the container, so every path in your config file is valid without any translation.

### Building the Image

**Docker or Podman:**

```bash
docker build -t focus .
# or
podman build -t focus .
```

**Singularity / Apptainer:**

```bash
singularity build focus.sif focus.def
# or
apptainer build focus.sif focus.def
```

> Building from `focus.def` does not require Docker; it bootstraps directly from `python:3.11-slim`.

Both images bake in a **CPU** PyTorch build by default. For GPU images, pass the `TORCH_INDEX` build arg matching your CUDA (the wheel bundles CUDA, so no CUDA base image is needed):

```bash
docker build --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu128 -t focus:gpu .
apptainer build --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu128 focus.sif focus.def
```

The `focus-container.sh --build --gpu` shortcut selects the CUDA index automatically. See [Container Deployment](docs/deployment/containers.md) for details.

---

### macOS and Linux (Docker · Podman · Singularity)

Use `focus-container.sh`. It auto-detects the first available runtime (Docker → Podman → Singularity → Apptainer) and mounts your data directory at the same path inside the container.

**GUI mode** (opens `http://localhost:5050`):

```bash
bash focus-container.sh --mount /path/to/your/data
```

**CLI mode:**

```bash
bash focus-container.sh --mount /path/to/your/data -- --config /path/to/your/data/project/focus_config.json
```

> Note the `--` separator: everything after it is passed directly to the `focus` command inside the container.

**Select a specific runtime:**

```bash
bash focus-container.sh --runtime podman --mount /data/mylab -- --config /data/mylab/project/focus_config.json
bash focus-container.sh --runtime singularity --mount /data/mylab -- --config /data/mylab/project/focus_config.json
```

**GPU support:**

```bash
bash focus-container.sh --gpu --mount /data/mylab -- --config /data/mylab/project/focus_config.json
```

> The image must contain a CUDA PyTorch build for the GPU to be used — build it with `--build --gpu` (see below), or via the `TORCH_INDEX` build arg.

**Mount multiple directories:**

```bash
bash focus-container.sh --mount /data/images --mount /data/omics -- --config /data/omics/project/focus_config.json
```

**Build and run in one step:**

```bash
# CPU image
bash focus-container.sh --build --mount /data/mylab

# GPU image (bakes in a CUDA PyTorch build, then runs with GPU access)
bash focus-container.sh --build --gpu --mount /data/mylab -- --config /data/mylab/project/focus_config.json
```

---

### Windows (Docker Desktop · Podman Desktop)

Use `focus-container.ps1` from a PowerShell prompt. Windows paths are automatically converted to the Unix-style paths that Docker uses internally (`C:\path\to\data` → `/c/path/to/data`), preserving the directory tree so no path translation is needed in your config file.

**GUI mode:**

```powershell
.\focus-container.ps1 -Mount C:\data\mylab
```

**CLI mode:**

```powershell
.\focus-container.ps1 -Mount C:\data\mylab -- --config /c/data/mylab/project/focus_config.json
```

> On Windows, use the converted Unix path (`/c/...`) in your config file when running inside a container.

**Select runtime:**

```powershell
.\focus-container.ps1 -Runtime podman -Mount C:\data\mylab
```

**GPU support:**

```powershell
.\focus-container.ps1 -Gpu -Mount C:\data\mylab -- --config /c/data/mylab/project/focus_config.json
```

> Singularity/Apptainer is not natively supported on Windows. Use [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install) and run `focus-container.sh` from within the WSL2 terminal for Singularity support.

---

### HPC / Headless Servers (Singularity · Apptainer)

On HPC clusters, Singularity or Apptainer is typically the only available container runtime. Copy the pre-built `focus.sif` file to the cluster and use `focus-container.sh` directly, or invoke Singularity manually:

**CLI mode** (recommended on HPC):

```bash
singularity run --bind /scratch/mylab focus.sif --config /scratch/mylab/project/focus_config.json
```

`--bind /path` maps the host path to the same path inside the container. All paths in your config remain unchanged.

**GUI mode on a remote server:**

The GUI requires access to port 5050. Set up an SSH tunnel from your local machine:

```bash
# On your local machine:
ssh -L 5050:localhost:5050 username@hpc-cluster.example.org

# Then on the cluster:
singularity run --bind /scratch/mylab focus.sif

# Open http://localhost:5050 in your local browser
```

**Submitting a batch job (SLURM):**

The repo ships a ready-to-use, parameterised batch script, [`slurm/submit_focus.sbatch`](slurm/submit_focus.sbatch) (Singularity + GPU by default, with a commented host-install variant):

```bash
sbatch slurm/submit_focus.sbatch

# Override the config / image paths without editing the file:
sbatch --export=ALL,FOCUS_CONFIG=/scratch/$USER/proj/config.json,FOCUS_SIF=/scratch/$USER/focus.sif \
       slurm/submit_focus.sbatch
```

See the [HPC & Headless Servers](docs/deployment/hpc.md) guide for the full breakdown of the script's resource directives and overridable variables.

> The `--nv` flag (set in the script) passes NVIDIA GPU access to the Singularity container; drop it and `#SBATCH --gres=gpu:1` for CPU-only runs.

---

## Platform Compatibility

| Feature | Windows 10/11 | macOS | Linux (desktop) | Linux (headless / HPC) |
|---|:---:|:---:|:---:|:---:|
| Host install (`install.sh` / `install.ps1`) | ✓ | ✓ | ✓ | ✓ |
| GUI mode | ✓ | ✓ | ✓ | via SSH tunnel |
| CLI mode | ✓ | ✓ | ✓ | ✓ |
| Docker / Podman container | ✓ | ✓ | ✓ | ✓ |
| Singularity / Apptainer container | via WSL2 | ✓ | ✓ | ✓ |
| GPU acceleration (feature extraction) | ✓ | — | ✓ | ✓ |

> GPU acceleration via CUDA is not available on macOS (Apple Silicon uses MPS, which is not currently supported). All pipeline stages that do not use `feature_extraction` registration run fully on CPU and are supported on all platforms.