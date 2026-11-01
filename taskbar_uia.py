"""Win11 taskbar activation via Microsoft's UI Automation COM API.

Pure ctypes (no pip dependencies). All COM calls MUST run off the Tk and
Interception capture threads. Never manipulates the real system pointer.
Some controls do not expose an actionable UIA pattern; caller should fall back.
"""
import ctypes
import sys
import uuid
from ctypes import wintypes


class GUID(ctypes.Structure):
    _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16),
                ('Data3', ctypes.c_uint16), ('Data4', ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


CLSID_UIA = GUID.parse('FF48DBA4-60EF-4201-AA87-54103EEF594E')
IID_UIA = GUID.parse('30CBE57D-D9D0-452A-AB13-7AC5AC4825EE')
UIA_INVOKE = 10000
UIA_SELECTION_ITEM = 10010
UIA_TOGGLE = 10015
UIA_EXPAND_COLLAPSE = 10005


class TaskbarUIAutomation:
    """Transient objects, worker-thread-only COM; no global cursor or focus calls."""

    def __init__(self):
        self.available = sys.platform == 'win32' and ctypes.sizeof(ctypes.c_void_p) == 8
        if self.available:
            self.ole = ctypes.WinDLL('ole32')
            self.ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            self.ole.CoInitializeEx.restype = ctypes.c_long
            self.ole.CoUninitialize.argtypes = []
            self.ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p,
                                                   ctypes.c_uint, ctypes.POINTER(GUID),
                                                   ctypes.POINTER(ctypes.c_void_p)]
            self.ole.CoCreateInstance.restype = ctypes.c_long

    @staticmethod
    def _method(obj, slot, *params):
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *params)(vtbl[slot])

    def _release(self, obj):
        if obj.value:
            try:
                self._method(obj, 2)(obj)
            except (OSError, ValueError):
                pass

    def activate(self, x, y):
        """Invoke, select, toggle or expand taskbar control at a SCREEN point.

        False means no control offered a suitable UIA action. This does not
        emulate right-click menus, hover animations or native drag-and-drop.
        """
        if not self.available:
            return False
        hr = self.ole.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
        if hr not in (0, 1):
            return False
        ui = ctypes.c_void_p()
        element = ctypes.c_void_p()
        try:
            hr = self.ole.CoCreateInstance(ctypes.byref(CLSID_UIA), None, 1,
                                           ctypes.byref(IID_UIA), ctypes.byref(ui))
            if hr < 0 or not ui.value:
                return False
            # IUIAutomation::ElementFromPoint, slot 7 after IUnknown + Compare...
            lookup = self._method(ui, 7, POINT, ctypes.POINTER(ctypes.c_void_p))
            if lookup(ui, POINT(int(x), int(y)), ctypes.byref(element)) < 0 or not element.value:
                return False
            # IUIAutomationElement::GetCurrentPattern, slot 16.
            get_pattern = self._method(element, 16, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
            for pattern_id in (UIA_INVOKE, UIA_SELECTION_ITEM, UIA_TOGGLE, UIA_EXPAND_COLLAPSE):
                pattern = ctypes.c_void_p()
                try:
                    if get_pattern(element, pattern_id, ctypes.byref(pattern)) < 0 or not pattern.value:
                        continue
                    # Pattern's first interface method is always slot 3.
                    action = self._method(pattern, 3)
                    if action(pattern) >= 0:
                        return True
                except (OSError, ValueError, ctypes.ArgumentError):
                    pass
                finally:
                    self._release(pattern)
            return False
        except (OSError, ValueError, ctypes.ArgumentError):
            return False
        finally:
            self._release(element)
            self._release(ui)
            self.ole.CoUninitialize()
