# Installation Guide

## System Requirements

### Minimum Requirements

| Component | Requirement | Notes |
|-----------|-------------|-------|
| **Operating System** | Windows 10/11, macOS 10.15+, Linux (Ubuntu 20.04+, CentOS 7+) | |
| **CPU** | x86_64 or ARM64 | Apple Silicon supported |
| **RAM** | 8GB minimum, 16GB+ recommended | Depends on dataset size |
| **Storage** | 20GB free space | For conda environments and data |
| **Python** | 3.11 | Automatically installed |
| **Conda** | Miniconda or Anaconda | Required for environment management |

### GPU Requirements (Optional)

For **feature extraction registration** using deep learning:

| Component | Requirement | Notes |
|-----------|-------------|-------|
| **GPU** | NVIDIA GPU with CUDA support | AMD/Intel GPUs not supported |
| **CUDA** | CUDA 11.8+ | |
| **Driver** | Latest NVIDIA drivers | |
| **VRAM** | 8GB+ recommended | For large images |

> **Note**: GPU acceleration is **optional**. All other pipeline stages run on CPU and work without GPU.

## Installation Methods

FOCUS can be installed in several ways:

1. **Host Machine Installation** (Recommended for most users)
2. **Container Deployment** (For reproducibility and HPC)
3. **Manual Installation** (For developers)

## Method 1: Host Machine Installation

### Step 1: Install Conda

