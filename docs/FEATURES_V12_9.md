# CoopInput v12.8 — Native Windows Cursors & Desktop Compatibility

**Built on your working v8 game input engine and v12.7 assignment UI.**
This release replaces handmade virtual-pointer artwork with the real, currently
installed Windows cursor scheme. It also improves taskbar interaction and
adds an optional app-focus switch, while keeping Player 2's physical mouse
intercepted and separate from Player 1's OS cursor.

## Quick start

1. Extract the ZIP into a new folder.
2. Run `START_COOPINPUT.bat` as usual (same working DLLs, no rebuild).
3. Assign Player 2's keyboard and mouse through drag-and-drop.
4. Move the virtual cursor over a standard Windows edit box, button, resize
   border, or taskbar button to see the matching **real Windows cursor icon**.
5. For Windows controls outside the game, optionally select **Win Keys: ON**.
6. For apps that require keyboard focus (like browser address bars), open
   **Mouse / Cursor → Pointer actions → Focus clicked desktop app**.
   **CAUTION:** Windows focus is shared, so doing this may interrupt Player 1's
   keyboard/game window. It never hands Player 2's physical mouse to Player 1.

## Native Windows cursor artwork

Rather than shipping potentially outdated third-party PNGs or .cur assets,
we call Microsoft's `LoadCursorW` for the actual system shapes (arrow, I-beam,
hand, horizontal/vertical/diagonal resize, move, crosshair, wait, forbidden).
We render those resources into an isolated click-through cursor overlay using
`GetIconInfo`, `DrawIconEx`, and an in-memory PNG. Original click hotspots are
preserved. The cursor theme installed on **your own Windows** is respected at
app startup; restart CoopInput after changing the Windows cursor scheme.

This version adds no Python package dependencies. It **does not** call
`SetCursor`, `SetCursorPos`, `ShowCursor`, or `SendInput` to move or hide the
real primary pointer. The code still uses the same v8 Interception and hook DLLs.

## Better desktop / taskbar / browser behavior

- **Taskbar:** On standard Windows taskbar buttons, a simple left click first
  attempts Microsoft Active Accessibility (`IAccessible.accDoDefaultAction`).
  It runs on a worker thread so a slow Shell component doesn't stall the input
  capture loop; failure falls back to the ordinary mouse messages.
- **Desktop icons:** Keep click/selection and drag semantics through ordinary
  mouse messages. We deliberately do **not** use accessibility default actions
  on a desktop icon: doing so could OPEN an item when you meant single-click
  selection.
- **Standard Windows apps:** Existing dragging, resizing, hover, scrolling,
  title bars, modifier keys, and click capture remain available.
- **Browsers and modern controls:** A separate nonblocking accessibility-role
  probe can indicate links/buttons/text via system hand/I-beam where their
  accessibility tree exposes those elements. The optional **Focus clicked app**
  feature requests normal Windows foreground focus when clicking a window.

### Actual compatibility limits

This is a **virtual pointer, not a second native Windows mouse/desktop seat**.
Win32 `PostMessageW` cannot completely reproduce physical mouse semantics for
Explorer OLE file drag/drop, Chromium mouse capture, browser link hover,
UWP/WinUI/XAML controls, elevated apps, secure desktop dialogs, hardware DPI,
or every taskbar action. Some apps validate the real physical system cursor
and won't accept the virtual one. Windows also provides only one active
foreground window, regardless of how many virtual cursors CoopInput draws.

The native pointer icons are therefore **real Windows artwork**, but the app
still uses best-effort hit testing and accessibility information to choose the
correct icon. We deliberately do not read the primary Windows pointer's
current shape, because it belongs to Player 1.

## Verification

`python test_native_windows_pointer.py` tests PNG generation, image mapping,
Shell taskbar click fallback, drag behavior and the no-global-mouse contract.
`python test_adaptive_cursor.py` checks cursor shapes and input-path separation.
The other `test_*.py` files cover all preexisting v8/v12 game, hotkey, desktop,
UI and mouse routing invariants. Testing the exact icons and interaction in
Windows 11 still requires running this build on an actual Windows PC.

---

# CoopInput v12.6 — Advanced Virtual Mouse

