"""Windows-free integration tests for independent Player 2 desktop drag behavior."""
import ast
import pathlib
import types
from desktop_interaction import DesktopDragRouter, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE, HTCAPTION

class API:
    def __init__(self):
        self.events=[]
        self.rects={10:(0,0,400,300),20:(400,0,800,300)}
        self.active={10,20}
        self.hit_result=1
    def hit(self,x,y): return 10 if x<400 else 20
    def root(self,h): return h
    def alive(self,h): return h in self.active
    def hit_test(self,h,x,y):return self.hit_result
    def window_rect(self,h):return self.rects[h]
    def post_client(self,h,msg,flags,x,y):
        self.events.append(('post',h,msg,flags,x,y));return True
    def post_wheel(self,h,x,y,rolling,horizontal,flags):
        self.events.append(('wheel',h,rolling,horizontal));return True
    def move_window(self,h,l,t,w,ht):
        self.rects[h]=(l,t,l+w,t+ht)
        self.events.append(('geometry',h,l,t,w,ht));return True

api=API();route=DesktopDragRouter(api)
# Hold left on window A, move onto window B, release: A still receives full sequence.
assert route.button(2,0,True,120,90,1)
assert route.move(2,500,100,1)
assert route.button(2,0,False,500,100,0)
assert [e[1] for e in api.events if e[0]=='post']==[10,10,10]
assert [e[2] for e in api.events if e[0]=='post']==[WM_LBUTTONDOWN,WM_MOUSEMOVE,WM_LBUTTONUP]
assert 2 not in route.active
print('PASS: client-area drag and text-selection keep original HWND until mouse-up')
# Different players capture independent HWNDs.
api.events.clear()
route.button(2,0,True,120,90,1)
route.button(3,0,True,490,90,1)
route.move(2,470,80,1)
route.move(3,140,80,1)
assert [x[1] for x in api.events[-2:]]==[10,20]
route.cancel(2)
assert 3 in route.active and 2 not in route.active
route.cancel_all()
print('PASS: player captures do not mix; cancel releases held client button')
# Window title bar drag is manual and never moves OS cursor/focus.
api.events.clear();api.hit_result=HTCAPTION
route.button(2,0,True,100,25,1)
route.move(2,125,60,1)
route.button(2,0,False,125,60,0)
assert api.rects[10]==(25,35,425,335)
assert not any(e[0]=='post' for e in api.events)
print('PASS: caption dragging moves ONLY the target window without global cursor API')
# Resize bottom right + restrict minimum sizes.
api.hit_result=17
route.button(2,0,True,420,335,1)
route.move(2,600,500,1)
route.button(2,0,False,600,500,0)
assert api.rects[20]==(400,0,980,465)
api.hit_result=10
route.button(2,0,True,410,50,1)
route.move(2,2000,50,1)
assert api.rects[20][2]-api.rects[20][0]>=180
route.button(2,0,False,2000,50,0)
print('PASS: titlebar movement and edge/corner resizing with minimum bounds')
# Wheels and mouse buttons still route to hovered or captured window.
api.hit_result=1;api.events.clear()
route.button(2,0,True,50,50,1)
route.wheel(2,500,90,120,False,1)
route.button(2,1,True,500,90,3)
assert api.events[-2]==('wheel',10,120,False)
assert api.events[-1][1]==10
route.button(2,0,False,500,90,0)
route.button(2,1,False,500,90,0)  # right capture now persists until right-up
route.wheel(2,500,90,-120,True,0)
assert api.events[-1]==('wheel',20,-120,True)
print('PASS: scroll and other buttons stay in Player 2 capture')
# Closing/destroying a captured HWND automatically ends capture.
route.button(2,0,True,50,50,1)
api.active.remove(10)
route.move(2,500,70,1)
assert not route.active
print('PASS: destroyed window cancels capture without stale HWND use')

source=pathlib.Path(__file__).with_name('coopinput.py').read_text(encoding='utf-8')
tree=ast.parse(source)
fn={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
assert 'desktop_mouse_move(player)' in ast.get_source_segment(source,fn['on_mouse'])
assert 'desktop_mouse_button(player, idx, down_now)' in ast.get_source_segment(source,fn['on_mouse'])
assert 'desktop_mouse_wheel(player, m.rolling)' in ast.get_source_segment(source,fn['on_mouse'])
assert 'linked = bool(state["forward"] and not desktop_control.get(player, False)' in ast.get_source_segment(source,fn['on_mouse'])
# No mouse handoff to Windows; no global cursor manipulation in new engine.
virtual=ast.parse(pathlib.Path(__file__).with_name('desktop_interaction.py').read_text(encoding='utf-8'))
all_calls={n.func.id for n in ast.walk(virtual) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
for bad in ('SendInput', 'SetCursorPos', 'ClipCursor', 'SetCapture', 'ShowCursor', 'interception_send'):
    assert bad not in all_calls
print('PASS: desktop interactions remain isolated and game branch preserved')
