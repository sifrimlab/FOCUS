# Container Deployment (Docker / Podman / Singularity)

FOCUS ships a `Dockerfile`, a Singularity/Apptainer definition file (`focus.def`), and launcher scripts (`focus-container.sh` for macOS/Linux/WSL2, `focus-container.ps1` for Windows PowerShell). The key design principle is **same-path mounting**: the directory you choose to mount is mapped to the *identical absolute path* inside the container, so every path in your `focus_config.json` remains valid without any translation.

---

## When to Use Containers

- Reproducible environment across machines and operating systems
- Share a single pre-built image with collaborators
- Host has conflicting Python or CUDA dependencies
- Preferred for production HPC deployments (see also [HPC and Headless Servers](hpc.md))
- No conda available on the target machine

---

## Building the Container Image

=== "Docker / Podman"

    Build from the `Dockerfile` in the repository root:

    ```bash
    docker build -t focus .
    # or
    podman build -t focus .
    ```

    The image is based on `python:3.11-slim`, installs system libraries (`libgl1`, `libglib2.0-0`, `libgomp1`, `libsm6`, `libxext6`) for OpenCV and OpenMP, then installs the FOCUS Python dependencies, **the PyTorch ecosystem (torch, torchvision, timm, huggingface-hub)**, and the `focus` package itself. PyTorch is installed from the wheel index given by the `TORCH_INDEX` build arg, which defaults to the CPU index — so `feature_extraction` works out of the box on CPU.

    !!! note "GPU (CUDA) images"
        The PyTorch wheels at `download.pytorch.org` bundle the CUDA runtime inside the wheel, so a CUDA build runs on the same `python:3.11-slim` base — no CUDA base image needed. Pass the `TORCH_INDEX` build arg matching your target CUDA:

        ```bash
        # CUDA 12.8+ (other indices: cu126, cu124, cu121, cu118)
        docker build --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu128 -t focus:gpu .
        ```

        Then run with `--gpus all` (Docker/Podman). The `focus-container.sh --build --gpu` shortcut selects the CUDA index automatically. See `install.sh` `resolve_torch_index` for the CUDA-version → index mapping.

    The container runs as an unprivileged user (`focususer`, UID 1000) and exposes port `5050`.

=== "Singularity / Apptainer"

    Build from `focus.def` — no Docker daemon required; the definition bootstraps directly from `python:3.11-slim`:

    ```bash
    singularity build focus.sif focus.def
    # or
    apptainer build focus.sif focus.def
    ```

    !!! warning "Root or fakeroot required"
        Building a Singularity SIF image typically requires root privileges or the `--fakeroot` flag:

        ```bash
        singularity build --fakeroot focus.sif focus.def
        ```

        On HPC clusters where you cannot build locally, build on a machine with root access (or in a VM) and then copy `focus.sif` to the cluster.

    The definition installs FOCUS into a dedicated venv at `/opt/focus-env/`, including a CPU PyTorch build by default. The `%runscript` directive maps `singularity run ... focus.sif [args]` directly to `focus [args]`.

    !!! note "GPU (CUDA) SIF images"
        Pass the `TORCH_INDEX` build arg to bake in a CUDA PyTorch build (requires Apptainer ≥ 1.1 / SingularityCE ≥ 3.11), then run with `--nv`:

        ```bash
        apptainer build --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu128 focus.sif focus.def
        apptainer run --nv --bind /scratch/$USER focus.sif --config /scratch/$USER/project/focus_config.json
        ```

---

## Running on macOS / Linux

Use the `focus-container.sh` launcher script. It auto-detects the first available container runtime (Docker → Podman → Singularity → Apptainer) and handles port forwarding, GPU flags, and same-path volume mounts automatically.

**GUI mode** (opens `http://localhost:5050`):

```bash
bash focus-container.sh --mount /path/to/your/data
```

**CLI mode:**

```bash
bash focus-container.sh --mount /path/to/your/data -- --config /path/to/your/data/project/focus_config.json
```

!!! note "The `--` separator"
    Everything after `--` is passed directly to the `focus` command *inside* the container. The launcher uses the presence of `--config` (or `-c`) after `--` to decide whether to enable GUI mode (and expose port 5050).

**Select a specific runtime:**

=== "Docker"

    ```bash
    bash focus-container.sh --runtime docker --mount /data/mylab -- --config /data/mylab/project/focus_config.json
    ```

=== "Podman"

    ```bash
    bash focus-container.sh --runtime podman --mount /data/mylab -- --config /data/mylab/project/focus_config.json
    ```

