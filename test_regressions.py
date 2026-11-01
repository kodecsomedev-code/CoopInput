"""Offline Windows-independent regression tests using only actual v12 AST functions.
No real physical device or injection DLL loaded on this machine.
"""
import ast, pathlib, queue, types, hashlib, zipfile
HERE=pathlib.Path(__file__).resolve().parent
SRC=HERE/'coopinput.py'
V8=HERE/'v8_reference.py'
s=SRC.read_text(encoding='utf-8'); tree=ast.parse(s)
functions={n.name:n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
app_cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='App')
appfunc={n.name:n for n in app_cls.body if isinstance(n,ast.FunctionDef)}

def extract(name,cls=False,env=None):
 node=(appfunc if cls else functions)[name]
 sub=ast.Module(body=[node],type_ignores=[])
 ast.fix_missing_locations(sub)
 local={} if env is None else env
 exec(compile(sub,'coopinput.py','exec'),local)
 return local[name]

# Startup-order regression: evaluate real top-level definitions in source order.
# This caught the MAXP NameError that py_compile and isolated method tests missed.
initializers = []
for node in tree.body:
    if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in
            {'MAXP', 'desktop_control', 'desktop_toggle_scancodes'}
            for target in node.targets):
        initializers.append(node)
boot = ast.Module(body=initializers, type_ignores=[])
ast.fix_missing_locations(boot)
boot_state = {}
exec(compile(boot, str(SRC), 'exec'), boot_state)
assert boot_state['MAXP'] == 4
assert boot_state['desktop_control'] == {2: False, 3: False, 4: False}
assert boot_state['desktop_toggle_scancodes'] == {2: 0x44, 3: 0x44, 4: 0x44}
print('PASS: real startup initializers run in source order (no MAXP NameError)')

# Public GitHub source does not contain proprietary external DLLs.
# Check ABI from original hook C source; optionally test a locally compiled DLL.
hook_src = (HERE/'coopinput_hook.c').read_text(encoding='utf-8')
assert 'COOPINPUT-HOOK-ABI-8' in hook_src
assert (HERE/'coopinput_hook.c').stat().st_size > 10000
if (HERE/'coopinput_hook.dll').exists():
    assert b"COOPINPUT-HOOK-ABI-8" in (HERE/'coopinput_hook.dll').read_bytes()
v8=ast.parse(V8.read_text(encoding='utf-8'))
v8f={n.name:n for n in v8.body if isinstance(n,ast.FunctionDef)}
mouse_src=ast.get_source_segment(s,functions['on_mouse'])
assert 'lk.push(EV_MOVE, dx, dy)' in mouse_src and 'lk.push(EV_BTN, idx, int(down_now))' in mouse_src
assert 'desktop_mouse_button(player, idx, down_now)' in mouse_src
# The v8 keyboard logic remains unchanged AFTER the new routing guard.
assert [ast.dump(n,include_attributes=False) for n in (functions['on_key'].body[:9]+functions['on_key'].body[12:])]==[ast.dump(n,include_attributes=False) for n in v8f['on_key'].body],'on_key v8 keyboard tail modified beyond safety guards'
print('PASS: v8 mouse events, keyboard tail and hook ABI source preserved')

# Monitor changes should never mutate device-to-player mapping, even during desktop mode.
class Link:
    def __init__(self): self.sh=types.SimpleNamespace(pos_x=0,pos_y=0)
    def alive(self): return True
