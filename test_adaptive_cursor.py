"""Offline cursor tests: no Windows driver or Tk display required."""
from pathlib import Path
from cursor_visuals import (
    CURSOR_SHAPES, HOTSPOTS, RESIZE_CURSOR, draw_shape, shape_for_target,
)

class DummyCanvas:
    def __init__(self):
        self.calls = []
    def delete(self, *a, **kw): self.calls.append(('delete', a, kw))
    def create_line(self, *a, **kw): self.calls.append(('line', a, kw))
    def create_polygon(self, *a, **kw): self.calls.append(('polygon', a, kw))
    def create_oval(self, *a, **kw): self.calls.append(('oval', a, kw))
    def create_arc(self, *a, **kw): self.calls.append(('arc', a, kw))

for cursor in CURSOR_SHAPES:
    cv = DummyCanvas()
    assert draw_shape(cv, cursor, '#57b8ff') == HOTSPOTS[cursor]
    assert len(cv.calls) >= 2, cursor
    assert cv.calls[0][0] == 'delete', cursor
    x, y = HOTSPOTS[cursor]
    assert 0 <= x < 36 and 0 <= y < 36
print('PASS: every shape has a rendered icon and valid hotspot')

assert shape_for_target('Edit') == 'ibeam'
assert shape_for_target('RichEdit20W') == 'ibeam'
assert shape_for_target('Scintilla') == 'ibeam'
assert shape_for_target('SysLink') == 'hand'
assert shape_for_target('Button') == 'hand'
assert shape_for_target('Chrome_RenderWidgetHostHWND') == 'arrow'
assert shape_for_target('Unknown', class_cursor='ibeam') == 'ibeam'
assert shape_for_target('Unknown', class_cursor='hand') == 'hand'
assert shape_for_target('Unknown', class_cursor='busy') == 'busy'
for hit, want in RESIZE_CURSOR.items():
    assert shape_for_target('Unknown', hit=hit) == want
assert shape_for_target('Edit', dragging=('client', 1)) == 'ibeam'
assert shape_for_target('Button', dragging=('client', 1)) == 'grabbing'
assert shape_for_target('Any', dragging=('move', 2)) == 'move'
assert shape_for_target('Any', dragging=('resize', 13)) == 'size_nwse'
print('PASS: text, clickable controls, window borders and drag state classified')

src = Path(__file__).with_name('coopinput.py').read_text(encoding='utf-8')
assert "self.overlays[p] = make_overlay" in src
assert "virtual_pointer_shape(p, *q)" in src
assert "paint_system_cursor(ov.pointer_canvas, new_shape, getattr(ov, 'pointer_tint', None), system_cursor_art)" in src
assert 'q[0]-hx' in src and 'q[1]-hy' in src
assert "getattr(ov, 'pointer_hotspot'" in src
assert "if link and link.alive() and not desktop_control.get(player, False):" in src
assert 'desktop_drag.active.get(player)' in src
assert "ov.pointer_shape = new_shape" in src
assert 'user32.SetCursor(' not in src
assert 'user32.SetCursorPos(' not in src
assert 'user32.ShowCursor(' not in src
print('PASS: isolated overlay, game-safe fallback, and no physical cursor mutation')
print('ALL ADAPTIVE CURSOR TESTS PASSED')
