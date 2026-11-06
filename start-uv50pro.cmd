@echo off
call "%~dp0scripts\run-gps-rssi.cmd" start --profile fixed --config "%~dp0uv50pro.toml"
exit /b %ERRORLEVEL%
