# CoopInput — multi-keyboard and multi-mouse controller for Windows

**Open-source application source · Windows 10/11 x64 · CoopInput v12.9 core**

CoopInput assigns keyboards and mice to up to four players, supports drag-and-
drop device assignment, attached game windows, and independent **virtual**
pointers with customizable colors, cursor shapes, and desktop interactions.
It is built around the stable v8 game-input engine.

> **Platform limitation:** Windows has only one standard system pointer and
> keyboard focus. CoopInput's other cursors are virtual; some apps, games,
> taskbar actions, native drag-and-drop and protected windows cannot be
> controlled exactly like Player 1's physical pointer.

## For normal users: install

1. Download the `CoopInput-Setup-*.exe` installer artifact from a **trusted**
   project release or build one yourself (see below).
2. Follow the installer; it makes Start Menu shortcuts and adds an uninstaller.
3. Set up the external Interception driver/library following `DRIVER_SETUP.md`.
   The kernel driver is NOT included in the installer. Local builds can include the official x64 `interception.dll` when supplied in `vendor/Interception/x64/`.
4. Launch CoopInput, assign secondary devices, and open your games normally.

## Build an installer yourself (Windows)

1. Install **Python x64 (3.11–3.14)** with `py` launcher, **Visual Studio C++
   Build Tools** with MSVC x64, and **Inno Setup 6**.
2. Clone/download this repository, open PowerShell inside it.
3. Run `powershell -NoProfile -ExecutionPolicy Bypass -File .\BUILD_RELEASE.ps1`.
4. Find `output/CoopInput-Setup-12.9.3.exe` and the unpacked `dist/CoopInput/`.
5. To run it, install the external Interception driver and DLL as documented.

If the official `vendor/Interception/x64/interception.dll` is present, the installer includes it automatically (noncommercial licensing conditions apply). The script builds `coopinput_hook.dll` from source using the **existing v8 C
hook** and MinHook, packages Python with PyInstaller, and compiles an Inno Setup
installer. Windows users do NOT need Python after installing the resulting app.
No unverified third-party binary is silently fetched or installed by CoopInput.

For a faster portable development run, install the official x64 Interception
library beside `coopinput.py` and run `py -3 coopinput.py`.

## Open-source collaboration

Original CoopInput code is released under **MIT** (subject to contributors'
rights to grant this license). You can fork it, open issues, send pull requests,
modify the UI and mouse routing, and build your own version.
See `CONTRIBUTING.md`, `docs/LICENSING.md`, and `THIRD_PARTY_NOTICES.md`.
The external Interception *driver* has separate terms and is **not** wholly
open source; avoid describing the entire stack as fully open source.

## Build automatically on GitHub

Push this clean repository to GitHub. In **Actions → Windows installer →
Run workflow**, the job builds the source, portable app, and installer on a
Windows runner and attaches downloadable artifacts to the build. No secrets or
local device settings are included. This does not publish a release automatically.

## Privacy, permissions and anti-cheat

- Inputs are processed locally. No account, telemetry, or cloud service is
  included in the uploaded application source.
- The Interception driver requires admin permission to **install** (not to run
  the app as a normal user, though some games need additional privileges).
- Game-hook injection may trigger anti-cheat or antivirus warnings. Use only
  with offline/LAN games and on systems where you have permission.
- Logs and user-specific device assignments are kept in
  `%LOCALAPPDATA%\CoopInput\` for the installed EXE, not inside the installer.
- Emergency release: **Ctrl + Alt + F12** (from the existing application).

## Original feature guide

See `docs/FEATURES_V12_9.md` for the complete feature history and controls.

## Local build with both DLLs included

This release kit includes a copy of the x64 `interception.dll` from the uploader's functioning v12.9 distribution, **for building and testing under the upstream noncommercial terms**. The original DLL is under `vendor/Interception/x64/interception.dll` and is excluded from Git by `.gitignore`.

Run `powershell -NoProfile -ExecutionPolicy Bypass -File .\BUILD_RELEASE.ps1` on Windows to compile the v8 game hook, bundle both DLLs, and create the installer. No manual DLL copying is required by the person installing this local build.

**Still required:** the Interception **kernel driver**, a different component, must be installed with administrator approval following the official upstream instructions. This kit does not silently install a system driver. See `DRIVER_SETUP.md`.

For the public open-source GitHub project, the `vendor/` DLL is excluded, so continuous integration builds are source-only and require the library separately. Never advertise the Interception dependency as covered by CoopInput's MIT license. Commercial use needs appropriate upstream licensing.
