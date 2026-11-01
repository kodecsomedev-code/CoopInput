@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL_INTERCEPTION_LIBRARY.ps1"
if errorlevel 1 (
  echo Could not install the library. See DRIVER_SETUP.md.
  pause
)
