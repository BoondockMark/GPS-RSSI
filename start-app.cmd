@echo off
call "%~dp0scripts\run-gps-rssi.cmd" workspace --directory "%~dp0." %*
exit /b %ERRORLEVEL%
