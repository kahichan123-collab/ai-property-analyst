@echo off
rem ===================================================================
rem  AI Property Analyst - double-click this file to start (Windows).
rem  ASCII only on purpose: cmd.exe reads .cmd files in the OEM codepage
rem  (cp950 on a zh-HK machine), so any Chinese text here would be split
rem  into bogus commands. The Chinese banner lives in _launch.ps1.
rem  Mac / Linux users: see README.txt and run  bash start_here.sh
rem ===================================================================
chcp 65001 >nul
title AI Property Analyst
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   Python not found / Python 3.10+ is required.
  echo   Install from https://www.python.org/downloads/
  echo   Tick "Add python.exe to PATH" during setup, then run this file again.
  echo.
  pause
  exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo.
  echo   Python 3.10 or newer is required. Current version:
  python -c "import sys;print(sys.version)"
  echo.
  pause
  exit /b 1
)

rem Bind to 127.0.0.1 only: reachable from this computer alone, no admin rights.
set ANALYST_PASSWORD=hkhouse2026
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set PORT=%1
if "%PORT%"=="" set PORT=8765

powershell -NoProfile -ExecutionPolicy Bypass -File "_launch.ps1" -Port %PORT%
start "" "http://127.0.0.1:%PORT%/"

python analyst_server.py %PORT% local

echo.
echo   Server stopped.
pause