**Built on the working CoopInput v8 mouse/keyboard game engine.** The v8 `coopinput_hook.dll`, `interception.dll`, game-side raw-input logic and camera fixes are unchanged. This update enhances **desktop virtual mouse interactions** only. The dual-game launcher is still removed.

## Launch

1. Extract the ZIP to a new folder, not on top of a running installation.
2. Run `START_COOPINPUT.bat` on Windows 11/10 64-bit. The Interception driver must already be installed, as with v8.
3. Drag Player 2's physical keyboard and mouse into the Player 2 assignment area.
4. Open **Mouse / Cursor** → **Pointer actions ›** for mouse preferences.
5. Keep **Win Keys: OFF** for isolated game input. Set **Win Keys: ON** when Player 2 needs to pass **keyboard** shortcuts through to the active Windows application. **This never transfers Player 2's mouse to Player 1's system cursor.**
6. Use **Attach game** (manual attach) for a compatible, already-running game. Dual-game launching is NOT included.

Copy your existing `coopinput_config.json` into the new folder if you want to keep assignments. The v12.6 version adds a safe `mouse_features` section; older settings files remain compatible.

### Important shortcuts

- `Ctrl+Alt+F12` on one physical keyboard: panic reset, return assigned devices to Windows.
- The configured Win Keys shortcut (default F10) toggles only **keyboard** Windows passthrough; Player 2's physical mouse stays isolated.
- Player 2 Mouse / Cursor retains the older show/hide hotkey and automatic in-game hiding.

## Desktop virtual mouse: supported actions and limits

Windows is designed around one native system cursor, mouse capture state, global app focus and its native OLE drag-and-drop system. CoopInput uses **posted Win32 messages to client windows** plus independent per-player capture. That is not a second native Windows cursor. Some applications, browser surfaces, GPU-rendered windows, elevated windows and security-sensitive UI ignore posted mouse messages. **No universal equivalence to Player 1's mouse is claimed.**

| Action | v12.6 implementation | Compatibility |
| --- | --- | --- |
| Hover / tooltip | `WM_MOUSEMOVE`, timed `WM_MOUSEHOVER` and `WM_MOUSELEAVE` | Best-effort; many apps check native pointer and TrackMouseEvent |
| Left/right/middle click | Distinct button up/down Win32 messages | Works with receptive desktop windows |
| Double click | Recognizes physical sequences and sends `WM_*DBLCLK` | Best-effort; requires app support |
| Triple / quadruple click | Correct successive button press/release with double-click messages for second and fourth presses | App decides selection semantics |
| Left drag / marquee / text selection | Held capture targets original HWND until last release | Best-effort in receptive client applications |
| Right drag | Right button independently holds its capture | Explorer's native shell *Copy/Move/Shortcut* OLE drop menu not guaranteed |
| Ctrl-/Shift-/Alt-drag | Physical modifier keys are tracked; `MK_CONTROL` and `MK_SHIFT` sent with pointer messages | Ctrl, Shift application-dependent; Alt relies on shared OS key state |
| Chording / simultaneous buttons | Individual button-ownership state and full held-button flags | Depends on app |
| Title-bar move / window edge resize | Moves or resizes top-level window via `SetWindowPos` without OS pointer movement | Standard non-elevated Win32 windows |
| Vertical scroll | Wheel messages with actual physical deltas | Receptive windows |
| Horizontal scroll | Native horizontal tilt or Shift + vertical wheel | Receptive windows; configure substitution |
| Ctrl + scroll zoom | Modifier forwarded with wheel messages | App implements zoom semantics |
| Middle-button auto-pan | Optional timer-driven wheel messages; click middle again to stop | Experimental; off by default |
| Back/Forward X1/X2 buttons | Proper X-button messages with correct high word | Browser/Explorer may support them |
| ClickLock | Optional long hold then release keeps virtual left down until next press | Off by default; use consciously |
| NumPad Mouse Keys | Optional numeric keypad pointer movement/left click/left hold/release | Only with Win Keys ON; compatible keyboard layout needed |
| Dwell click | Optional hover timer triggers one left click, resets after pointer moves | Off by default |
| Sensitivity / profile switching | Software pointer-motion multipliers (Precision, Normal, Fast), optionally cycle from a selected X1/X2 button | **Not hardware DPI**; never affects attached v8 game hook |
| Software gesture substitutions | X1/X2 buttons for browser navigation, Ctrl+Wheel for zoom, Alt+Tab / Win+Tab through keyboard pass-through | Actual multitouch gestures are not generated |
| Hardware DPI / wheel release switch | Controlled by mouse's hardware/vendor firmware | **Cannot operate hardware switches** through virtual mouse messages |
| Native drag-and-drop of files across windows | Windows OLE and underlying cursor capture | **Not implemented as universal native DnD** |
| macOS Mission Control / Magic Mouse gestures | macOS-only gesture APIs; not part of Windows Win32 mouse | **Not available on Windows** |
| Windows Task View / desktop switching | Player 2 can press Win+Tab or Ctrl+Win+Arrow with Windows keyboard access | Changes the **shared** Windows session (affects Player 1) |

