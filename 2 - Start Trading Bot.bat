@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo The bot is not installed yet. Please run "1 - First Time Setup.bat" first.
  pause
  exit /b 1
)
title UK Trading Bot - PAPER TRADING - keep this window open
".venv\Scripts\python.exe" app.py run
echo.
echo The trading bot has stopped.
pause
