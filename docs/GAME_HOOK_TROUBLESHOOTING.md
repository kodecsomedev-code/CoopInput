# Game-hook troubleshooting

This guide applies to the **unreleased** source repair, not the old installer or
source ZIP committed before it. The game hook is an experimental Windows x64
input-compatibility layer, not a guarantee of independent native mouse/keyboard seats.

## Before attaching

1. Install CoopInput and the official Interception driver separately as documented in
   [DRIVER_SETUP.md](../DRIVER_SETUP.md). Restart Windows if the driver installer asks.
2. Assign both the secondary player's keyboard and mouse in CoopInput. The primary
   physical Windows cursor should remain under Player 1's control.
3. Launch a supported **64-bit** game completely and keep its **actual game window**
   visible. Select that window, not a game launcher or background utility.
4. For Minecraft **Java Edition**, start with the game's windowed mode and select the
   `RAW` mouse mode. Test menus and a single-player/offline world first.
5. Click Attach once and wait up to 13 seconds. Avoid repeatedly clicking during
   initialization. If it fails, check the log and restart the game before testing
   the older 12.9 DLL again.

## Reading the log

Installed builds write `%LOCALAPPDATA%\\CoopInput\\coopinput_debug.log`.
Source builds use `coopinput_debug.log` next to `coopinput.py`.
Find the line containing `Game hook initialization failed` and its `stage=0xNN`.

| Stage | Meaning | Next step |
| --- | --- | --- |
| 0x00 | Hook could not open or report to shared memory | Check same Windows session, elevation/integrity level and game security restrictions |
| 0x01 | Shared memory opened | Wait; include the full log in a bug report if it times out |
| 0x02 | Waiting for a suitable game window | Select the real game process/window; avoid minimized/hidden states |
| 0x03 | Window located | Initialization stalled after locating the window; provide a log |
| 0x04 | Win32 hooks are being installed | Hook installation may be blocked or unsupported |
| 0x05 | DLL marked ready but heartbeat not seen | Check for a game-process crash or a stale DLL |
| 0x81 | No suitable visible window found in ten seconds | Wait for the game main menu; verify the process selection |
| 0x82 | MinHook initialization failed | Report the Windows version, game version and log |
| 0x83 | An input API hook could not be created | The game may be incompatible; do not disable game protection |
| 0x84 | Input hooks could not be enabled | Report the log; test only in permitted offline software |
| 0x85 | Game-window subclass installation failed | The window may have changed or may reject subclassing |

The older DLL lacks stage reporting; a generic timeout does not prove which
operation failed. The updated C DLL must be rebuilt together with the Python
controller. Do **not** replace only one component with a different ABI version.

## Known limitations

- Minecraft Java uses GLFW and can consume raw mouse motion, but some versions,
  launchers, mod loaders, overlays and multiple-instance arrangements may differ.
- Windows has one shared system keyboard focus. Virtual input does not create
  a second independent desktop, and some games rely on unsupported input APIs.
- CoopInput's injection path is **not supported for Roblox**. Do not attempt to
  evade or disable anti-cheat; prefer officially supported controls and tools.
- Driver installation, C compilation, and game compatibility require testing
  on a real Windows 10/11 x64 machine. Offline static tests alone cannot prove
  that a third-party game accepts the hook.

## Reporting a failure

Include the game edition/version (for example, Minecraft Java), Windows build,
source/build commit, the attempted game-window title (not private identifiers),
mouse mode, and relevant **redacted** error lines from the log. Do not publish
personal device IDs, user paths, or account details.
