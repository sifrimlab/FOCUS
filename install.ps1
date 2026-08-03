<#
.SYNOPSIS
    FOCUS installation script for Windows (PowerShell).

.DESCRIPTION
    PowerShell port of install.sh with the same behaviour: detects the system
    CUDA version, installs a CUDA-matched PyTorch from the pytorch.org wheel
    index (so the bundled CUDA does not clash with PyPI nvidia-* packages),
    creates the main FOCUS conda environment, installs the FOCUS package, and
    sets up one conda environment per tool under tools\.

.PARAMETER Reinstall
    Recreate environments from scratch instead of skipping existing ones.

.EXAMPLE
    .\install.ps1
    .\install.ps1 -Reinstall
    $env:TORCH_VERSION='2.5.1'; .\install.ps1 -Reinstall   # pin a torch version

.NOTES
    Run from an "Anaconda Prompt (PowerShell)" or any PowerShell where `conda`
    is on PATH. If `feature_extraction` (GPU registration) will be used, make
    sure the CUDA toolkit / driver is installed before running so the correct
    PyTorch wheel index is selected.
#>

[CmdletBinding()]
param(
    [switch] $Reinstall
)

$ErrorActionPreference = "Stop"

# Pins torch + torchvision so that later pip installs cannot replace them from
# PyPI. Set by Install-PytorchPackages.
$script:TorchConstraints = $null

function Write-Info ($msg) { Write-Host "[INFO]  $msg" -ForegroundColor Cyan   }
function Write-Ok   ($msg) { Write-Host "[OK]    $msg" -ForegroundColor Green  }
function Write-Warn ($msg) { Write-Host "[WARN]  $msg" -ForegroundColor Yellow }
function Write-Err  ($msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red    }

$ScriptDir = $PSScriptRoot

# 1. Verify conda is available
if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    Write-Err "conda not found in PATH."
    Write-Host ""
    Write-Host "  Please install Miniconda or Anaconda and make sure it is initialised"
    Write-Host "  for your shell before running this script."
    Write-Host ""
    Write-Host "  Miniconda: https://docs.conda.io/en/latest/miniconda.html"
    Write-Host "  Anaconda:  https://www.anaconda.com/download"
    Write-Host ""
    Write-Host "  After installation, open a new 'Anaconda Prompt (PowerShell)' and re-run."
    exit 1
}
Write-Ok "conda found: $((conda --version) 2>&1)"

function Test-CondaEnv ([string]$Name) {
    $envs = & conda env list 2>$null
    foreach ($line in $envs) {
        if ($line -match '^\s*#') { continue }
        if ($line -match '^\s*([^\s]+)') {
            if ($Matches[1] -eq $Name) { return $true }
        }
    }
    return $false
}

# 2. Detect system CUDA and resolve the right PyTorch wheel index
#
# Same rationale as install.sh: `pip install torch` from default PyPI pulls
# separate nvidia-cuda-*-cu12 packages that clash with a system-managed CUDA. The
# pytorch.org wheel index bundles CUDA inside the wheel and creates no separate
# nvidia-* pip packages, so it coexists with any system CUDA.
#
# Detection order: nvcc (CUDA toolkit version, most accurate), $env:CUDA_PATH and
# $env:CUDA_HOME (nvcc.exe, version.json, version.txt), nvidia-smi (driver's
# maximum supported version). None found means the CPU-only wheel.
function Get-CudaVersion {
    # 1. nvcc in PATH
    if (Get-Command nvcc -ErrorAction SilentlyContinue) {
        $out = (& nvcc --version 2>$null | Out-String)
        if ($out -match 'release (\d+\.\d+)') { return $Matches[1] }
    }

    # 2. CUDA_PATH / CUDA_HOME. CUDA_PATH is the Windows installer convention.
    foreach ($cudaDir in @($env:CUDA_PATH, $env:CUDA_HOME)) {
        if (-not $cudaDir) { continue }

        $nvccExe = Join-Path $cudaDir 'bin\nvcc.exe'
        if (Test-Path $nvccExe) {
            $out = (& $nvccExe --version 2>$null | Out-String)
            if ($out -match 'release (\d+\.\d+)') { return $Matches[1] }
        }

        $vjson = Join-Path $cudaDir 'version.json'
        if (Test-Path $vjson) {
            try {
                $j = Get-Content $vjson -Raw | ConvertFrom-Json
                $v = $j.cuda.version
                if ($v) { $p = $v.Split('.'); return "$($p[0]).$($p[1])" }
            } catch { }
        }

        $vtxt = Join-Path $cudaDir 'version.txt'
        if (Test-Path $vtxt) {
            $t = Get-Content $vtxt -Raw
            if ($t -match 'CUDA Version (\d+\.\d+)') { return $Matches[1] }
        }
    }

    # 3. nvidia-smi: the driver's maximum supported CUDA version
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $out = (& nvidia-smi 2>$null | Out-String)
        if ($out -match 'CUDA Version:\s*(\d+\.\d+)') { return $Matches[1] }
    }

    return ""
}

