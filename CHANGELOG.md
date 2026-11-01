# Changelog

## 12.9.2 — Safe startup and emergency mouse recovery (2026-11-01)
- Always start with Input OFF, even when the previous session saved it as ON.
- Keep saved device assignments pending until the user explicitly restores them.
- Make Input OFF pass secondary physical mouse and keyboard events to Windows.
- Close the Interception context on capture-thread failures to release devices.
- Add a confirmation before restoring assignments that can take over a mouse.

## 12.9.1 — Game-hook startup repair (2026-11-01)
- Allow the C hook's full game-window detection window before reporting timeout.
- Report injection initialization stages and unload failed hooks for clean retries.
- Restore a reproducible Windows CI build from unpacked source files.
- Prefer the selected Python environment instead of an arbitrary newer launcher version.
- Roblox client injection remains unsupported; Minecraft Java compatibility requires real-device testing.

## 12.9.0 — Open-source packaging kit (2026-10-12)
- Preserves CoopInput v12.9 keyboard/mouse controller and game-hook source.
- Adds PyInstaller onedir Windows application build, Inno Setup installer,
  GitHub Actions workflow, MIT license and contributor documentation.
- Makes configuration/logs writable from installed EXE in LocalAppData.
- Removes all private log/config files and compiled DLLs from public source.
- Adds an optional wizard to copy a user-supplied official x64 Interception DLL.

## Earlier versions
See `docs/FEATURES_V12_9.md`.
