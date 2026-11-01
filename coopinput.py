"""
CoopInput v12.9.3 - v8 engine / virtual desktop dragging / polished device board, strict input isolation and automatic keyboard detection
- Arraste dispositivos (mouse/teclado) entre areas: Dispositivos, Jogador 1..4
- Jogador 1 = principal (cursor normal do Windows). Jogadores 2+ = isolados,
  cada um com cursor virtual proprio (cor diferente) e tela escolhida.
- O cursor virtual segue o modo do jogo: menus mostram, camera captura/esconde.
- Atalho personalizado por teclado/mouse isolado alterna mostrar/esconder.
- Sem jogo anexado, o envio Win32 e best-effort; alguns jogos usam Raw Input/DirectInput.
- Configuracao salva em coopinput_config.json
- "Anexar jogo": injeta coopinput_hook.dll no jogo (64-bit, offline/LAN) para que ele
  trate o mouse/teclado do jogador como o mouse/teclado dele (sem mexer no seu PC)

Requisitos: Windows 64-bit + Python 64-bit + driver Interception instalado
e interception.dll (x64) na mesma pasta deste arquivo.
Panico: Ctrl+Alt+F12 em qualquer teclado devolve tudo ao Windows.
Debug: log em coopinput_debug.log; rode com -v para logar todo evento.
"""
import ctypes
import json
import logging
import mmap
import os
import platform
import queue
import re
import struct
import sys
import threading
import time
import traceback
import tkinter as tk
from desktop_interaction import DesktopDragRouter, DEFAULT_FEATURES, PROFILES, validated_features
from cursor_visuals import shape_for_target, HOTSPOTS
from windows_cursor_art import NativeSystemCursorArt, paint_system_cursor, COLOR_PRESETS, normalize_cursor_color
from shell_accessibility import ShellAccessibility, is_shell_class, accessible_cursor_shape
from ctypes import wintypes as wt
from tkinter import messagebox, ttk, colorchooser

# Installer-compatible paths: binaries beside the EXE; user data in AppData.
# Source-mode keeps the original v12.9 portable behavior.
HERE = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
if getattr(sys, "frozen", False):
    _appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    USER_DATA_DIR = os.path.join(_appdata, "CoopInput")
    os.makedirs(USER_DATA_DIR, exist_ok=True)
else:
    USER_DATA_DIR = HERE
LOG_PATH = os.path.join(USER_DATA_DIR, "coopinput_debug.log")
CFG_PATH = os.path.join(USER_DATA_DIR, "coopinput_config.json")
# Import an older portable config once, without bundling anyone's personal device IDs.
if getattr(sys, "frozen", False) and not os.path.exists(CFG_PATH):
    _old_config = os.path.join(HERE, "coopinput_config.json")
    if os.path.isfile(_old_config):
        try:
            import shutil as _shutil
            _shutil.copyfile(_old_config, CFG_PATH)
        except OSError:
            pass
VERBOSE = "-v" in sys.argv   # loga TODO evento de entrada (gera muito log)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d [%(threadName)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
              logging.StreamHandler()])
log = logging.getLogger("coopinput")


def _fmt(t, v, tb):
    return "".join(traceback.format_exception(t, v, tb))


sys.excepthook = lambda t, v, tb: log.critical("Excecao nao tratada:\n%s", _fmt(t, v, tb))
threading.excepthook = lambda a: log.critical(
    "Excecao na thread %s:\n%s", a.thread.name, _fmt(a.exc_type, a.exc_value, a.exc_traceback))

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception as e:
    log.debug("SetProcessDpiAwareness falhou (%s), usando SetProcessDPIAware", e)
    ctypes.windll.user32.SetProcessDPIAware()

user32 = ctypes.windll.user32

log.info("Python %s | %d-bit | %s", sys.version.split()[0],
         struct.calcsize("P") * 8, platform.platform())
log.info("Log em: %s (use -v para logar todo evento)", LOG_PATH)


def fatal(msg):
    log.critical("FATAL: %s", msg)
    user32.MessageBoxW(0, msg + "\n\nVeja coopinput_debug.log", "CoopInput", 0x10)
    raise SystemExit(1)


DLL_PATH = os.path.join(HERE, "interception.dll")
try:
    dll = ctypes.CDLL(DLL_PATH)
    log.info("interception.dll carregada")
except OSError as e:
    we = getattr(e, "winerror", None)
    log.error("Falha ao carregar a DLL: %s (winerror=%s)", e, we)
    if not os.path.exists(DLL_PATH):
        fatal("interception.dll nao encontrada. Siga DRIVER_SETUP.md para instalar o driver e colocar a biblioteca ao lado do CoopInput.exe.")
    if we == 193:
        fatal("DLL com arquitetura errada: use a x64 com Python 64-bit.")
    fatal(f"Nao consegui carregar interception.dll (erro {we}).")

# ---------- ctypes setup ----------
PRED = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)
KB_PRED = PRED(lambda d: 1 if 1 <= d <= 10 else 0)
MS_PRED = PRED(lambda d: 1 if 11 <= d <= 20 else 0)

dll.interception_create_context.restype = ctypes.c_void_p
dll.interception_destroy_context.argtypes = [ctypes.c_void_p]
dll.interception_set_filter.argtypes = [ctypes.c_void_p, PRED, ctypes.c_ushort]
dll.interception_wait_with_timeout.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
dll.interception_wait_with_timeout.restype = ctypes.c_int
dll.interception_receive.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
dll.interception_receive.restype = ctypes.c_int
dll.interception_send.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
dll.interception_send.restype = ctypes.c_int
dll.interception_get_hardware_id.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
dll.interception_get_hardware_id.restype = ctypes.c_size_t

user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = wt.HWND
ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [ENUM_WINDOWS_PROC, wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
user32.ChildWindowFromPointEx.argtypes = [wt.HWND, wt.POINT, wt.UINT]
user32.ChildWindowFromPointEx.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.GetParent.argtypes = [wt.HWND]
user32.GetParent.restype = wt.HWND
user32.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
user32.MapVirtualKeyW.restype = wt.UINT
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.GetGUIThreadInfo.argtypes = [wt.DWORD, ctypes.c_void_p]
user32.GetGUIThreadInfo.restype = wt.BOOL
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindow.restype = wt.BOOL
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = wt.BOOL
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetWindowPos.restype = wt.BOOL
# SendMessageTimeoutW handles a hung window without freezing the input thread.
user32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM,
                                      wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = ctypes.c_size_t

# Read class-defined cursor metadata WITHOUT calling SetCursor/ShowCursor.
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
if hasattr(user32, 'GetClassLongPtrW'):
    _get_class_cursor = user32.GetClassLongPtrW
    _get_class_cursor.argtypes = [wt.HWND, ctypes.c_int]
    _get_class_cursor.restype = ctypes.c_void_p
else:
    _get_class_cursor = user32.GetClassLongW
    _get_class_cursor.argtypes = [wt.HWND, ctypes.c_int]
    _get_class_cursor.restype = ctypes.c_void_p
user32.LoadCursorW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.LoadCursorW.restype = ctypes.c_void_p
_CURSOR_IDS = {
    'arrow': 32512, 'ibeam': 32513, 'busy': 32514,
    'crosshair': 32515, 'size_nwse': 32642, 'size_nesw': 32643,
    'size_we': 32644, 'size_ns': 32645, 'move': 32646,
    'forbidden': 32648, 'hand': 32649,
}
SYSTEM_CURSOR_HANDLES = {
    int(h): name for name, resource in _CURSOR_IDS.items()
    if (h := user32.LoadCursorW(None, ctypes.c_void_p(resource)))
}


class MouseStroke(ctypes.Structure):
    _fields_ = [("state", ctypes.c_ushort), ("flags", ctypes.c_ushort),
                ("rolling", ctypes.c_short), ("x", ctypes.c_int),
                ("y", ctypes.c_int), ("information", ctypes.c_uint)]


class KeyStroke(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("state", ctypes.c_ushort),
                ("information", ctypes.c_uint)]


class Stroke(ctypes.Union):
    _fields_ = [("mouse", MouseStroke), ("key", KeyStroke)]


user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
user32.GetWindow.restype = wt.HWND
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]

# ---------- hook DLL (injecao + memoria compartilhada) ----------
HOOK_PATH = next((q for q in (os.path.join(HERE, "coopinput_hook.dll"),
                              os.path.join(HERE, "hook", "coopinput_hook.dll"))
                  if os.path.exists(q)), os.path.join(HERE, "coopinput_hook.dll"))
k32 = ctypes.windll.kernel32
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.OpenProcess.restype = wt.HANDLE
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.IsWow64Process.argtypes = [wt.HANDLE, ctypes.POINTER(wt.BOOL)]
k32.VirtualAllocEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, wt.DWORD]
k32.VirtualAllocEx.restype = ctypes.c_void_p
k32.VirtualFreeEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD]
k32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
k32.GetModuleHandleW.restype = wt.HMODULE
k32.GetProcAddress.argtypes = [wt.HMODULE, wt.LPCSTR]
k32.GetProcAddress.restype = ctypes.c_void_p
k32.CreateRemoteThread.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                   ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
k32.CreateRemoteThread.restype = wt.HANDLE
k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
k32.GetExitCodeThread.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
k32.GetTickCount.restype = wt.DWORD

RING = 2048
HOOK_ABI_VERSION = 8
HOOK_BINARY_MARKER = b"COOPINPUT-HOOK-ABI-8"
HOOK_INIT_TIMEOUT = 13.0  # The DLL may wait 10s for a visible game window.
HOOK_BOOT_STAGES = {
    0: "The game DLL did not open the CoopInput shared memory. Check that both applications run in the same Windows session and at compatible privilege levels.",
    1: "The DLL opened shared memory but has not started searching for a game window.",
    2: "The DLL is waiting for a visible game window belonging to the selected process.",
    3: "The game window was found; input hooks are being prepared.",
    4: "The DLL is installing Windows input hooks.",
    5: "The DLL reported ready, but its heartbeat was not received.",
    0x81: "The game process has no suitable visible window. Select the game itself, not its launcher, and wait for its main menu.",
    0x82: "MinHook could not initialize inside the game process.",
    0x83: "One or more Win32 input hooks could not be installed in this game process.",
    0x84: "Windows input hooks were created but could not be enabled.",
    0x85: "The game's window procedure could not be attached.",
}


def hook_boot_status(link):
    """Read the optional startup status without changing the v8 shared ABI."""
    return (int(link.sh.pad[1]) >> 16) & 0xFF


def hook_startup_diagnostic(pid, since):
    """Collect a worker failure that occurs before it can report over IPC."""
    path = os.path.join(USER_DATA_DIR, f"hook_startup_{pid}.log")
    try:
        if os.stat(path).st_mtime < since - 1:
            return ""  # Ignore stale logs from a previous process with this PID.
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read(300).strip().replace("\r", " ").replace("\n", " ")
    except OSError:
        return ""


def hook_binary_compatible(path):
    """Fail closed rather than injecting an old DLL with a broken message queue.

    Presence of the marker is a format check, not a security signature.
    The shared-memory handshake below verifies the running DLL's ABI.
    """
    try:
        with open(path, "rb") as f:
            return HOOK_BINARY_MARKER in f.read()
    except OSError:
        return False

EV_MOVE, EV_BTN, EV_WHEEL, EV_KEY, EV_EXIT = 1, 2, 3, 4, 99
# Only the hook DLL sends mouse movement. Keyboard processing may emit EV_KEY,
# but must NEVER mutate the mouse position or generate a mouse event.
HOOK_MODE_RAW = 0x02
HOOK_MODE_LEGACY = 0x04
MOUSE_MODE_BITS = {'raw': HOOK_MODE_RAW, 'legacy': HOOK_MODE_LEGACY,
                   'both': HOOK_MODE_RAW | HOOK_MODE_LEGACY}
mouse_modes = {p: 'raw' for p in range(2, 5)}

# Visibility is specific to each SECONDARY virtual cursor. Never call the
# system-wide ShowCursor(), SetCursor() or ClipCursor() from the controller.
HOOK_CURSOR_HINT_VALID = 0x0100
HOOK_CURSOR_HINT_GRABBED = 0x0200
CURSOR_DISPLAY_MODES = ('auto', 'show', 'hide')
cursor_display_modes = {p: 'auto' for p in range(2, 5)}
cursor_manual_overrides = {p: None for p in range(2, 5)}  # hotkey override
cursor_hotkeys = {p: None for p in range(2, 5)}  # physical device + key/button
cursor_capture_request = None       # worker thread may consume the next key/button
cursor_ui_events = queue.SimpleQueue()  # capture thread -> Tk main thread
cursor_pressed_bindings = set()      # ignores key autorepeat and mouse bounce

MAXP = 4  # Define player limit BEFORE all per-player state is initialized.

# Optional WINDOWS KEYBOARD shortcuts, OFF at startup.
# A secondary mouse MUST NEVER pass through to the physical Windows cursor.
# Even when this mode is ON, only keyboard packets can go to the shared desktop;
# the player's mouse remains Interception-isolated and uses its own virtual
# coordinates, overlay and best-effort per-window mouse messages.
# Monitor changes never modify device ownership.
DESKTOP_KEYS = {'Off': None, 'F8': 0x42, 'F9': 0x43,
                'F10': 0x44, 'F11': 0x57, 'F12': 0x58}
desktop_control = {p: False for p in range(2, MAXP + 1)}
desktop_toggle_scancodes = {p: 0x44 for p in range(2, MAXP + 1)}
desktop_key_held = set()   # physical key down/up; prevents key-repeat toggles
desktop_requests = queue.SimpleQueue()  # UI -> capture thread only
# Mouse feature profile is independent for each player. All settings are opt-in
# except Win32 hover/click sequence compatibility and Shift+Wheel behavior.
mouse_features = {p: dict(DEFAULT_FEATURES) for p in range(2, MAXP + 1)}
physical_modifier_keys = {p: set() for p in range(2, MAXP + 1)}
mouse_subpixel = {p: [0., 0.] for p in range(2, MAXP + 1)}
mouse_key_latches = {p: set() for p in range(2, MAXP + 1)}
profile_key_latches = set()  # (physical device, button): one switch per press




def desktop_toggle_event(player, device, scan, up):
    """Consume both edges of the per-keyboard shortcut, never mouse events."""
    if player < 2 or assign.get(device, 0) != player:
        return False
    # Keyboard routing remains identical to v8; optional physical modifiers
    # and NumPad Mouse Keys are handled only here, in the existing v12 guard.
    # Windows-free hotkey unit tests don't load physical I/O globals.
    if 'physical_modifier_keys' in globals():
        keyboard_event_modifier(player, device, scan, up)
    token = (device, scan)
    if token in desktop_key_held:
        if up:
            desktop_key_held.discard(token)
        return True
    if desktop_toggle_scancodes[player] != scan:
        if ('mouse_features' in globals() and desktop_control[player]
                and mouse_features[player]['mouse_keys']):
            flags = keyboard_last[device][1]
            return handle_mouse_keys(player, scan, flags, up)
        return False
    if not up:
        desktop_key_held.add(token)
        set_desktop_mode(player, not desktop_control[player])
    return True


def keyboard_event_modifier(player, device, scan, up):
    """Modifier state for actual assigned physical keyboard in Windows mode."""
    flags = keyboard_last[device][1]
    vk = key_vk(KeyStroke(scan, flags, 0))
    if vk in (0x10, 0xA0, 0xA1, 0x11, 0xA2, 0xA3, 0x12, 0xA4, 0xA5):
        token = device, vk
        if up:
            physical_modifier_keys[player].discard(token)
        else:
            physical_modifier_keys[player].add(token)


def set_desktop_mode(player, enabled):
    """Runs on capture thread. Only keyboard can enter Windows' input stream."""
    if player not in desktop_control:
        return False
    enabled = bool(enabled)
    if desktop_control[player] == enabled:
        return False
    # Release held *keyboard* keys before switching delivery.
    # Mouse buttons and mouse ownership must remain isolated throughout.
    for d, p in list(assign.items()):
        if p == player and d <= 10:
            release_device(d, player)
    desktop_drag.cancel(player, *pos[player])
    desktop_control[player] = enabled
    cursor_ui_events.put(('desktop', player))
    log.info('Player %d Windows keyboard keys = %s; secondary mouse remains isolated',
             player, 'ON' if enabled else 'OFF')
    return True