# Maps a CUDA version string such as "12.4" to the closest PyTorch wheel index
# that is <= that version. Keep in sync with resolve_torch_index in install.sh
# and with https://download.pytorch.org/whl/.
function Resolve-TorchIndex ([string]$CudaVer) {
    if (-not $CudaVer) { return "https://download.pytorch.org/whl/cpu" }

    $p = $CudaVer.Split('.')
    $major = [int]$p[0]
    $minor = [int]$p[1]

    if (($major -ge 13) -or ($major -eq 12 -and $minor -ge 8)) {
        return "https://download.pytorch.org/whl/cu128"
    } elseif ($major -eq 12 -and $minor -ge 6) {
        return "https://download.pytorch.org/whl/cu126"
    } elseif ($major -eq 12 -and $minor -ge 4) {
        return "https://download.pytorch.org/whl/cu124"
    } elseif ($major -eq 12) {
        return "https://download.pytorch.org/whl/cu121"
    } elseif ($major -eq 11 -and $minor -ge 8) {
        return "https://download.pytorch.org/whl/cu118"
    } else {
        Write-Warn "CUDA $CudaVer predates PyTorch's oldest supported wheel. Using the CPU build."
        return "https://download.pytorch.org/whl/cpu"
    }
}

# Reads the installed version of a pip package inside a conda env.
function Get-PipVersion ([string]$EnvName, [string]$Package) {
    $line = (& conda run --no-capture-output -n $EnvName pip show $Package 2>$null | Select-String '^Version:')
    if ($line) { return (($line.Line -split '\s+')[1]) }
    return ""
}

