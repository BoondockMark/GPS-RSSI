@echo off
setlocal
cd /d "%~dp0"
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 goto use_python
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 goto use_py
if exist ".venv\Scripts\python.exe" goto use_venv
echo Python 3.11 or later is required. Install Python, then rerun install.cmd.
set "GPS_RSSI_EXIT=1"
goto finish
:use_python
python "%~dp0scripts\install.py" %*
set "GPS_RSSI_EXIT=%ERRORLEVEL%"
goto finish
:use_py
py -3 "%~dp0scripts\install.py" %*
set "GPS_RSSI_EXIT=%ERRORLEVEL%"
goto finish
:use_venv
".venv\Scripts\python.exe" "%~dp0scripts\install.py" %*
set "GPS_RSSI_EXIT=%ERRORLEVEL%"
:finish
if /i "%~1"=="--help" goto done
if /i "%~1"=="-h" goto done
if not defined CI pause
:done
exit /b %GPS_RSSI_EXIT%
