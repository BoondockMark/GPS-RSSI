@echo off
rem Compatibility launcher; use start-fixed.cmd for any supported radio.
call "%~dp0start-fixed.cmd" %*
exit /b %ERRORLEVEL%