def request_desktop_mode(player, enabled):
    """Safe from Tk callbacks; mouse routing is never changed to physical."""
    desktop_requests.put((player, bool(enabled)))



def virtual_cursor_visible(player):
    """Cursor visibility for one secondary player, without changing Windows."""
    if desktop_control.get(player, False):
        # Windows keyboard-access mode deliberately keeps the SECONDARY mouse
        # overlay visible even if an attached game still reports cursor capture.
        # This never alters ShowCursor or the real Windows pointer.
        return True
    override = cursor_manual_overrides[player]
    if override is not None:
        return bool(override)
    mode = cursor_display_modes[player]
    if mode != 'auto':
        return mode == 'show'
    link = links.get(player)
    if not link or not link.alive():
        return True  # no game attached -> desktop pointer
    hints = link.sh.pad[1]
    # Prior to the first ClipCursor() call, preserve v7's hidden-in-game default.
    if not hints & HOOK_CURSOR_HINT_VALID:
        return False
    return not bool(hints & HOOK_CURSOR_HINT_GRABBED)


def cursor_hotkey_event(player, device, kind, code, flags, down):
    """Called on the capture thread; NEVER touches Tk or sends mouse input."""
    global cursor_capture_request
    if player < 2 or assign.get(device, 0) != player:
        return False
    key = (device, kind, code, flags)
    request = cursor_capture_request
    if request and request['player'] == player and request['kind'] == kind and down:
        # Panic Ctrl+Alt+F12 is handled before this function by on_key().
        release_device(device, player)  # don't leave a formerly forwarded key stuck
        cursor_hotkeys[player] = {'device': device, 'kind': kind, 'code': code, 'flags': flags}
        cursor_pressed_bindings.difference_update({v for v in cursor_pressed_bindings if v[0] == device})
        cursor_pressed_bindings.add(key)
        cursor_capture_request = None
        cursor_ui_events.put(('bound', player))
        return True
    binding = cursor_hotkeys.get(player)
    if not binding or any((binding[k] != v) for k,v in (
            ('device',device), ('kind',kind), ('code',code), ('flags',flags))):
        return False
    if not down:
        cursor_pressed_bindings.discard(key)
    elif key not in cursor_pressed_bindings:
        cursor_pressed_bindings.add(key)
        cursor_ui_events.put(('toggle', player))
    return True  # swallow both down/up: hotkey never leaks into the game



class Ev(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int32), ("a", ctypes.c_int32),
                ("b", ctypes.c_int32), ("c", ctypes.c_int32)]


class Shared(ctypes.Structure):
    _fields_ = [("magic", ctypes.c_uint32), ("head", ctypes.c_uint32),
                ("dll_tick", ctypes.c_uint32), ("pos_x", ctypes.c_int32),
                ("pos_y", ctypes.c_int32), ("flags", ctypes.c_uint32),
                ("pad", ctypes.c_uint32 * 2), ("ev", Ev * RING)]


class Link:
    """Ligacao com a DLL injetada em um jogo (um jogador = um jogo)."""

    def __init__(self, pid, title):
        self.pid, self.title = pid, title
        self.mm = mmap.mmap(-1, ctypes.sizeof(Shared), tagname=f"CoopInput_{pid}")
        self.sh = Shared.from_buffer(self.mm)
        self.sh.magic = 0x504F4F43
        self.sh.head = 0
        self.sh.flags = HOOK_MODE_RAW
        self.sh.pad[0] = 0
        self.sh.pad[1] = 0

    def push(self, t, a=0, b=0, c=0):
        try:
            s = self.sh
            e = s.ev[s.head % RING]
            e.a, e.b, e.c, e.type = a, b, c, t
            s.head = (s.head + 1) & 0xFFFFFFFF
        except Exception:
            pass

    def alive(self):
        try:
            return bool(self.sh.flags & 1) and self.sh.pad[0] == HOOK_ABI_VERSION and ((k32.GetTickCount() - self.sh.dll_tick) & 0xFFFFFFFF) < 2000
        except Exception:
            return False

    def close(self):
        try:
            self.sh = None
            self.mm.close()
        except Exception:
            pass


links = {}   # jogador -> Link


def attach(p, pid, title):
    """Injeta a DLL no processo do jogo e liga ao jogador p. Retorna (ok, mensagem)."""
    if not os.path.exists(HOOK_PATH):
        return False, "Updated game hook not found. Run BUILD_HOOK.bat to compile coopinput_hook.dll before attaching a game."
    if not hook_binary_compatible(HOOK_PATH):
        return False, ("Outdated game-hook DLL detected. v8 is required for cursor-capture telemetry. Older DLLs may include input-routing bugs "
                       "that can cause unintended mouse movement. Run BUILD_HOOK.bat and replace the old DLL.")
    if any(l.pid == pid and q != p for q, l in links.items()):
        return False, "Esse jogo ja esta anexado a outro jogador."
    log.info("Attaching Player %d to '%s' (pid %d), hook %s", p, title, pid, HOOK_PATH)
    attach_started = time.time()
    h = k32.OpenProcess(0x0002 | 0x0400 | 0x0008 | 0x0020 | 0x0010, False, pid)
    if not h:
        return False, ("Nao consegui abrir o processo (erro %d). Se o jogo roda como admin, "
                       "abra o CoopInput como admin tambem." % k32.GetLastError())
    try:
        wow = wt.BOOL()
        k32.IsWow64Process(h, ctypes.byref(wow))
        if wow.value:
            return False, "Jogo 32-bit ainda nao e suportado (so 64-bit)."
        detach(p)
        lk = Link(pid, title)
        path = (HOOK_PATH + "\0").encode("utf-16-le")
        mem = k32.VirtualAllocEx(h, None, len(path), 0x3000, 0x04)
        k32.WriteProcessMemory(h, mem, path, len(path), None)
        loadlib = k32.GetProcAddress(k32.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
        th = k32.CreateRemoteThread(h, None, 0, loadlib, mem, 0, None)
        k32.WaitForSingleObject(th, 8000)
        code = wt.DWORD()
        k32.GetExitCodeThread(th, ctypes.byref(code))
        k32.CloseHandle(th)
        k32.VirtualFreeEx(h, mem, 0, 0x8000)
        log.info("LoadLibrary no pid %d -> 0x%X", pid, code.value)
        if not code.value:
            lk.close()
            return False, "O jogo recusou a DLL (processo protegido ou anti-cheat?)."
        deadline = time.monotonic() + HOOK_INIT_TIMEOUT
        while time.monotonic() < deadline:
            if lk.alive():
                break
            if hook_boot_status(lk) >= 0x80:
                break
            time.sleep(0.1)
        if not lk.alive():
            stage = hook_boot_status(lk)
            log.error("Game hook initialization failed for pid %d: stage=0x%02X flags=0x%X abi=%d", pid,
                      stage, lk.sh.flags, lk.sh.pad[0])
            startup_diag = hook_startup_diagnostic(pid, attach_started)
            if startup_diag:
                log.error("Game hook worker diagnostic: %s", startup_diag)
            old_api = bool(lk.sh.flags & 1) and lk.sh.pad[0] != HOOK_ABI_VERSION
            # A failed hook must be allowed to shut down while the mapping is still valid.
            lk.push(EV_EXIT)
            time.sleep(0.12)
            lk.close()
            return False, ("The game's hook is using the wrong ABI. Close the game, rebuild the v8 DLL, "
                           "and restart both applications." if old_api else
                           HOOK_BOOT_STAGES.get(stage, f"Unknown hook initialization status 0x{stage:02X}.") +
                           (f" DLL diagnostic: {startup_diag}." if startup_diag else "") +
                           " Restart the game before retrying. See coopinput_debug.log for details.")
        lk.sh.flags = (lk.sh.flags & ~6) | MOUSE_MODE_BITS[mouse_modes[p]]
        links[p] = lk
        log.info("Jogador %d anexado a '%s' (pid %d)", p, title, pid)
        return True, ""
    finally:
        k32.CloseHandle(h)


def detach(p):
    lk = links.pop(p, None)
    if lk:
        lk.push(EV_EXIT)
        time.sleep(0.05)
        lk.close()
        log.info("Jogador %d desanexado de '%s'", p, lk.title)


def list_windows():
    res, me = [], os.getpid()
    PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, lp):
        if not user32.IsWindowVisible(h) or user32.GetWindow(h, 4):
            return True
        n = user32.GetWindowTextLengthW(h)
        if n == 0 or user32.GetWindowLongW(h, -20) & 0x80:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, buf, n + 1)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value != me and buf.value not in ("Program Manager", "Settings", "Configuracoes"):
            res.append((buf.value, pid.value))
        return True

    proc = PROC(cb)
    user32.EnumWindows(proc, 0)
    return res


# ---------- shared state ----------
COLORS = {0: "#3ddc84", 1: "#4c8dff", 2: "#00e5ff", 3: "#ff5fd2", 4: "#ffd23f"}

assign = {}        # device -> 0 (livre, passa direto) | 1 (principal) | 2..MAXP (isolado)
seen = set()
hwids = {}
last_active = {}
pos = [None] + [[0, 0] for _ in range(MAXP)]                    # cursor virtual por jogador
bounds = [None] + [[0, 0, 1920, 1080] for _ in range(MAXP)]     # tela por jogador
# Fail-safe startup: Interception can identify devices while unarmed, but must
# pass every physical packet through until the user explicitly enables input.
state = {"running": True, "forward": False, "panic": False, "error": None, "players": 2}
# Raw key state belongs to a PHYSICAL keyboard, never mixes different keyboards.
# Profiles are *keyboard layout labels*, not scan-code caps: a 70% board may
# still send any normal/E0/E1 code, and custom macro keys are not guaranteed by
# the Interception API. Only explicit Custom mode applies an upper scan-code cap.
PROFILE_OPTIONS = [('auto', 'Auto / All keys'),
                   ('70', '70% Compact'), ('100', '100% Full'),
                   ('200', '200% Expanded'), ('custom', 'Custom scan range')]
PROFILE_LABELS = dict(PROFILE_OPTIONS)
keyboard_profiles = {}   # device -> profile id
keyboard_maxcode = {}    # device -> custom maximum scan code, inclusive (0..65535)
held_keys = {}       # device -> {(scan_code, extension): virtual_key}
held_mouse = {}      # device -> {button indices pressed}
key_state = {p: bytearray(256) for p in range(2, MAXP + 1)}
key_owners = {p: {} for p in range(2, MAXP + 1)}   # VK -> set(device)
mouse_owners = {p: {} for p in range(2, MAXP + 1)} # button -> set(device)
panic_keys = {}     # device -> held scan codes, for per-keyboard panic hotkey
# Layout estimation combines Raw Input's reported key count with keys actually
# observed. Not all USB keyboard descriptors report a true physical key count.
keyboard_reported = {}     # Interception slot -> reported keys if VID/PID match unique
keyboard_observed = {}     # slot -> unique (scan, E0/E1) key-down events
keyboard_last = {}         # slot -> last physical key event
stats = {"moves": 0, "keys": 0, "forward_fail": 0}
overlay_hwnds = set()
ready = threading.Event()


def dev_name(d):
    return f"Teclado {d}" if d <= 10 else f"Mouse {d - 10}"


class RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [("hDevice", wt.HANDLE), ("dwType", wt.DWORD)]


class RIDKEYBOARD(ctypes.Structure):
    _fields_ = [("dwType", wt.DWORD), ("dwSubType", wt.DWORD),
                ("dwKeyboardMode", wt.DWORD), ("dwNumberOfFunctionKeys", wt.DWORD),
                ("dwNumberOfIndicators", wt.DWORD), ("dwNumberOfKeys", wt.DWORD)]


class RIDUNION(ctypes.Union):
    _fields_ = [("keyboard", RIDKEYBOARD), ("_buffer", ctypes.c_byte * 32)]


class RIDDEVICEINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("dwType", wt.DWORD), ("details", RIDUNION)]


def raw_keyboard_descriptors():
    """Best-effort size hints from Windows Raw Input; never used to block keys."""
    fn_list = user32.GetRawInputDeviceList
    fn_info = user32.GetRawInputDeviceInfoW
    fn_list.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.UINT), wt.UINT]
    fn_list.restype = wt.UINT
    fn_info.argtypes = [wt.HANDLE, wt.UINT, ctypes.c_void_p, ctypes.POINTER(wt.UINT)]
    fn_info.restype = wt.UINT
    count = wt.UINT(0)
    if fn_list(None, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST)) == 0xFFFFFFFF:
        return []
    if not count.value or count.value > 1000:
        return []
    arr = (RAWINPUTDEVICELIST * count.value)()
    got = fn_list(arr, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))
    if got == 0xFFFFFFFF:
        return []
    found = []
    for entry in arr[:got]:
        if entry.dwType != 1:  # RIM_TYPEKEYBOARD
            continue
        size = wt.UINT(0)
        fn_info(entry.hDevice, 0x20000007, None, ctypes.byref(size))
        if not size.value or size.value > 65536:
            continue
        name_buf = ctypes.create_unicode_buffer(size.value)
        if fn_info(entry.hDevice, 0x20000007, name_buf, ctypes.byref(size)) == 0xFFFFFFFF:
            continue
        detail = RIDDEVICEINFO()
        detail.cbSize = ctypes.sizeof(detail)
        sz = wt.UINT(ctypes.sizeof(detail))
        if fn_info(entry.hDevice, 0x2000000B, ctypes.byref(detail), ctypes.byref(sz)) == 0xFFFFFFFF:
            continue
        found.append((name_buf.value, int(detail.details.keyboard.dwNumberOfKeys)))
    return found


def hardware_vidpid(name):
    match = re.search(r"VID[_#]([0-9A-Fa-f]{4}).*?PID[_#]([0-9A-Fa-f]{4})", name or "", re.I)
    return (match.group(1).lower(), match.group(2).lower()) if match else None


def refresh_keyboard_hints():
    """Associate unambiguous VID/PID counts. Ambiguous hardware stays unknown."""
    try:
        records = raw_keyboard_descriptors()
        by_pair = {}
        for name, size in records:
            pair = hardware_vidpid(name)
            if pair and 40 <= size <= 512:
                by_pair.setdefault(pair, set()).add(size)
        for d, hwid in list(hwids.items()):
            if d > 10:
                continue
            counts = by_pair.get(hardware_vidpid(hwid), set())
            if len(counts) == 1:
                keyboard_reported[d] = next(iter(counts))
        log.info('Keyboard size hints: %s', keyboard_reported)
    except Exception:
        log.exception('Could not query keyboard device metadata (non-fatal)')


def infer_keyboard_layout(reported=None, observed=()):
    """Return (label, explanation); inference must never constrain input coverage."""
    keys = set(observed)
    numpad = any(sc in (0x37, 0x47, 0x48, 0x49, 0x4A, 0x4B, 0x4C, 0x4D,
                              0x4E, 0x4F, 0x50, 0x51, 0x52, 0x53) and not (flags & 2)
                 for sc, flags in keys)
    extended_extra = len([1 for sc, flags in keys if sc > 0x7F])
    if reported and reported > 115:
        return '200%?', f'Driver reports {reported} keys; macro keys may use separate HID endpoints'
    if extended_extra >= 8:
        return 'Expanded?', 'Multiple unusual physical scan codes detected'
    if numpad:
        return '100%?', 'Dedicated numpad key activity detected'
    if reported and 40 <= reported <= 85:
        return '70%?', f'Driver reports {reported} keys (layout estimate)'
    if reported and 99 <= reported <= 115:
        return '100%?', f'Driver reports {reported} keys; compact boards may report a generic count'
    if reported and 86 <= reported < 99:
        return 'TKL?', f'Driver reports {reported} keys (layout estimate)'
    return 'Unknown', 'Cannot determine physical size reliably; all keys are accepted'


