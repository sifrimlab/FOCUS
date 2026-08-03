# FOCUS container launcher for Windows PowerShell (Docker Desktop / Podman)
#
# Runs FOCUS inside Docker or Podman on Windows.
# The mount directory is mounted at the same Unix-style path inside the
# container, so all paths in your config file require no translation.
#
# Requirements:
#   - Docker Desktop (https://docs.docker.com/desktop/install/windows-install/)
#     OR Podman Desktop (https://podman.io/docs/installation)
#   - Run from a PowerShell prompt (not CMD)
#
# Usage:
#   .\focus-container.ps1 [OPTIONS] [-- FOCUS_ARGS...]
#
# Options:
#   -Runtime   docker|podman           (default: first available)
#   -Mount     DIR                     host directory to bind-mount (repeatable)
#                                      default: current directory
#   -Image     IMAGE                   docker/podman image  (default: focus)
#   -Port      INT                     GUI port             (default: 5050)
#   -Gpu                               pass GPU flags (--gpus all) at run time;
#                                      with -Build also bakes a CUDA PyTorch build
#   -Build                             build image before running (CPU torch by
#                                      default; CUDA when -Gpu is also set;
#                                      override with the TORCH_INDEX env var)
#
# Examples:
#   # GUI mode
#   .\focus-container.ps1 -Mount C:\data\mylab
#
#   # CLI mode
#   .\focus-container.ps1 -Mount C:\data\mylab -- --config /data/mylab/project/config.json
#
#   # Explicit runtime
#   .\focus-container.ps1 -Runtime podman -Mount C:\data\mylab

param(
    [string]   $Runtime = "",
    [string[]] $Mount   = @(),
    [string]   $Image   = "focus",
    [int]      $Port    = 5050,
    [switch]   $Gpu,
    [switch]   $Build,
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]] $FocusArgs = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Helpers
function Write-Info  ($msg) { Write-Host "[INFO]  $msg" -ForegroundColor Cyan   }
function Write-Ok    ($msg) { Write-Host "[OK]    $msg" -ForegroundColor Green  }
function Write-Warn  ($msg) { Write-Host "[WARN]  $msg" -ForegroundColor Yellow }
function Write-Err   ($msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red; exit 1 }

# Convert a Windows path (C:\foo\bar) to a Unix-style path (/c/foo/bar)
# so Docker mounts work correctly and preserve the directory tree.
function ConvertTo-UnixPath ([string]$WinPath) {
    $abs = [System.IO.Path]::GetFullPath($WinPath)
    # Drive letter → lowercase, backslash → slash, prepend /
    if ($abs -match '^([A-Za-z]):\\(.*)$') {
        $drive = $Matches[1].ToLower()
        $rest  = $Matches[2] -replace '\\','/'
        return "/$drive/$rest"
    }
    # Already a Unix-style path (WSL2 passthrough)
    return $abs -replace '\\','/'
}

# Runtime detection
if ($Runtime -eq "") {
    foreach ($rt in @("docker","podman")) {
        if (Get-Command $rt -ErrorAction SilentlyContinue) { $Runtime = $rt; break }
    }
}

if ($Runtime -eq "") {
    Write-Err @"
No container runtime found in PATH.

  Install one of:
    Docker Desktop: https://docs.docker.com/desktop/install/windows-install/
    Podman Desktop: https://podman.io/docs/installation

  After installation, restart PowerShell and try again.
  TIP: Singularity/Apptainer is not natively supported on Windows.
       Use WSL2 + focus-container.sh for Singularity support.
"@
}

Write-Ok "Using runtime: $Runtime"

# Default mount: current directory
if ($Mount.Count -eq 0) { $Mount = @((Get-Location).Path) }

# Resolve and convert mount paths
$UnixMounts = @()
foreach ($m in $Mount) {
    if (-not (Test-Path $m)) { Write-Err "Mount path does not exist: $m" }
    $unix = ConvertTo-UnixPath $m
    $UnixMounts += $unix
    Write-Info "Mounting: $m  →  $unix (same directory tree inside container)"
}

# Detect GUI vs CLI mode
$GuiMode = $true
foreach ($arg in $FocusArgs) {
    if ($arg -eq "--config" -or $arg -eq "-c") { $GuiMode = $false; break }
}

# Build
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Build) {
    # PyTorch wheel index baked into the image: CPU by default, CUDA when -Gpu
    # is requested at build time. Override explicitly with the TORCH_INDEX env var.
    if ($env:TORCH_INDEX) {
        $BuildTorchIndex = $env:TORCH_INDEX
    } elseif ($Gpu) {
        $BuildTorchIndex = "https://download.pytorch.org/whl/cu128"
    } else {
        $BuildTorchIndex = "https://download.pytorch.org/whl/cpu"
    }
    Write-Info "Building image '$Image' (TORCH_INDEX=$BuildTorchIndex)..."
    & $Runtime build --build-arg "TORCH_INDEX=$BuildTorchIndex" -t $Image $ScriptDir
    if ($LASTEXITCODE -ne 0) { Write-Err "Image build failed." }
}

# Verify image exists
$imgCheck = & $Runtime image inspect $Image 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Err @"
Image '$Image' not found.
  Build it first:
    $Runtime build -t $Image $ScriptDir
  Or pass -Build to this script.
"@
}

# Assemble run command
$RunArgs = @("run", "--rm", "--interactive", "--tty")

# Volume mounts: same Unix path on both sides
foreach ($unix in $UnixMounts) {
    $RunArgs += @("-v", "${unix}:${unix}")
}

# Port forwarding for GUI mode
if ($GuiMode) {
    $RunArgs += @("-p", "${Port}:${Port}")
    Write-Info "GUI mode: open http://localhost:$Port in your browser"
}

# GPU
if ($Gpu) { $RunArgs += @("--gpus", "all") }

# Image and FOCUS arguments
$RunArgs += $Image
$RunArgs += $FocusArgs

Write-Info "Running: $Runtime $($RunArgs -join ' ')"
& $Runtime @RunArgs
