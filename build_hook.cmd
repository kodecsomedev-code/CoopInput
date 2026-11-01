@echo off
setlocal EnableExtensions
cd /d "%~dp0"
where cl >nul 2>nul
if errorlevel 1 (
  echo ERROR: x64 Microsoft C++ compiler not found in PATH.
  echo Run BUILD_HOOK.bat ^(automatically detects Visual Studio Build Tools^).
  exit /b 1
)
if not exist "minhook\include\MinHook.h" (
  echo ERROR: Official MinHook source is not present.
  echo Run BUILD_HOOK.bat to download the pinned official release.
  exit /b 1
)
cl /nologo /O2 /W3 /LD /D_WIN64 /I"minhook\include" /I"minhook\src" ^
  coopinput_hook.c ^
  "minhook\src\buffer.c" "minhook\src\hook.c" ^
  "minhook\src\trampoline.c" "minhook\src\hde\hde64.c" ^
  /link user32.lib /OUT:"coopinput_hook_v8.dll"
if errorlevel 1 (
  echo ERROR: DLL build failed. Existing hook DLL was NOT replaced.
  exit /b 1
)
echo Built coopinput_hook_v8.dll successfully.
exit /b 0
