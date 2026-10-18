@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo The bot is not installed yet. Please run "1 - First Time Setup.bat" first.
  pause
  exit /b 1
)
title Trading Bot - automated tests
echo Running the automated test suite (takes a few minutes)...
".venv\Scripts\python.exe" -m pytest -q
echo.
pause
