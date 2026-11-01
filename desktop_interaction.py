"""Isolated Win32-message pointer router for CoopInput.

No SetCursorPos, SendInput, SetCapture, ShowCursor, or injected system mouse.
This is not a second OS mouse seat. Desktop behaviors are best-effort for
applications accepting posted client mouse messages; OLE drag/drop, pointer
capture, secure desktops, elevated apps and gestures are not universal.
"""
import math
import time

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK = 0x0201, 0x0202, 0x0203
WM_RBUTTONDOWN, WM_RBUTTONUP, WM_RBUTTONDBLCLK = 0x0204, 0x0205, 0x0206
WM_MBUTTONDOWN, WM_MBUTTONUP, WM_MBUTTONDBLCLK = 0x0207, 0x0208, 0x0209
WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x020A, 0x020E
WM_XBUTTONDOWN, WM_XBUTTONUP, WM_XBUTTONDBLCLK = 0x020B, 0x020C, 0x020D
WM_MOUSEHOVER, WM_MOUSELEAVE = 0x02A1, 0x02A3
HTCAPTION = 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT = 10, 11, 12, 13
HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 14, 15, 16, 17
RESIZE_HITS = frozenset(range(HTLEFT, HTBOTTOMRIGHT + 1))
BUTTON_MSGS = (
    (WM_LBUTTONDOWN, WM_LBUTTONUP), (WM_RBUTTONDOWN, WM_RBUTTONUP),
    (WM_MBUTTONDOWN, WM_MBUTTONUP), (WM_XBUTTONDOWN, WM_XBUTTONUP),
    (WM_XBUTTONDOWN, WM_XBUTTONUP),
)
DBLCLK_MSGS = (WM_LBUTTONDBLCLK, WM_RBUTTONDBLCLK, WM_MBUTTONDBLCLK,
                WM_XBUTTONDBLCLK, WM_XBUTTONDBLCLK)

DEFAULT_FEATURES = {
    'hover': True,
    'focus_on_click': False,  # explicit: Windows focus is shared between players
    'double_click': True,
    'double_click_ms': 500,
    'hover_ms': 600,
    'dwell': False,
    'dwell_ms': 1200,
    'clicklock': False,
    'clicklock_ms': 800,
    'auto_scroll': False,
    'shift_horizontal_wheel': True,
    'sensitivity': 1.0,
    'mouse_keys': False,
    'profile': 'normal',  # software input-speed profile; hardware DPI unchanged
    'profile_button': 0,  # 0=disabled, 3=X1, 4=X2; desktop only
}
PROFILES = {'precision': 0.5, 'normal': 1.0, 'fast': 1.8}


def validated_features(patch, base=None):
    """Use strict allowlist and ranges to keep config files safe and portable."""
    values = dict(DEFAULT_FEATURES if base is None else base)
    if not isinstance(patch, dict):
        return values
    for name, original in DEFAULT_FEATURES.items():
        if name not in patch:
            continue
        val = patch[name]
        if isinstance(original, bool):
            if isinstance(val, bool):
                values[name] = val
        elif name == 'profile':
            if isinstance(val, str) and val in PROFILES:
                values[name] = val
        elif name == 'profile_button':
            if type(val) is int and val in (0, 3, 4):
                values[name] = val
        elif isinstance(original, int):
            if isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val):
                values[name] = max(150, min(3000, int(val)))
        elif isinstance(original, float):
            if isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val):
                values[name] = max(.2, min(4., float(val)))
    return values


