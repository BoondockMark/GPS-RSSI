@echo off
call "%~dp0scripts\run-gps-rssi.cmd" start --config "%~dp0mobile.toml" %*
exit /b %ERRORLEVEL%
