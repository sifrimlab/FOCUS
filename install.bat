@echo off
:: ── FOCUS installation script — Windows shim ─────────────────────────────────
:: The real installer is install.ps1 (PowerShell), which mirrors install.sh:
:: CUDA detection, CUDA-matched PyTorch wheels, the FOCUS env, and tool envs.
:: This shim keeps the documented `install.bat [--reinstall]` entry point
:: working by forwarding to install.ps1.
::
:: Usage: install.bat [--reinstall]
setlocal EnableDelayedExpansion

:: Translate the legacy --reinstall flag to the PowerShell -Reinstall switch;
:: pass any other arguments through unchanged.
set "PS_ARGS="
for %%A in (%*) do (
    if /I "%%~A"=="--reinstall" (
        set "PS_ARGS=!PS_ARGS! -Reinstall"
    ) else (
        set "PS_ARGS=!PS_ARGS! %%~A"
    )
)

:: Prefer PowerShell 7+ (pwsh) when present, otherwise Windows PowerShell.
where pwsh >nul 2>&1
if !errorlevel!==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" !PS_ARGS!
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" !PS_ARGS!
)

set "RC=!errorlevel!"
endlocal & exit /b %RC%
