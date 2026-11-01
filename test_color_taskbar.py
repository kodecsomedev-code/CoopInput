"""v12.9 regression checks: color tint, settings/persistence and taskbar routing."""
import ast
import ctypes
import pathlib
import struct
import time
from windows_cursor_art import (tint_cursor_pixels, normalize_cursor_color,
                                COLOR_PRESETS, png_rgba, NativeSystemCursorArt)
from taskbar_uia import (GUID, CLSID_UIA, IID_UIA, TaskbarUIAutomation,
                         UIA_INVOKE, UIA_SELECTION_ITEM, UIA_TOGGLE, UIA_EXPAND_COLLAPSE)
from shell_accessibility import ShellAccessibility
from desktop_interaction import DesktopDragRouter
P=pathlib.Path(__file__).parent
source=(P/'coopinput.py').read_text()
assert normalize_cursor_color('#00e5ff') == '#00E5FF'
assert normalize_cursor_color('original') is None
assert normalize_cursor_color(None) is None
for invalid in ('red', 'blue', '#abcd', '#gggggg', '', '../evil', 2):
    assert normalize_cursor_color(invalid) is None
src=bytes([0,0,0,255, 255,255,255,255, 120,120,120,160, 0,0,0,0])
assert tint_cursor_pixels(src,None)==src
colored=tint_cursor_pixels(src,'#FF0000')
assert colored[:4]==src[:4] # dark border retained
assert colored[4]==255 and colored[5]==0 and colored[6]==0
assert colored[8] > 0 and colored[9]==0 and colored[10]==0
assert colored[11]==160 and colored[-4:]==src[-4:]
assert len(COLOR_PRESETS)>=7
assert 'Windows original' in COLOR_PRESETS
renderer=NativeSystemCursorArt()
assert renderer.available is False # Linux-only unit suite
print('PASS: configurable color preserves native image transparency and dark outline')
assert str(CLSID_UIA.Data1) == str(int('FF48DBA4',16))
assert GUID.parse('30cbe57d-d9d0-452a-ab13-7ac5ac4825ee').Data1 == IID_UIA.Data1
assert {UIA_INVOKE,UIA_SELECTION_ITEM,UIA_TOGGLE,UIA_EXPAND_COLLAPSE}=={10000,10010,10015,10005}
assert not TaskbarUIAutomation().available # no Windows calls from Linux
print('PASS: native UI Automation COM contracts and safe unsupported-platform path')
assert 'self.cursor_colors' in source and '"cursor_colors"' in source
assert 'cursor_color_settings' in source and 'colorchooser.askcolor' in source
assert 'self.set_cursor_color(player' in source
assert 'pointer_tint' in source
assert 'user32.SetCursorPos(' not in source
assert 'user32.SendInput(' not in source
print('PASS: per-player saved colors and isolation in controller')

# Production accessibility action worker prefers UIA then MSAA; no app focus.
sh=ShellAccessibility()
assert not sh.available
uia=type('DemoUIA',(), {'activate':lambda self,x,y:(x,y)==(20,40)})()
sh._uia=uia
assert sh.activate(20,40)
assert not sh.activate(10,40)
print('PASS: taskbar action prefers UIA and degrades without external provider')

# Critical taskbar behavior: click invokes ONE accessible action, dragging uses
# the original isolated drag messages, never native physical mouse.
class Shell:
    def __init__(self): self.ops=[]
    def hit(self,x,y): return 21
    def root(self,h): return h
    def alive(self,h): return True
    def hit_test(self,h,x,y): return 1
    def window_rect(self,h): return (0,0,900,600)
    def move_window(self,*args): return True
    def post_client(self,h,m,f,x,y): self.ops.append(('post',m));return True
    def post_wheel(self,*args): return True
    def is_shell_target(self,h): return True
    def shell_activate_async(self,x,y,fallback):
        self.ops.append(('uia',x,y));return True
x=Shell();r=DesktopDragRouter(x)
r.button(2,0,True,50,75,1)
r.button(2,0,False,50,75,0)
assert x.ops==[('uia',50,75)], x.ops
x=Shell();r=DesktopDragRouter(x)
r.button(2,0,True,50,75,1);r.move(2,85,115,1);r.button(2,0,False,85,115,0)
assert x.ops == [('post',513),('post',512),('post',514)],x.ops
print('PASS: taskbar tap invokes accessible control; drag stays safely isolated')
print('ALL v12.9 COLOR/TASKBAR TESTS PASSED')