If clicking works in an app but drag/drop does not, the app likely requires actual OS pointer capture, OLE drag-and-drop, or global focus. CoopInput will not take the real cursor away from Player 1 to emulate these.

## Mouse / Cursor → Pointer actions

New options are per-player, saved under `mouse_features`, validated when loaded:

- Hover / double-click recognition on by default.
- Shift+wheel horizontal substitution on by default.
- Auto-pan, Dwell, ClickLock, and Mouse Keys off by default.
- Sensitivity 0.2–4.0; profiles Precision ×0.5, Normal ×1.0, Fast ×1.8.
- Select X1 or X2 to cycle the software profile (disabled by default). Assigning a button as both cursor-hotkey and profile-switch is unsupported; cursor visibility hotkey takes priority.
- Multi-click / hover / dwell / ClickLock timings are configurable.
- All mouse enhancements apply to desktop virtual pointer routing. Game mode uses the **original v8 hook events**.

### Numeric keypad mouse keys (optional)

Only with **Win Keys ON** and NumPad Mouse Keys enabled:

- NumPad 7–9, 4/6, 1–3: 8-direction pointer movement (key repeat steps movement).
- NumPad 5: left click (down/up).
- NumPad 0: hold left button.
- NumPad decimal: release left button.

These keys are consumed from Player 2's assigned keyboard instead of being sent to a game. Other keys retain their v8 behavior. Keep disabled when gaming.

## Testing

From a terminal with Python (no driver or Windows required for these simulated tests):

```sh
python test_advanced_pointer.py
python test_input_extensions.py
python test_desktop_drag.py
python test_regressions.py
python test_strict_mouse_isolation.py
python test_visual_regressions.py
```

Tests simulate window targets, click sequences, button ownership, profile validation, hover/dwell/ClickLock, autoscroll, keyboard/game input preservation, and monitor assignment. They **do not** verify Windows-specific physical devices, actual fullscreen Minecraft behavior, elevated windows, Explorer OLE operations, or gestures. Do those tests on a Windows PC before relying on the new features.

## Files

- `coopinput.py`: controller and UI; stable game input path with desktop-feature extensions.
- `desktop_interaction.py`: isolated virtual-pointer desktop state engine, no native system-pointer APIs.
- `coopinput_hook.dll`, `interception.dll`: binaries copied unchanged from your working v8 lineage.
- `v8_reference.py`: preserved reference code for regression comparisons.
- `test_*.py`: offline tests.

**Safety:** As with the previous versions, refrain from injecting hooks into anti-cheat-protected multiplayer games. Do not run with elevated privileges unless a specific trusted use case needs them.

## v12.7 — Smart virtual cursor shapes

The old overlay was a permanently drawn arrow, even over text fields or window-resize handles. v12.7 replaces it with an adaptive **visual-only** secondary pointer:

| Situation under virtual pointer | New cursor icon |
| --- | --- |
| Normal background and desktop | Arrow |
| Standard Win32 button, toolbar, link control | Hand |
| Standard edit box, RichEdit, Scintilla | I-beam/text |
| Window border (horizontal, vertical, diagonal) | Corresponding two-headed resize arrow |
| Dragging a standard title bar | Four-direction move |
| Dragging client content | Grabbing hand (or I-beam for text selection) |
| Windows control exposes one of the standard OS cursor class handles | Matching cursor shape, including crosshair or busy where available |

