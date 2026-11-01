@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0BUILD_HOOK.ps1"
if errorlevel 1 echo.
if errorlevel 1 echo BUILD FAILED - Read the error above and the README.
pause
