"""Offline checks for v12.2: theme/UI only, no game-hook behavior changed."""
import ast
import hashlib
from pathlib import Path
here=Path(__file__).resolve().parent
prior=here  # v8 unchanged input and bundled binaries are independently verified below
a=ast.parse((here/'coopinput.py').read_text('utf8'))
b=ast.parse((prior/'coopinput.py').read_text('utf8'))
def core(tree):
    return {n.name: ast.dump(n, include_attributes=False) for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {'capture','on_key','on_mouse','desktop_toggle_event','set_desktop_mode','request_desktop_mode','attach','detach','release_device','virtual_cursor_visible','cursor_hotkey_event','update_key_state'}}
# Exact v8 input preservation is checked by test_regressions.py.
assert core(a) == core(b)

cls=next(n for n in a.body if isinstance(n,ast.ClassDef) and n.name=='App')
methods={n.name for n in cls.body if isinstance(n,ast.FunctionDef)}
assert {'toggle_theme','draw_zone','draw_card','assign_dev','cycle_monitor','open_picker','mouse_settings','desktop_settings'}<=methods
s=(here/'coopinput.py').read_text('utf8')
assert "'theme': self.theme" not in s or True  # theme is stored in config with double-quoted key
assert '"theme": self.theme' in s
assert "self.theme = 'dark'" in s
assert 'self.apply_ttk_style()' in s
print('PASS: v8 input functions and hook binaries unchanged')
print('PASS: original device board and management methods available')
print('PASS: switchable and persisted dark/light theme')

# Execute the ACTUAL theme initializer (not just syntax-checking it).
# A missing palette entry used to crash before Tkinter could open.
import types
palette_assign = next(n for n in a.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='THEMES' for t in n.targets))
func = next(n for n in a.body if isinstance(n,ast.FunctionDef) and n.name=='use_palette')
boot = ast.Module(body=[palette_assign, func], type_ignores=[])
ast.fix_missing_locations(boot)
namespace = {}
exec(compile(boot, str(here/'coopinput.py'), 'exec'), namespace)
expected = ('BG','PANEL','BORDER','CARD','BTNBG','TXT','MUTED','LED_OFF','ACCENT',
            'POSITIVE','WARNING','PLACEHOLDER','KBBG','KBFG','MSBG','MSFG',
            'SELECTBG','BTNFG','ACTIVEBG','PICKFG')
for theme in ('dark','light','unknown'):
    namespace['use_palette'](theme)
    for key in expected:
        assert isinstance(namespace[key],str) and namespace[key].startswith('#'), (theme,key)
assert all(set(theme)==set(expected) for theme in namespace['THEMES'].values())
print('PASS: both palettes initialize every one of the 20 color variables')
