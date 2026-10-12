# CoopInput

**Multiple keyboards. Multiple mice. Independent controls. One Windows PC.**

CoopInput is an open-source Windows application that assigns physical keyboards and mice to different players, routes secondary input to compatible games, and displays independent virtual mouse pointers. It's designed for local multiplayer, multi-device experiments, and advanced desktop control—without handing Player 2's physical mouse over to Player 1's system cursor.

> **Current version:** v12.9 · **Platform:** Windows 10/11, 64-bit · **Core:** Python + C/Win32 · **App source license:** MIT (third-party components have their own terms)

## Features

### 🎮 Local multiplayer and device assignment

- Assign physical **keyboards and mice to up to four players**.
- **Drag and drop** devices between player cards in the control panel.
- Choose a **monitor for each secondary player** without changing the device's player assignment.
- Attach a compatible **already-running game window manually**.
- Keep Player 2's keyboard and mouse input separate from Player 1's standard Windows input.
- Forward keyboard and mouse events to compatible game windows through the C game hook, including **RAW, LEGACY and BOTH** mouse-input modes.
- Preserve the v8 game-input fixes, including protection against keyboard-generated camera spinning in Minecraft Java.
- **No built-in dual-game-instance launcher:** open your game instances with your preferred launcher and attach them manually.

### ⌨️ Keyboard support

- **Automatic keyboard identification / layout estimation** from available device information and observed keys.
- Labels for compact, full-size, expanded and **custom** keyboards; layouts are *not* artificial limits on recognized keys.
- Custom scan-code range where needed, plus modifier and key-state handling.
- Optional **Windows keyboard passthrough** for the assigned player, including shortcuts such as the Windows key, Alt+Tab and F11 when supported by the focused application.
- Keyboard passthrough **does not turn the secondary physical mouse into the primary Windows mouse**.
- Configurable keyboard shortcuts for secondary-cursor visibility and Windows keyboard mode.

### 🖱️ Independent virtual pointers

- Each secondary player has their own **visible virtual cursor**, screen position and mouse-button state.
- Use **actual Windows system cursor shapes** (arrow, hand, I-beam, resize, move, etc.), selected through best-effort hit testing.
- **Change the cursor color separately for each player** with presets, a custom RGB picker, or the original Windows appearance.
- **Automatically hide the virtual cursor during compatible fullscreen/gameplay capture**, show it in menus, or toggle it with a configurable keyboard/mouse shortcut.
- Set cursor visibility to automatic, always visible, or always hidden.
- Three desktop pointer-sensitivity profiles (**Precision, Normal, Fast**); optional profile switching using mouse side buttons.

### 🖥️ Virtual desktop mouse actions

The virtual desktop interaction layer supports or attempts the following actions in compatible Windows applications:

- Hover, left/right/middle click, **double-click**, and longer click sequences (including triple and quadruple clicks).
- **Click-and-hold**, left/right dragging, window title-bar movement, edge/corner resizing, text and marquee selection where applications accept virtual input.
- **Ctrl/Shift/Alt**-modified mouse actions, multiple held buttons, and mouse chording.
- Vertical and horizontal scrolling, Shift+scroll substitution, and Ctrl+wheel zoom where the target application supports them.
- Side-button **Back/Forward** events, optional middle-button auto-scroll, and optional click-to-focus for desktop applications.
- Optional accessibility tools: **ClickLock**, dwell-to-click, and **NumPad Mouse Keys**.
- Best-effort taskbar activation using **Windows UI Automation**, with accessibility and Win32-message fallbacks.
- Best-effort interactions with common desktop applications and accessible browser controls.

**Compatibility note:** This is a *virtual* cursor, not an additional native Windows mouse seat. Some apps ignore posted input messages or require real OS mouse capture. Native Explorer file drag-and-drop, secure/elevated windows, certain Windows 11 taskbar actions, and browser interactions are **not universally supported**. Hardware DPI switches and mechanical free-scroll are controlled by the mouse itself, not by CoopInput. Windows still has one shared keyboard focus.

### 🎨 Interface and safety

- **Dark/light themes**, modern device cards and a drag-and-drop player-assignment board.
- Device status, game attachment controls, monitor selection and per-player configuration.
- Saved settings and logs in **`%LOCALAPPDATA%\CoopInput`** for installed builds.
- **Emergency release: Ctrl + Alt + F12**.
- No cloud account or telemetry service is included in the supplied application source; input processing is local.

## Get started

### Install the Windows app

1. Download a trusted **`CoopInput-Setup-*.exe`** installer from the project's Releases page (when a release is published), or build the installer from source.
2. Install CoopInput with the Windows installer.
3. Install the external **Interception kernel driver** separately, with administrator approval, following [`DRIVER_SETUP.md`](DRIVER_SETUP.md). The driver is **not** silently installed by CoopInput.
4. Launch CoopInput, connect your keyboards and mice, and drag each device onto the desired player.
5. Open a compatible game normally, then use **Attach game** to connect the selected game window.

The installer's game-hook DLL is built from the included C source. Local installer builds may bundle the official x64 `interception.dll` when supplied and redistributed under its separate licensing terms; public source checkouts do not automatically include it.

### Build from source

Requirements: **Windows x64, Python 3.11–3.14, Visual Studio C++ Build Tools (x64/MSVC), and Inno Setup 6**.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\BUILD_RELEASE.ps1
```

When successful, the script builds the game-hook DLL, uses PyInstaller to bundle the Windows app, and produces the installer at:

```text
output/CoopInput-Setup-12.9.0.exe
```

For direct Python development, install the required Interception library and driver as documented, then run:

```powershell
py -3 coopinput.py
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development information and the automated tests (`test_*.py`). Windows hardware and game compatibility must still be tested on an actual Windows machine.

## Open source and licensing

CoopInput's **original application code is MIT-licensed** and welcomes forks, bug reports, pull requests, and custom builds. Its dependencies have their own licenses. In particular:

- **Interception** (external keyboard/mouse driver and library) has **separate distribution terms**; the complete driver stack is **not wholly open source**. Review [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and [`DRIVER_SETUP.md`](DRIVER_SETUP.md) before redistributing binaries, particularly for commercial use.
- **MinHook** is a third-party component with its own permissive license and notices.

## Known limitations and responsible use

- Compatibility varies by game, anti-cheat system, Windows version and application input APIs.
- Game hooking can trigger antivirus or anti-cheat warnings. Prefer offline/LAN games and software where hooking is permitted; do not use CoopInput to bypass anti-cheat restrictions.
- Secondary cursors do **not** create extra independent Windows desktops, foreground windows or protected-session access.
- Accessibility-based taskbar and browser control is **best-effort**, not guaranteed.
- The Windows installer/build scripts are provided as source; independently test resulting executables on Windows before public release.

---

**CoopInput makes it easier to share one Windows computer across multiple keyboards and mice, while keeping control over how each player's devices behave.**
