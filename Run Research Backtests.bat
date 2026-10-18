@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo The bot is not installed yet. Please run "1 - First Time Setup.bat" first.
  pause
  exit /b 1
)
title Trading Bot - research backtests
echo Walk-forward research for both strategies on your downloaded data.
echo This can take 10-30 minutes. Reports are saved in exports\reports.
echo.
".venv\Scripts\python.exe" app.py research --strategy trend_pullback breakout_momentum
echo.
pause
