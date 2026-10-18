@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
title Trading Bot - First Time Setup
echo ==========================================================
echo   UK Trading Bot - first time setup
echo   Everything is installed inside this folder only.
echo ==========================================================
echo.
set "PY="
where py >nul 2>nul
if not errorlevel 1 py -3.14 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=py -3.14"
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY goto nopython
%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"
if errorlevel 1 goto oldpython
echo Using Python:
%PY% --version
echo.
if not exist ".venv\Scripts\python.exe" (
  echo Creating the project's own Python environment in the .venv folder...
  %PY% -m venv .venv
  if errorlevel 1 goto venvfail
)
echo Installing the required packages - this can take a few minutes...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto pipfail
echo.
".venv\Scripts\python.exe" app.py init
if errorlevel 1 goto initfail
echo.
".venv\Scripts\python.exe" app.py selfcheck
echo.
echo ==========================================================
echo   Setup finished. Next: double-click "2 - Start Trading Bot.bat"
echo ==========================================================
pause
exit /b 0

:nopython
echo Python was not found. Install Python 3.14 from python.org and tick "Add python.exe to PATH".
pause
exit /b 1
:oldpython
echo Your Python is too old. Python 3.12 or newer is required; 3.14 is recommended.
pause
exit /b 1
:venvfail
echo Could not create the virtual environment. See the message above.
pause
exit /b 1
:pipfail
echo Package installation failed. Check your internet connection and the messages above.
pause
exit /b 1
:initfail
echo The first start failed. See logs\tradebot.log and the messages above.
pause
exit /b 1
