@echo off
setlocal EnableDelayedExpansion
title PumpAI — Startup Automation Console
chcp 65001 >nul 2>&1

:: =============================================================================
::  run_pump_ai.bat  —  PumpAI Daily Startup Automation (Windows)
::  Usage:  Double-click  OR  run from CMD / PowerShell
::  Effect: Boots the FastAPI backend + static frontend server, opens browser.
::          Closing this window (or pressing Q) kills BOTH background tasks.
:: =============================================================================

:: ── Constants ──────────────────────────────────────────────────────────────
set "REPO_ROOT=%~dp0"
:: Strip trailing backslash
if "%REPO_ROOT:~-1%"=="\" set "REPO_ROOT=%REPO_ROOT:~0,-1%"

set "BACKEND_DIR=%REPO_ROOT%\backend"
set "BACKEND_HOST=0.0.0.0"
set "BACKEND_PORT=8000"
set "FRONTEND_HOST=127.0.0.1"
set "FRONTEND_PORT=3000"
set "BROWSER_URL=http://127.0.0.1:%FRONTEND_PORT%"

set "BACKEND_PID="
set "FRONTEND_PID="

:: ── Detect LAN IP ──────────────────────────────────────────────────────────
for /f "delims=" %%I in ('python -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(0.5); s.connect(('8.8.8.8',80)); print(s.getsockname()[0]); s.close()" 2^>nul') do set "LAN_IP=%%I"
if not defined LAN_IP set "LAN_IP=127.0.0.1"

:: ── Banner ─────────────────────────────────────────────────────────────────
call :section "PumpAI Startup Automation"
echo  Date: %DATE%   Time: %TIME%
echo  LAN IP detected: %LAN_IP%

:: ==========================================================================
:: STEP 1 — Verify Python Environment
:: ==========================================================================
call :section "STEP 1 — Verifying Python Environment"

cd /d "%BACKEND_DIR%"

:: Prefer venv, fall back to system Python
set "PYTHON_BIN="
if exist "%BACKEND_DIR%\venv\Scripts\python.exe" (
    echo [INFO]  Virtual environment found at backend\venv — activating...
    call "%BACKEND_DIR%\venv\Scripts\activate.bat"
    set "PYTHON_BIN=%BACKEND_DIR%\venv\Scripts\python.exe"
    echo [ OK ]  venv activated: !PYTHON_BIN!
) else if exist "%BACKEND_DIR%\.venv\Scripts\python.exe" (
    echo [INFO]  Virtual environment found at backend\.venv — activating...
    call "%BACKEND_DIR%\.venv\Scripts\activate.bat"
    set "PYTHON_BIN=%BACKEND_DIR%\.venv\Scripts\python.exe"
    echo [ OK ]  .venv activated: !PYTHON_BIN!
) else (
    echo [WARN]  No venv found. Checking system Python...
    for /f "delims=" %%P in ('where python 2^>nul') do (
        set "PYTHON_BIN=%%P"
        goto :py_found
    )
    for /f "delims=" %%P in ('where python3 2^>nul') do (
        set "PYTHON_BIN=%%P"
        goto :py_found
    )
    echo [ERROR] Python not found on PATH. Install Python 3.9+ and retry.
    pause
    exit /b 1
)
:py_found
for /f "delims=" %%V in ('"%PYTHON_BIN%" --version 2^>&1') do echo [ OK ]  Runtime: %%V

:: ==========================================================================
:: STEP 2 — Install / sync requirements.txt
:: ==========================================================================
call :section "STEP 2 — Installing / Verifying Python Dependencies"

if exist "%BACKEND_DIR%\requirements.txt" (
    echo [INFO]  Running: pip install -r requirements.txt
    "%PYTHON_BIN%" -m pip install -r "%BACKEND_DIR%\requirements.txt" --quiet --disable-pip-version-check
    if !ERRORLEVEL! NEQ 0 (
        echo [WARN]  pip install reported errors — some packages may be missing.
    ) else (
        echo [ OK ]  All requirements satisfied.
    )
) else (
    echo [WARN]  requirements.txt not found — skipping pip install.
)

:: Verify uvicorn is available
"%PYTHON_BIN%" -m uvicorn --version >nul 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo [ERROR] uvicorn is not installed. Run: pip install uvicorn
    pause
    exit /b 1
)
for /f "delims=" %%U in ('"%PYTHON_BIN%" -m uvicorn --version 2^>&1') do echo [ OK ]  %%U

:: ==========================================================================
:: STEP 3 — Launch PumpAI System Tray Application
:: ==========================================================================
call :section "STEP 3 — Launching PumpAI in System Tray"

cd /d "%BACKEND_DIR%"
echo [INFO]  Launching tray_app.py silently in the background...

:: Resolve virtualenv pythonw.exe from python.exe
set "PYTHONW_BIN=!PYTHON_BIN:python.exe=pythonw.exe!"

start "" "!PYTHONW_BIN!" "%BACKEND_DIR%\tray_app.py"

echo [ OK ]  PumpAI has been launched in the system tray.
echo         You can open the workspace or manage the application via the system tray icon.
echo.
timeout /t 3 /nobreak >nul
exit /b 0

:: ==========================================================================
:: Helper: Print a section divider with a title
:: ==========================================================================
:section
echo.
echo ══════════════════════════════════════════════════════════════
echo   %~1
echo ══════════════════════════════════════════════════════════════
exit /b 0
