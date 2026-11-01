"""Best-effort Microsoft Active Accessibility activation on Windows Shell surfaces.

Player 2's pointer NEVER moves the real system mouse. This attempts accessible
Invoke/default-action for taskbar and desktop icons (especially modern Windows
UI, which often ignores posted WM_LBUTTON messages). Failure is non-fatal.

This cannot grant independent focus, interact with secure desktops, or replace
native pointer capture/OLE drag-and-drop in arbitrary applications.
"""
import ctypes
import sys
import threading
import queue
import time
import logging
from taskbar_uia import TaskbarUIAutomation
from ctypes import wintypes

# Do not invoke Desktop icon default actions on a single click: that can OPEN
# files rather than merely selecting them. Desktop icons use pointer messages.
SHELL_CLASSES = frozenset({'Shell_TrayWnd', 'Shell_SecondaryTrayWnd'})


def is_shell_class(class_name):
    return (class_name or '').lower() in {s.lower() for s in SHELL_CLASSES}


class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


class VARIANT(ctypes.Structure):
    """24-byte x64 Windows VARIANT with integer VT_I4 child ID."""
    _fields_ = [('vt', ctypes.c_ushort), ('r1', ctypes.c_ushort),
                ('r2', ctypes.c_ushort), ('r3', ctypes.c_ushort),
                ('value', ctypes.c_longlong), ('reserved', ctypes.c_longlong)]


def accessible_cursor_shape(role):
    # Windows oleacc.h role values: link, pushbutton, text/edit, check/radio.
    if role in (0x1E, 0x2B, 0x2C, 0x2D, 0x25):
        return 'hand'
    if role in (0x2A, 0x2E):
        return 'ibeam'
    return None


class ShellAccessibility:
    def __init__(self):
        self.available = sys.platform == 'win32' and ctypes.sizeof(ctypes.c_void_p) == 8
        self.local = threading.local()
        self._uia = None
        self._logger = logging.getLogger("coopinput.shell")
        self._role_jobs = queue.Queue(maxsize=1)
        self._role_latest = None
        self._role_thread = None
        self._action_jobs = queue.Queue(maxsize=24)
        self._action_thread = None
        if not self.available:
            return
        self.acc = ctypes.windll.oleacc
        self.ole = ctypes.windll.ole32
        self.acc.AccessibleObjectFromPoint.argtypes = [POINT, ctypes.POINTER(ctypes.c_void_p),
                                                       ctypes.POINTER(VARIANT)]
        self.acc.AccessibleObjectFromPoint.restype = ctypes.c_long
        self.ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.ole.CoInitializeEx.restype = ctypes.c_long
        self.oleaut = ctypes.windll.oleaut32
        self.oleaut.VariantClear.argtypes = [ctypes.POINTER(VARIANT)]

    def _com_ready(self):
        if not getattr(self.local, 'ready', False):
            # COINIT_MULTITHREADED for the capture worker; if another component
            # already chose STA, COM is initialized and MSAA can still run.
            result = self.ole.CoInitializeEx(None, 0)
            if result not in (0, 1, -2147417850):  # S_OK, S_FALSE, RPC_E_CHANGED_MODE
                return False
            self.local.ready = True
        return True

    def _method(self, obj, slot, restype, *argtypes):
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        fn_addr = table[slot]
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(fn_addr)


    def _accessible_object(self, x, y):
        acc = ctypes.c_void_p()
        child = VARIANT()
        hr = self.acc.AccessibleObjectFromPoint(POINT(int(x), int(y)), ctypes.byref(acc), ctypes.byref(child))
        return acc, child, hr

    def _release_object(self, obj):
        if obj.value:
            try:
                self._method(obj, 2, ctypes.c_ulong)(obj)
            except Exception:
                pass

    def _role_at(self, x, y):
        if not self.available or not self._com_ready():
            return None
        obj = ctypes.c_void_p()
        role = VARIANT()
        try:
            obj, child, hr = self._accessible_object(x, y)
            if hr < 0 or not obj.value:
                return None
            # get_accRole is IAccessible vtbl slot 13. No mouse mutation.
            get_role = self._method(obj, 13, ctypes.c_long, VARIANT, ctypes.POINTER(VARIANT))
            if get_role(obj, child, ctypes.byref(role)) >= 0 and role.vt == 3:
                return int(role.value & 0xFFFFFFFF)
            return None
        except (OSError, ValueError, TypeError, ctypes.ArgumentError):
            return None
        finally:
            if role.vt:
                self.oleaut.VariantClear(ctypes.byref(role))
            self._release_object(obj)

    def _role_worker(self):
        while True:
            x, y = self._role_jobs.get()
            role = self._role_at(x, y)
            self._role_latest = (x, y, time.monotonic(), role)

    def role_hint(self, x, y):
        """Nonblocking MSAA role hint, including accessible HTML links.

        A hung or slow accessibility provider cannot block the input or Tk
        threads. If the app has no accessibility tree, arrow is used instead.
        """
        if not self.available:
            return None
        if self._role_thread is None:
            self._role_thread = threading.Thread(target=self._role_worker, daemon=True,
                                                 name='accessible-pointer-shape')
            self._role_thread.start()
        last = self._role_latest
        if last and abs(x-last[0]) <= 3 and abs(y-last[1]) <= 3 and time.monotonic()-last[2] < .50:
            return last[3]
        if self._role_jobs.empty():
            try:
                self._role_jobs.put_nowait((x, y))
            except queue.Full:
                pass
        return None

    def _action_worker(self):
        while True:
            x, y, queued_at, fallback = self._action_jobs.get()
            # A stale click must not unexpectedly activate the taskbar later.
            if time.monotonic() - queued_at > 2.0:
                self._logger.warning('Taskbar action expired; skipping stale click at %s,%s', x, y)
                continue
            result = self.activate(x, y)
            self._logger.info('Taskbar virtual click at (%d,%d): %s', x, y,
                              'accessibility action sent' if result else 'no supported action; trying client messages')
            if not result:
                try:
                    fallback()
                except Exception as exc:
                    self._logger.debug('Taskbar click fallback error: %s', exc)

    def activate_async(self, x, y, fallback):
        """Taskbar click: never block the mouse capture thread on Shell COM."""
        if not self.available:
            return False
        if self._action_thread is None:
            self._action_thread = threading.Thread(target=self._action_worker, daemon=True,
                                                    name='accessible-taskbar-action')
            self._action_thread.start()
        try:
            self._action_jobs.put_nowait((x, y, time.monotonic(), fallback))
            return True
        except queue.Full:
            return False

    def activate(self, x, y):
        # Modern Windows 11 taskbar buttons are often XAML/WinUI elements;
        # posted WM_LBUTTONDOWN and legacy MSAA default actions are insufficient.
        # Use the UIA patterns first; do NOT call SendInput or alter P1 cursor.
        if self._uia is None:
            self._uia = TaskbarUIAutomation()
        if self._uia.activate(x, y):
            return True
        if not self.available or not self._com_ready():
            return False
        obj = ctypes.c_void_p()
        child = VARIANT()
        try:
            hr = self.acc.AccessibleObjectFromPoint(POINT(int(x), int(y)),
                                                    ctypes.byref(obj), ctypes.byref(child))
            if hr < 0 or not obj.value:
                return False
            # IAccessible inherits IDispatch: accDoDefaultAction is vtbl slot 25.
            do_action = self._method(obj, 25, ctypes.c_long, VARIANT)
            return do_action(obj, child) >= 0
        except (OSError, ValueError, TypeError, ctypes.ArgumentError):
            return False
        finally:
            self._release_object(obj)
