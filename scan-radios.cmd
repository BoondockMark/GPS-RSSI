@echo off
setlocal
call "%~dp0scripts\run-gps-rssi.cmd" scan --select
set "GPS_RSSI_EXIT=%ERRORLEVEL%"
if "%GPS_RSSI_EXIT%"=="0" (
  echo.
  pause
)
exit /b %GPS_RSSI_EXIT%