=== "Singularity / Apptainer"

    ```bash
    bash focus-container.sh --runtime singularity --mount /data/mylab -- --config /data/mylab/project/focus_config.json
    # or
    bash focus-container.sh --runtime apptainer --mount /data/mylab -- --config /data/mylab/project/focus_config.json
    ```

**GPU support:**

```bash
bash focus-container.sh --gpu --mount /data/mylab -- --config /data/mylab/project/focus_config.json
```

This passes `--gpus all` to Docker/Podman or `--nv` to Singularity/Apptainer at run time. **The image must contain a CUDA PyTorch build** for the GPU to be used — build it with `--gpu` as well (see *Build and run in one step* below), or via the `TORCH_INDEX` build arg.

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

Override the baked PyTorch wheel index explicitly with the `TORCH_INDEX` environment variable, e.g. `TORCH_INDEX=https://download.pytorch.org/whl/cu126 bash focus-container.sh --build --gpu ...`.

---

## Running on Windows

Use `focus-container.ps1` from a **PowerShell prompt**. Docker Desktop or Podman Desktop must be installed and running. Singularity/Apptainer is not natively supported on Windows — use [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install) and run `focus-container.sh` from within the WSL2 terminal.

Windows paths are automatically converted to the Unix-style paths that Docker/Podman use internally (`C:\data\mylab` → `/c/data/mylab`), preserving the directory tree so paths in your config file remain consistent.

**GUI mode:**

```powershell
.\focus-container.ps1 -Mount C:\data\mylab
```

**CLI mode:**

```powershell
.\focus-container.ps1 -Mount C:\data\mylab -- --config /c/data/mylab/project/focus_config.json
```

!!! warning "Use the converted Unix path in your config"
    When running inside a container on Windows, the mount lands at the Unix-style path (`/c/data/mylab`, not `C:\data\mylab`). Your `focus_config.json`'s `dataset_path` must use this Unix form.

**Select runtime:**

```powershell
.\focus-container.ps1 -Runtime podman -Mount C:\data\mylab
```

**GPU support:**

```powershell
.\focus-container.ps1 -Gpu -Mount C:\data\mylab -- --config /c/data/mylab/project/focus_config.json
```

This passes `--gpus all` to Docker or Podman.

---

## Using the GUI via Container

When running in GUI mode, the launcher script automatically exposes port `5050`. Open `http://localhost:5050` in your browser once the container starts.

If the alignment stage is reached, the alignment tool is served at port `8000`. To expose it, run the container directly with both ports mapped:

=== "Docker / Podman"

    ```bash
    docker run --rm -it -p 5050:5050 -p 8000:8000 -v /data/mylab:/data/mylab focus
    ```

=== "Singularity"

    Singularity does not perform port mapping — the container inherits the host network namespace, so ports are accessible on the host directly without `-p` flags.

---

## `focus-container.sh` Flag Reference

| Flag | Short | Description | Default |
|---|---|---|---|
| `--runtime` | `-r` | Container runtime: `docker`, `podman`, `singularity`, `apptainer` | First available |
| `--mount` | `-m` | Host directory to bind-mount (repeatable) | Current working directory |
| `--image` | `-i` | Docker/Podman image name | `focus` |
| `--sif` | `-s` | Path to the Singularity `.sif` file | `./focus.sif` |
| `--gpu` | | Pass GPU flags (`--gpus all` or `--nv`) | Off |
| `--build` | | Build the image/SIF before running | Off |
| `--help` | `-h` | Print usage and exit | |
| `--` | | Separator: everything after is passed to `focus` | |

## `focus-container.ps1` Flag Reference

| Flag | Description | Default |
|---|---|---|
| `-Runtime` | Container runtime: `docker` or `podman` | First available |
| `-Mount` | Host directory to bind-mount (repeatable) | Current directory |
| `-Image` | Docker/Podman image name | `focus` |
| `-Port` | GUI port to expose | `5050` |
| `-Gpu` | Pass `--gpus all` to the runtime | Off |
| `-Build` | Build the image before running | Off |
| `--` | Separator: everything after is passed to `focus` | |

---

## Volume Mounting Rules

- **Always mount the directory that contains your data.** The mount path must be an ancestor of the `dataset_path` in your config.
- **Same-path mounting:** the launcher scripts mount `<host_path>:<host_path>` (identical paths on both sides). This means every path you write in `focus_config.json` is valid inside the container without modification.
- **Multiple mounts are supported:** use `--mount` (or `-Mount`) more than once if your images and omics data live in separate directories.
- **On Windows:** paths are converted to Unix style (`C:\foo\bar` → `/c/foo/bar`). Use the Unix-style path in `focus_config.json` when running in a container.
