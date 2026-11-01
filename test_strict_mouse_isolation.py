"""Executable, Windows-free tests for the v12.4 mouse isolation regression.
    No Interception DLL or GUI is initialized.
"""
import ast, pathlib, types
ROOT=pathlib.Path(__file__).resolve().parent
SRC=(ROOT/'coopinput.py').read_text(encoding='utf-8')
TREE=ast.parse(SRC)
FUNCTIONS={n.name:n for n in TREE.body if isinstance(n,ast.FunctionDef)}

def build(name, env):
    module=ast.Module(body=[FUNCTIONS[name]],type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module,'coopinput.py','exec'),env)
    return env[name]

class Mouse:
    def __init__(self,x=11,y=-6,buttons=0,wheel=0):
        self.flags=0
        self.x=x
        self.y=y
        self.state=buttons
        self.rolling=wheel

class Link:
    def __init__(self):
        self.events=[]
        self.sh=types.SimpleNamespace(pad=[0,0x0300],pos_x=450,pos_y=320)
    def alive(self):return True
    def push(self,*a):self.events.append(a)

class Logger:
    def info(self,*args):pass

def env_for(enabled,forward=True):
    clicks=[]; moves=[]; wheels=[]
    link=Link()
    env={
        'desktop_control':{2:enabled,3:False},
        'pos':{2:[300,280]},'bounds':{2:[0,0,1920,1080]},
        'links':{2:link},'state':{'forward':forward},
        'held_mouse':{},'mouse_owners':{2:{}},
        'mouse_features':{2:{'sensitivity':1.0,'profile':'normal','profile_button':0}},'mouse_subpixel':{2:[0.,0.]},
        'PROFILES':{'normal':1.0,'precision':0.5,'fast':1.8},'profile_key_latches':set(),
        'stats':{'moves':0},'BUTTON_EVENTS':((0x001,0,True),(0x002,0,False)),
        'EV_MOVE':1,'EV_BTN':2,'EV_WHEEL':3,'WM_MOUSEMOVE':0x200,
        'WM_XBUTTONDOWN':0x20b,'WM_XBUTTONUP':0x20c,
        'cursor_hotkey_event':lambda *a:False,
        'post_mouse':lambda *args,**kw: (moves.append(args) if args[1]==0x200 else clicks.append(args)),
        'post_wheel':lambda *args,**kw:wheels.append((args,kw)),
        'user32':None,
        'desktop_mouse_move':lambda p:moves.append((p,0x200)),
        'desktop_mouse_button':lambda p,i,down:clicks.append((p,i,down)),
        'desktop_mouse_wheel':lambda p,roll,horizontal=False:wheels.append((p,roll,horizontal)),
    }
    return env,link,moves,clicks,wheels

# Player 2 physical events are ALWAYS consumed (never sent via interception_send),
# including when the Windows keyboard shortcut mode is enabled or forwarding is off.
for enabled in (False,True):
    for forward in (False,True):
        env,link,moves,clicks,wheels=env_for(enabled,forward)
        fn=build('on_mouse',env)
        assert fn(Mouse(x=10,y=14,buttons=0x001|0x400,wheel=120),2,12) is True
        assert env['pos'][2]==[310,294]
        assert fn(Mouse(x=0,y=0,buttons=0x002),2,12) is True
        assert fn(Mouse(),1,11) is False
        if enabled and forward:
            assert not link.events, 'desktop mode may not feed linked game'
            assert moves and clicks and wheels, 'secondary desktop cursor posts events'
        if not enabled and forward:
            assert link.events and not moves and not clicks and not wheels, 'v8 game hook unaffected'
        if not forward:
            assert not link.events and not moves and not clicks and not wheels
print('PASS: P2 mouse physically isolated in all Win Keys and forwarding combinations')

# Visibility must not follow GLFW's cursor-grab state when the user requests
# keyboard-only Windows access; no global cursor APIs are used.
for enabled in (False,True):
    env,link,*_=env_for(enabled)
    env.update(cursor_manual_overrides={2:None},cursor_display_modes={2:'auto'},
               HOOK_CURSOR_HINT_VALID=0x100,HOOK_CURSOR_HINT_GRABBED=0x200)
    f=build('virtual_cursor_visible',env)
    assert f(2) is enabled
    env['links'][2]=None
    assert f(2) is True
print('PASS: Win Keys ON shows P2 virtual overlay regardless of attached-game capture')

# P2 keyboard-only mode may not release held mouse buttons or reassign devices.
released=[];notices=[]
env={'desktop_control':{2:False},'assign':{3:2,12:2,13:1},
     'release_device':lambda d,p:released.append(d),
     'cursor_ui_events':types.SimpleNamespace(put=lambda x:notices.append(x)),
     'desktop_drag':types.SimpleNamespace(cancel=lambda *a:None),'pos':{2:[120,200]},
     'log':Logger()}
f=build('set_desktop_mode',env)
assert f(2,True) and released==[3] and env['assign']=={3:2,12:2,13:1}
assert f(2,False) and released==[3,3]
print('PASS: Windows keyboard mode changes never touch mouse ownership')

# Cursor-overlay positioning must switch to P2's actual virtual coordinates,
# not the stale game-hook shared cursor coordinates, while keyboard shortcuts
# are enabled. Tick is UI-specific, so assert this branch in its real AST.
app=next(x for x in TREE.body if isinstance(x,ast.ClassDef) and x.name=='App')
tick=next(x for x in app.body if isinstance(x,ast.FunctionDef) and x.name=='tick')
assert 'not desktop_control.get(p, False)' in ast.get_source_segment(SRC,tick)
assert "'Win Keys: ON'" in SRC and "'Win Keys: OFF'" in SRC
print('PASS: GUI routes P2 virtual cursor coordinates and identifies Windows keys clearly')
print('ALL STRICT MOUSE ISOLATION TESTS PASSED')
