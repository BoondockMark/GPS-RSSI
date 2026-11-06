@echo off
rem Compatibility launcher; use start-mobile.cmd for any supported radio.
call "%~dp0start-mobile.cmd" %*
exit /b %ERRORLEVEL%
