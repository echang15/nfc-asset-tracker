@echo off
REM ================================================================
REM  Asset Tracker - Windows launcher. Double-click to start.
REM  First run creates a private Python environment (needs internet).
REM ================================================================
setlocal
cd /d "%~dp0"
title Asset Tracker

if exist ".venv\Scripts\python.exe" goto :install

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY goto :nopython
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul || goto :nopython

echo Setting up Asset Tracker for the first time (this takes a minute)...
%PY% -m venv .venv || goto :nopython

:install
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :pipfail
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements-reader.txt || echo WARNING: reader support (pyscard) could not be installed. Smart-card readers and tag programming will not work.

".venv\Scripts\python.exe" run.py --pcsc --open %*
echo.
echo Asset Tracker has stopped.
pause
exit /b 0

:nopython
echo.
echo Python 3.9 or newer is required (3.12 recommended).
echo 1. Download it from https://www.python.org/downloads/windows/
echo 2. In the installer, tick "Add python.exe to PATH".
echo 3. Double-click start-windows.bat again.
echo.
pause
exit /b 1

:pipfail
echo.
echo Installing the required packages failed. Check the internet connection
echo (needed on first run only) and try again.
pause
exit /b 1