class FakeUI:
    mon_idx={2:0}
    mons=[(0,0,1920,1080),(1920,0,3840,1080)]
    last_pos={}
    def apply_monitor(self,p): bounds[p][:]=list(self.mons[self.mon_idx[p]]); pos[p][:]=[(bounds[p][0]+bounds[p][2])//2,(bounds[p][1]+bounds[p][3])//2]
    def refresh_cursors(self): pass
    def save_config(self): pass
    def redraw(self): pass
assign={1:1,2:2,11:1,12:2}
owner_before=dict(assign)
pos={2:[1000,500]};bounds={2:[0,0,1920,1080]}
desktop_control={2:True}
requested=[]
link=Link()
ns={'bounds':bounds,'pos':pos,'desktop_control':desktop_control,'request_desktop_mode':lambda p,v: requested.append((p,v)),
    'links':{2:link},'log':types.SimpleNamespace(info=lambda *a:None),
    'desktop_drag':types.SimpleNamespace(cancel=lambda *a:None)}
fn=extract('cycle_monitor',True,ns)
ui=FakeUI()
fn(ui,2)
assert assign==owner_before
assert requested==[(2,False)]
assert (1920<=pos[2][0]<3840) and link.sh.pos_x==pos[2][0]
fn(ui,2)
assert assign==owner_before and pos[2][0]<1920
print('PASS: monitor cycle preserves assignments; desktop mode safely exits')

# Desktop shortcut consume down, repeat and up; other keys left alone.
desktop_control={2:False}; assign={2:2}; held=set();called=[]
ns={'assign':assign,'desktop_control':desktop_control,'desktop_key_held':held,
    'desktop_toggle_scancodes':{2:0x44},'set_desktop_mode':lambda p,v:called.append((p,v))}
hotkey=extract('desktop_toggle_event',False,ns)
assert hotkey(2,2,0x44,False)
assert hotkey(2,2,0x44,False)  # repeat, no second mode change
assert called==[(2,True)]
assert hotkey(2,2,0x44,True)
assert (2,0x44) not in held
assert not hotkey(2,2,0x57,False) # F11 passes to Windows in desktop mode
assert not hotkey(2,1,0x44,False) # not an assigned keyboard
print('PASS: desktop hotkey consumes one press, F11 stays available')

# Desktop mode switch releases isolated input, never edits assignments.
released=[];notifications=queue.SimpleQueue()
ns={'desktop_control':{2:False},'assign':{1:1,2:2,12:2},'release_device':lambda d,p:released.append((d,p)),
    'cursor_ui_events':notifications,'log':types.SimpleNamespace(info=lambda *a:None),
    'desktop_drag':types.SimpleNamespace(cancel=lambda *a:None), 'pos':{2:[500,500]}}
switch=extract('set_desktop_mode',False,ns)
assert switch(2,True) and released==[(2,2)]
assert ns['assign']=={1:1,2:2,12:2}
assert not switch(2,True)
assert switch(2,False) and not ns['desktop_control'][2]
print('PASS: keyboard-only Windows access maintains device ownership')

# No multiple-instance launcher code.
for bad in ('def launch_both(', 'def open_dual_launcher(', 'Prism Launcher', 'subprocess.Popen('):
 assert bad not in s,bad
print('PASS: no dual game launching or subprocess polling')

# The DLL searches for a visible window for up to ten seconds. The controller
# must wait longer, interpret the shared-memory startup stage, and avoid
# misleading the user into changing a working Interception driver.
assert 'HOOK_INIT_TIMEOUT = 13.0' in s
assert 'time.monotonic() + HOOK_INIT_TIMEOUT' in s
assert 'hook_boot_status(lk)' in s
status = extract('hook_boot_status')
assert status(types.SimpleNamespace(sh=types.SimpleNamespace(pad=[0, 0x00810000]))) == 0x81
assert status(types.SimpleNamespace(sh=types.SimpleNamespace(pad=[0, 0x02050306]))) == 0x05
assert 'BOOT_NO_WINDOW' in hook_src and 'BOOT_CREATE_HOOK_ERROR' in hook_src
assert 'boot_status(BOOT_READY)' in hook_src
assert 'if (MH_EnableHook(MH_ALL_HOOKS) != MH_OK)' in hook_src
assert 'FreeLibraryAndExitThread(g_mod, stage)' in hook_src
print('PASS: game-hook startup timeout, failure stages and retryable DLL cleanup')

# Startup must NEVER take physical ownership of saved devices silently.
state_initializer = next(node for node in tree.body if isinstance(node,ast.Assign)
    and any(isinstance(t,ast.Name) and t.id=='state' for t in node.targets))
ns={}
exec(compile(ast.fix_missing_locations(ast.Module(body=[state_initializer],type_ignores=[])),str(SRC),'exec'),ns)
assert ns['state']['forward'] is False
class SavedUI:
    saved={'mouse#0':2,'keyboard#0':2}
    saved_profiles={}
    saved_maxcodes={}
    saved_cursor_hotkeys={}
    applied=set()
    restore_saved_on_start=False
    def dev_keys(self): return {12:'mouse#0',2:'keyboard#0'}
ui=SavedUI()
owners={}
sync=extract('sync_devices',True,{'assign':owners,'state':{'players':2},
    'keyboard_profiles':{},'keyboard_maxcode':{},'PROFILE_LABELS':{'auto':'Auto'},
    'cursor_hotkeys':{},'seen':set(),'log':types.SimpleNamespace(info=lambda *a:None),
    'dev_name':lambda d:str(d)})
sync(ui)
assert owners=={}, 'saved Player 2 devices captured at startup'
ui.restore_saved_on_start=True
ui.applied.clear()
sync(ui)
assert owners=={12:2,2:2}, 'explicit saved-device restore did not work'
assert 'def _capture_events(ctx):' in s and 'finally:\n        # Even an unexpected input-processing exception' in s
print('PASS: safe unarmed startup, explicit saved-device restore, and context cleanup')
print('ALL v12 OFFLINE TESTS PASSED')