# Installs torch, torchvision, timm and huggingface-hub into a conda env using a
# CUDA-matched wheel index.
#
# None of these packages may appear in requirements.txt or in pyproject.toml
# [project.dependencies]. A later `pip install -r` or `pip install -e .` would
# resolve them against default PyPI and overwrite the CUDA-matched wheels. The pip
# constraints file below is an additional guard.
function Install-PytorchPackages ([string]$EnvName) {
    $cudaVer    = Get-CudaVersion
    $torchIndex = Resolve-TorchIndex $cudaVer

    if (-not $cudaVer) {
        Write-Info "No CUDA detected. Installing CPU-only PyTorch."
    } else {
        Write-Info "Detected CUDA $cudaVer. Using PyTorch wheel index: $torchIndex"
        Write-Info "These wheels bundle CUDA internally and do not install separate"
        Write-Info "nvidia-* pip packages, avoiding conflicts with the system CUDA."
    }

    # Step 1: torch + torchvision from the pytorch.org wheel index. Both must come
    # from the same index, since torchvision is version-locked to torch.
    # $env:TORCH_VERSION pins torch when the latest does not work on this system.
    $torchSpec = "torch"
    if ($env:TORCH_VERSION) {
        $torchSpec = "torch==$($env:TORCH_VERSION)"
        Write-Info "TORCH_VERSION is set. Installing $torchSpec"
    }

    & conda run --no-capture-output -n $EnvName pip install $torchSpec torchvision --index-url $torchIndex
    if ($LASTEXITCODE -ne 0) { Write-Err "Failed to install torch/torchvision from $torchIndex."; exit 1 }

    # Step 2: verify torch imports on this system.
    & conda run --no-capture-output -n $EnvName python -c "import torch" 2>$null
    if ($LASTEXITCODE -ne 0) {
        $badVer = Get-PipVersion $EnvName "torch"
        Write-Err "torch $badVer installed but crashes on import (likely a CUDA preloading issue)."
        Write-Err "Specify a known-working version and re-run:"
        Write-Err "  `$env:TORCH_VERSION='<version>'; .\install.ps1 -Reinstall"
        Write-Err ""
        Write-Err "To find a working version, test manually in a clean conda env:"
        Write-Err "  pip install torch==<version> --index-url $torchIndex"
        Write-Err "  python -c `"import torch; print(torch.__version__, torch.cuda.is_available())`""
        exit 1
    }
    Write-Ok "torch $(Get-PipVersion $EnvName 'torch')"

    # Step 3: pin torch + torchvision to the versions just installed. Later
    # `pip install` calls use `-c $TorchConstraints` so pip cannot replace them
    # through transitive dependencies such as timm.
    $script:TorchConstraints = [System.IO.Path]::GetTempFileName()
    $torchVer = Get-PipVersion $EnvName "torch"
    $tvVer    = Get-PipVersion $EnvName "torchvision"
    Set-Content -Path $script:TorchConstraints -Value "torch==$torchVer"
    Add-Content -Path $script:TorchConstraints -Value "torchvision==$tvVer"

    Write-Info "Pinned PyTorch constraints:"
    Write-Info "  torch==$torchVer"
    Write-Info "  torchvision==$tvVer"

    # Step 4: timm and huggingface-hub from default PyPI, constrained so pip cannot
    # pull a different torch or torchvision through timm's dependencies.
    & conda run --no-capture-output -n $EnvName pip install timm huggingface-hub -c $script:TorchConstraints
    if ($LASTEXITCODE -ne 0) { Write-Err "Failed to install timm/huggingface-hub."; exit 1 }
}

function Setup-Env {
    param(
        [string]$EnvName,
        [string]$ReqFile,
        [string]$PythonVer = "3.11",
        [bool]  $InstallTorch = $false
    )

    if ((Test-CondaEnv $EnvName) -and (-not $Reinstall)) {
        Write-Warn "Conda environment '$EnvName' already exists. Skipping creation."
        Write-Warn "Run with -Reinstall to recreate it from scratch."
        return
    }

    if ((Test-CondaEnv $EnvName) -and $Reinstall) {
        Write-Info "Removing existing environment '$EnvName' for reinstall..."
        & conda env remove -y -n $EnvName
    }

    Write-Info "Creating conda environment '$EnvName' (python=$PythonVer)..."
    & conda create -y -n $EnvName "python=$PythonVer"
    if ($LASTEXITCODE -ne 0) { Write-Err "Failed to create environment '$EnvName'."; exit 1 }

    # Install torch before the general requirements so the CUDA-matched wheel is
    # already present when pip processes requirements.txt.
    if ($InstallTorch) { Install-PytorchPackages $EnvName }

    if (Test-Path $ReqFile) {
        Write-Info "Installing dependencies from $(Split-Path -Leaf $ReqFile) into '$EnvName'..."
        if ($script:TorchConstraints) {
            & conda run --no-capture-output -n $EnvName pip install -r $ReqFile -c $script:TorchConstraints
        } else {
            & conda run --no-capture-output -n $EnvName pip install -r $ReqFile
        }
        if ($LASTEXITCODE -ne 0) { Write-Err "pip install failed for '$EnvName'."; exit 1 }
    } else {
        Write-Warn "No requirements.txt found at $ReqFile. Skipping dependency install."
    }
}

# 3. Main FOCUS environment
Write-Info "Setting up main FOCUS environment..."
Setup-Env -EnvName "FOCUS" -ReqFile (Join-Path $ScriptDir "requirements.txt") -PythonVer "3.11" -InstallTorch $true

# Editable install, so the 'focus' console script entry point is registered.
Write-Info "Installing FOCUS package into 'FOCUS' environment..."
if ($script:TorchConstraints) {
    & conda run --no-capture-output -n FOCUS pip install -e $ScriptDir -c $script:TorchConstraints
} else {
    & conda run --no-capture-output -n FOCUS pip install -e $ScriptDir
}
if ($LASTEXITCODE -ne 0) { Write-Err "FOCUS package install failed."; exit 1 }
Write-Ok "FOCUS package installed. You can now run 'focus [--config ...]' after activating the FOCUS env."

# 4. Optional tool environments (tools\<Name>\)
$toolsDir = Join-Path $ScriptDir "tools"
if (Test-Path $toolsDir) {
    Get-ChildItem -Path $toolsDir -Directory | ForEach-Object {
        $subName = $_.Name
        $envName = "FOCUS_$subName"
        $reqFile = Join-Path $_.FullName "requirements.txt"

        Write-Info "Setting up tool environment '$envName'..."
        Setup-Env -EnvName $envName -ReqFile $reqFile -PythonVer "3.11" -InstallTorch $false

        # OpenJDK is required by Java-dependent tools such as ASHLAR.
        if (Test-CondaEnv $envName) {
            Write-Info "Ensuring OpenJDK is present in '$envName'..."
            & conda install -y -n $envName -c conda-forge openjdk 2>$null
            if ($LASTEXITCODE -ne 0) {
                Write-Warn "OpenJDK install skipped (may already be present or unavailable)."
            }
        }
    }
} else {
    Write-Info "No 'tools\' directory found. Skipping tool environments."
}

if ($script:TorchConstraints -and (Test-Path $script:TorchConstraints)) {
    Remove-Item -Force $script:TorchConstraints
}

Write-Host ""
Write-Ok "All environments are ready."
Write-Host ""
Write-Host "  To start FOCUS, activate the environment and run:"
Write-Host "    conda activate FOCUS"
Write-Host "    focus                                  # launches the GUI"
Write-Host "    focus --config C:\path\to\config.json  # CLI mode"
Write-Host ""
