@echo off
call "%~dp0scripts\run-gps-rssi.cmd" start --config "%~dp0uvpro.toml"
exit /b %ERRORLEVEL%
