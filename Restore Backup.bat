@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo The bot is not installed yet. Please run "1 - First Time Setup.bat" first.
  pause
  exit /b 1
)
echo Backups in the backups folder:
dir /b backups\*.zip
echo.
set /p BK="Type the backup file name to restore (e.g. backup-20261018-120000.zip): "
if "%BK%"=="" exit /b 0
".venv\Scripts\python.exe" app.py restore "backups\%BK%"
pause
