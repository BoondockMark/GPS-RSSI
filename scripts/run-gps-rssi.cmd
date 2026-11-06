@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo The project virtual environment was not found.
  echo See README.md for installation, then try the launcher again.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m gps_rssi.cli %*
set "GPS_RSSI_EXIT=%ERRORLEVEL%"
if not "%GPS_RSSI_EXIT%"=="0" (
  echo.
  echo GPS-RSSI stopped with an error. See the message above.
  pause
)
exit /b %GPS_RSSI_EXIT%
