"""
CoopInput v8 - game-aware virtual cursor, strict input isolation and automatic keyboard detection
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
from ctypes import wintypes as wt
from tkinter import messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "coopinput_debug.log")
CFG_PATH = os.path.join(HERE, "coopinput_config.json")
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
        fatal("interception.dll nao encontrada ao lado do coopinput.py.")
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


def virtual_cursor_visible(player):
    """Cursor visibility for one secondary player, without changing Windows."""
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
        for _ in range(60):
            if lk.alive():
                break
            time.sleep(0.1)
        if not lk.alive():
            old_api = bool(lk.sh.flags & 1) and lk.sh.pad[0] != HOOK_ABI_VERSION
            # A failed hook must be allowed to shut down while the mapping is still valid.
            lk.push(EV_EXIT)
            time.sleep(0.12)
            lk.close()
            return False, ("The game's hook is using the wrong ABI. Close the game, rebuild the v8 DLL, "
                           "and restart both applications." if old_api else
                           "DLL loaded but did not initialize. The game may use an unsupported input path; "
                           "check coopinput_debug.log and try windowed mode.")
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
MAXP = 4
COLORS = {0: "#3ddc84", 1: "#4c8dff", 2: "#00e5ff", 3: "#ff5fd2", 4: "#ffd23f"}

assign = {}        # device -> 0 (livre, passa direto) | 1 (principal) | 2..MAXP (isolado)
seen = set()
hwids = {}
last_active = {}
pos = [None] + [[0, 0] for _ in range(MAXP)]                    # cursor virtual por jogador
bounds = [None] + [[0, 0, 1920, 1080] for _ in range(MAXP)]     # tela por jogador
state = {"running": True, "forward": True, "panic": False, "error": None, "players": 2}
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
    h = user32.WindowFromPoint(wt.POINT(int(x), int(y)))
    return h if h and h not in overlay_hwnds else None


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
    if key_state[p][0x10] & 0x80:
        flags |= 0x0004  # MK_SHIFT
    if key_state[p][0x11] & 0x80:
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
    lk = links.get(old_player)
    for (scan, flags), vk in old.items():
        update_key_state(old_player, d, vk, False)
        if lk:
            lk.push(EV_KEY, vk, 0, scan | ((flags & 0x06) << 7))
        elif state["forward"]:
            post_key(old_player, KeyStroke(scan, (flags & 0x06) | 1, 0), vk)
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


# Interception button bits: left/right/middle/X1/X2, including their up events.
BUTTON_EVENTS = ((0x001, 0, True), (0x002, 0, False),
                 (0x004, 1, True), (0x008, 1, False),
                 (0x010, 2, True), (0x020, 2, False),
                 (0x040, 3, True), (0x080, 3, False),
                 (0x100, 4, True), (0x200, 4, False))


def on_mouse(m, player, device):
    if player < 2:
        return False
    lk = links.get(player)
    linked = bool(state["forward"] and lk and lk.alive())
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
        dx, dy = m.x, m.y
    if dx or dy:
        p[0] = min(max(p[0] + dx, b[0]), b[2] - 1)
        p[1] = min(max(p[1] + dy, b[1]), b[3] - 1)
        if linked:
            # Pass the actual clamped displacement to the hooked game.
            lk.push(EV_MOVE, dx, dy)
        elif state["forward"]:
            post_mouse(player, WM_MOUSEMOVE)
        stats["moves"] += 1
    pressed = held_mouse.setdefault(device, set())
    for bit, idx, down_now in BUTTON_EVENTS:
        if not m.state & bit:
            continue
        # Mouse-button hotkeys affect only the assigned virtual cursor.
        # Do not forward hotkey click/release to the game.
        if cursor_hotkey_event(player, device, 'mouse', idx, 0, down_now):
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
            msgs = ((0x201, 0x202), (0x204, 0x205), (0x207, 0x208),
                    (WM_XBUTTONDOWN, WM_XBUTTONUP), (WM_XBUTTONDOWN, WM_XBUTTONUP))
            post_mouse(player, msgs[idx][0 if down_now else 1],
                       button=(idx - 2) if idx >= 3 else 0)
    if m.state & 0x400:
        if linked:
            lk.push(EV_WHEEL, m.rolling)
        elif state["forward"]:
            post_wheel(player, m.rolling)
    if m.state & 0x800:
        if linked:
            lk.push(EV_WHEEL, m.rolling, 1)  # requires rebuilt hook DLL
        elif state["forward"]:
            post_wheel(player, m.rolling, horizontal=True)
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
        dev = dll.interception_wait_with_timeout(ctx, 8)
        now = time.time()
        if counts and now - last_stat > 5:
            log.debug("eventos nos ultimos 5s: %s", {dev_name(k): v for k, v in counts.items()})
            counts.clear()
            last_stat = now
        if dev == 0:
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
    log.info("Encerrando captura e devolvendo dispositivos ao Windows")
    dll.interception_destroy_context(ctx)


# ---------- UI ----------
FONT = "Segoe UI"
BG, PANEL, BORDER = "#0a111e", "#141f31", "#2a3c55"
CARD, BTNBG, TXT, MUTED, LED_OFF = "#1c2b43", "#243653", "#f0f5ff", "#9baec7", "#43546c"
ACCENT, POSITIVE, WARNING = "#6db4ff", "#38ce9a", "#ffc375"


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


def make_overlay(root, color):
    ov = tk.Toplevel(root)
    ov.overrideredirect(True)
    ov.attributes("-topmost", True)
    ov.attributes("-transparentcolor", "#010101")
    ov.config(bg="#010101")
    cv = tk.Canvas(ov, width=24, height=24, bg="#010101", highlightthickness=0)
    cv.pack()
    cv.create_polygon(1, 1, 1, 19, 5, 15, 9, 22, 12, 21, 8, 14, 14, 14,
                      fill=color, outline="black")
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
        root.title("CoopInput Studio | v8")
        root.geometry("1260x760")
        root.minsize(1050, 650)
        root.configure(bg=BG)
        self.c = tk.Canvas(root, bg=BG, highlightthickness=0)
        self.c.pack(fill="both", expand=True)

        self.mons = monitors()
        log.info("Monitores: %s", self.mons)
        self.mon_idx = {p: len(self.mons) - 1 for p in range(2, MAXP + 1)}
        self.overlays, self.visible, self.last_pos = {}, {}, {}
        self.zones, self.zone_rect, self.leds = {}, {}, {}
        self.drag, self.nseen, self.nb, self.resize_job = None, -1, 0, None
        self.last_layout_refresh = 0
        self.saved = {}
        self.saved_profiles = {}
        self.saved_maxcodes = {}
        self.saved_cursor_hotkeys = {}
        self.hotkey_dialogs = {}
        self.applied = set()
        self.load_config()
        for p in range(2, MAXP + 1):
            self.apply_monitor(p)

        self.c.bind("<Configure>", self.on_resize)
        self.c.bind("<B1-Motion>", self.drag_move)
        self.c.bind("<ButtonRelease-1>", self.drag_end)
        self.sync_devices()
        self.refresh_cursors()

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
            cfg = {"players": state["players"], "forward": state["forward"],
                   "monitors": {str(p): i for p, i in self.mon_idx.items()},
                   "mouse_modes": {str(p): mouse_modes[p] for p in mouse_modes},
                   "cursor_modes": {str(p): cursor_display_modes[p] for p in cursor_display_modes},
                   "cursor_hotkeys": {
                       str(p): {"kind": bind["kind"], "device": keys[bind["device"]],
                                "code": bind["code"], "flags": bind["flags"]}
                       for p, bind in cursor_hotkeys.items()
                       if bind and bind["device"] in keys},
                   "assign": {keys[d]: p for d, p in list(assign.items()) if p and d in keys},
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
        state["players"] = min(max(int(cfg.get("players", 2)), 2), MAXP)
        state["forward"] = bool(cfg.get("forward", True))
        for p, i in cfg.get("monitors", {}).items():
            if p.isdigit() and int(p) in self.mon_idx and 0 <= i < len(self.mons):
                self.mon_idx[int(p)] = i
        self.saved = cfg.get("assign", {})
        self.saved_profiles = cfg.get("keyboard_profiles", {})
        self.saved_maxcodes = cfg.get("keyboard_maxcode", {})
        self.saved_cursor_hotkeys = cfg.get("cursor_hotkeys", {})
        for p, mode in cfg.get("cursor_modes", {}).items():
            if p.isdigit() and int(p) in cursor_display_modes and mode in CURSOR_DISPLAY_MODES:
                cursor_display_modes[int(p)] = mode
        for p, mode in cfg.get("mouse_modes", {}).items():
            if p.isdigit() and int(p) in mouse_modes and mode in MOUSE_MODE_BITS:
                mouse_modes[int(p)] = mode
        log.info("Config carregada: %d jogadores", state["players"])

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
            if p and 1 <= p <= state["players"]:
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
                             state='readonly', width=35)
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
        tk.Button(win, text='Save keyboard profile', command=save, bg='#1f6b46', fg=TXT,
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

    # ----- actions -----
    def apply_monitor(self, p):
        l, t, r, b = self.mons[self.mon_idx[p]]
        bounds[p][:] = [l, t, r, b]
        pos[p][:] = [(l + r) // 2, (t + b) // 2]

    def cycle_monitor(self, p):
        self.mon_idx[p] = (self.mon_idx[p] + 1) % len(self.mons)
        self.apply_monitor(p)
        log.info("Jogador %d -> tela %d", p, self.mon_idx[p] + 1)
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
        log.info("Jogadores: %d", n)
        self.save_config()
        self.refresh_cursors()
        self.redraw()

    def toggle_forward(self):
        if state["forward"]:
            for d, p in list(assign.items()):
                release_device(d, p)
        state["forward"] = not state["forward"]
        self.save_config()
        self.redraw()

    def reset(self):
        log.info("Reset: tudo para a lista de dispositivos")
        for d, old in list(assign.items()):
            release_device(d, old)
        assign.clear()
        self.save_config()
        self.refresh_cursors()
        self.redraw()

    def assign_dev(self, d, p):
        old = assign.get(d, 0)
        if old == p:
            return
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
        log.info("%s -> %s", dev_name(d), f"Jogador {p}" if p else "livre")
        self.save_config()
        self.refresh_cursors()

    def refresh_cursors(self):
        for p in range(2, MAXP + 1):
            want = p <= state["players"] and any(
                v == p and d > 10 for d, v in list(assign.items()))
            if want and p not in self.overlays:
                self.overlays[p] = make_overlay(self.root, COLORS[p])
            ov = self.overlays.get(p)
            # Only secondary virtual pointers are affected. In AUTO mode GLFW
            # captures the cursor in gameplay (ClipCursor(rect)) and releases it
            # for menus (ClipCursor(NULL)). A manual hotkey can override either.
            show = bool(want and virtual_cursor_visible(p))
            if show and not self.visible.get(p):
                ov.deiconify()
            elif not show and ov and self.visible.get(p):
                ov.withdraw()
            self.visible[p] = show
            self.last_pos.pop(p, None)

    # ----- drawing -----
    def hot(self, tag):
        self.c.tag_bind(tag, "<Enter>", lambda e: self.c.config(cursor="hand2"))
        self.c.tag_bind(tag, "<Leave>", lambda e: self.c.config(cursor=""))

    def btn(self, xr, cy, label, cb, fill=BTNBG, fg=TXT, w=None, h=32):
        w = w or len(label) * 8 + 30
        x1 = xr - w
        tag = f"b{self.nb}"
        self.nb += 1
        rrect(self.c, x1, cy - h / 2, xr, cy + h / 2, 10, fill=fill, outline="", tags=tag)
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
        c.delete("all")
        self.nb = 0
        self.zones, self.zone_rect, self.leds = {}, {}, {}
        n = state["players"]

        # clearer visual hierarchy + persistent build status at the bottom
        c.create_rectangle(0, 0, W, 88, fill='#101a2b', outline='')
        c.create_rectangle(0, 85, W, 88, fill=ACCENT, outline='')
        c.create_text(24, 29, text="COOPINPUT", anchor="w", fill=TXT,
                      font=(FONT, 20, "bold"))
        c.create_text(184, 29, text="STUDIO  /  v8", anchor="w", fill=ACCENT,
                      font=(FONT, 10, "bold"))
        c.create_text(24, 62, text="Independent devices  /  Drag keyboards and mice into the player zones",
                      anchor="w", fill=MUTED, font=(FONT, 10))
        x = W - 24
        x = self.btn(x, 34, "Reset", self.reset)
        x -= 10
        on = state["forward"]
        x = self.btn(x, 34, f"Enviar input: {'ON' if on else 'OFF'}", self.toggle_forward,
                     fill="#1f6b46" if on else BTNBG)
        x -= 22
        x = self.btn(x, 34, "+", lambda: self.set_players(n + 1), w=34)
        c.create_text(x - 18, 34, text=str(n), fill=TXT, font=(FONT, 13, "bold"))
        x -= 36
        x = self.btn(x, 34, "\u2212", lambda: self.set_players(n - 1), w=34)
        c.create_text(x - 10, 34, text="Jogadores", anchor="e", fill=MUTED, font=(FONT, 10))

        # zones
        pad, top, gap, pool_w = 16, 102, 14, 276
        zone_bottom = H - 68
        self.draw_zone(0, pad, top, pad + pool_w, zone_bottom)
        ax1, ax2 = pad + pool_w + pad, W - pad
        cols = 2
        rows = (n + cols - 1) // cols
        zw = (ax2 - ax1 - gap * (cols - 1)) / cols
        zh = (zone_bottom - top - gap * (rows - 1)) / rows
        for p in range(1, n + 1):
            r, col = divmod(p - 1, cols)
            x1 = ax1 + col * (zw + gap)
            y1 = top + r * (zh + gap)
            self.draw_zone(p, x1, y1, x1 + zw, y1 + zh)

        # Stable telemetry and actionable build status (never says v8 is active with an old DLL).
        c.create_rectangle(0, H - 52, W, H, fill='#101a2b', outline='')
        c.create_line(0, H - 52, W, H - 52, fill=BORDER)
        hook_ready = hook_binary_compatible(HOOK_PATH)
        info = 'HOOK: v8 FILE READY' if hook_ready else 'HOOK: BUILD REQUIRED'
        c.create_text(22, H - 26, text=info, anchor='w',
                      fill=POSITIVE if hook_ready else WARNING, font=(FONT, 10, 'bold'))
        c.create_text(250, H - 26,
                      text=f"Keyboard events: {stats['keys']}    Mouse moves: {stats['moves']}    Forward errors: {stats['forward_fail']}",
                      anchor='w', fill=MUTED, font=(FONT, 9))
        c.create_text(W - 20, H - 26, text='Ctrl+Alt+F12  emergency reset', anchor='e',
                      fill=ACCENT, font=(FONT, 9))

    def draw_zone(self, p, x1, y1, x2, y2):
        c = self.c
        color = COLORS[p]
        self.zones[p] = (x1, y1, x2, y2)
        self.zone_rect[p] = rrect(c, x1, y1, x2, y2, 16, fill=PANEL, outline=BORDER, width=2)
        c.create_oval(x1 + 18, y1 + 20, x1 + 30, y1 + 32, fill=color, outline="")
        if p == 0:
            title, sub = "Dispositivos", "Livres \u00b7 funcionam normal no Windows"
        elif p == 1:
            title, sub = "Jogador 1", "Principal \u00b7 cursor do Windows"
        else:
            mode_label = cursor_display_modes[p].upper()
            title, sub = f"Jogador {p}", f"Cursor {mode_label}: {'visible' if virtual_cursor_visible(p) else 'hidden'}"
        c.create_text(x1 + 40, y1 + 26, text=title, anchor="w", fill=TXT, font=(FONT, 13, "bold"))
        c.create_text(x1 + 18, y1 + 50, text=sub, anchor="w", fill=MUTED, font=(FONT, 9))
        if p >= 2:
            self.btn(x2 - 14, y1 + 26, f"Tela {self.mon_idx[p] + 1} \u203a",
                     lambda p=p: self.cycle_monitor(p), h=26, w=84)
            lk = links.get(p)
            label = "Anexar jogo" if not lk else "\u25cf " + (lk.title[:16] + ("\u2026" if len(lk.title) > 16 else ""))
            self.btn(x2 - 14, y1 + 58, label, lambda p=p: self.open_picker(p), h=26, w=170,
                     fill="#1f6b46" if lk else BTNBG)
            self.btn(x2 - 190, y1 + 58, 'MOUSE / CURSOR  \u2699',
                     lambda p=p: self.mouse_settings(p), h=26, w=145)

        ids = sorted(set(hwids) | set(seen))
        devs = [d for d in ids if assign.get(d, 0) == p]
        y0 = y1 + 72
        if not devs:
            c.create_rectangle(x1 + 14, y0, x2 - 14, y2 - 14, outline=BORDER, dash=(5, 4))
            c.create_text((x1 + x2) / 2, (y0 + y2) / 2, text="Solte dispositivos aqui",
                          fill=MUTED, font=(FONT, 10))
            return
        avail = y2 - 14 - y0
        h = max(34, min(54, (avail - (len(devs) - 1) * 8) / len(devs)))
        for i, d in enumerate(devs):
            cy1 = y0 + i * (h + 8)
            self.draw_card(d, x1 + 14, cy1, x2 - 14, cy1 + h)

    def draw_card(self, d, x1, y1, x2, y2):
        c = self.c
        tag = f"card{d}"
        cy = (y1 + y2) / 2
        rrect(c, x1, y1, x2, y2, 12, fill=CARD, outline="", tags=tag)
        is_kb = d <= 10
        rrect(c, x1 + 10, cy - 13, x1 + 38, cy + 13, 8,
              fill="#7aa2ff" if is_kb else "#ffb86b", outline="", tags=tag)
        c.create_text(x1 + 24, cy, text="KB" if is_kb else "MS", fill="#10131a",
                      font=(FONT, 9, "bold"), tags=tag)
        c.create_text(x1 + 48, cy - 8, text=dev_name(d), anchor="w", fill=TXT,
                      font=(FONT, 10, "bold"), tags=tag)
        c.create_text(x1 + 48, cy + 9, text=short_id(d), anchor="w", fill=MUTED,
                      font=(FONT, 8), tags=tag)
        if is_kb:
            profile = keyboard_profiles.get(d, 'auto')
            auto_label, _ = keyboard_layout_text(d)
            text = ('Auto ' + auto_label if profile == 'auto' else
                    PROFILE_LABELS.get(profile, 'Custom').split(' ')[0])
            prof_tag = f"keyboard_profile_{d}"
            rrect(c, x2 - 125, cy - 13, x2 - 14, cy + 13, 6, fill='#31405e', tags=prof_tag)
            c.create_text(x2 - 69, cy, text=text + ' \u25be', fill=TXT,
                          font=(FONT, 8, 'bold'), tags=prof_tag)
            c.tag_bind(prof_tag, '<ButtonPress-1>', lambda e, d=d: self.configure_keyboard(d))
        self.leds[d] = c.create_oval(x2 - (135 if is_kb else 26), cy - 5,
                                     x2 - (125 if is_kb else 16), cy + 5,
                                     fill=LED_OFF, outline='', tags=tag)
        c.tag_bind(tag, "<ButtonPress-1>", lambda e, d=d: self.drag_start(e, d))
        c.tag_bind(tag, "<Enter>", lambda e: self.c.config(cursor="fleur"))
        c.tag_bind(tag, "<Leave>", lambda e: self.c.config(cursor=""))

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
        lb = tk.Listbox(win, bg=PANEL, fg=TXT, selectbackground=COLORS[p], selectforeground="#10131a",
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
            tk.Button(row, text=text, command=cmd, bg=fill, fg=TXT, bd=0, relief="flat",
                      activebackground="#33405a", activeforeground=TXT, padx=16, pady=7,
                      font=(FONT, 10, "bold")).pack(side=side, padx=(0, 8))

        mk("Atualizar", refresh, "left")
        if p in links:
            mk("Desanexar", do_detach, "left")
        mk("Anexar", do_attach, "right", fill="#1f6b46")
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
            want_visible = has_mouse and virtual_cursor_visible(p)
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
            if lk and lk.alive():
                q = (lk.sh.pos_x, lk.sh.pos_y)
            else:
                q = (int(pos[p][0]), int(pos[p][1]))
            if self.last_pos.get(p) != q:
                self.last_pos[p] = q
                # Just the overlay moves here; game/desktop mouse input was already
                # forwarded immediately by the capture thread.
                ov.geometry(f"{q[0]:+d}{q[1]:+d}")
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
        input("Erro. Veja coopinput_debug.log. Enter para fechar...")
