"""No-Windows-needed checks for clean public release source."""
from pathlib import Path
import ast
import struct
p = Path(__file__).resolve().parents[1]
modules = ['coopinput','cursor_visuals','desktop_interaction','shell_accessibility',
           'taskbar_uia','windows_cursor_art','v8_reference']
for module in modules:
    f = p / f'{module}.py'
    assert f.is_file(), module
    ast.parse(f.read_text('utf-8'), filename=str(f))
assert (p/'coopinput_hook.c').is_file()
assert (p/'minhook'/'LICENSE.txt').is_file()
assert (p/'installer.iss').is_file()
assert (p/'assets'/'CoopInput.ico').is_file()
assert not (p/'interception.dll').exists(), 'Never include upstream Interception DLL in public source.'
# Older checkouts may retain a legacy installer download in the repository
# root. It must not be mistaken for the newly built portable application.
assert not (p/'CoopInput.exe').exists(), 'Do not publish a portable app executable as source.'
assert not (p/'coopinput_config.json').exists(), 'Do not publish personal device assignments.'
assert not any(p.glob('*.log')), 'Do not publish personal debug logs.'
s = (p/'coopinput.py').read_text('utf-8')
assert 'getattr(sys, "frozen", False)' in s
assert 'USER_DATA_DIR' in s and 'LOCALAPPDATA' in s
assert 'interception.dll' in s
assert 'def ' in s
print('PASS: syntax, assets, license guard, and writable packaged-app storage')
