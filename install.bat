@echo off
:: FOCUS installation script — Windows 10/11
:: Usage: install.bat [--reinstall]
setlocal EnableDelayedExpansion

set REINSTALL=0
for %%A in (%*) do (
    if "%%A"=="--reinstall" set REINSTALL=1
)

:: ── Resolve script directory ─────────────────────────────────────────────────
set "SCRIPT_DIR=%~dp0"
:: Remove trailing backslash
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

:: ── 1. Verify conda is available ─────────────────────────────────────────────
where conda >nul 2>&1
if errorlevel 1 (
    echo [ERROR] conda not found in PATH.
    echo.
    echo   Please install Miniconda or Anaconda and make sure it is initialised
    echo   for your shell before running this script.
    echo.
    echo   Miniconda: https://docs.conda.io/en/latest/miniconda.html
    echo   Anaconda:  https://www.anaconda.com/download
    echo.
    echo   After installation, open a new "Anaconda Prompt" and re-run this script.
    exit /b 1
)
for /f "tokens=*" %%v in ('conda --version 2^>^&1') do echo [OK]    conda found: %%v

:: ── Helper: check if a conda env exists ──────────────────────────────────────
:: Sets CONDA_ENV_EXISTS=1 if found, 0 otherwise
:check_env
    set "CHECK_NAME=%~1"
    set CONDA_ENV_EXISTS=0
    for /f "tokens=1" %%e in ('conda info --envs 2^>nul') do (
        if "%%e"=="%CHECK_NAME%" set CONDA_ENV_EXISTS=1
    )
goto :eof

:: ── Helper: setup a single environment ───────────────────────────────────────
:setup_env
    set "ENV_NAME=%~1"
    set "REQ_FILE=%~2"
    set "PY_VER=%~3"
    if "%PY_VER%"=="" set "PY_VER=3.11"

    call :check_env "%ENV_NAME%"

    if "!CONDA_ENV_EXISTS!"=="1" (
        if "%REINSTALL%"=="0" (
            echo [WARN]  Conda environment '!ENV_NAME!' already exists -- skipping creation.
            echo [WARN]  Run with --reinstall to recreate it from scratch.
            goto :eof
        )
        echo [INFO]  Removing existing environment '!ENV_NAME!' for reinstall...
        conda env remove -y -n "!ENV_NAME!"
    )

    echo [INFO]  Creating conda environment '!ENV_NAME!' (python=!PY_VER!)...
    conda create -y -n "!ENV_NAME!" python="!PY_VER!"
    if errorlevel 1 ( echo [ERROR] Failed to create environment '!ENV_NAME!'. & exit /b 1 )

    if exist "!REQ_FILE!" (
        echo [INFO]  Installing dependencies from requirements.txt into '!ENV_NAME!'...
        conda run --no-capture-output -n "!ENV_NAME!" pip install -r "!REQ_FILE!"
        if errorlevel 1 ( echo [ERROR] pip install failed for '!ENV_NAME!'. & exit /b 1 )
    ) else (
        echo [WARN]  No requirements.txt at !REQ_FILE! -- skipping dependency install.
    )
goto :eof

:: ── 2. Main FOCUS environment ─────────────────────────────────────────────────
echo [INFO]  Setting up main FOCUS environment...
call :setup_env "FOCUS" "%SCRIPT_DIR%\requirements.txt" "3.11"

echo [INFO]  Installing FOCUS package into 'FOCUS' environment...
conda run --no-capture-output -n FOCUS pip install -e "%SCRIPT_DIR%"
if errorlevel 1 ( echo [ERROR] FOCUS package install failed. & exit /b 1 )
echo [OK]    FOCUS package installed.

:: ── 3. Optional tool environments (tools\<Name>\) ─────────────────────────────
set "TOOLS_DIR=%SCRIPT_DIR%\tools"
if exist "%TOOLS_DIR%\" (
    for /d %%T in ("%TOOLS_DIR%\*") do (
        set "SUB_NAME=%%~nxT"
        set "ENV_NAME=FOCUS_!SUB_NAME!"
        set "REQ_FILE=%%T\requirements.txt"

        echo [INFO]  Setting up tool environment '!ENV_NAME!'...
        call :setup_env "!ENV_NAME!" "!REQ_FILE!" "3.11"

        call :check_env "!ENV_NAME!"
        if "!CONDA_ENV_EXISTS!"=="1" (
            echo [INFO]  Ensuring OpenJDK is present in '!ENV_NAME!'...
            conda install -y -n "!ENV_NAME!" -c conda-forge openjdk >nul 2>&1
        )
    )
) else (
    echo [INFO]  No 'tools\' directory found -- skipping tool environments.
)

:: ── Done ──────────────────────────────────────────────────────────────────────
echo.
echo [OK]    All environments are ready.
echo.
echo   To start FOCUS, activate the environment and run:
echo     conda activate FOCUS
echo     focus                            ^(launches the GUI^)
echo     focus --config C:\path\to\config.json    ^(CLI mode^)
echo.
endlocal