def keyboard_layout_text(d):
    return infer_keyboard_layout(keyboard_reported.get(d), keyboard_observed.get(d, set()))


def short_id(d):
    h = hwids.get(d, "")
    m = re.search(r"VID_([0-9A-Fa-f]{4}).*?PID_([0-9A-Fa-f]{4})", h)
    if m:
        return f"VID {m.group(1)} \u00b7 PID {m.group(2)}"
    return h[-26:] or "dispositivo"


# ---------- Windows message forwarding (low latency, best effort) ----------
# Secondary Win32 input is NOT equivalent to a second Windows desktop seat.
# A linked game uses the injected hook; other desktop apps receive messages.
# WM_MOUSEMOVE now posts on the CAPTURE thread instead of the 10-ms Tk UI timer.

WM_MOUSEMOVE, WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x0200, 0x020A, 0x020E
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C
WM_KEYDOWN, WM_KEYUP, WM_CHAR = 0x100, 0x101, 0x102
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x104, 0x105
KEYEVENT_EXTENDED = 0x02
KEYEVENT_E1 = 0x04


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD),
                ("hwndActive", wt.HWND), ("hwndFocus", wt.HWND),
                ("hwndCapture", wt.HWND), ("hwndMenuOwner", wt.HWND),
                ("hwndMoveSize", wt.HWND), ("hwndCaret", wt.HWND),
                ("rcCaret", wt.RECT)]


def window_at(x, y):
    """Hit-test UNDER a secondary click-through overlay if Win32 reports it.

    Some Windows 11 compositions report layered Tk overlays to WindowFromPoint
    despite WS_EX_TRANSPARENT. The fallback runs ONLY for that case, not for
    every 1-ms mouse movement. No Windows cursor position is changed.
    """
    x, y = int(x), int(y)
    h = user32.WindowFromPoint(wt.POINT(x, y))
    if h and h not in overlay_hwnds:
        return h
    if h not in overlay_hwnds:
        return None
    found = [None]
    def choose(candidate, _):
        if candidate in overlay_hwnds or not user32.IsWindowVisible(candidate):
            return True
        # Click-through layered windows must not block the real Shell HWND.
        if user32.GetWindowLongW(candidate, -20) & 0x20:
            return True
        rect = wt.RECT()
        if (user32.GetWindowRect(candidate, ctypes.byref(rect)) and
                rect.left <= x < rect.right and rect.top <= y < rect.bottom):
            found[0] = candidate
            return False
        return True
    callback = ENUM_WINDOWS_PROC(choose)
    user32.EnumWindows(callback, 0)
    hwnd = found[0]
    if not hwnd:
        return None
    # Resolve native child control rather than posting to the shell root HWND.
    point = wt.POINT(x,y)
    for _ in range(10):
        local = wt.POINT(point.x,point.y)
        if not user32.ScreenToClient(hwnd, ctypes.byref(local)):
            break
        child = user32.ChildWindowFromPointEx(hwnd, local, 1|2|4)
        if not child or child == hwnd or child in overlay_hwnds:
            break
        hwnd = child
    return hwnd


def virtual_pointer_shape(player, x, y):
    """Probe the target window; this does not change the real OS pointer.

    The game hook uses its own capture state. Don't interrogate or modify the
    physical cursor for games: return an arrow for linked game overlays.
    """
    link = links.get(player)
    if link and link.alive() and not desktop_control.get(player, False):
        return 'arrow'
    drag = desktop_drag.active.get(player)
    h = drag['hwnd'] if drag else window_at(x, y)
    if not h or not user32.IsWindow(h):
        return 'arrow'
    name = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(h, name, len(name))
    class_cursor = SYSTEM_CURSOR_HANDLES.get(int(_get_class_cursor(h, -12) or 0))
    root = user32.GetAncestor(h, 2) or h
    hit = drag['hit'] if drag else 1
    # Non-client resize shape can be discovered without moving the system mouse.
    # SendMessageTimeout prevents an unresponsive application hanging the UI.
    if not drag:
        # Most pointer positions are in client content. Avoid synchronous IPC
        # to external window threads except close to a possible resize edge.
        # This keeps hover probes from adding input/UI latency.
        rectangle = wt.RECT()
        if (user32.GetWindowRect(root, ctypes.byref(rectangle))
                and (x <= rectangle.left + 14 or x >= rectangle.right - 14
                     or y <= rectangle.top + 14 or y >= rectangle.bottom - 14)):
            hit = VirtualDesktopWin32().hit_test(root, x, y)
    holding = (drag['mode'], drag['hit']) if drag else None
    shape = shape_for_target(name.value, hit, class_cursor, holding)
    if shape == 'arrow' and not holding:
        # Chromium and modern XAML draw many controls inside one HWND. Look
        # for an accessibility role asynchronously, never in the Tk/UI thread.
        hint = shell_accessibility.role_hint(x, y)
        return accessible_cursor_shape(hint) or shape
    return shape


def target_at(p):
    x, y = pos[p]
    return window_at(x, y)


def post_message(h, msg, wparam, lparam):
    if h and user32.PostMessageW(h, msg, wparam, lparam):
        return True
    stats["forward_fail"] += 1
    return False


def mouse_flags(p):
    own = mouse_owners[p]
    flags = 0
    for btn, mask in ((0, 0x0001), (1, 0x0002), (2, 0x0010),
                      (3, 0x0020), (4, 0x0040)):
        if own.get(btn):
            flags |= mask
    physical_mods = {key for _device, key in physical_modifier_keys[p]}
    if key_state[p][0x10] & 0x80 or physical_mods.intersection((0x10, 0xA0, 0xA1)):
        flags |= 0x0004  # MK_SHIFT, works in Windows-keys mode too
    if key_state[p][0x11] & 0x80 or physical_mods.intersection((0x11, 0xA2, 0xA3)):
        flags |= 0x0008  # MK_CONTROL
    return flags


def mouse_lparam(h, p):
    pt = wt.POINT(int(pos[p][0]), int(pos[p][1]))
    user32.ScreenToClient(h, ctypes.byref(pt))
    return ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)


def post_mouse(p, msg, wparam=None, button=0):
    h = target_at(p)
    if not h:
        return False
    if wparam is None:
        wparam = mouse_flags(p)
    if msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
        wparam |= button << 16
    ok = post_message(h, msg, wparam, mouse_lparam(h, p))
    if VERBOSE and msg != WM_MOUSEMOVE:
        log.debug("post_mouse P%d msg=0x%X hwnd=%s ok=%s", p, msg, h, ok)
    return ok


def post_wheel(p, rolling, horizontal=False):
    h = target_at(p)
    if h:
        x, y = int(pos[p][0]), int(pos[p][1])
        post_message(h, WM_MOUSEHWHEEL if horizontal else WM_MOUSEWHEEL,
                     mouse_flags(p) | ((rolling & 0xFFFF) << 16),
                     ((y & 0xFFFF) << 16) | (x & 0xFFFF))


# ---------- per-player desktop mouse capture (never touches Windows cursor) ----------
shell_accessibility = ShellAccessibility()
system_cursor_art = NativeSystemCursorArt()

class VirtualDesktopWin32:
    def hit(self, x, y):
        return window_at(x, y)

    def root(self, hwnd):
        return user32.GetAncestor(hwnd, 2) or hwnd

    def alive(self, hwnd):
        return bool(hwnd and user32.IsWindow(hwnd))
    def _class_name(self, hwnd):
        if not hwnd:
            return ''
        name = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, name, len(name))
        return name.value

    def is_shell_target(self, hwnd):
        """Only taskbar / desktop roots; other app clicks keep original routing."""
        return bool(hwnd and is_shell_class(self._class_name(self.root(hwnd))))

    def shell_activate(self, x, y):
        """Compatibility fallback for synchronous accessibility activation."""
        return shell_accessibility.activate(x, y)

    def shell_activate_async(self, x, y, fallback):
        """Use a worker: unresponsive Shell components cannot delay mouse movement."""
        return shell_accessibility.activate_async(x, y, fallback)

    def activate_window(self, hwnd):
        """Opt-in global focus transfer. It does NOT transfer mouse ownership."""
        if not self.alive(hwnd):
            return False
        return bool(user32.SetForegroundWindow(self.root(hwnd)))


    def note_interaction(self, player, hwnd):
        # The user explicitly opted into sharing Windows application focus.
        if mouse_features.get(player, {}).get('focus_on_click'):
            self.activate_window(hwnd)

    def hit_test(self, root, x, y):
        # Non-client hit testing is bounded to 60 ms to protect the input loop.
        packed = ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)
        result = ctypes.c_size_t()
        ok = user32.SendMessageTimeoutW(root, 0x0084, 0, packed,
                                        0x0002, 60, ctypes.byref(result))
        return int(result.value) if ok else 1  # HTCLIENT fallback

    def window_rect(self, hwnd):
        rect = wt.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return rect.left, rect.top, rect.right, rect.bottom

    def move_window(self, hwnd, left, top, width, height):
        if not self.alive(hwnd):
            return False
        # SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER | ASYNCWINDOWPOS.
        # Do not wait for the target app's UI thread to process each mouse move.
        return bool(user32.SetWindowPos(hwnd, 0, int(left), int(top),
                                        int(width), int(height), 0x4214))

    def post_client(self, hwnd, msg, flags, x, y):
        if not self.alive(hwnd):
            return False
        pt = wt.POINT(int(x), int(y))
        if not user32.ScreenToClient(hwnd, ctypes.byref(pt)):
            return False
        lp = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)
        return post_message(hwnd, msg, flags, lp)

    def post_wheel(self, hwnd, x, y, rolling, horizontal, flags):
        if not self.alive(hwnd):
            return False
        return post_message(hwnd, 0x020E if horizontal else 0x020A,
                            flags | ((rolling & 0xFFFF) << 16),
                            ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF))


desktop_drag = DesktopDragRouter(VirtualDesktopWin32())
for _p, _options in mouse_features.items():
    desktop_drag.configure(_p, **_options)


def desktop_mouse_move(player):
    x, y = pos[player]
    return desktop_drag.move(player, x, y, mouse_flags(player))


def desktop_mouse_button(player, index, down):
    x, y = pos[player]
    return desktop_drag.button(player, index, down, x, y, mouse_flags(player))


def desktop_mouse_wheel(player, rolling, horizontal=False):
    x, y = pos[player]
    return desktop_drag.wheel(player, x, y, rolling, horizontal, mouse_flags(player))


def key_vk(k):
    # MAPVK_VSC_TO_VK_EX preserves extended right control/alt, numpad Enter,
    # navigation keys and the 102nd key in many regional keyboard layouts.
    e0 = bool(k.state & KEYEVENT_EXTENDED)
    e1 = bool(k.state & KEYEVENT_E1)
    sc = k.code & 0xFFFF
    # A high scan code is not a Windows set-1 scan code. Never truncate its
    # high byte and silently transform it into an unrelated key or mouse VK.
    if sc > 0xFF:
        return 0
    if e1 and sc in (0x1D, 0x45):
        return 0x13  # Pause/Break (E1 sequence)
    if sc == 0x45 and not e0:
        # scan 0x45 is NumLock; Pause is special E1 sequence
        return 0x90
    vk = user32.MapVirtualKeyW(sc | (0xE000 if e0 else (0xE100 if e1 else 0)), 3)
    return vk or user32.MapVirtualKeyW(sc, 1)


def update_key_state(player, device, vk, is_down):
    own = key_owners[player].setdefault(vk, set())
    previously = bool(own)
    if is_down:
        own.add(device)
    else:
        own.discard(device)
    if not own:
        key_owners[player].pop(vk, None)
    keys = key_state[player]
    held = bool(own)
    # Keyboard-state low bit tracks toggles, unlike GetAsyncKeyState.
    keys[vk] = (keys[vk] & 1) | (0x80 if held else 0)
    if vk in (0xA0, 0xA1):
        keys[0x10] = (keys[0x10] & 1) | (0x80 if (keys[0xA0] | keys[0xA1]) & 0x80 else 0)
    if vk in (0xA2, 0xA3):
        keys[0x11] = (keys[0x11] & 1) | (0x80 if (keys[0xA2] | keys[0xA3]) & 0x80 else 0)
    if vk in (0xA4, 0xA5):
        keys[0x12] = (keys[0x12] & 1) | (0x80 if (keys[0xA4] | keys[0xA5]) & 0x80 else 0)
    if is_down and not previously and vk in (0x14, 0x90, 0x91):
        keys[vk] ^= 1  # CapsLock, NumLock, ScrollLock


def post_key(p, k, vk=None):
    h = target_at(p)
    if not h:
        return False
    # Route to a focused child HWND belonging to the target GUI thread, not
    # blindly to its root.  This helps Notepad/edit controls and classic apps.
    root = user32.GetAncestor(h, 2) or h
    tid = user32.GetWindowThreadProcessId(root, None)
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(info)
    if tid and user32.GetGUIThreadInfo(tid, ctypes.byref(info)) and info.hwndFocus:
        focus = info.hwndFocus
        if user32.GetAncestor(focus, 2) == root or focus == root:
            root = focus
    ext = bool(k.state & KEYEVENT_EXTENDED)
    up = bool(k.state & 1)
    vk = key_vk(k) if vk is None else vk
    if not vk:
        return False
    alt = bool(key_state[p][0x12] & 0x80)
    syskey = (vk in (0x12, 0xA4, 0xA5)) or alt
    msg = (WM_SYSKEYUP if up else WM_SYSKEYDOWN) if syskey else (WM_KEYUP if up else WM_KEYDOWN)
    # Include the previous and transition bits according to Win32's conventions.
    lp = (1 | ((k.code & 0xFF) << 16) | (int(ext) << 24) |
          (int(alt) << 29) | (0xC0000000 if up else 0))
    return post_message(root, msg, vk, lp)


def release_device(d, old_player):
    """Avoid stuck keys/buttons when switching devices or panic/reset."""
    if old_player < 2:
        held_keys.pop(d, None)
        held_mouse.pop(d, None)
        panic_keys.pop(d, None)
        return
    old = held_keys.pop(d, {})
    physical_modifier_keys[old_player] = {token for token in physical_modifier_keys[old_player]
                                          if token[0] != d}
    mouse_key_latches[old_player].clear()
    lk = links.get(old_player)
    for (scan, flags), vk in old.items():
        update_key_state(old_player, d, vk, False)
        if lk:
            lk.push(EV_KEY, vk, 0, scan | ((flags & 0x06) << 7))
        elif state["forward"]:
            post_key(old_player, KeyStroke(scan, (flags & 0x06) | 1, 0), vk)
    if d > 10:
        profile_key_latches.difference_update({token for token in profile_key_latches if token[0] == d})
        desktop_drag.cancel(old_player, *pos[old_player])
    buttons = held_mouse.pop(d, set())
    for idx in buttons:
        own = mouse_owners[old_player].get(idx, set())
        own.discard(d)
        if not own:
            mouse_owners[old_player].pop(idx, None)
        if lk:
            lk.push(EV_BTN, idx, 0)
        elif state["forward"]:
            msgs = (0x202, 0x205, 0x208, WM_XBUTTONUP, WM_XBUTTONUP)
            post_mouse(old_player, msgs[idx], button=(idx - 2) if idx >= 3 else 0)
    panic_keys.pop(d, None)


