@echo off
call "%~dp0scripts\run-gps-rssi.cmd" start --profile fixed --config "%~dp0fixed.toml" %*
exit /b %ERRORLEVEL%
