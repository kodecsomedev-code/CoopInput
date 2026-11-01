"""Per-player *visual-only* cursor shapes for the isolated virtual mouse.

Rendering never calls SetCursor, ShowCursor, SetCursorPos or SendInput. A game's
hooked mouse still uses the original v8 input path; these are Tk overlay shapes.
"""

CURSOR_SHAPES = (
    'arrow', 'hand', 'ibeam', 'size_we', 'size_ns', 'size_nwse',
    'size_nesw', 'move', 'crosshair', 'busy', 'forbidden', 'grabbing',
)
HOTSPOTS = {
    'arrow': (1, 1), 'hand': (8, 1), 'grabbing': (13, 12),
    'ibeam': (17, 17), 'size_we': (17, 17), 'size_ns': (17, 17),
    'size_nwse': (17, 17), 'size_nesw': (17, 17), 'move': (17, 17),
    'crosshair': (17, 17), 'busy': (17, 17), 'forbidden': (17, 17),
}

HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT = 10, 11, 12, 13
HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 14, 15, 16, 17
RESIZE_CURSOR = {
    HTLEFT: 'size_we', HTRIGHT: 'size_we',
    HTTOP: 'size_ns', HTBOTTOM: 'size_ns',
    HTTOPLEFT: 'size_nwse', HTBOTTOMRIGHT: 'size_nwse',
    HTTOPRIGHT: 'size_nesw', HTBOTTOMLEFT: 'size_nesw',
}

# A control class indicates a probable cursor intent. These are hints, not
# promises about custom-drawn links inside Chrome, games, or other applications.
TEXT_CLASSES = ('edit', 'richedit', 'richedit20', 'scintilla', 'windowsforms10.edit')
LINK_CLASSES = ('syslink', 'button', 'toolbarwindow32', 'windowsforms10.button')


def shape_for_target(class_name='', hit=1, class_cursor=None, dragging=None):
    """Decide a pointer shape from window hit-testing and class information.

    class_cursor is an optional shape name recognized from GCLP_HCURSOR.
    dragging is a (mode, hit) tuple locked by DesktopDragRouter.
    """
    if dragging:
        mode, drag_hit = dragging
        if mode == 'move':
            return 'move'
        if mode == 'resize':
            return RESIZE_CURSOR.get(drag_hit, 'move')
        # Keep the original text indication during drag selection.
        if mode == 'client':
            lower = (class_name or '').lower()
            return 'ibeam' if lower.startswith(TEXT_CLASSES) else 'grabbing'
    if hit in RESIZE_CURSOR:
        return RESIZE_CURSOR[hit]
    if hit == -2:  # HTERROR - no interactive hit target
        return 'forbidden'
    if class_cursor in CURSOR_SHAPES and class_cursor != 'arrow':
        return class_cursor
    lower = (class_name or '').lower()
    if lower.startswith(TEXT_CLASSES):
        return 'ibeam'
    if lower.startswith(LINK_CLASSES):
        return 'hand'
    return 'arrow'


def draw_shape(canvas, shape, accent):
    """Paint a high-contrast 36px icon, keeping the screen-point hotspot."""
    if shape not in CURSOR_SHAPES:
        shape = 'arrow'
    canvas.delete('all')
    black = '#0b0f18'
    white = '#ffffff'

    def line(points, fill=white, width=2, **kw):
        return canvas.create_line(*points, fill=fill, width=width, **kw)

    def poly(points, fill=white, outline=black, width=2):
        return canvas.create_polygon(*points, fill=fill, outline=outline, width=width)

    if shape == 'arrow':
        poly((2, 2, 3, 28, 9, 21, 14, 32, 20, 29, 14, 18, 25, 18), accent)
    elif shape in ('hand', 'grabbing'):
        # Pointing hand / closed hand, with a dark stroke against any wallpaper.
        if shape == 'hand':
            poly((9, 3, 12, 2, 14, 5, 14, 15, 17, 11, 21, 13, 24, 13,
                  27, 16, 30, 20, 28, 29, 24, 33, 14, 33, 7, 26, 4, 20,
                  5, 17, 9, 18), white)
            line((10, 7, 10, 23), fill=accent, width=2)
        else:
            poly((7, 14, 9, 10, 13, 10, 15, 7, 20, 9, 23, 11,
                  27, 11, 31, 15, 29, 24, 23, 32, 11, 32, 5, 25), white)
            line((12, 12, 12, 20), fill=accent, width=2)
    elif shape == 'ibeam':
        line((17, 5, 17, 29), black, 6)
        line((17, 5, 17, 29), white, 3)
        for y in (5, 29):
            line((11, y, 23, y), black, 6)
            line((11, y, 23, y), white, 3)
    elif shape in ('size_we', 'size_ns', 'size_nwse', 'size_nesw'):
        vec = {'size_we': (1, 0), 'size_ns': (0, 1),
               'size_nwse': (1, 1), 'size_nesw': (1, -1)}[shape]
        vx, vy = vec
        a = (17 - 11*vx, 17 - 11*vy)
        b = (17 + 11*vx, 17 + 11*vy)
        line((*a, *b), black, 7)
        line((*a, *b), accent, 4)
        for x, y, sign in ((a[0], a[1], -1), (b[0], b[1], 1)):
            ox, oy = -sign*5*vx, -sign*5*vy
            # symmetric arrow heads
            line((x+ox-4*vy, y+oy+4*vx, x, y, x+ox+4*vy, y+oy-4*vx), black, 6)
            line((x+ox-4*vy, y+oy+4*vx, x, y, x+ox+4*vy, y+oy-4*vx), accent, 3)
    elif shape == 'move':
        line((17, 3, 17, 31), black, 6)
        line((3, 17, 31, 17), black, 6)
        line((17, 3, 17, 31), accent, 3)
        line((3, 17, 31, 17), accent, 3)
        for x,y,xx,yy in ((17,3,13,9),(17,3,21,9),(17,31,13,25),
                          (17,31,21,25),(3,17,9,13),(3,17,9,21),
                          (31,17,25,13),(31,17,25,21)):
            line((x,y,xx,yy), accent, 3)
    elif shape == 'crosshair':
        canvas.create_oval(10, 10, 24, 24, outline=black, width=5)
        canvas.create_oval(10, 10, 24, 24, outline=accent, width=2)
        line((17, 2, 17, 12), accent, 2)
        line((17, 22, 17, 32), accent, 2)
        line((2, 17, 12, 17), accent, 2)
        line((22, 17, 32, 17), accent, 2)
    elif shape == 'forbidden':
        canvas.create_oval(5, 5, 29, 29, outline=white, width=4)
        line((9, 27, 27, 9), accent, 5)
    elif shape == 'busy':
        canvas.create_oval(5, 5, 29, 29, outline=white, width=3)
        canvas.create_arc(5, 5, 29, 29, start=0, extent=125,
                          outline=accent, style='arc', width=4)
    return HOTSPOTS[shape]