FOCUS requires Conda for environment management. Install [Miniconda](https://docs.conda.io/en/latest/miniconda.html) or [Anaconda](https://www.anaconda.com/download):

**Linux/macOS:**
```bash
# Download and install Miniconda
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh

# Follow the installer prompts
source ~/.bashrc  # or restart your terminal
```

**Windows:**
1. Download the [Miniconda installer](https://docs.conda.io/en/latest/miniconda.html)
2. Run the installer with default settings
3. Open **Anaconda Prompt** (not regular Command Prompt)

### Step 2: Clone FOCUS Repository

```bash
git clone https://github.com/sifrimlab/FOCUS.git
cd FOCUS
```

### Step 3: Run Installation Script

**Linux/macOS:**
```bash
bash install.sh
```

**Windows:**
```batch
install.bat
```

The installation script will:
1. Create a `FOCUS` conda environment with all dependencies
2. Register the `focus` command for easy access
3. Optionally create auxiliary environments for Raman processing (`FOCUS_ASHLAR`, `FOCUS_BaSiCpy`)

### Step 4: Activate Environment

```bash
conda activate FOCUS
```

### Step 5: Verify Installation

```bash
focus --help
```

You should see the FOCUS help message.

## Method 2: Container Deployment

### Docker/Podman Installation

#### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop) or [Podman](https://podman.io/)
- Docker/Podman must be running

#### Build Container Image

```bash
docker build -t focus .
# or
podman build -t focus .
```

#### Run FOCUS Container

Use the provided launcher script:

```bash
bash focus-container.sh --mount /path/to/your/data
```

**Options:**
- `--mount`: Mount your data directory (required)
- `--runtime`: Specify container runtime (`docker`, `podman`, `singularity`)
- `--gpu`: Enable GPU support
- `--build`: Build image before running

**Examples:**

```bash
# GUI mode
bash focus-container.sh --mount /data/mylab

# CLI mode with config file
bash focus-container.sh --mount /data/mylab -- --config /data/mylab/project/focus_config.json

# With GPU support
bash focus-container.sh --gpu --mount /data/mylab
```

### Singularity/Apptainer Installation (HPC)

#### Prerequisites

- Singularity 3.8+ or Apptainer 1.1+
- Root access may be required for building

#### Build Container Image

```bash
singularity build focus.sif focus.def
# or
apptainer build focus.sif focus.def
```

#### Run FOCUS Container

```bash
# CLI mode
singularity run --bind /scratch/mylab focus.sif --config /scratch/mylab/project/focus_config.json

# GUI mode (requires SSH tunnel)
singularity run --bind /scratch/mylab focus.sif
```

## Method 3: Manual Installation (Developers)

### Step 1: Create Conda Environment

```bash
conda create -n FOCUS python=3.11
conda activate FOCUS
```

### Step 2: Install Dependencies

```bash
# Install core dependencies
pip install -r requirements.txt

# Install FOCUS in development mode
pip install -e .
```

### Step 3: Install Optional Dependencies

For Raman processing:
```bash
# Create auxiliary environments
conda create -n FOCUS_ASHLAR python=3.9
conda create -n FOCUS_BaSiCpy python=3.11

# Install ASHLAR in FOCUS_ASHLAR environment
conda activate FOCUS_ASHLAR
pip install ashlar

# Install BaSiC in FOCUS_BaSiCpy environment  
conda activate FOCUS_BaSiCpy
pip install basicpy
```

## Platform-Specific Instructions

### Windows

1. **Use Anaconda Prompt**: Always use **Anaconda Prompt** (not regular Command Prompt or PowerShell)
2. **Install Conda**: Download and install [Miniconda for Windows](https://docs.conda.io/en/latest/miniconda.html)
3. **Run Installation**:
   ```batch
   install.bat
   ```
4. **Container Usage**: Use the PowerShell script:
   ```powershell
   .\focus-container.ps1 -Mount C:\data\mylab
   ```

### macOS

1. **Install Xcode Tools**: Ensure Xcode command line tools are installed:
   ```bash
   xcode-select --install
   ```
2. **Install Conda**: Use the macOS Miniconda installer
3. **GPU Limitations**: Feature extraction registration is not supported on Apple Silicon (no CUDA)

### Linux (Desktop)

1. **Install Dependencies**:
   ```bash
   # Ubuntu/Debian
   sudo apt update
   sudo apt install git wget
   
   # CentOS/RHEL
   sudo yum install git wget
   ```
2. **Install Conda**: Follow standard Miniconda installation
3. **GPU Setup**: Install NVIDIA drivers and CUDA toolkit for GPU support

### HPC/Headless Servers

1. **Use Singularity**: Most HPC clusters support Singularity/Apptainer
2. **Build Locally**: Build the container on your local machine and transfer the `.sif` file
3. **Batch Jobs**: Use the provided SLURM example for batch processing
4. **SSH Tunneling**: For GUI access on remote servers:
   ```bash
   # On local machine
   ssh -L 5050:localhost:5050 username@hpc-cluster
   
   # On HPC cluster
   singularity run --bind /scratch/mylab focus.sif
   ```

## Post-Installation Setup

### Environment Variables

FOCUS automatically sets up required environment variables during installation. No manual configuration needed.

### HuggingFace Token (Optional)

For **feature extraction registration**, you need a HuggingFace token:

1. Create an account at [https://huggingface.co/](https://huggingface.co/)
2. Generate a token in your account settings
3. The token will be requested when you run feature extraction registration

> **Note**: The token is only used to download the Prov-GigaPath model and is cached locally after first use.

### Data Directory Structure

Prepare your data directory following the [expected structure](overview.md#directory-structure):

```
dataset_path/
├── sample_001/
│   ├── microscopy/		# Raw microscopy images
│   └── msi/			# Raw MSI data
├── sample_002/
│   ├── microscopy/
│   └── msi/
└── ...
```

## Troubleshooting Installation

### Common Issues

**Issue: `conda: command not found`**
- **Solution**: Restart your terminal or run `source ~/.bashrc` (Linux/macOS)
- **Windows**: Use Anaconda Prompt instead of regular Command Prompt

**Issue: Installation script fails**
- **Solution**: Run with `--reinstall` flag:
  ```bash
  bash install.sh --reinstall
  ```

**Issue: Docker/Podman permission denied**
- **Solution**: Add your user to the docker group:
  ```bash
  sudo usermod -aG docker $USER
  newgrp docker
  ```

**Issue: GPU not detected in container**
- **Solution**: Use the `--gpu` flag and ensure NVIDIA Container Toolkit is installed

### Logs and Debugging

- **Installation logs**: Check `focus_install.log` in the FOCUS directory
- **Pipeline logs**: Located in your dataset directory under `logs/`
- **Verbose mode**: Add `-v` flag to see detailed output

## Uninstallation

### Host Installation

```bash
# Deactivate and remove conda environment
conda deactivate
conda env remove -n FOCUS
conda env remove -n FOCUS_ASHLAR
conda env remove -n FOCUS_BaSiCpy

# Remove FOCUS directory
rm -rf FOCUS
```

### Container Installation

```bash
# Remove container images
docker rmi focus
podman rmi focus
rm focus.sif

# Remove FOCUS directory
rm -rf FOCUS
```

## Updating FOCUS

### Host Installation

```bash
cd FOCUS
git pull origin main
bash install.sh --reinstall
```

### Container Installation

```bash
cd FOCUS
git pull origin main
bash focus-container.sh --build --mount /path/to/data
```

## Verification

After installation, verify everything works:

```bash
# Activate environment
conda activate FOCUS

# Check FOCUS command
focus --help

# Test with sample data (if available)
focus --config /path/to/sample_config.json
```

## Next Steps

Now that FOCUS is installed, you can:

1. **Try the GUI**: Run `focus` to start the interactive interface
2. **Explore CLI**: Check the [CLI Usage Guide](quick_start/cli_usage.md)
3. **Learn Configuration**: Read about [Configuration Files](configuration/config_structure.md)
4. **Run Examples**: Try with sample datasets if available

## Support

If you encounter issues:

1. **Check logs**: Review installation and pipeline logs
2. **Consult documentation**: See [Troubleshooting Guide](troubleshooting.md)
3. **GitHub Issues**: Report bugs on the GitHub repository
4. **Community**: Ask questions in GitHub discussions

## License

FOCUS is released under the MIT License. See [LICENSE](../LICENSE) for details.