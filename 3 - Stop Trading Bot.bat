@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo The bot is not installed yet. Please run "1 - First Time Setup.bat" first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" app.py stop
timeout /t 3 >nul