def on_key(k, player, device):
    up = bool(k.state & 1)
    sc = k.code & 0xFFFF
    flags = k.state & 0x06
    keyboard_last[device] = (sc, flags, up)
    if not up:
        keyboard_observed.setdefault(device, set()).add((sc, flags))
    held = panic_keys.setdefault(device, set())
    if up:
        held.discard(sc)
    else:
        held.add(sc)
    if {0x1D, 0x38, 0x58}.issubset(held):  # Ctrl + Alt + F12, same keyboard
        for d, old in list(assign.items()):
            release_device(d, old)
        assign.clear()
        state["panic"] = True
        log.warning("PANIC Ctrl+Alt+F12: all devices returned to Windows")
        return False
    if player < 2:
        return False
    if not state["forward"]:
        return False  # Input OFF is a real physical-device release mode.
    if desktop_toggle_event(player, device, sc, up):
        return True
    if desktop_control[player]:
        return False  # KEYBOARD ONLY: shortcuts go to active Windows app
    # The per-player hotkey is intercepted BEFORE any game keyboard messages.
    # Panic is handled above, regardless of whether the configured hotkey is held.
    if cursor_hotkey_event(player, device, 'keyboard', sc, flags, not up):
        return True
    # Strict keyboard isolation: an assigned keyboard must not fall back to
    # the normal Windows input stream when it has an unfamiliar key code.
    if keyboard_profiles.get(device) == 'custom' and sc > keyboard_maxcode.get(device, 65535):
        if VERBOSE:
            log.debug("Excluded keyboard scan code 0x%X on device %d", sc, device)
        return True
    vk = key_vk(k)
    if not vk or 1 <= vk <= 6:
        # Windows VK 0x01..0x06 are mouse buttons. An unusual/invalid keyboard
        # scancode must never be able to change a mouse button via VK mapping.
        log.debug("Swallowing unknown/mouse-VK keyboard scan code: 0x%X vk=0x%X device=%d", sc, vk, device)
        return True
    identity = sc, flags
    physical = held_keys.setdefault(device, {})
    if up:
        physical.pop(identity, None)
    else:
        physical[identity] = vk
    update_key_state(player, device, vk, not up)
    lk = links.get(player)
    if state["forward"] and lk and lk.alive():
        lk.push(EV_KEY, vk, 0 if up else 1, sc | ((flags & 0x06) << 7))
    elif state["forward"]:
        post_key(player, k, vk)
    stats["keys"] += 1
    return True


# Optional NumPad mouse keys. Only active in Windows Keyboard mode and only
# for the assigned secondary physical keyboard. They never move the OS cursor.
NUMPAD_MOUSE_MOVES = {0x47:(-1,-1), 0x48:(0,-1), 0x49:(1,-1),
                     0x4B:(-1,0), 0x4D:(1,0),
                     0x4F:(-1,1), 0x50:(0,1), 0x51:(1,1)}

def handle_mouse_keys(player, scan, flags, up):
    if flags & 0x02:  # extended/navigation keys aren't numeric keypad events
        return False
    if scan in NUMPAD_MOUSE_MOVES:
        if not up:
            vx, vy = NUMPAD_MOUSE_MOVES[scan]
            x, y = pos[player]
            rect = bounds[player]
            amount = max(2, round(12 * mouse_features[player]['sensitivity']))
            pos[player][:] = [min(rect[2]-1, max(rect[0], x+vx*amount)),
                              min(rect[3]-1, max(rect[1], y+vy*amount))]
            desktop_mouse_move(player)
        return True
    if scan == 0x4C:  # keypad 5: left-click, once on physical press
        if up:
            desktop_mouse_button(player, 0, False)
            mouse_key_latches[player].discard(0x4C)
        elif 0x4C not in mouse_key_latches[player]:
            mouse_key_latches[player].add(0x4C)
            desktop_mouse_button(player, 0, True)
        return True
    if scan == 0x52:  # keypad 0: hold left button
        if not up and 0x52 not in mouse_key_latches[player]:
            mouse_key_latches[player].add(0x52)
            desktop_mouse_button(player, 0, True)
        return True
    if scan == 0x53:  # keypad decimal: release hold
        if not up:
            mouse_key_latches[player].discard(0x52)
            desktop_mouse_button(player, 0, False)
        return True
    return False


# Interception button bits: left/right/middle/X1/X2, including their up events.
BUTTON_EVENTS = ((0x001, 0, True), (0x002, 0, False),
                 (0x004, 1, True), (0x008, 1, False),
                 (0x010, 2, True), (0x020, 2, False),
                 (0x040, 3, True), (0x080, 3, False),
                 (0x100, 4, True), (0x200, 4, False))


def on_mouse(m, player, device):
    # While armed, Player 2+ mice stay isolated (including Win Keys mode).
    # While Input is OFF, return every mouse packet to the real Windows cursor.
    if player < 2 or not state["forward"]:
        return False
    lk = links.get(player)
    # When Windows keyboard keys are enabled, virtual pointer events target
    # the hovered desktop window rather than an attached game's hook. The
    # real physical cursor remains untouched; this is best-effort Win32 input.
    linked = bool(state["forward"] and not desktop_control.get(player, False)
                  and lk and lk.alive())
    p, b = pos[player], bounds[player]
    if m.flags & 1:   # Absolute HID/tablet/touchpad pointer (0..65535)
        if m.flags & 2:  # MOUSE_VIRTUAL_DESKTOP
            left = user32.GetSystemMetrics(76)
            top = user32.GetSystemMetrics(77)
            width = max(1, user32.GetSystemMetrics(78))
            height = max(1, user32.GetSystemMetrics(79))
        else:
            left, top = b[0], b[1]
            width, height = b[2] - b[0], b[3] - b[1]
        next_x = left + int(m.x * max(1, width - 1) / 65535)
        next_y = top + int(m.y * max(1, height - 1) / 65535)
        dx, dy = next_x - p[0], next_y - p[1]
    else:
        if linked:
            # CRITICAL: leave working v8 hook input entirely unchanged, even
            # if the desktop pointer previously used precision sensitivity.
            dx, dy = m.x, m.y
            mouse_subpixel[player][:] = [0., 0.]
        else:
            # Software sensitivity changes only the desktop virtual pointer.
            # Fractional remainder prevents losing small motions in precision.
            ratio = (mouse_features[player]['sensitivity']
                     * PROFILES[mouse_features[player]['profile']])
            fx = m.x * ratio + mouse_subpixel[player][0]
            fy = m.y * ratio + mouse_subpixel[player][1]
            dx, dy = int(fx), int(fy)
            mouse_subpixel[player][:] = [fx-dx, fy-dy]
    if dx or dy:
        p[0] = min(max(p[0] + dx, b[0]), b[2] - 1)
        p[1] = min(max(p[1] + dy, b[1]), b[3] - 1)
        if linked:
            # Pass the actual clamped displacement to the hooked game.
            lk.push(EV_MOVE, dx, dy)
        elif state["forward"]:
            desktop_mouse_move(player)  # no game link: per-player desktop routing
        stats["moves"] += 1
    pressed = held_mouse.setdefault(device, set())
    for bit, idx, down_now in BUTTON_EVENTS:
        if not m.state & bit:
            continue
        # Mouse-button hotkeys affect only the assigned virtual cursor.
        # Do not forward hotkey click/release to the game.
        if cursor_hotkey_event(player, device, 'mouse', idx, 0, down_now):
            continue
        # Optional desktop-only mouse button profile cycle; never changes game
        # hook input or sends a side-button shortcut into the desktop app.
        if (not linked and mouse_features[player]['profile_button'] in (3, 4)
                and mouse_features[player]['profile_button'] == idx):
            token = (device, idx)
            if down_now and token not in profile_key_latches:
                profile_key_latches.add(token)
                profiles = ('precision', 'normal', 'fast')
                current = mouse_features[player]['profile']
                mouse_features[player]['profile'] = profiles[(profiles.index(current) + 1) % len(profiles)]
                cursor_ui_events.put(('pointer_profile', player))
            elif not down_now:
                profile_key_latches.discard(token)
            continue
        # Recording a new hotkey may release currently held buttons, so do not
        # reuse the pre-release set reference created before the button loop.
        pressed = held_mouse.setdefault(device, set())
        if down_now:
            pressed.add(idx)
        else:
            pressed.discard(idx)
        owners = mouse_owners[player].setdefault(idx, set())
        if down_now:
            owners.add(device)
        else:
            owners.discard(device)
        if not owners:
            mouse_owners[player].pop(idx, None)
        if linked:
            lk.push(EV_BTN, idx, int(down_now))
        elif state["forward"]:
            desktop_mouse_button(player, idx, down_now)
    if m.state & 0x400:
        if linked:
            lk.push(EV_WHEEL, m.rolling)
        elif state["forward"]:
            desktop_mouse_wheel(player, m.rolling)
    if m.state & 0x800:
        if linked:
            lk.push(EV_WHEEL, m.rolling, 1)  # requires rebuilt hook DLL
        elif state["forward"]:
            desktop_mouse_wheel(player, m.rolling, horizontal=True)
    return True


# ---------- capture ----------
def capture():
    try:
        _capture()
    except Exception:
        state["error"] = "Erro na thread de captura."
        log.critical("Falha na thread de captura:\n%s", traceback.format_exc())
        ready.set()


def _capture():
    log.info("Criando contexto Interception...")
    ctx = dll.interception_create_context()
    log.debug("contexto = %s", ctx)
    if not ctx:
        log.error("create_context retornou NULL: driver nao instalado/carregado")
        state["error"] = "Driver Interception nao encontrado. Instale (como admin) e reinicie o PC."
        ready.set()
        return
    try:
        _capture_events(ctx)
    finally:
        # Even an unexpected input-processing exception must release the
        # driver context rather than leave physical devices intercepted.
        log.info("Encerrando captura e devolvendo dispositivos ao Windows")
        dll.interception_destroy_context(ctx)


def _capture_events(ctx):
    dll.interception_set_filter(ctx, KB_PRED, 0xFFFF)
    dll.interception_set_filter(ctx, MS_PRED, 0xFFFF)
    for d in range(1, 21):
        buf = ctypes.create_unicode_buffer(512)
        if dll.interception_get_hardware_id(ctx, d, buf, 1024):
            hwids[d] = buf.value
            log.info("%s: %s", dev_name(d), buf.value)
    log.info("%d dispositivo(s) detectado(s)", len(hwids))
    refresh_keyboard_hints()
    ready.set()

    st = Stroke()
    counts, last_stat = {}, time.time()
    while state["running"]:
        while True:
            try:
                p, value = desktop_requests.get_nowait()
            except queue.Empty:
                break
            set_desktop_mode(p, value)
        dev = dll.interception_wait_with_timeout(ctx, 8)
        now = time.time()
        if counts and now - last_stat > 5:
            log.debug("eventos nos ultimos 5s: %s", {dev_name(k): v for k, v in counts.items()})
            counts.clear()
            last_stat = now
        if dev == 0:
            # Run optional hover, dwell click and auto-scroll only for desktop
            # pointers, with no game hook or system-mouse involvement.
            for player in range(2, state['players'] + 1):
                if not state['forward']:
                    continue
                link = links.get(player)
                if not desktop_control.get(player) and link and link.alive():
                    continue
                x, y = pos[player]
                desktop_drag.tick(player, x, y, mouse_flags(player))
            continue
        n = dll.interception_receive(ctx, dev, ctypes.byref(st), 1)
        if n < 1:
            log.warning("receive falhou: dev=%d ret=%d", dev, n)
            continue
        if dev not in seen:
            log.info("Primeiro evento de %s (jogador atual: %d)", dev_name(dev), assign.get(dev, 0))
        seen.add(dev)
        last_active[dev] = now
        counts[dev] = counts.get(dev, 0) + 1
        player = assign.get(dev, 0)
        if VERBOSE:
            if dev <= 10:
                log.debug("%s code=0x%02X state=%d P%d", dev_name(dev),
                          st.key.code, st.key.state, player)
            else:
                log.debug("%s state=0x%X flags=%d dx=%d dy=%d rolling=%d P%d", dev_name(dev),
                          st.mouse.state, st.mouse.flags, st.mouse.x, st.mouse.y,
                          st.mouse.rolling, player)
        swallowed = on_key(st.key, player, dev) if dev <= 10 else on_mouse(st.mouse, player, dev)
        if not swallowed:
            if dll.interception_send(ctx, dev, ctypes.byref(st), 1) < 1:
                log.warning("send falhou: %s", dev_name(dev))


# ---------- UI ----------
FONT = "Segoe UI"
# Presentation-only palettes. The v8 Interception/game-hook engine is untouched.
THEMES = {
    "dark": {
        "BG": "#0C1017", "PANEL": "#171D27", "BORDER": "#2A3443",
        "CARD": "#222B39", "BTNBG": "#293446", "TXT": "#F3F6FC",
        "MUTED": "#A1AEC2", "LED_OFF": "#647185", "ACCENT": "#4E9BFF",
        "POSITIVE": "#20B990", "WARNING": "#F1B764", "PLACEHOLDER": "#1D2532",
        "KBBG": "#213E65", "KBFG": "#B7D8FF", "MSBG": "#51372F",
        "MSFG": "#FFCFB7", "SELECTBG": "#344761", "BTNFG": "#FFFFFF",
        "ACTIVEBG": "#37485F", "PICKFG": "#FFFFFF"
    },
    "light": {
        "BG": "#F1F3F7", "PANEL": "#FFFFFF", "BORDER": "#DDE2E9",
        "CARD": "#F3F6FA", "BTNBG": "#E9EEF5", "TXT": "#171E2B",
        "MUTED": "#667487", "LED_OFF": "#AAB5C4", "ACCENT": "#176DDF",
        "POSITIVE": "#14856B", "WARNING": "#A66C0A", "PLACEHOLDER": "#F8FAFD",
        "KBBG": "#DCEAFF", "KBFG": "#254F9E", "MSBG": "#FFE9D9",
        "MSFG": "#9A4C17", "SELECTBG": "#D6E7FF", "BTNFG": "#FFFFFF",
        "ACTIVEBG": "#D9E5F3", "PICKFG": "#17202D"
    },
}

def use_palette(name):
    """Change only drawing/widget colors; never touches capture or device routing."""
    global BG, PANEL, BORDER, CARD, BTNBG, TXT, MUTED, LED_OFF
    global ACCENT, POSITIVE, WARNING, PLACEHOLDER, KBBG, KBFG, MSBG, MSFG
    global SELECTBG, BTNFG, ACTIVEBG, PICKFG
    values = THEMES[name if name in THEMES else 'dark']
    (BG, PANEL, BORDER, CARD, BTNBG, TXT, MUTED, LED_OFF,
     ACCENT, POSITIVE, WARNING, PLACEHOLDER, KBBG, KBFG, MSBG, MSFG,
     SELECTBG, BTNFG, ACTIVEBG, PICKFG) = (values[key] for key in (
         'BG','PANEL','BORDER','CARD','BTNBG','TXT','MUTED','LED_OFF',
         'ACCENT','POSITIVE','WARNING','PLACEHOLDER','KBBG','KBFG','MSBG','MSFG',
         'SELECTBG','BTNFG','ACTIVEBG','PICKFG'))

use_palette('dark')


def monitors():
    res = []
    PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HANDLE, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)

    def cb(h, dc, rect, lp):
        r = rect.contents
        res.append((r.left, r.top, r.right, r.bottom))
        return True

    proc = PROC(cb)
    user32.EnumDisplayMonitors(None, None, proc, 0)
    return res or [(0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))]


def rrect(c, x1, y1, x2, y2, r, **kw):
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


def make_overlay(root, color, fallback_color=None):
    ov = tk.Toplevel(root)
    ov.overrideredirect(True)
    ov.attributes("-topmost", True)
    ov.attributes("-transparentcolor", "#010101")
    ov.config(bg="#010101")
    cv = tk.Canvas(ov, width=64, height=64, bg="#010101", highlightthickness=0)
    cv.pack()
    ov.pointer_hotspot = paint_system_cursor(cv, 'arrow', color, system_cursor_art)
    ov.pointer_canvas = cv
    ov.pointer_shape = 'arrow'
    ov.pointer_color = color or fallback_color or '#FFFFFF'
    ov.update_idletasks()
    child = ov.winfo_id()
    wrap = user32.GetParent(child) or child
    overlay_hwnds.update({child, wrap})
    ex = user32.GetWindowLongW(wrap, -20)
    # layered | transparent (click-through) | toolwindow | noactivate
    user32.SetWindowLongW(wrap, -20, ex | 0x80000 | 0x20 | 0x80 | 0x08000000)
    ov.withdraw()
    return ov