class DesktopDragRouter:
    """Per-player capture, click counting, optional hover, dwell, click-lock, pan.

    Each player has independent settings and state. Buttons are routed to the
    original HWND until the final held button is released, including right-drag.
    """

    def __init__(self, api, min_width=180, min_height=110, clock=None):
        self.api = api
        self.min_width = min_width
        self.min_height = min_height
        self.clock = clock or time.monotonic
        self.active = {}
        self.held = {}  # player -> physical held buttons, distinct from lock
        self.last_clicks = {}  # (player, button) -> (time, x, y, hwnd, count)
        self.settings = {}
        self.hover = {}  # player -> (hwnd, x, y, time, fired, dwell_fired)
        self.locked = set()
        self.lock_start = {}
        self.lock_swallow_up = set()
        self.autoscroll = {}  # player -> (hwnd, anchor_x, anchor_y, last_tick)
        self.positions = {}
        self.stopping_pan = set()
        self.shell_clicks = {}  # P -> (target, button, down_x, down_y), deferred until mouse-up

    def configure(self, player, **kwargs):
        self.settings[player] = validated_features(kwargs, self.settings.get(player))
        if not self.settings[player]['auto_scroll']:
            self.autoscroll.pop(player, None)
        if not self.settings[player]['clicklock'] and player in self.locked:
            self._unlock(player, *self.positions.get(player, (0, 0)))

    def options(self, player):
        return self.settings.get(player, DEFAULT_FEATURES)

    def _capture(self, player):
        data = self.active.get(player)
        if data and not self.api.alive(data['hwnd']):
            self.active.pop(player, None)
            self.held.pop(player, None)
            self.locked.discard(player)
            return None
        return data

    def _flags_for_button(self, flags, index, down):
        mask = (1, 2, 16, 32, 64)[index]
        return (flags | mask) if down else (flags & ~mask)

    def _wparam(self, flags, index):
        return flags | (((index - 2) << 16) if index >= 3 else 0)

    def _post(self, hwnd, msg, flags, x, y, index=None):
        return self.api.post_client(hwnd, msg,
                                    self._wparam(flags, index) if index is not None else flags,
                                    x, y)

    def _start_capture(self, player, target, index, x, y):
        root = self.api.root(target)
        mode = 'client'
        rect = None
        hit = 1
        if index == 0:
            hit = self.api.hit_test(root, x, y)
            if hit == HTCAPTION or hit in RESIZE_HITS:
                rect = self.api.window_rect(root)
                if rect:
                    mode = 'move' if hit == HTCAPTION else 'resize'
        self.active[player] = {'hwnd': target, 'root': root, 'mode': mode,
                               'hit': hit, 'rect': rect, 'start': (x, y)}
        return self.active[player]

    def _unlock(self, player, x, y, flags=0):
        data = self._capture(player)
        self.locked.discard(player)
        self.lock_start.pop(player, None)
        if data and data['mode'] == 'client':
            self._post(data['hwnd'], WM_LBUTTONUP, flags & ~1, x, y, 0)
        if not self.held.get(player):
            self.active.pop(player, None)

    def move(self, player, x, y, flags):
        x, y = int(x), int(y)
        self.positions[player] = (x, y)
        data = self._capture(player)
        pending = self.shell_clicks.get(player)
        if data and pending and (abs(x - pending[2]) > 6 or abs(y - pending[3]) > 6):
            # Reconstruct the original down event and preserve window capture.
            # This enables Explorer marquee selection without accidentally
            # invoking shell icons for tiny pointer movements.
            target, index, px, py = self.shell_clicks.pop(player)
            self._post(target, BUTTON_MSGS[index][0],
                       self._flags_for_button(flags, index, True), px, py, index)
        if not data:
            target = self.api.hit(x, y)
            now = self.clock()
            prev = self.hover.get(player)
            if prev and prev[0] != target and self.options(player)['hover']:
                if self.api.alive(prev[0]):
                    self._post(prev[0], WM_MOUSELEAVE, 0, x, y)
            if not prev or prev[0] != target or abs(x-prev[1]) > 3 or abs(y-prev[2]) > 3:
                self.hover[player] = (target, x, y, now, False, False)
            return self._post(target, WM_MOUSEMOVE, flags, x, y) if target else False
        mode = data['mode']
        if mode == 'client':
            return self._post(data['hwnd'], WM_MOUSEMOVE, flags, x, y)
        dx, dy = x - data['start'][0], y - data['start'][1]
        l, t, r, b = data['rect']
        if mode == 'move':
            return self.api.move_window(data['root'], l + dx, t + dy, r-l, b-t)
        hit = data['hit']
        if hit in (HTLEFT, HTTOPLEFT, HTBOTTOMLEFT):
            l = min(l + dx, r-self.min_width)
        if hit in (HTRIGHT, HTTOPRIGHT, HTBOTTOMRIGHT):
            r = max(r + dx, l+self.min_width)
        if hit in (HTTOP, HTTOPLEFT, HTTOPRIGHT):
            t = min(t + dy, b-self.min_height)
        if hit in (HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT):
            b = max(b + dy, t+self.min_height)
        return self.api.move_window(data['root'], l, t, r-l, b-t)

    def button(self, player, index, down, x, y, flags):
        if not 0 <= index < len(BUTTON_MSGS):
            return False
        self.positions[player] = (int(x), int(y))
        opts = self.options(player)
        now = self.clock()
        held = self.held.setdefault(player, set())
        data = self._capture(player)
        if down:
            if index in held:
                return True  # bouncing press does not destroy drag origin
            held.add(index)
            if index != 2:
                self.autoscroll.pop(player, None)
            elif player in self.autoscroll:
                self.autoscroll.pop(player, None)
                self.stopping_pan.add(player)
            if index == 0 and player in self.locked:
                # A second press unlocks ClickLock; don't begin another drag.
                self._unlock(player, x, y, flags)
                self.lock_swallow_up.add(player)
                return True
            if not data:
                target = self.api.hit(x, y)
                if not target:
                    held.discard(index)
                    return False
                data = self._start_capture(player, target, index, x, y)
                # Share focus only when the player has explicitly selected it.
                note = getattr(self.api, 'note_interaction', None)
                if index == 0 and callable(note):
                    note(player, target)
            if index == 0 and opts['clicklock']:
                self.lock_start[player] = now
            if data['mode'] != 'client':
                return True
            # Modern Windows taskbar and Desktop UI sometimes ignore posted
            # button messages. Defer their simple click and try the Shell's
            # accessibility default action on release. Right-click/context
            # menus and actual drag motions keep ordinary mouse-message flow.
            shell_test = getattr(self.api, 'is_shell_target', None)
            if (index == 0 and callable(shell_test) and shell_test(data['hwnd'])
                    and not self.held[player] - {index}):
                self.shell_clicks[player] = (data['hwnd'], index, x, y)
                return True
            key = (player, index)
            prev = self.last_clicks.get(key)
            count = 1
            if prev and prev[3] == data['hwnd'] and (now-prev[0])*1000 <= opts['double_click_ms'] and abs(x-prev[1]) <= 4 and abs(y-prev[2]) <= 4:
                count = prev[4]+1
            # On the 2nd / 4th press send WM_*DBLCLK. Other presses remain
            # regular down/up pairs; apps decide if triple/quad means anything.
            self.last_clicks[key] = (now, x, y, data['hwnd'], count)
            msg = DBLCLK_MSGS[index] if opts['double_click'] and count % 2 == 0 else BUTTON_MSGS[index][0]
            return self._post(data['hwnd'], msg, self._flags_for_button(flags, index, True), x, y, index)
        # Release
        if index not in held:
            return True  # no orphan button-up
        held.remove(index)
        if index == 0 and player in self.lock_swallow_up:
            self.lock_swallow_up.discard(player)
            if not held and player not in self.locked:
                self.active.pop(player, None)
            return True
        if index == 0 and opts['clicklock'] and data and data['mode'] == 'client' and now-self.lock_start.pop(player, now) >= opts['clicklock_ms']/1000:
            self.locked.add(player)
            return True
        if not data:
            return False
        if data['mode'] != 'client':
            if not held and player not in self.locked:
                self.active.pop(player, None)
            return True
        pending = self.shell_clicks.pop(player, None)
        if pending and pending[1] == index:
            # An accessibility provider may be slow or hung. Dispatch modern
            # taskbar actions to a separate COM worker, never the input loop.
            def fallback():
                self._post(data['hwnd'], BUTTON_MSGS[index][0],
                           self._flags_for_button(flags, index, True), pending[2], pending[3], index)
                self._post(data['hwnd'], BUTTON_MSGS[index][1],
                           self._flags_for_button(flags, index, False), x, y, index)

            async_action = getattr(self.api, 'shell_activate_async', None)
            if callable(async_action):
                result = bool(async_action(x, y, fallback))
                if not result:
                    fallback()
                    result = True
            else:
                shell_action = getattr(self.api, 'shell_activate', None)
                result = bool(callable(shell_action) and shell_action(x, y))
                if not result:
                    fallback()
                    result = True
        else:
            result = self._post(data['hwnd'], BUTTON_MSGS[index][1],
                                self._flags_for_button(flags, index, False), x, y, index)
        if not held and player not in self.locked:
            self.active.pop(player, None)
        if index == 2 and opts['auto_scroll'] and not held:
            # Optional synthetic pan; depends on a receptive client HWND.
            if player in self.stopping_pan:
                self.stopping_pan.discard(player)
            else:
                self.autoscroll[player] = (data['hwnd'], x, y, now)
        return result

    def wheel(self, player, x, y, rolling, horizontal=False, flags=0):
        data = self._capture(player)
        target = data['hwnd'] if data else self.api.hit(x, y)
        if not target:
            return False
        if flags & 4 and not horizontal and self.options(player)['shift_horizontal_wheel']:
            horizontal = True
        return self.api.post_wheel(target, x, y, rolling, horizontal, flags)

    def tick(self, player, x, y, flags):
        """Called from capture thread idle loop; never blocks or touches Tk."""
        if self._capture(player):
            return
        opts = self.options(player)
        now = self.clock()
        hover = self.hover.get(player)
        if hover and hover[0] and self.api.alive(hover[0]):
            target, hx, hy, when, fired, dwell_fired = hover
            if not fired and opts['hover'] and (now-when)*1000 >= opts['hover_ms']:
                self._post(target, WM_MOUSEHOVER, flags, x, y)
                fired = True
            if not dwell_fired and opts['dwell'] and (now-when)*1000 >= opts['dwell_ms']:
                self.button(player, 0, True, x, y, flags | 1)
                self.button(player, 0, False, x, y, flags & ~1)
                dwell_fired = True
            self.hover[player] = (target, hx, hy, when, fired, dwell_fired)
        pan = self.autoscroll.get(player)
        if pan and opts['auto_scroll'] and now-pan[3] >= .065:
            target, ax, ay, _ = pan
            if not self.api.alive(target):
                self.autoscroll.pop(player, None)
                return
            dx, dy = x-ax, y-ay
            # Dead zone around the middle-click origin; capped for safety.
            if abs(dy) >= 16:
                self.api.post_wheel(target, x, y, int(max(-360, min(360, -dy*2))), False, flags)
            if abs(dx) >= 16:
                self.api.post_wheel(target, x, y, int(max(-360, min(360, dx*2))), True, flags)
            self.autoscroll[player] = (target, ax, ay, now)

    def cancel(self, player, x=0, y=0):
        data = self._capture(player)
        buttons = self.held.pop(player, set())
        if player in self.locked:
            buttons.add(0)
        if data and data['mode'] == 'client':
            for index in buttons:
                self._post(data['hwnd'], BUTTON_MSGS[index][1], 0, x, y, index)
        self.active.pop(player, None)
        self.locked.discard(player)
        self.lock_swallow_up.discard(player)
        self.lock_start.pop(player, None)
        self.autoscroll.pop(player, None)
        self.stopping_pan.discard(player)
        self.hover.pop(player, None)
        self.shell_clicks.pop(player, None)

    def cancel_all(self):
        for player in set(self.active) | set(self.held) | set(self.autoscroll) | set(self.shell_clicks):
            self.cancel(player, *self.positions.get(player, (0, 0)))
