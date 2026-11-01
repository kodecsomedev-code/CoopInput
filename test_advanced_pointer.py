"""Cross-platform automated checks: no Windows or interception DLL required."""
import ast
import pathlib
from desktop_interaction import (
    DesktopDragRouter, validated_features, PROFILES,
    WM_MOUSEMOVE, WM_MOUSEHOVER, WM_MOUSELEAVE,
    WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK,
    WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MBUTTONDOWN, WM_MBUTTONUP)

class Clock:
    t = 10.0
    def __call__(self): return self.t
    def advance(self, seconds): self.t += seconds

class API:
    def __init__(self):
        self.events=[]; self.alive_hwnds={10,20}; self.hit_code=1
        self.rect={10:(0,0,400,300),20:(400,0,800,300)}
    def hit(self,x,y): return 10 if x<400 else 20
    def root(self,h): return h
    def alive(self,h): return h in self.alive_hwnds
    def hit_test(self,h,x,y): return self.hit_code
    def window_rect(self,h): return self.rect.get(h)
    def post_client(self,h,msg,wp,x,y): self.events.append(('msg',h,msg,wp,x,y)); return True
    def post_wheel(self,h,x,y,delta,horizontal,wp): self.events.append(('wheel',h,delta,horizontal,wp));return True
    def move_window(self,h,l,t,w,hg): self.events.append(('rect',h,l,t,w,hg));return True

def fresh():
    api=API();clk=Clock();r=DesktopDragRouter(api,clock=clk)
    return api,clk,r

def click(r,player,x=100,y=100,index=0,flags=0):
    r.button(player,index,True,x,y,flags)
    r.button(player,index,False,x,y,flags)

api,c,r=fresh()
for count in range(4):
    click(r,2)
    c.advance(.13)
msgs=[e[2] for e in api.events]
assert msgs==[WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK,WM_LBUTTONUP,
              WM_LBUTTONDOWN,WM_LBUTTONUP,WM_LBUTTONDBLCLK,WM_LBUTTONUP],msgs
assert all(e[1]==10 for e in api.events)
print('PASS: single/double/triple/quad click sequences target same virtual HWND')

api,c,r=fresh()
r.button(2,1,True,100,100,2)
r.move(2,525,105,2)
r.button(2,0,True,525,105,3)
r.move(2,560,105,3)
r.button(2,0,False,560,105,2)
assert 2 in r.active, 'right button still captures even after left is released'
r.button(2,1,False,560,105,0)
assert 2 not in r.active and all(e[1]==10 for e in api.events)
assert WM_RBUTTONDOWN in [e[2] for e in api.events] and WM_RBUTTONUP in [e[2] for e in api.events]
print('PASS: right-drag and left+right chord retain initial target until last release')

api,c,r=fresh()
r.button(2,0,True,100,100,1)
r.button(3,0,True,500,100,1)
r.move(2,500,160,1)
r.move(3,90,160,1)
assert api.events[-2][1:3]==(10,WM_MOUSEMOVE) and api.events[-1][1:3]==(20,WM_MOUSEMOVE)
r.cancel(2)
assert 2 not in r.active and 3 in r.active
r.cancel_all()
print('PASS: independent Player 2+ capture and panic cleanup')

api,c,r=fresh()
r.configure(2,hover=True,hover_ms=300,dwell=True,dwell_ms=800)
r.move(2,50,40,0)
c.advance(.36);r.tick(2,50,40,0)
assert api.events[-1][2]==WM_MOUSEHOVER
c.advance(.5);r.tick(2,50,40,0)
assert [e[2] for e in api.events][-2:]==[WM_LBUTTONDOWN,WM_LBUTTONUP]
count=len(api.events)
c.advance(2);r.tick(2,50,40,0)
assert len(api.events)==count, 'dwell must be one-shot until pointer moves'
r.move(2,505,40,0)
assert any(e[2]==WM_MOUSELEAVE for e in api.events)
print('PASS: hover, leave, dwell once per stationary position')

api,c,r=fresh()
r.configure(2,clicklock=True,clicklock_ms=500)
r.button(2,0,True,50,50,1)
c.advance(.6)
r.button(2,0,False,50,50,0)
assert 2 in r.locked and 2 in r.active
r.move(2,500,50,1)
r.button(2,0,True,500,50,1)
r.button(2,0,False,500,50,0)
assert 2 not in r.active and 2 not in r.locked
assert [e[2] for e in api.events].count(WM_LBUTTONUP)==1
print('PASS: ClickLock releases on next press, no stuck drag capture')

api,c,r=fresh()
r.configure(2,auto_scroll=True)
click(r,2,70,70,index=2)
assert 2 in r.autoscroll
c.advance(.1)
r.tick(2,80,180,0)
assert any(e[0]=='wheel' for e in api.events)
click(r,2,80,180,index=2)
assert 2 not in r.autoscroll
print('PASS: optional middle-button pan starts/stops and generates scroll ticks')

api,c,r=fresh()
r.wheel(2,90,100,120,False,4)
assert api.events[-1]==('wheel',10,120,True,4)
r.wheel(2,90,100,-120,False,8)
assert api.events[-1]==('wheel',10,-120,False,8)
assert validated_features({'sensitivity':.001,'profile':'fast','profile_button':4})['sensitivity']==.2
assert validated_features({'sensitivity':999})['sensitivity']==4.
assert validated_features({'profile':'no','profile_button':1})['profile']=='normal'
assert PROFILES['precision'] < PROFILES['normal'] < PROFILES['fast']
print('PASS: Shift-scroll, Ctrl-scroll flags, and validated sensitivity profiles')

# Don't add global cursor APIs or reroute Player 2 to Windows' physical input.
src=pathlib.Path(__file__).with_name('desktop_interaction.py').read_text(encoding='utf-8')
tree=ast.parse(src)
calls={n.func.id for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
assert not {'SetCursorPos','SendInput','ShowCursor','SetCapture','ClipCursor','interception_send'} & calls
controller=pathlib.Path(__file__).with_name('coopinput.py').read_text(encoding='utf-8')
assert 'if player < 2:' in controller
assert 'profile_button' in controller
assert 'Pointer actions' in controller
assert 'desktop_drag.tick(player, x, y, mouse_flags(player))' in controller
print('PASS: no Windows primary cursor controls, feature UI connected to capture loop')
print('ALL ADVANCED POINTER TESTS PASSED')