class App:
    def __init__(self, root):
        self.root = root
        root.title("CoopInput  ·  Studio v12.9.3")
        try:
            icon_file = os.path.join(HERE, "CoopInput.ico")
            if os.path.isfile(icon_file):
                root.iconbitmap(icon_file)
        except tk.TclError:
            pass
        root.geometry("1320x790")
        root.minsize(1080, 680)
        root.configure(bg=BG)
        self.c = tk.Canvas(root, bg=BG, highlightthickness=0)
        self.c.pack(fill="both", expand=True)

        self.mons = monitors()
        log.info("Monitores: %s", self.mons)
        self.mon_idx = {p: len(self.mons) - 1 for p in range(2, MAXP + 1)}
        self.overlays, self.visible, self.last_pos = {}, {}, {}
        self.cursor_status = {}
        self.pointer_probe = {}  # player -> (monotonic timestamp, x, y, shape)
        self.zones, self.zone_rect, self.leds = {}, {}, {}
        self.drag, self.nseen, self.nb, self.resize_job = None, -1, 0, None
        self.last_layout_refresh = 0
        self.theme = 'dark'
        # Rendering preference only; never affects physical-device ownership.
        self.cursor_colors = {p: normalize_cursor_color(COLORS[p]) for p in range(2, MAXP+1)}
        self.saved = {}
        self.saved_profiles = {}
        self.saved_maxcodes = {}
        self.saved_cursor_hotkeys = {}
        self.restore_saved_on_start = False
        self.assignments_modified = False
        self.hotkey_dialogs = {}
        self.applied = set()
        self.load_config()
        use_palette(self.theme)
        root.configure(bg=BG)
        self.c.configure(bg=BG)
        self.apply_ttk_style()
        for p in range(2, MAXP + 1):
            self.apply_monitor(p)

        self.c.bind("<Configure>", self.on_resize)
        self.c.bind("<B1-Motion>", self.drag_move)
        self.c.bind("<ButtonRelease-1>", self.drag_end)
        self.sync_devices()
        self.refresh_cursors()

    def apply_ttk_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure('Coop.TCombobox', foreground=TXT, background=CARD,
                        fieldbackground=CARD, arrowcolor=TXT, bordercolor=BORDER,
                        lightcolor=CARD, darkcolor=CARD, padding=7)
        style.map('Coop.TCombobox', fieldbackground=[('readonly', CARD)],
                  foreground=[('readonly', TXT)],
                  background=[('readonly', CARD)])
        try:
            self.root.option_add('*TCombobox*Listbox.background', CARD)
            self.root.option_add('*TCombobox*Listbox.foreground', TXT)
            self.root.option_add('*TCombobox*Listbox.selectBackground', ACCENT)
        except tk.TclError:
            pass

    def toggle_theme(self):
        self.theme = 'light' if self.theme == 'dark' else 'dark'
        use_palette(self.theme)
        self.root.configure(bg=BG)
        self.c.configure(bg=BG)
        self.apply_ttk_style()
        self.save_config()
        self.redraw()

    # ----- config -----
    def dev_keys(self):
        keys, cnt = {}, {}
        for d in sorted(set(hwids) | set(seen)):
            h = hwids.get(d, f"slot{d}")
            k = cnt.get(h, 0)
            cnt[h] = k + 1
            keys[d] = f"{h}#{k}"
        return keys

    def save_config(self):
        try:
            keys = self.dev_keys()
            # Keep saved assignments until the user explicitly restores,
            # replaces, or resets them. Theme changes must not erase them.
            saved_assign = ({keys[d]: p for d, p in list(assign.items()) if p and d in keys}
                            if self.assignments_modified or self.restore_saved_on_start
                            else dict(self.saved))
            cfg = {"theme": self.theme, "players": state["players"], "forward": state["forward"],
                   "monitors": {str(p): i for p, i in self.mon_idx.items()},
                   "mouse_modes": {str(p): mouse_modes[p] for p in mouse_modes},
                   "mouse_features": {str(p): mouse_features[p] for p in mouse_features},
                   "cursor_modes": {str(p): cursor_display_modes[p] for p in cursor_display_modes},
                   "cursor_colors": {str(p): self.cursor_colors[p] for p in self.cursor_colors},
                   "desktop_toggle_keys": {str(p): desktop_toggle_scancodes[p] for p in desktop_toggle_scancodes},
                   "cursor_hotkeys": {
                       str(p): {"kind": bind["kind"], "device": keys[bind["device"]],
                                "code": bind["code"], "flags": bind["flags"]}
                       for p, bind in cursor_hotkeys.items()
                       if bind and bind["device"] in keys},
                   "assign": saved_assign,
                   "keyboard_profiles": {keys[d]: v for d, v in keyboard_profiles.items() if d in keys},
                   "keyboard_maxcode": {keys[d]: v for d, v in keyboard_maxcode.items() if d in keys}}
            with open(CFG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=1)
        except Exception:
            log.exception("Falha ao salvar config")

    def load_config(self):
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
        except FileNotFoundError:
            return
        except Exception:
            log.exception("Config invalida, ignorando")
            return
        theme = cfg.get('theme', 'dark')
        self.theme = theme if theme in THEMES else 'dark'
        state["players"] = min(max(int(cfg.get("players", 2)), 2), MAXP)
        # Never auto-arm physical interception based on a previous session.
        state["forward"] = False
        for p, i in cfg.get("monitors", {}).items():
            if p.isdigit() and int(p) in self.mon_idx and 0 <= i < len(self.mons):
                self.mon_idx[int(p)] = i
        self.saved = cfg.get("assign", {})
        self.saved_profiles = cfg.get("keyboard_profiles", {})
        self.saved_maxcodes = cfg.get("keyboard_maxcode", {})
        self.saved_cursor_hotkeys = cfg.get("cursor_hotkeys", {})
        for pid, color in cfg.get('cursor_colors', {}).items():
            if str(pid).isdigit() and int(pid) in self.cursor_colors:
                self.cursor_colors[int(pid)] = normalize_cursor_color(color)
        for pid, code in cfg.get('desktop_toggle_keys', {}).items():
            if str(pid).isdigit() and int(pid) in desktop_toggle_scancodes and code in DESKTOP_KEYS.values():
                desktop_toggle_scancodes[int(pid)] = code
        for p, mode in cfg.get("cursor_modes", {}).items():
            if p.isdigit() and int(p) in cursor_display_modes and mode in CURSOR_DISPLAY_MODES:
                cursor_display_modes[int(p)] = mode
        for p, values in cfg.get('mouse_features', {}).items():
            if str(p).isdigit() and int(p) in mouse_features:
                player = int(p)
                mouse_features[player] = validated_features(values)
                desktop_drag.configure(player, **mouse_features[player])
        for p, mode in cfg.get("mouse_modes", {}).items():
            if p.isdigit() and int(p) in mouse_modes and mode in MOUSE_MODE_BITS:
                mouse_modes[int(p)] = mode
        log.info("Config loaded: %d players. Input OFF; saved device assignments await manual restore.", state["players"])

    def restore_saved_assignments(self):
        pairs = [(d, self.saved[key]) for d, key in self.dev_keys().items()
                 if isinstance(self.saved.get(key), int)
                 and not isinstance(self.saved.get(key), bool)
                 and 1 <= self.saved[key] <= state["players"]]
        if not pairs:
            messagebox.showinfo("CoopInput", "No saved devices are currently connected.", parent=self.root)
            return
        if not messagebox.askyesno(
            "Restore saved devices?",
            "This may take control of a physical mouse or keyboard for Player 2.\n\n"
            "Keep another mouse or trackpad available. Emergency release: Ctrl+Alt+F12.\n\n"
            f"Restore {len(pairs)} saved device assignment(s)?",
            parent=self.root,
        ):
            return
        self.restore_saved_on_start = True
        self.applied.clear()
        self.sync_devices()
        self.assignments_modified = True
        self.save_config()
        self.refresh_cursors()
        self.redraw()

    def sync_devices(self):
        """Aplica a config salva aos dispositivos conforme eles aparecem."""
        for d, key in self.dev_keys().items():
            if d in self.applied:
                continue
            self.applied.add(d)
            if d <= 10:
                profile = self.saved_profiles.get(key, 'auto')
                keyboard_profiles[d] = profile if profile in PROFILE_LABELS else 'auto'
                try:
                    keyboard_maxcode[d] = min(65535, max(0, int(self.saved_maxcodes.get(key, 65535))))
                except (ValueError, TypeError):
                    keyboard_maxcode[d] = 65535
            p = self.saved.get(key)
            if self.restore_saved_on_start and isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= state["players"]:
                assign[d] = p
                log.info("%s -> Jogador %d (config salva)", dev_name(d), p)
            for player, saved in self.saved_cursor_hotkeys.items():
                try:
                    pp = int(player)
                    kind = saved['kind']
                    code = int(saved['code'])
                    flags = int(saved.get('flags', 0))
                    if (pp in cursor_hotkeys and kind in ('keyboard','mouse')
                            and 0 <= code <= 65535 and saved.get('device') == key
                            and ((d <= 10) == (kind == 'keyboard'))):
                        cursor_hotkeys[pp] = {'device': d, 'kind':kind, 'code':code, 'flags':flags}
                except (ValueError, TypeError, KeyError):
                    continue
        self.nseen = len(seen)

    # ----- keyboard/mouse configuration -----
    def configure_keyboard(self, d):
        """Per-physical-device layout profile with live automatic size hints."""
        win = tk.Toplevel(self.root)
        win.title(f"Keyboard coverage - {dev_name(d)}")
        win.configure(bg=BG)
        win.geometry("535x410")
        win.resizable(False, False)
        win.transient(self.root)
        tk.Label(win, text=f"{dev_name(d)} - Keyboard coverage", bg=BG, fg=TXT,
                 font=(FONT, 14, 'bold')).pack(anchor='w', padx=18, pady=(20, 9))
        estimated, evidence = keyboard_layout_text(d)
        tk.Label(win, text=f"Detected: {estimated}   |   {evidence}",
                 bg=BG, fg=ACCENT, wraplength=492, justify='left').pack(anchor='w', padx=18, pady=(4, 9))
        tk.Label(win, text="Auto learns from the driver and actual pressed keys. Manual profiles are labels, not limits.",
                 bg=BG, fg=MUTED, wraplength=492, justify='left').pack(anchor='w', padx=18)
        chosen = tk.StringVar(value=PROFILE_LABELS.get(keyboard_profiles.get(d, 'auto'), PROFILE_LABELS['auto']))
        combo = ttk.Combobox(win, textvariable=chosen, values=[label for _, label in PROFILE_OPTIONS],
                             state='readonly', width=35, style='Coop.TCombobox')
        combo.pack(anchor='w', padx=18, pady=(14, 14))
        tk.Label(win, text="Custom maximum scan code (decimal, 0-65535):", bg=BG, fg=TXT).pack(anchor='w', padx=18)
        maximum = tk.StringVar(value=str(keyboard_maxcode.get(d, 65535)))
        entry = tk.Entry(win, textvariable=maximum, bg=CARD, fg=TXT, insertbackground=TXT,
                         relief='flat', width=18)
        entry.pack(anchor='w', padx=18, pady=(7, 10))
        tk.Label(win, text=("Only Custom limits captured scan codes. 70%, 100%, and 200% "
                            "accept every key that Windows/Interception reports, including "
                            "extended keys. Vendor-only macro keys need an HID-specific driver."),
                 bg=BG, fg=MUTED, wraplength=452, justify='left').pack(anchor='w', padx=18)
        def save():
            option = next((k for k, label in PROFILE_OPTIONS if label == chosen.get()), 'auto')
            try:
                n = int(maximum.get(), 0)
                if not 0 <= n <= 65535: raise ValueError()
            except ValueError:
                messagebox.showerror('Invalid scan code', 'Enter a value from 0 to 65535.', parent=win)
                return
            old = assign.get(d, 0)
            release_device(d, old)
            keyboard_profiles[d] = option
            keyboard_maxcode[d] = n
            self.save_config()
            log.info('%s layout profile=%s custom max=%d', dev_name(d), option, n)
            win.destroy()
            self.redraw()
        tk.Button(win, text='Save keyboard profile', command=save, bg=POSITIVE, fg=BTNFG,
                  relief='flat', padx=15, pady=8).pack(anchor='e', padx=18, pady=16)

    def apply_mouse_mode(self, p, mode):
        if mode not in MOUSE_MODE_BITS:
            return
        mouse_modes[p] = mode
        lk = links.get(p)
        if lk and lk.alive():
            lk.sh.flags = (lk.sh.flags & ~6) | MOUSE_MODE_BITS[mode]
        log.info('Player %d requested mouse mode: %s (%s)', p, mode,
                 'active on v8 hook' if lk and lk.alive() else 'saved; active after attaching v8 hook')
        self.save_config()
        self.redraw()

    def cursor_hotkey_description(self, p):
        bind = cursor_hotkeys.get(p)
        if not bind:
            return 'Not assigned'
        device = dev_name(bind['device'])
        if bind['kind'] == 'mouse':
            button = ('Left', 'Right', 'Middle', 'X1 / Back', 'X2 / Forward')[bind['code']]
            return f'{device}  /  {button} button'
        sc = bind['code']
        flags = bind['flags']
        buf = ctypes.create_unicode_buffer(96)
        name = ''
        try:
            lp = ((sc & 0xFF) << 16) | ((1 if flags & KEYEVENT_EXTENDED else 0) << 24)
            if user32.GetKeyNameTextW(lp, buf, len(buf)):
                name = buf.value
        except Exception:
            pass
        return f"{device}  /  {name or ('Scan 0x%02X' % sc)}"

    def begin_cursor_binding(self, p, kind):
        global cursor_capture_request
        cursor_capture_request = {'player':p, 'kind':kind}
        for player, labels in self.hotkey_dialogs.items():
            if player == p:
                labels['status'].set(f'Press a {"KEY on an assigned keyboard" if kind == "keyboard" else "BUTTON on an assigned mouse"}...')
            else:
                labels['status'].set('')

    def clear_cursor_binding(self, p):
        global cursor_capture_request
        if cursor_capture_request and cursor_capture_request['player'] == p:
            cursor_capture_request = None
        bind = cursor_hotkeys.get(p)
        if bind:
            cursor_pressed_bindings.difference_update({v for v in cursor_pressed_bindings if v[0] == bind['device']})
        cursor_hotkeys[p] = None
        self.save_config()
        self.update_hotkey_dialog(p)

    def update_hotkey_dialog(self, p):
        d = self.hotkey_dialogs.get(p)
        if d and d['win'].winfo_exists():
            values = {'binding':self.cursor_hotkey_description(p),
                      'mode':cursor_display_modes[p],
                      'visibility':'VISIBLE' if virtual_cursor_visible(p) else 'HIDDEN'}
            for name, value in values.items():
                if d[name].get() != value:
                    d[name].set(value)

    def change_cursor_mode(self, p, mode):
        if mode not in CURSOR_DISPLAY_MODES:
            return
        cursor_display_modes[p] = mode
        cursor_manual_overrides[p] = None
        log.info('Player %d cursor display: %s', p, mode)
        self.save_config()
        self.refresh_cursors()
        self.update_hotkey_dialog(p)
        self.redraw()

    def restore_cursor_auto(self, p):
        self.change_cursor_mode(p, 'auto')

    def set_cursor_color(self, player, color):
        """Only recolor Player N's Tk overlay. Do not route any input."""
        color = normalize_cursor_color(color)
        if self.cursor_colors[player] == color:
            return
        self.cursor_colors[player] = color
        overlay = self.overlays.get(player)
        if overlay:
            overlay.pointer_tint = color
            overlay.pointer_color = color or COLORS[player]
            overlay.pointer_hotspot = paint_system_cursor(
                overlay.pointer_canvas, overlay.pointer_shape, color, system_cursor_art)
            self.last_pos.pop(player, None)  # update hotspot/geometry
        self.save_config()

    def cursor_color_settings(self, player, parent=None):
        popup = tk.Toplevel(self.root)
        popup.title(f'Player {player}  ·  Cursor color')
        popup.configure(bg=BG)
        popup.geometry('410x430')
        popup.resizable(False, False)
        popup.transient(parent or self.root)
        # The parent Mouse dialog uses a modal grab: move it to this picker so
        # the swatches actually respond instead of appearing frozen.
        if parent and parent.winfo_exists():
            try:
                parent.grab_release()
            except tk.TclError:
                pass
        popup.grab_set()
        def close_picker():
            if popup.winfo_exists():
                popup.grab_release()
                popup.destroy()
            if parent and parent.winfo_exists():
                parent.grab_set()
        popup.protocol('WM_DELETE_WINDOW', close_picker)
        tk.Label(popup, text=f'Player {player} cursor color', bg=BG, fg=TXT,
                 font=(FONT, 16, 'bold')).pack(anchor='w', padx=22, pady=(22, 6))
        tk.Label(popup, text='Real Windows cursor shapes, personalized color.\nYour primary Windows mouse never changes.',
                 justify='left', bg=BG, fg=MUTED, font=(FONT, 10)).pack(anchor='w', padx=22, pady=(0,14))
        grid = tk.Frame(popup, bg=BG)
        grid.pack(fill='x', padx=20)
        for ix,(name,color) in enumerate(COLOR_PRESETS.items()):
            # Text gives accessible feedback even for small swatches.
            btn = tk.Button(grid, text=('●  ' if color else '◉  ') + name,
                            bg=CARD, fg=color or TXT, activebackground=ACTIVEBG,
                            activeforeground=color or TXT, borderwidth=0,
                            anchor='w', relief='flat', font=(FONT, 10),
                            command=lambda c=color: self.set_cursor_color(player,c))
            btn.grid(row=ix//2,column=ix%2,sticky='ew',padx=4,pady=5,ipady=7)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        def custom():
            chosen = colorchooser.askcolor(color=self.cursor_colors[player] or '#FFFFFF',
                                          title=f'Player {player}: custom cursor color', parent=popup)
            if chosen and chosen[1]:
                self.set_cursor_color(player, chosen[1])
        tk.Button(popup,text='Choose custom color…',command=custom,bg=BTNBG,fg=TXT,
                  relief='flat',borderwidth=0,pady=9).pack(fill='x',padx=24,pady=(18,6))
        tk.Button(popup,text='Done',command=close_picker,bg=ACCENT,fg=BTNFG,
                  relief='flat',borderwidth=0,pady=9).pack(fill='x',padx=24)

    def mouse_settings(self, p):
        win = tk.Toplevel(self.root)
        win.title(f'Player {p} | Mouse and cursor')
        win.configure(bg=BG)
        win.geometry('610x665')
        win.minsize(570, 620)
        win.transient(self.root)
        win.grab_set()  # avoid overlapping capture dialogs
        heading = tk.Frame(win, bg=BG)
        heading.pack(fill='x', padx=22, pady=(18, 10))
        tk.Button(heading, text='Cursor color  ●',
                  command=lambda: self.cursor_color_settings(p, win),
                  bg=BTNBG, fg=TXT, relief='flat', padx=12, pady=8).pack(side='right', padx=5)

        tk.Button(heading, text='Pointer actions  ›',
                  command=lambda: self.advanced_pointer_settings(p, win), bg=BTNBG, fg=TXT,
                  relief='flat', padx=12, pady=7).pack(side='right', padx=(10, 0))
        tk.Label(heading, text=f'PLAYER {p}   /   MOUSE & CURSOR', bg=BG, fg=TXT,
                 font=(FONT, 15, 'bold')).pack(anchor='w')
        lk = links.get(p)
        active = bool(lk and lk.alive())
        tk.Label(heading, text=('Game hook active: cursor follows the game by default' if active else
                                'No game hook: cursor stays visible in AUTO mode'),
                 bg=BG, fg=POSITIVE if active else WARNING, font=(FONT, 9),
                 wraplength=550, justify='left').pack(anchor='w', pady=(5, 0))

        tk.Label(win, text='MOUSE INPUT ROUTING', bg=BG, fg=ACCENT,
                 font=(FONT, 10, 'bold')).pack(anchor='w', padx=22, pady=(7, 5))
        chosen = tk.StringVar(value=mouse_modes[p])
        for value, title in [
            ('raw','RAW - Minecraft and modern games (recommended)'),
            ('legacy','LEGACY - Classic cursor/message based games'),
            ('both','BOTH - Compatibility; may cause doubled movement')]:
            tk.Radiobutton(win, text=title, variable=chosen, value=value, bg=BG, fg=TXT,
                           selectcolor=CARD, activebackground=BG, activeforeground=ACCENT,
                           anchor='w', highlightthickness=0).pack(fill='x', padx=25, pady=1)
        tk.Button(win, text='Apply mouse input mode', command=lambda: self.apply_mouse_mode(p, chosen.get()),
                  bg=BTNBG, fg=TXT, relief='flat', padx=12, pady=6).pack(anchor='w', padx=23, pady=(5,12))

        tk.Frame(win, bg=BORDER, height=1).pack(fill='x', padx=22, pady=(2,11))
        tk.Label(win, text='SECONDARY CURSOR VISIBILITY', bg=BG, fg=ACCENT,
                 font=(FONT, 10, 'bold')).pack(anchor='w', padx=22)
        mode = tk.StringVar(value=cursor_display_modes[p])
        for value, title in [
            ('auto','AUTO - Hide in gameplay; show in menus (supported games)'),
            ('show','ALWAYS SHOW - Force virtual pointer visible'),
            ('hide','ALWAYS HIDE - Hide virtual pointer')]:
            tk.Radiobutton(win, text=title, variable=mode, value=value, bg=BG, fg=TXT,
                           selectcolor=CARD, activebackground=BG, activeforeground=ACCENT,
                           anchor='w', highlightthickness=0,
                           command=lambda: self.change_cursor_mode(p, mode.get())).pack(fill='x', padx=25, pady=2)
        vis = tk.StringVar()
        tk.Label(win, textvariable=vis, bg=BG, fg=POSITIVE,
                 font=(FONT, 10, 'bold')).pack(anchor='w', padx=25, pady=(5,7))
        tk.Button(win, text='Restore automatic game control',
                  command=lambda: self.restore_cursor_auto(p), bg=BTNBG, fg=TXT,
                  relief='flat', padx=12, pady=6).pack(anchor='w', padx=23, pady=(0,12))

        tk.Frame(win, bg=BORDER, height=1).pack(fill='x', padx=22, pady=(0,11))
        tk.Label(win, text='CUSTOM SHOW / HIDE HOTKEY', bg=BG, fg=ACCENT,
                 font=(FONT, 10, 'bold')).pack(anchor='w', padx=22)
        bind_label = tk.StringVar()
        tk.Label(win, textvariable=bind_label, bg=BG, fg=TXT,
                 font=(FONT, 10, 'bold')).pack(anchor='w', padx=24, pady=(7,8))
        row = tk.Frame(win, bg=BG)
        row.pack(anchor='w', padx=22)
        for name, action in [
                ('Record keyboard key', lambda: self.begin_cursor_binding(p,'keyboard')),
                ('Record mouse button', lambda: self.begin_cursor_binding(p,'mouse')),
                ('Clear', lambda: self.clear_cursor_binding(p))]:
            tk.Button(row, text=name, command=action, bg=BTNBG, fg=TXT,
                      relief='flat', padx=10, pady=7).pack(side='left', padx=(0,8))
        info = tk.StringVar(value='Use a dedicated key (e.g. F8) or a side mouse button.\nHotkeys are exclusive to their recorded physical device.')
        tk.Label(win, textvariable=info, bg=BG, fg=MUTED,
                 justify='left', wraplength=552).pack(anchor='w', padx=23, pady=(9,6))
        tk.Label(win, text='Only the secondary pointer is toggled. Your primary Windows cursor\nand the working v7 keyboard isolation are unchanged.',
                 bg=BG, fg=MUTED, justify='left', wraplength=550).pack(anchor='w', padx=23)
        tk.Label(win, text='SMART CURSOR: Arrow · Hand (buttons) · I-beam (text) · Resize (edges)',
                 bg=BG, fg=ACCENT, justify='left', wraplength=550,
                 font=(FONT, 9)).pack(anchor='w', padx=23, pady=(6, 0))
        self.hotkey_dialogs[p] = {'win':win,'status':info,'binding':bind_label,
                                  'mode':mode,'visibility':vis}
        self.update_hotkey_dialog(p)

        def closing():
            global cursor_capture_request
            if cursor_capture_request and cursor_capture_request['player'] == p:
                cursor_capture_request = None
            self.hotkey_dialogs.pop(p, None)
            win.grab_release()
            win.destroy()
        win.protocol('WM_DELETE_WINDOW', closing)

    def advanced_pointer_settings(self, p, parent=None):
        """Advanced desktop-only mouse features, independent of game hook."""
        if parent and parent.winfo_exists():
            parent.grab_release()  # allow interaction with child settings dialog
        win = tk.Toplevel(parent if parent else self.root)
        win.title(f'Player {p} — Advanced virtual pointer')
        win.geometry(f'540x{min(755, max(490, win.winfo_screenheight()-100))}')
        win.minsize(470, 455)
        win.configure(bg=BG)
        win.transient(parent if parent else self.root)
        scrolling = tk.Frame(win, bg=BG)
        scrolling.pack(fill='both', expand=True, padx=14, pady=(12, 2))
        canvas = tk.Canvas(scrolling, bg=BG, highlightthickness=0, borderwidth=0)
        bar = tk.Scrollbar(scrolling, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        content_frame = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=content_frame, anchor='nw')
        content_frame.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: content_frame.configure(width=max(250, e.width)))
        win.bind('<MouseWheel>', lambda e: canvas.yview_scroll(-int(e.delta / 120), 'units'))
        win.grab_set()
        def close():
            win.grab_release()
            win.destroy()
            if parent and parent.winfo_exists():
                parent.grab_set()
        win.protocol('WM_DELETE_WINDOW', close)
        tk.Label(content_frame, text=f'PLAYER {p}  /  POINTER ACTIONS', bg=BG, fg=TXT,
                 font=(FONT, 16, 'bold')).pack(anchor='w')
        tk.Label(content_frame, text='For Windows desktop windows. Game mouse stays on the original v8 engine.',
                 bg=BG, fg=MUTED, wraplength=465, justify='left',
                 font=(FONT, 10)).pack(anchor='w', pady=(4, 12))
        toggles = {}
        toggle_items = [
            ('hover', 'Hover and tooltip messages'),
            ('focus_on_click', 'Focus clicked desktop app (shared Windows focus)'),
            ('double_click', 'Double-click message compatibility'),
            ('shift_horizontal_wheel', 'Shift + vertical wheel → horizontal'),
            ('auto_scroll', 'Middle-click continuous pan (experimental)'),
            ('clicklock', 'ClickLock (hold, then release to continue dragging)'),
            ('dwell', 'Dwell click after hovering (accessibility)'),
            ('mouse_keys', 'NumPad mouse keys when Win Keys is ON'),
        ]
        current = mouse_features[p]
        for key, description in toggle_items:
            var = tk.BooleanVar(value=current[key])
            toggles[key] = var
            tk.Checkbutton(content_frame, text=description, variable=var, bg=BG,
                           fg=TXT, selectcolor=CARD, activeforeground=TXT,
                           activebackground=BG, anchor='w', highlightthickness=0,
                           font=(FONT, 10)).pack(fill='x', pady=4)
        tk.Label(content_frame, text='DESKTOP SENSITIVITY PROFILES', bg=BG, fg=ACCENT,
                 font=(FONT, 9, 'bold')).pack(anchor='w', pady=(9, 4))
        profile_row = tk.Frame(content_frame, bg=BG)
        profile_row.pack(fill='x', pady=(1, 4))
        profile = tk.StringVar(value=current['profile'])
        tk.Label(profile_row, text='Profile', bg=BG, fg=MUTED).pack(side='left')
        ttk.Combobox(profile_row, textvariable=profile, values=list(PROFILES),
                     state='readonly', width=13, style='Coop.TCombobox').pack(side='right')
        key_row = tk.Frame(content_frame, bg=BG)
        key_row.pack(fill='x', pady=(2, 6))
        tk.Label(key_row, text='Cycle profile with side button', bg=BG, fg=MUTED).pack(side='left')
        profile_key_map = {'Disabled':0, 'X1 / Back':3, 'X2 / Forward':4}
        profile_button = tk.StringVar(value=next(k for k, v in profile_key_map.items()
                                                if v == current['profile_button']))
        ttk.Combobox(key_row, textvariable=profile_button, values=list(profile_key_map),
                     state='readonly', width=13, style='Coop.TCombobox').pack(side='right')
        rows = [
            ('sensitivity', 'Base sensitivity (0.2–4.0)', current['sensitivity']),
            ('double_click_ms', 'Multi-click window (ms)', current['double_click_ms']),
            ('hover_ms', 'Hover delay (ms)', current['hover_ms']),
            ('dwell_ms', 'Dwell delay (ms)', current['dwell_ms']),
            ('clicklock_ms', 'ClickLock hold (ms)', current['clicklock_ms']),
        ]
        fields = {}
        for key, desc, value in rows:
            row = tk.Frame(content_frame, bg=BG)
            row.pack(fill='x', pady=4)
            tk.Label(row, text=desc, bg=BG, fg=MUTED,
                     font=(FONT, 9)).pack(side='left')
            v = tk.StringVar(value=str(value))
            fields[key] = v
            tk.Entry(row, textvariable=v, width=9, bg=CARD, fg=TXT,
                     insertbackground=TXT, relief='flat', justify='center',
                     font=(FONT, 10)).pack(side='right', ipady=4)
        tk.Label(content_frame, text='Focusing an app also changes Player 1\'s keyboard focus.\n'
                 'Hardware DPI, mechanical free-scroll, native file drag/drop,\n'
                 'and OS-wide gestures cannot be guaranteed by a virtual pointer.',
                 bg=BG, fg=MUTED, justify='left',
                 font=(FONT, 9)).pack(anchor='w', pady=(10, 7))
        def save():
            try:
                update = {key: v.get() for key, v in toggles.items()}
                for key, variable in fields.items():
                    value = float(variable.get())
                    if not __import__('math').isfinite(value):
                        raise ValueError('Non-finite value')
                    update[key] = value
                update['profile'] = profile.get()
                update['profile_button'] = profile_key_map[profile_button.get()]
                mouse_features[p] = validated_features(update)
                desktop_drag.configure(p, **mouse_features[p])
                self.save_config()
                close()
            except ValueError:
                messagebox.showerror('Invalid setting', 'Use numbers for sensitivity and timing.', parent=win)
        tk.Button(win, text='Save pointer settings', command=save, bg=ACCENT,
                  fg=BTNFG, relief='flat', padx=20, pady=10,
                  font=(FONT, 10, 'bold')).pack(anchor='e', padx=24, pady=(5,16))

    def desktop_shortcut_label(self, p):
        return next((label for label, code in DESKTOP_KEYS.items()
                     if code == desktop_toggle_scancodes[p]), 'F10')

    def toggle_desktop(self, p):
        if p not in desktop_control:
            return
        request_desktop_mode(p, not desktop_control[p])

    def desktop_settings(self, p):
        win = tk.Toplevel(self.root)
        win.title(f'Player {p}  ·  Windows access')
        win.geometry('500x300')
        win.resizable(False, False)
        win.configure(bg=BG)
        win.transient(self.root)
        body = tk.Frame(win, bg=BG, padx=28, pady=22)
        body.pack(fill='both', expand=True)
        tk.Label(body, text='Windows keyboard shortcuts', bg=BG, fg=TXT,
                 font=(FONT, 17, 'bold')).pack(anchor='w')
        tk.Label(body, text='Optional · Off by default', bg=BG, fg=MUTED,
                 font=(FONT, 10)).pack(anchor='w', pady=(4, 17))
        tk.Label(body, text='When enabled, Player 2 keyboard keys can use Windows shortcuts '
                 '(Win, Alt+Tab, F11) in the active Windows app. '
                 'The secondary MOUSE ALWAYS stays separate, with its own visible '
                 'virtual cursor, drag capture, and best-effort desktop window resizing. '
                 'Windows focus is still shared; some apps ignore virtual clicks. '
                 'Turn off to restore isolated game keyboard input.',
                 bg=BG, fg=TXT, wraplength=430, justify='left',
                 font=(FONT, 10)).pack(anchor='w')
        hot_row = tk.Frame(body, bg=BG)
        hot_row.pack(fill='x', pady=(20, 14))
        tk.Label(hot_row, text='Toggle hotkey (assigned keyboard)', bg=BG, fg=MUTED,
                 font=(FONT, 10)).pack(side='left')
        chosen = tk.StringVar(value=self.desktop_shortcut_label(p))
        selector = ttk.Combobox(hot_row, textvariable=chosen, state='readonly',
                                values=list(DESKTOP_KEYS), width=8, style='Coop.TCombobox')
        selector.pack(side='right')
        def save():
            desktop_toggle_scancodes[p] = DESKTOP_KEYS[chosen.get()]
            self.save_config()
            self.redraw()
            win.destroy()
        tk.Button(body, text='Save shortcut', command=save, bg=ACCENT, fg=BTNFG,
                  font=(FONT, 10, 'bold'), relief='flat', padx=18, pady=10).pack(side='right')

    # ----- actions -----
    def apply_monitor(self, p):
        l, t, r, b = self.mons[self.mon_idx[p]]
        bounds[p][:] = [l, t, r, b]
        pos[p][:] = [(l + r) // 2, (t + b) // 2]

    def cycle_monitor(self, p):
        """Change P2+ virtual cursor bounds, NEVER device ownership or input path.

        Desktop Control is an explicit temporary handoff. Changing displays
        requests isolated mode again, so the old 'P2 became P1' symptom cannot
        persist as a hidden desktop routing state.
        """
        if p not in self.mon_idx or len(self.mons) <= 1:
            return
        if desktop_control.get(p):
            request_desktop_mode(p, False)
        desktop_drag.cancel(p, *pos[p])
        old_bounds = bounds[p][:]
        old_pos = pos[p][:]
        new_idx = (self.mon_idx[p] + 1) % len(self.mons)
        self.mon_idx[p] = new_idx
        self.apply_monitor(p)
        l, t, r, b = bounds[p]
        ow = max(1, old_bounds[2] - old_bounds[0])
        oh = max(1, old_bounds[3] - old_bounds[1])
        xf = max(0., min(1., (old_pos[0] - old_bounds[0]) / ow))
        yf = max(0., min(1., (old_pos[1] - old_bounds[1]) / oh))
        pos[p][:] = [min(r - 1, l + int(xf * (r - l))),
                     min(b - 1, t + int(yf * (b - t)))]
        # A linked game is still the SAME player's hook; no input reassignment.
        link = links.get(p)
        if link and link.alive():
            link.sh.pos_x, link.sh.pos_y = pos[p]
        self.last_pos.pop(p, None)
        self.refresh_cursors()
        log.info('Player %d display -> %d; assigned devices unchanged', p, new_idx + 1)
        self.save_config()
        self.redraw()

    def set_players(self, n):
        n = min(max(n, 2), MAXP)
        if n == state["players"]:
            return
        state["players"] = n
        for d, p in list(assign.items()):
            if p > n:
                release_device(d, p)
                del assign[d]
        for q in list(links):
            if q > n:
                detach(q)
        for p in range(n + 1, MAXP + 1):
            if desktop_control.get(p):
                request_desktop_mode(p, False)
        log.info("Jogadores: %d", n)
        self.save_config()
        self.refresh_cursors()
        self.redraw()

    def toggle_forward(self):
        if state["forward"]:
            desktop_drag.cancel_all()
            for d, p in list(assign.items()):
                release_device(d, p)
        state["forward"] = not state["forward"]
        log.info("Input forwarding %s (explicit user action)", "ON" if state["forward"] else "OFF")
        self.save_config()
        self.refresh_cursors()
        self.redraw()

    def reset(self):
        log.info("Reset: tudo para a lista de dispositivos")
        self.assignments_modified = True
        self.restore_saved_on_start = False
        self.saved = {}
        desktop_drag.cancel_all()
        for d, old in list(assign.items()):
            release_device(d, old)
        assign.clear()
        for p in desktop_control:
            if desktop_control[p]:
                request_desktop_mode(p, False)
        self.save_config()
        self.refresh_cursors()
        self.redraw()

    def assign_dev(self, d, p):
        old = assign.get(d, 0)
        if old == p:
            return
        self.assignments_modified = True
        release_device(d, old)
        for player, bind in cursor_hotkeys.items():
            if bind and bind['device'] == d and old != p:
                # Keep hotkey for reassigning the same device back, but don't
                # act on shortcuts from devices no longer assigned to player.
                cursor_pressed_bindings.difference_update(
                    {key for key in cursor_pressed_bindings if key[0] == d})
        if p:
            assign[d] = p
        else:
            assign.pop(d, None)
        desktop_key_held.difference_update({k for k in desktop_key_held if k[0] == d})
        # Prevent an old player's Desktop Control from silently capturing
        # a newly reassigned device.
        if old >= 2 and desktop_control.get(old):
            request_desktop_mode(old, False)
        log.info("%s -> %s", dev_name(d), f"Jogador {p}" if p else "livre")
        self.save_config()
        self.refresh_cursors()

    def refresh_cursors(self):
        for p in range(2, MAXP + 1):
            want = state["forward"] and p <= state["players"] and any(
                v == p and d > 10 for d, v in list(assign.items()))
            if want and p not in self.overlays:
                self.overlays[p] = make_overlay(self.root, self.cursor_colors[p], COLORS[p])
                self.overlays[p].pointer_tint = self.cursor_colors[p]
            ov = self.overlays.get(p)
            # Only secondary virtual pointers are affected. In AUTO mode GLFW
            # captures the cursor in gameplay (ClipCursor(rect)) and releases it
            # for menus (ClipCursor(NULL)). A manual hotkey can override either.
            show = bool(want and virtual_cursor_visible(p))
            status = (bool(want), show, cursor_display_modes[p], cursor_manual_overrides[p])
            if self.cursor_status.get(p) != status:
                self.cursor_status[p] = status
                log.info("Player %d virtual cursor: %s; assigned mouse=%s, input=%s, mode=%s, "
                         "override=%s, screen_position=%s", p, "shown" if show else "hidden",
                         bool(want), bool(state["forward"]), cursor_display_modes[p],
                         cursor_manual_overrides[p], tuple(pos[p]))
            if show and not self.visible.get(p):
                ov.deiconify()
                ov.lift()
            elif not show and ov and self.visible.get(p):
                ov.withdraw()
            self.visible[p] = show
            self.last_pos.pop(p, None)
            self.pointer_probe.pop(p, None)

    # ----- drawing -----
    def hot(self, tag):
        self.c.tag_bind(tag, "<Enter>", lambda e: self.c.config(cursor="hand2"))
        self.c.tag_bind(tag, "<Leave>", lambda e: self.c.config(cursor=""))

    def btn(self, xr, cy, label, cb, fill=BTNBG, fg=TXT, w=None, h=32):
        w = w or len(label) * 8 + 30
        x1 = xr - w
        tag = f"b{self.nb}"
        self.nb += 1
        rrect(self.c, x1, cy - h / 2, xr, cy + h / 2, 10,
              fill=fill, outline=BORDER if fill == BTNBG else fill, tags=tag)
        self.c.create_text((x1 + xr) / 2, cy, text=label, fill=fg,
                           font=(FONT, 10, "bold"), tags=tag)
        self.c.tag_bind(tag, "<Button-1>", lambda e: cb())
        self.hot(tag)
        return x1

    def on_resize(self, e):
        if self.resize_job:
            self.root.after_cancel(self.resize_job)
        self.resize_job = self.root.after(50, self.redraw)

    def redraw(self):
        if self.drag:
            return
        c = self.c
        W, H = c.winfo_width(), c.winfo_height()
        if W < 300 or H < 200:
            return
        c.delete('all')
        self.nb = 0
        self.zones, self.zone_rect, self.leds = {}, {}, {}
        n = state['players']

        # Spacious header: actions never overlap the product name or subtitle.
        header_h = 110
        c.create_rectangle(0, 0, W, header_h, fill=PANEL, outline='')
        c.create_line(0, header_h, W, header_h, fill=BORDER)
        c.create_text(24, 34, text='CoopInput', anchor='w', fill=TXT,
                      font=(FONT, 23, 'bold'))
        c.create_text(191, 37, text='STUDIO', anchor='w', fill=ACCENT,
                      font=(FONT, 10, 'bold'))
        c.create_text(24, 82, text='Safe start: Input OFF. Assign devices, then enable input. Emergency: Ctrl+Alt+F12.',
                      anchor='w', fill=MUTED, font=(FONT, 10))
        if self.saved and not self.restore_saved_on_start and not self.assignments_modified:
            self.btn(W - 22, 82, 'Restore saved', self.restore_saved_assignments,
                     w=148, h=30)
        x = W - 22
        x = self.btn(x, 35, 'Find P2 devices', self.open_device_guide,
                     fill=ACCENT, fg=BTNFG, w=145, h=36)
        x -= 9
        x = self.btn(x, 35, 'Reset', self.reset, w=72, h=36)
        x -= 9
        on = state['forward']
        x = self.btn(x, 35, f'Input: {"ON" if on else "OFF"}', self.toggle_forward,
                     fill=POSITIVE if on else BTNBG, fg=BTNFG if on else TXT,
                     w=108, h=36)
        x -= 10
        x = self.btn(x, 35, '☀  Light' if self.theme == 'dark' else '☾  Dark',
                     self.toggle_theme, w=104, h=36)
        x -= 24
        x = self.btn(x, 35, '+', lambda: self.set_players(n + 1), w=36, h=36)
        c.create_text(x - 20, 35, text=str(n), anchor='center', fill=TXT,
                      font=(FONT, 13, 'bold'))
        x -= 40
        x = self.btn(x, 35, '−', lambda: self.set_players(n - 1), w=36, h=36)
        c.create_text(x - 12, 35, text='Players', anchor='e', fill=MUTED,
                      font=(FONT, 10))

        # Scales to 2–4 player zones while retaining original drag/drop hit boxes.
        pad, top, gap, pool_w = 18, header_h + 18, 16, 304
        zone_bottom = H - 74
        self.draw_zone(0, pad, top, pad + pool_w, zone_bottom)
        ax1, ax2 = pad + pool_w + gap, W - pad
        cols = 2
        rows = (n + cols - 1) // cols
        zw = (ax2 - ax1 - gap * (cols - 1)) / cols
        zh = (zone_bottom - top - gap * (rows - 1)) / rows
        for p in range(1, n + 1):
            r, col = divmod(p - 1, cols)
            x1 = ax1 + col * (zw + gap)
            y1 = top + r * (zh + gap)
            self.draw_zone(p, x1, y1, x1 + zw, y1 + zh)

        footer_h = 56
        c.create_rectangle(0, H - footer_h, W, H, fill=PANEL, outline='')
        c.create_line(0, H - footer_h, W, H - footer_h, fill=BORDER)
        hook_ready = hook_binary_compatible(HOOK_PATH)
        info = 'v8 ENGINE  ·  READY' if hook_ready else 'GAME HOOK NOT READY'
        c.create_text(24, H - 28, text=info, anchor='w',
                      fill=POSITIVE if hook_ready else WARNING,
                      font=(FONT, 10, 'bold'))
        c.create_text(224, H - 28,
                      text=f'Keys: {stats["keys"]}     Mouse: {stats["moves"]}     Errors: {stats["forward_fail"]}',
                      anchor='w', fill=MUTED, font=(FONT, 9))
        c.create_text(W - 24, H - 28, text='Ctrl + Alt + F12    Emergency release',
                      anchor='e', fill=ACCENT, font=(FONT, 9))

    def draw_zone(self, p, x1, y1, x2, y2):
        c = self.c
        color = COLORS[p]
        self.zones[p] = (x1, y1, x2, y2)
        self.zone_rect[p] = rrect(c, x1, y1, x2, y2, 18,
                                  fill=PANEL, outline=BORDER, width=1)
        c.create_oval(x1 + 19, y1 + 23, x1 + 31, y1 + 35,
                      fill=color, outline='')
        if p == 0:
            title, sub = 'Unassigned', 'Drag a device into a player card'
        elif p == 1:
            title, sub = 'Player 1', 'Primary · Windows cursor'
        else:
            title = f'Player {p}'
            sub = ('Windows keys enabled · Independent desktop drag' if desktop_control[p] else
                   f'Isolated input · Cursor {cursor_display_modes[p]}')
        c.create_text(x1 + 43, y1 + 29, text=title,
                      anchor='w', fill=TXT, font=(FONT, 14, 'bold'))
        c.create_text(x1 + 19, y1 + 59, text=sub, anchor='w',
                      fill=MUTED, font=(FONT, 9))

        # Compact two-column controls, with consistent gutters and row height.
        if p >= 2:
            inset, gap = 17, 10
            usable = max(260, x2 - x1 - inset * 2)
            half = (usable - gap) / 2
            left_end = x1 + inset + half
            right_end = x2 - inset
            # Hotkey sits on the title row, away from the longer status label.
            self.btn(right_end, y1 + 28,
                     f'Hotkey: {self.desktop_shortcut_label(p)}  ⚙',
                     lambda p=p: self.desktop_settings(p), h=28, w=135)
            lk = links.get(p)
            game_label = ('Attached: ' + lk.title[:12] + ('…' if len(lk.title)>12 else '')) if lk else 'Attach game'
            self.btn(left_end, y1 + 91, game_label,
                     lambda p=p: self.open_picker(p), h=33,
                     w=half, fill=ACCENT if lk else BTNBG,
                     fg=BTNFG if lk else TXT)
            self.btn(right_end, y1 + 91,
                     f'Display {self.mon_idx[p] + 1}  ›',
                     lambda p=p: self.cycle_monitor(p), h=33, w=half)
            self.btn(left_end, y1 + 132, 'Mouse / Cursor',
                     lambda p=p: self.mouse_settings(p), h=33, w=half)
            self.btn(right_end, y1 + 132,
                     'Win Keys: ON' if desktop_control[p] else 'Win Keys: OFF',
                     lambda p=p: self.toggle_desktop(p), h=33, w=half,
                     fill=POSITIVE if desktop_control[p] else BTNBG,
                     fg=BTNFG if desktop_control[p] else TXT)
            # Visual divider separates player actions from draggable hardware.
            c.create_line(x1+18, y1+157, x2-18, y1+157, fill=BORDER)

        ids = sorted(set(hwids) | set(seen))
        devs = [d for d in ids if assign.get(d, 0) == p]
        y0 = y1 + (169 if p >= 2 else 83)
        if not devs:
            if y0 + 25 < y2 - 14:
                rrect(c, x1 + 15, y0, x2 - 15, y2 - 15, 10,
                      fill=PLACEHOLDER, outline=BORDER, dash=(4, 5))
                c.create_text((x1+x2)/2, (y0+y2)/2,
                              text='Drop devices here', fill=MUTED, font=(FONT, 10))
            return
        avail = max(30, y2 - 16 - y0)
        h = max(32, min(60, (avail - (len(devs) - 1) * 10) / len(devs)))
        for i, d in enumerate(devs):
            cy1 = y0 + i*(h+10)
            if cy1+h > y2-8:
                # Do not draw overflowing devices over neighboring player cards.
                break
            self.draw_card(d, x1+15, cy1, x2-15, cy1+h)

    def draw_card(self, d, x1, y1, x2, y2):
        c = self.c
        tag = f'card{d}'
        cy = (y1+y2)/2
        rrect(c, x1, y1, x2, y2, 11, fill=CARD, outline=BORDER, tags=tag)
        is_kb = d <= 10
        rrect(c, x1+10, cy-14, x1+40, cy+14, 8,
              fill=KBBG if is_kb else MSBG, outline='', tags=tag)
        c.create_text(x1+25, cy, text='KB' if is_kb else 'MS',
                      fill=KBFG if is_kb else MSFG,
                      font=(FONT, 9, 'bold'), tags=tag)
        # Reserve fixed space for profiles and activity indicators, so text
        # never paints underneath the controls even on the unassigned column.
        reserved = 130 if is_kb else 32
        label_px = max(48, x2 - reserved - (x1+51) - 8)
        # Deliberately truncate instead of wrapping into the adjacent row.
        def short(text, width_px, font_px):
            limit = max(5, int(width_px/max(5.5,font_px)))
            return text if len(text) <= limit else text[:limit-1]+'…'
        c.create_text(x1+51, cy-9, text=short(dev_name(d),label_px,7.1),
                      anchor='w', fill=TXT, font=(FONT, 10, 'bold'), tags=tag)
        c.create_text(x1+51, cy+10, text=short(short_id(d),label_px,6.0),
                      anchor='w', fill=MUTED, font=(FONT, 8), tags=tag)
        if is_kb:
            profile = keyboard_profiles.get(d,'auto')
            auto_label,_ = keyboard_layout_text(d)
            label = ('Auto ' + auto_label if profile == 'auto' else
                     PROFILE_LABELS.get(profile,'Custom').split(' ')[0])
            prof_tag = f'keyboard_profile_{d}'
            rrect(c, x2-130, cy-14, x2-14, cy+14, 7,
                  fill=SELECTBG, tags=prof_tag)
            c.create_text(x2-72, cy, text=short(label,87,7.1)+'  ▾',
                          fill=TXT, font=(FONT, 8, 'bold'), tags=prof_tag)
            c.tag_bind(prof_tag,'<ButtonPress-1>',
                       lambda e,d=d: self.configure_keyboard(d))
        led_x = x2-(139 if is_kb else 26)
        self.leds[d] = c.create_oval(led_x, cy-4.5, led_x+9, cy+4.5,
                                    fill=LED_OFF, outline='', tags=tag)
        c.tag_bind(tag,'<ButtonPress-1>',lambda e,d=d:self.drag_start(e,d))
        c.tag_bind(tag,'<Enter>',lambda e:self.c.config(cursor='fleur'))
        c.tag_bind(tag,'<Leave>',lambda e:self.c.config(cursor=''))

    def open_device_guide(self):
        old = self.pair_dialog
        if old and old.winfo_exists():
            old.lift()
            return
        win = tk.Toplevel(self.root)
        self.pair_dialog = win
        win.title('Connect Player 2 devices')
        win.configure(bg=BG)
        win.geometry('620x505')
        win.resizable(False, False)
        win.transient(self.root)
        # Do not auto-grab input: the user must be able to test physical devices.
        f = tk.Frame(win, bg=BG, padx=26, pady=20)
        f.pack(fill='both', expand=True)
        tk.Label(f, text='Player 2  /  Devices', bg=BG, fg=TXT,
                 font=(FONT, 21, 'bold')).pack(anchor='w')
        tk.Label(f, text='Quick detection, or simply drag devices on the main board.',
                 bg=BG, fg=MUTED, font=(FONT, 10)).pack(anchor='w', pady=(4, 17))
        selected = {'keyboard': next((d for d, p in assign.items() if p == 2 and d <= 10), None),
                    'mouse': next((d for d, p in assign.items() if p == 2 and d > 10), None)}
        scanning = {'kind':None, 'begin':0, 'until':0}
        textvars = {}
        for kind, title, hint in [
            ('keyboard', '1   Secondary keyboard', 'Press any key on the keyboard Player 2 will use.'),
            ('mouse', '2   Secondary mouse', 'Move the physical mouse Player 2 will use.')]:
            card = tk.Frame(f, bg=PANEL, padx=16, pady=12, highlightthickness=1,
                            highlightbackground=BORDER)
            card.pack(fill='x', pady=(0, 12))
            tk.Label(card, text=title, bg=PANEL, fg=TXT, font=(FONT, 12, 'bold')).pack(anchor='w')
            tk.Label(card, text=hint, bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(anchor='w', pady=(3, 7))
            row = tk.Frame(card, bg=PANEL)
            row.pack(fill='x')
            v = tk.StringVar(value=dev_name(selected[kind]) if selected[kind] else 'Not assigned yet')
            textvars[kind] = v
            tk.Label(row, textvariable=v, bg=PANEL, fg=ACCENT,
                     font=(FONT, 10, 'bold')).pack(side='left', fill='x', expand=True, anchor='w')
            tk.Button(row, text='Detect ' + kind, command=lambda k=kind: start(k), bg=ACCENT,
                      fg=TXT, relief='flat', bd=0, padx=16, pady=8).pack(side='right')
        info = tk.StringVar(value='Click Detect and then use the matching physical device.')
        tk.Label(f, textvariable=info, bg=BG, fg=MUTED, font=(FONT, 9),
                 wraplength=550, justify='left').pack(anchor='w', pady=(3, 13))
        bottom = tk.Frame(f, bg=BG)
        bottom.pack(fill='x', side='bottom')

        def start(kind):
            if scanning['kind']:
                info.set('Finish the current detection first.')
                return
            scanning['kind'] = kind
            scanning['begin'] = time.time() + 0.5  # Ignore the click used to start detection.
            scanning['until'] = time.time() + 14
            info.set('Listening for Player 2 ' + kind + '… You have 14 seconds.')
            check()

        def check():
            if not win.winfo_exists() or not scanning['kind']:
                return
            kind = scanning['kind']
            now = time.time()
            if now > scanning['until']:
                scanning['kind'] = None
                info.set('No device detected. Try again. Move the mouse or press a key.')
                return
            matches = [(d, stamp) for d, stamp in tuple(last_active.items())
                       if (d <= 10 if kind == 'keyboard' else d > 10)
                       and stamp >= scanning['begin']]
            if matches:
                d = max(matches, key=lambda pair: pair[1])[0]
                scanning['kind'] = None
                name = dev_name(d)
                if messagebox.askyesno('Confirm Player 2 ' + kind,
                        'Detected: ' + name + '\n\nWas that the device Player 2 used?'
                        '\n\nChoosing your primary keyboard or mouse will redirect it '
                        'until you reset assignments.', parent=win):
                    selected[kind] = d
                    textvars[kind].set(name)
                    info.set('Confirmed ' + kind + '. Detect the other device or save.')
                else:
                    info.set('No changes made. Try detection again.')
                return
            win.after(80, check)

        def save():
            if scanning['kind']:
                info.set('Finish the current detection first.')
                return
            try:
                keyboard, mouse = selected['keyboard'], selected['mouse']
                if not isinstance(keyboard, int) or keyboard not in range(1, 11):
                    raise ValueError("Choose Player 2's physical keyboard first.")
                if not isinstance(mouse, int) or mouse not in range(11, 21):
                    raise ValueError("Choose Player 2's physical mouse first.")
            except ValueError as exc:
                info.set(str(exc))
                return
            for d in (keyboard, mouse):
                self.assign_dev(d, 2)
            self.save_config()
            self.refresh_cursors()
            self.redraw()
            win.destroy()

        tk.Button(bottom, text='Done  /  Save devices', command=save, bg=POSITIVE,
                  fg=BTNFG, font=(FONT, 11, 'bold'), relief='flat', bd=0,
                  padx=25, pady=12).pack(side='right')
        tk.Button(bottom, text='Back to device board', command=win.destroy,
                  bg=BTNBG, fg=TXT, relief='flat', bd=0, padx=14, pady=12).pack(side='left')

    # ----- anexar jogo -----
    def open_picker(self, p):
        old = getattr(self, "picker", None)
        if old is not None and old.winfo_exists():
            old.destroy()
        win = self.picker = tk.Toplevel(self.root)
        win.title(f"Anexar jogo ao Jogador {p}")
        win.configure(bg=BG)
        win.geometry("560x440")
        win.transient(self.root)
        tk.Label(win, text=f"Anexar jogo ao Jogador {p}", bg=BG, fg=TXT,
                 font=(FONT, 13, "bold")).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(win, text="Abra o jogo antes e escolha a janela dele. So jogos 64-bit, offline/LAN.",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=16)
        lb = tk.Listbox(win, bg=PANEL, fg=TXT, selectbackground=COLORS[p], selectforeground=PICKFG,
                        highlightthickness=0, bd=0, font=(FONT, 10), activestyle="none")
        lb.pack(fill="both", expand=True, padx=16, pady=10)
        items = []

        def refresh():
            items[:] = list_windows()
            lb.delete(0, "end")
            for t, pid in items:
                lb.insert("end", f"{t}   (pid {pid})")

        def do_attach():
            sel = lb.curselection()
            if not sel:
                return
            t, pid = items[sel[0]]
            win.config(cursor="watch")
            win.update_idletasks()
            ok, msg = attach(p, pid, t)
            win.config(cursor="")
            if ok:
                win.destroy()
                self.refresh_cursors()
                self.redraw()
            else:
                log.error("Anexar falhou: %s", msg)
                messagebox.showerror("CoopInput", msg, parent=win)

        def do_detach():
            detach(p)
            win.destroy()
            self.refresh_cursors()
            self.redraw()

        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", padx=16, pady=(0, 14))

        def mk(text, cmd, side, fill=BTNBG):
            tk.Button(row, text=text, command=cmd, bg=fill,
                      fg=BTNFG if fill == POSITIVE else TXT, bd=0, relief="flat",
                      activebackground=ACTIVEBG, activeforeground=TXT, padx=16, pady=7,
                      font=(FONT, 10, "bold")).pack(side=side, padx=(0, 8))

        mk("Atualizar", refresh, "left")
        if p in links:
            mk("Desanexar", do_detach, "left")
        mk("Anexar", do_attach, "right", fill=POSITIVE)
        lb.bind("<Double-Button-1>", lambda e: do_attach())
        refresh()

    # ----- drag & drop -----
    def zone_at(self, x, y):
        for p, (x1, y1, x2, y2) in self.zones.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                return p
        return None

    def drag_start(self, e, d):
        self.c.tag_raise(f"card{d}")
        self.drag = {"dev": d, "x": e.x, "y": e.y, "hover": None}

    def drag_move(self, e):
        if not self.drag:
            return
        dr = self.drag
        self.c.move(f"card{dr['dev']}", e.x - dr["x"], e.y - dr["y"])
        dr["x"], dr["y"] = e.x, e.y
        z = self.zone_at(e.x, e.y)
        if z != dr["hover"]:
            if dr["hover"] is not None:
                self.c.itemconfig(self.zone_rect[dr["hover"]], outline=BORDER)
            if z is not None:
                self.c.itemconfig(self.zone_rect[z], outline=COLORS[z])
            dr["hover"] = z

    def drag_end(self, e):
        if not self.drag:
            return
        d = self.drag["dev"]
        z = self.zone_at(e.x, e.y)
        self.drag = None
        if z is not None:
            self.assign_dev(d, z)
        self.redraw()

    # ----- loop -----
    def tick(self):
        updated = False
        while True:
            try:
                action, p = cursor_ui_events.get_nowait()
            except queue.Empty:
                break
            if action == 'toggle':
                cursor_manual_overrides[p] = not virtual_cursor_visible(p)
                log.info('Player %d cursor hotkey -> %s', p,
                         'visible' if cursor_manual_overrides[p] else 'hidden')
                updated = True
            elif action == 'desktop':
                updated = True
            elif action == 'pointer_profile':
                log.info('Player %d virtual mouse profile: %s', p, mouse_features[p]['profile'])
                self.save_config()
                updated = True
            elif action == 'bound':
                self.save_config()
                dialog = self.hotkey_dialogs.get(p)
                if dialog and dialog['win'].winfo_exists():
                    dialog['status'].set('Shortcut saved. Press it again to show/hide the pointer.')
                updated = True
            self.update_hotkey_dialog(p)
        if updated:
            self.refresh_cursors()
            self.redraw()
        if state["panic"]:
            desktop_key_held.clear()
            for p in desktop_control:
                desktop_control[p] = False
            for p in cursor_manual_overrides:
                cursor_manual_overrides[p] = None
            state["panic"] = False
            self.refresh_cursors()
            self.save_config()
            self.redraw()
        if len(seen) != self.nseen:
            self.sync_devices()
            self.refresh_cursors()
            self.redraw()
        now = time.time()
        if now - self.last_layout_refresh > 2:
            self.last_layout_refresh = now
            if not self.drag:
                self.redraw()
        for p in range(2, state["players"] + 1):
            has_mouse = any(d > 10 and v == p for d, v in list(assign.items()))
            lk = links.get(p)
            want_visible = state["forward"] and has_mouse and virtual_cursor_visible(p)
            if self.visible.get(p, False) != want_visible:
                self.refresh_cursors()
                self.redraw()
            self.update_hotkey_dialog(p)
        for d, led in self.leds.items():
            on = now - last_active.get(d, 0) < 0.25
            self.c.itemconfig(led, fill=COLORS[assign.get(d, 0)] if on else LED_OFF)
        for p, ov in self.overlays.items():
            if not self.visible.get(p):
                continue
            lk = links.get(p)
            if lk and lk.alive() and not desktop_control.get(p, False):
                q = (lk.sh.pos_x, lk.sh.pos_y)
            else:
                # During Windows-keyboard mode, P2's mouse is still controlled
                # by the local virtual pointer, not the game-hook coordinates.
                q = (int(pos[p][0]), int(pos[p][1]))
            # Probe hover shape at most ~12 times per second per player.
            # Mouse forwarding still happens immediately in the capture thread.
            last_probe = self.pointer_probe.get(p)
            moving = self.last_pos.get(p) != q
            t = time.monotonic()
            # Refresh quickly while moving; re-check stationary targets slowly
            # (e.g. after a menu opens beneath the independent pointer).
            if not last_probe or (t - last_probe[0]) >= (0.085 if moving else 0.45):
                try:
                    new_shape = virtual_pointer_shape(p, *q)
                except Exception as e:
                    log.debug('P%d virtual pointer probe failed: %s', p, e)
                    new_shape = 'arrow'
                self.pointer_probe[p] = (t, *q, new_shape)
                if new_shape != ov.pointer_shape:
                    ov.pointer_shape = new_shape
                    ov.pointer_hotspot = paint_system_cursor(ov.pointer_canvas, new_shape, getattr(ov, 'pointer_tint', None), system_cursor_art)
                    moving = True  # align the new cursor's hotspot
            if moving:
                self.last_pos[p] = q
                hx, hy = getattr(ov, 'pointer_hotspot', HOTSPOTS.get(ov.pointer_shape, (1, 1)))
                # The OS pointer stays untouched; only the separate overlay moves.
                ov.geometry(f"{q[0]-hx:+d}{q[1]-hy:+d}")
        self.root.after(5, self.tick)


def main():
    t = threading.Thread(target=capture, daemon=True, name="capture")
    t.start()
    ready.wait(5)
    if not ready.is_set():
        log.error("Captura nao ficou pronta em 5s")
    if state["error"]:
        fatal(state["error"])
    root = tk.Tk()
    root.report_callback_exception = lambda *a: log.critical("Erro na UI:\n%s", _fmt(*a))
    app = App(root)

    def close():
        log.info("Fechando")
        desktop_drag.cancel_all()
        for d, old in list(assign.items()):
            release_device(d, old)
        assign.clear()
        for q in list(links):
            detach(q)
        state["running"] = False
        t.join(1)
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(5, app.tick)
    log.info("UI pronta")
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log.critical("Erro fatal:\n%s", traceback.format_exc())
        if getattr(sys, "frozen", False):
            try:
                ctypes.windll.user32.MessageBoxW(0,
                    "CoopInput encontrou um erro. Veja:\n" + LOG_PATH,
                    "CoopInput — Error", 0x10)
            except Exception:
                pass
        else:
            input("Erro. Veja coopinput_debug.log. Enter para fechar...")
