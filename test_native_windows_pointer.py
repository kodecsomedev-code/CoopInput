"""Offline contract checks for true Windows cursor images and Shell mouse actions."""
import struct
import zlib
from pathlib import Path
from windows_cursor_art import png_rgba, reconstruct_pixel, CURSOR_IDS, NativeSystemCursorArt
from shell_accessibility import is_shell_class, VARIANT, accessible_cursor_shape
from desktop_interaction import DesktopDragRouter

assert len(CURSOR_IDS) >= 12
assert CURSOR_IDS['arrow'] == 32512
assert CURSOR_IDS['ibeam'] == 32513
assert CURSOR_IDS['hand'] == 32649
assert CURSOR_IDS['size_we'] == 32644
assert not NativeSystemCursorArt().available  # offline Linux test only
assert struct.calcsize('>I') == 4
pix = bytes([0, 10, 255, 255, 255, 20, 0, 0])
png = png_rgba(2, 1, pix)
assert png.startswith(b'\x89PNG\r\n\x1a\n')
assert struct.unpack('>II', png[16:24]) == (2, 1)
pos = png.find(b'IDAT')
length = struct.unpack('>I', png[pos-4:pos])[0]
assert zlib.decompress(png[pos+4:pos+4+length]) == b'\0' + pix
assert reconstruct_pixel(bytes([0,0,0,0]), bytes([255,255,255,255])) == (0,0,0,0)
assert reconstruct_pixel(bytes([100,80,50,0]), bytes([100,80,50,255])) == (50,80,100,255)
assert is_shell_class('Shell_TrayWnd')
assert is_shell_class('Shell_SecondaryTrayWnd')
assert not is_shell_class('Progman')  # protect single-click selection
assert not is_shell_class('Chrome_WidgetWin_1')
assert accessible_cursor_shape(0x1E) == 'hand'
assert accessible_cursor_shape(0x2B) == 'hand'
assert accessible_cursor_shape(0x2A) == 'ibeam'
assert accessible_cursor_shape(None) is None
assert __import__('ctypes').sizeof(VARIANT) == 24
print('PASS: Windows HCURSOR scheme IDs and pure-Python PNG renderer')

class ShellApi:
    def __init__(self, succeeds=True):
        self.events=[]
        self.succeeds=succeeds
    def hit(self,x,y): return 101
    def root(self,h): return h
    def alive(self,h): return True
    def hit_test(self,h,x,y): return 1
    def window_rect(self,h): return (0,0,900,600)
    def move_window(self,*args): return True
    def post_client(self,h,msg,flags,x,y): self.events.append(('post',msg,x,y)); return True
    def post_wheel(self,*args): return True
    def is_shell_target(self,h): return True
    def shell_activate(self,x,y): self.events.append(('shell',x,y)); return self.succeeds
    def note_interaction(self,p,h): self.events.append(('focus',p,h))

# Simple click: accessibility activation with no duplicate posted down/up.
api=ShellApi(True)
r=DesktopDragRouter(api)
r.button(2,0,True,100,150,1)
assert api.events == [('focus',2,101)]
r.button(2,0,False,100,150,0)
assert ('shell',100,150) in api.events
assert not any(e[0]=='post' for e in api.events)
print('PASS: taskbar accessible click without duplicated mouse messages')

# Production Win32 backend dispatches to an asynchronous COM worker; it must
# not ever wait for another process inside the Interception capture thread.
class AsyncShellApi(ShellApi):
    def shell_activate_async(self, x, y, fallback):
        self.events.append(('queued', x, y))
        self._fallback = fallback
        return True
api=AsyncShellApi(True)
r=DesktopDragRouter(api)
r.button(2,0,True,90,120,1)
r.button(2,0,False,90,120,0)
assert ('queued',90,120) in api.events
assert not any(event[0]=='post' for event in api.events)
api._fallback()
assert [e[1] for e in api.events if e[0]=='post'] == [0x0201,0x0202]
print('PASS: taskbar accessibility dispatch is async and fallback remains balanced')

# No accessible provider: complete traditional down/up fallback.
api=ShellApi(False)
r=DesktopDragRouter(api)
r.button(2,0,True,100,150,1)
r.button(2,0,False,100,150,0)
assert [e[1] for e in api.events if e[0]=='post'] == [0x0201,0x0202]
print('PASS: Shell fallback keeps click balanced')

# Shell dragging: replay button-down at original position and keep capture.
api=ShellApi(True)
r=DesktopDragRouter(api)
r.button(2,0,True,100,150,1)
r.move(2,130,180,1)
r.button(2,0,False,130,180,0)
assert ('shell',130,180) not in api.events
assert [e[1] for e in api.events if e[0]=='post'] == [0x0201,0x0200,0x0202]
assert [e[2:4] for e in api.events if e[0]=='post'][0] == (100,150)
assert not r.active
print('PASS: Shell drag holds original HWND and does not invoke item')

# No OS cursor movement/hiding APIs added.
source=(Path(__file__).parent/'coopinput.py').read_text()
for forbidden in ('user32.SetCursorPos(', 'user32.ShowCursor(', 'user32.SetCursor(', 'user32.SendInput('):
    assert forbidden not in source, forbidden
assert 'paint_system_cursor' in source and 'desktop_drag' in source
assert 'focus_on_click' in (Path(__file__).parent/'desktop_interaction.py').read_text()
print('PASS: primary mouse isolation and opt-in focus')
print('ALL NATIVE POINTER TESTS PASSED')
