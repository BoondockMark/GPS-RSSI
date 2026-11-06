@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The project virtual environment was not found.
  echo See README.md for installation, then double-click this file again.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m gps_rssi.cli scan --select
set "GPS_RSSI_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %GPS_RSSI_EXIT%