**Notes:** Cursor detection is best-effort and **does not infer HTML link
state** inside custom-rendered browsers (Chrome, Edge, Firefox), modern XAML
interfaces or GPU-rendered games. These controls can still show an ordinary
arrow. Reading the native system cursor would report Player 1's cursor,
not Player 2's, so the controller deliberately does not do that.
The updated icon is an indication of an available action, not a guarantee
that an app will accept a virtual mouse click or OS drag-and-drop. The program
never changes Player 1's cursor via `SetCursor`, `ShowCursor` or `SetCursorPos`.

Cursor probing is throttled, and cross-thread hit testing runs only near
window edges. The input capture thread continues forwarding events independently.
No DLL rebuild is necessary. The working v8 DLLs are unchanged.

Run `python test_adaptive_cursor.py` to validate icon drawing, hit-test
classification, hot spots, game fallback and OS-pointer isolation.

---

## v12.9 — Custom cursor colors + improved Windows 11 taskbar interaction

This version is built on **v12.8**, preserving the original v8 keyboard/mouse
capture logic and the *same* `coopinput_hook.dll` and `interception.dll` binaries.
It does **not** reintroduce dual-game launching or physical-mouse passthrough.

### Change Player 2/3/4 cursor color

1. Assign your second mouse to Player 2 using the drag-and-drop board.
2. Open **Mouse / Cursor** for that player.
3. Click **Cursor color ●** at the top of the dialog.
4. Choose Blue, Cyan, Purple, Pink, Yellow, Green, Orange, Red, White,
   **Windows original**, or **Choose custom color…** for any RGB color.
5. The change is immediate and saved to `coopinput_config.json` under
   `cursor_colors`. Each player has their **own** cursor color.

Color is applied to **the actual Windows cursor shape pixels**, not the global
system mouse pointer. Dark outlines and transparency are retained. Choosing
**Windows original** uses the current untinted Windows theme. Cursor shapes,
hotspots, visibility hotkeys, and fullscreen-game behavior remain unchanged.

### Windows 11 taskbar buttons

Windows 11 often ignores mouse events sent with `PostMessageW` to Shell/XAML
controls. v12.9 adds **UI Automation control-pattern activation** (Invoke,
SelectionItem, Toggle, ExpandCollapse) specifically for Player 2/3/4 left clicks
on the Windows taskbar, with a legacy Microsoft Active Accessibility fallback.
This is handled on a background worker so a slow taskbar accessibility provider
should not stall physical input capture. Another new fallback hit-tests beneath
the secondary pointer's click-through overlay if Windows unexpectedly reports
that overlay as the target.

**Test:** Without attaching a game, bring Player 2's virtual cursor to the
Start button or a pinned taskbar app and left-click once. The virtual pointer
should remain independent. Also test hovering and interacting with a standard
window. A left-click on the taskbar writes a diagnostic line to
`coopinput_debug.log`, for example:

```
Taskbar virtual click at (x,y): accessibility action sent
```

or `no supported action; trying client messages`. If no `Taskbar virtual click`
line appears, the hit-test likely isn't finding the Shell taskbar window; send
the log and Windows version to help isolate it.

### Limitations (Windows OS, not a broken game hook)

UI Automation can activate supported taskbar controls, but Windows 11 taskbar,
Explorer desktop icons, browsers and applications are **not** fully independent
second mouse seats. Some buttons or taskbar context menus do not expose a
usable accessible action. `PostMessageW` returning success doesn't guarantee
an app handled the click. Secure surfaces, elevated windows and native file
OLE drag-and-drop remain outside the compatibility guarantee. Neither this
update nor its fallback uses `SetCursorPos` or `SendInput` to steal Player 1's
real cursor. The desktop's keyboard focus is still shared if Windows keys
or the optional Focus Clicked App setting is enabled.

**No DLL rebuild needed.** Both `.dll` files remain identical to v12.8.

Offline automated checks:

```
py test_color_taskbar.py
py test_native_windows_pointer.py
py test_regressions.py
```

These tests mock Win32 interactions and **cannot validate actual Windows 11
Shell/UI Automation providers**. Use the diagnostic log to confirm on your PC.
