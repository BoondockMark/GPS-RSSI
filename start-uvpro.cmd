@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The project virtual environment was not found.
  echo See README.md for installation, then double-click this file again.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m gps_rssi.cli start --config "%~dp0uvpro.toml"
set "GPS_RSSI_EXIT=%ERRORLEVEL%"
if not "%GPS_RSSI_EXIT%"=="0" (
  echo.
  echo GPS-RSSI could not start or stopped with an error. See the message above.
  pause
)
exit /b %GPS_RSSI_EXIT%
