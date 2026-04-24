# Local Installation (Host Machine)

Install FOCUS directly on your machine using conda. This is the recommended approach for most users and gives the best performance, as the pipeline runs natively without container overhead.

---

## System Requirements

| Requirement | Minimum | Recommended |
|---|---|---|
| OS | Windows 10, macOS 12, Ubuntu 20.04 | Latest stable |
| Python | 3.11 (managed by conda) | 3.11 |
| Conda | Any (Miniconda or Anaconda) | Miniconda 3 |
| RAM | 8 GB | 16 GB+ |
| Disk space | 20 GB free | 50 GB+ (for large datasets) |
| GPU | Not required | NVIDIA + CUDA 11.8+ (for `feature_extraction`) |

---

## Step 1: Install Conda

FOCUS uses conda to manage its Python environment. If conda is not already installed:

- **All platforms:** Download [Miniconda](https://docs.conda.io/en/latest/miniconda.html) (recommended) or [Anaconda](https://www.anaconda.com/download).

!!! tip "Windows users"
    After installing Miniconda or Anaconda, use the **Anaconda Prompt** (or a PowerShell session with conda initialised) for all subsequent steps. The standard Command Prompt does not have conda in its PATH by default.

---

## Step 2: Clone the Repository

```bash
git clone https://github.com/sifrimlab/FOCUS.git
cd FOCUS
```

---

## Step 3: Run the Install Script

=== "macOS / Linux"

    ```bash
    bash install.sh
    ```

=== "Windows"

    Open an **Anaconda Prompt** and run:

    ```bat
    install.bat
    ```

### What the script does

1. Verifies that conda is available and exits with a clear message if not.
2. Detects your system's CUDA version (via `nvcc`, `nvidia-smi`, or Lmod environment variables) and selects the matching PyTorch wheel index from `download.pytorch.org/whl/`.
3. Creates a `FOCUS` conda environment with Python 3.11 and installs all dependencies from `requirements.txt`.
4. Installs the `focus` package in editable mode, registering the `focus` CLI command so you can run it from any directory after activating the environment.
5. Scans the `tools/` directory and creates optional tool environments (e.g. `FOCUS_BaSiCpy`, `FOCUS_ASHLAR`) for Raman spectroscopy imaging preprocessing.

!!! note "Why not `pip install torch` directly?"
    The install script installs PyTorch from PyTorch's own wheel index (not default PyPI). PyTorch wheels from `download.pytorch.org/whl/` bundle CUDA internally and do **not** create separate `nvidia-*` pip packages, which avoids version conflicts with the system CUDA on HPC nodes. See [Troubleshooting PyTorch / CUDA on HPC](#troubleshooting-pytorch-cuda-on-hpc) for details.

---

## Step 4: Activate and Launch

```bash
conda activate FOCUS

# Launch the interactive GUI (served at http://localhost:5050):
focus

# Or run in CLI mode with an existing config file:
focus --config /path/to/your/dataset/focus_config.json
```

The GUI guides you through four stages: **Setup → Configuration → Running → Complete**. When the alignment stage is reached, the interactive alignment tool opens separately at `http://localhost:8000`.

---

## Reinstallation and Updates

To wipe and recreate all environments from scratch (e.g. after a dependency update or a broken install):

=== "macOS / Linux"

    ```bash
    bash install.sh --reinstall
    ```

=== "Windows"

    ```bat
    install.bat --reinstall
    ```

---

## GPU Setup (Optional — for `feature_extraction`)

GPU acceleration is required only for the `feature_extraction` registration type, which uses the [Prov-GigaPath](https://huggingface.co/prov-gigapath/prov-gigapath) model. All other pipeline stages run on CPU.

- Requires an NVIDIA GPU with **CUDA 11.8 or newer**.
- The install script auto-detects your CUDA version and installs the matching PyTorch build. No manual intervention is needed on most systems.
- On **macOS (Apple Silicon)**: GPU acceleration via CUDA is **not available**. MPS is not currently supported. The full pipeline (except `feature_extraction`) works on CPU.

Verify GPU availability after installation:

```bash
conda activate FOCUS
python -c "import torch; print(torch.cuda.is_available())"
# Expected: True
```

A HuggingFace token is required the first time `feature_extraction` is used, to download the Prov-GigaPath model weights. Provide the token in your FOCUS configuration file:

```json
{
  "huggingface_token": "hf_...",
  "modalities": [ ... ]
}
```

Or set it interactively in the GUI configuration panel (a token field appears automatically when `feature_extraction` registration is selected for any modality).

---

## Raman Environments (Optional)

The `FOCUS_BaSiCpy` and `FOCUS_ASHLAR` environments are only needed if your dataset contains Raman spectroscopy imaging data (`.lif` files). They are created automatically by the install script when a `tools/BaSiCpy/` or `tools/ASHLAR/` directory is present in the repository.

To install them explicitly on a clean checkout:

```bash
bash install.sh
```

The script discovers all subdirectories under `tools/` and creates a corresponding `FOCUS_<Name>` environment for each one. **Java (OpenJDK) is installed automatically** inside the `FOCUS_ASHLAR` environment via conda-forge, as ASHLAR requires it for tile stitching.

!!! warning "Java requirement for ASHLAR"
    The `FOCUS_ASHLAR` environment installs OpenJDK via `conda install -c conda-forge openjdk`. This is handled automatically by the install script. You do not need a system-level Java installation.

---

## Verifying the Installation

```bash
conda activate FOCUS
focus --help
```

Expected output: usage text listing the `--config` flag and other available options. If the command is not found, the package installation step may have failed — re-run `bash install.sh --reinstall`.

---

## Troubleshooting PyTorch / CUDA on HPC

The install script automatically detects your system's CUDA version (via `nvcc`, `nvidia-smi`, or environment module variables) and installs a matching PyTorch build. On most systems this works without any intervention. On **HPC clusters**, however, two classes of problems can occur.

### 1. CUDA not detected (CPU-only fallback)

If the install script prints:

```
[WARN]  HPC environment detected (SLURM) but no CUDA toolkit found.
```

it means none of the CUDA detection methods found a version. **Load the CUDA module before running the script:**

```bash
module load cuda          # use whatever CUDA module your cluster provides
bash install.sh --reinstall
```

The script checks for CUDA in the following order:

1. `nvcc` in `PATH` — most accurate (toolkit version)
2. `nvcc` inside `$CUDA_HOME`, `$CUDA_PATH`, or `$CUDA_ROOT` — set by Lmod when a CUDA module is loaded
3. `version.json` or `version.txt` inside those paths
4. `$LOADEDMODULES` — Lmod sets this to a colon-separated list (e.g. `gcc/12.2:cuda/12.8:...`) which the script parses directly
5. `nvidia-smi` — reports the driver's maximum supported CUDA version

### 2. PyTorch crashes on import (Bus error / SIGBUS)

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

!!! tip "Selecting the right CUDA wheel index"
    Replace `cu128` with the index that matches your system CUDA version:

    | System CUDA | Wheel index |
    |---|---|
    | 13.x or 12.8+ | `cu128` |
    | 12.6 – 12.7 | `cu126` |
    | 12.4 – 12.5 | `cu124` |
    | 12.0 – 12.3 | `cu121` |
    | 11.8 – 11.x | `cu118` |

    The install script prints which index it selects during installation.

#### Why does this happen?

Recent PyTorch versions (e.g. 2.11+) unconditionally call `_preload_cuda_lib` at import time, which uses `ctypes.CDLL` to load CUDA shared libraries installed as separate pip packages (`nvidia-cuda-runtime-*`, `cuda-bindings`, etc.). On some HPC configurations, the initialisation code inside these shared libraries fails when attempting to interface with the CUDA kernel driver, resulting in a SIGBUS signal that terminates the process. Older PyTorch versions (e.g. 2.9.0) use a different loading strategy that does not trigger this behaviour.
