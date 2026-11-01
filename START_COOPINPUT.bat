@echo off
cd /d "%~dp0"
py -3 coopinput.py
if errorlevel 1 (
  echo.
  echo ERROR: CoopInput failed. Check coopinput_debug.log and the README.
  pause
)
