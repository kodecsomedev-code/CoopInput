"""Windows-free AST execution of input extensions, no GUI or driver."""
import ast
import pathlib
import types
from desktop_interaction import PROFILES

SRC=pathlib.Path(__file__).with_name('coopinput.py').read_text(encoding='utf-8')
TREE=ast.parse(SRC)
NODES={n.name:n for n in TREE.body if isinstance(n,ast.FunctionDef)}

def extract(name,env):
    m=ast.Module(body=[NODES[name]],type_ignores=[])
    ast.fix_missing_locations(m)
    exec(compile(m,'coopinput.py','exec'),env)
    return env[name]

class Mouse:
    def __init__(self,state=0,x=0,y=0):
        self.state=state;self.x=x;self.y=y;self.flags=0;self.rolling=0

class Link:
    def __init__(self): self.events=[]
    def alive(self):return True
    def push(self,*e):self.events.append(e)

class Log:
    def info(self,*x):pass

def env_builder(linked):
    lk=Link() if linked else None
    events=[]
    env={
        'links':{2:lk},'desktop_control':{2:False},'state':{'forward':True},
        'pos':{2:[400,400]},'bounds':{2:[0,0,1920,1080]},
        'mouse_features':{2:{'profile':'fast','profile_button':0,'sensitivity':.5}},
        'mouse_subpixel':{2:[.6,.4]},'PROFILES':PROFILES,
        'BUTTON_EVENTS':((1,0,True),(2,0,False),(64,3,True),(128,3,False)),
        'held_mouse':{},'mouse_owners':{2:{}},'profile_key_latches':set(),
        'stats':{'moves':0},'cursor_hotkey_event':lambda *v:False,
        'cursor_ui_events':types.SimpleNamespace(put=lambda e:events.append(('status',e))),
        'EV_MOVE':1,'EV_BTN':2,'EV_WHEEL':3,
        'desktop_mouse_move':lambda p:events.append(('desktop_move',tuple(env['pos'][p]))),
        'desktop_mouse_button':lambda *v:events.append(('desktop_button',v)),
        'desktop_mouse_wheel':lambda *v,**kw:events.append(('desktop_wheel',v,kw)),
    }
    return extract('on_mouse',env), env, lk, events

fn,env,link,events=env_builder(True)
fn(Mouse(x=-1,y=1),2,11)
assert link.events[0]==(1,-1,1),link.events
assert env['mouse_subpixel'][2]==[0.,0.]
print('PASS: v8 game hook receives untouched raw mouse delta independent of desktop profile')
fn,env,link,events=env_builder(False)
fn(Mouse(x=2,y=0),2,11)
# fast 1.8 * .5 = .9, plus .6 remainder => 2 pixels
assert events[-1]==('desktop_move',(402,400)),events
assert env['mouse_subpixel'][2][0]>0
fn(Mouse(state=1,x=0,y=0),2,11)
assert events[-1][0]=='desktop_button'
print('PASS: desktop sensitivity has fractional precision; normal click unaffected by no profile shortcut')
fn,env,link,events=env_builder(False)
env['mouse_features'][2]['profile_button']=3
fn(Mouse(state=64),2,11)
assert env['mouse_features'][2]['profile']=='precision'
fn(Mouse(state=64),2,11)
assert env['mouse_features'][2]['profile']=='precision'  # no bounce double cycle
fn(Mouse(state=128),2,11)
fn(Mouse(state=64),2,11)
assert env['mouse_features'][2]['profile']=='normal'
assert not any(e[0]=='desktop_button' for e in events)
print('PASS: opt-in X1 profile switch cycles once per physical press and consumes only its own button')

env={'mouse_owners':{2:{}},'key_state':{2:bytearray(256)},
     'physical_modifier_keys':{2:{(3,0xA0),(3,0xA2)}}}
flags=extract('mouse_flags',env)
assert flags(2)&(4|8)==(4|8)
env['physical_modifier_keys'][2].clear()
assert flags(2)==0
print('PASS: Player 2 Ctrl/Shift modifiers appear in virtual message state')

# MouseKeys never routes keys to Windows' system mouse.
positions={2:[300,400]}; bounds={2:[0,0,1000,1000]}
events=[]
env={'pos':positions,'bounds':bounds,'mouse_features':{2:{'sensitivity':1}},
     'desktop_mouse_move':lambda p:events.append(('move',p)),
     'desktop_mouse_button':lambda *a:events.append(('button',*a)),
     'mouse_key_latches':{2:set()},
     'NUMPAD_MOUSE_MOVES':{0x48:(0,-1),0x50:(0,1)}}
handler=extract('handle_mouse_keys',env)
assert handler(2,0x48,0,False) and positions[2]==[300,388]
assert handler(2,0x4C,0,False)
assert handler(2,0x4C,0,False) and events.count(('button',2,0,True))==1
assert handler(2,0x4C,0,True) and ('button',2,0,False) in events
assert not handler(2,0x48,2,False)
print('PASS: NumPad virtual mouse movement, click, repeat control and E0 rejection')
print('ALL INPUT EXTENSION TESTS PASSED')
