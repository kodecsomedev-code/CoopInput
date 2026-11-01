"""Render the currently installed REAL Windows system cursor onto an isolated Tk overlay.

No downloaded cursor pack, no extra Python dependency, and importantly no
SetCursor/SetCursorPos/ShowCursor/SendInput. Images originate from the same
HCURSOR resources used by the Windows cursor scheme, via LoadCursorW.

Windows/GDI work is lazy and must be performed on the Tk UI thread.
"""
import base64
import re
import ctypes
import struct
import sys
import zlib

# Microsoft WinUser.h resource identifiers
CURSOR_IDS = {
    'arrow': 32512, 'ibeam': 32513, 'busy': 32514, 'crosshair': 32515,
    'size_nwse': 32642, 'size_nesw': 32643, 'size_we': 32644,
    'size_ns': 32645, 'move': 32646, 'forbidden': 32648,
    'hand': 32649, 'appstarting': 32650, 'help': 32651,
    'grabbing': 32646,  # Windows move cursor; no universal 'grabbing' system resource
}


def png_rgba(width, height, data):
    """Pure-standard-library PNG encoder: RGBA rows top-to-bottom."""
    if width < 1 or height < 1 or len(data) != width * height * 4:
        raise ValueError('Invalid RGBA cursor image')
    def chunk(name, payload):
        return struct.pack('>I', len(payload)) + name + payload + struct.pack('>I', zlib.crc32(name + payload) & 0xffffffff)
    scanlines = b''.join(b'\0' + data[y*width*4:(y+1)*width*4] for y in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(scanlines, 6)) + chunk(b'IEND', b''))


def reconstruct_pixel(black_bgra, white_bgra):
    """Recover foreground color/transparency from a cursor drawn over two backgrounds.

    This also accommodates classic monochrome cursors where DrawIconEx does not
    write a useful alpha byte into a 32-bit DIB. Windows XOR cursors approximate.
    """
    b, g, r = black_bgra[:3]
    wb, wg, wr = white_bgra[:3]
    transmission = max(0, min(255, round(((wb-b) + (wg-g) + (wr-r)) / 3)))
    alpha = 255 - transmission
    if alpha < 5:
        return (0, 0, 0, 0)
    return tuple(min(255, round(component*255/alpha)) for component in (r, g, b)) + (alpha,)


COLOR_PRESETS = {
    'Windows original': None,
    'Blue': '#4E9BFF',
    'Cyan': '#00D5FF',
    'Purple': '#B083FF',
    'Pink': '#FF68B8',
    'Yellow': '#FFD45E',
    'Green': '#48E3A0',
    'Orange': '#FF9764',
    'Red': '#FF6673',
    'White': '#FFFFFF',
}


def normalize_cursor_color(value):
    """Untrusted config colors never reach Tk as malformed values."""
    if value is None or value == 'original':
        return None
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}', value):
        return None
    return value.upper()


def tint_cursor_pixels(pixels, tint):
    """Retint native RGBA pixels; preserve alpha and dark outline of Windows art.

    The luminance becomes colored interior shading, while nearly-black border
    pixels remain nearly black so the cursor is distinguishable on light pages.
    """
    tint = normalize_cursor_color(tint)
    if tint is None:
        return bytes(pixels)
    rgb = tuple(int(tint[i:i+2],16) for i in (1,3,5))
    result = bytearray(pixels)
    for i in range(0,len(result),4):
        if not result[i+3]:
            continue
        r,g,b=result[i:i+3]
        lum=(54*r+183*g+19*b)//256
        if lum <= 35:
            continue  # keep Windows' crisp dark cursor outline
        # Windows cursor gradients and antialiasing remain visible.
        shade=(lum-20)/235
        result[i:i+3]=bytes(max(0,min(255,round(c*shade))) for c in rgb)
    return bytes(result)


class ICONINFO(ctypes.Structure):
    _fields_ = [('fIcon', ctypes.c_int), ('xHotspot', ctypes.c_uint),
                ('yHotspot', ctypes.c_uint), ('hbmMask', ctypes.c_void_p),
                ('hbmColor', ctypes.c_void_p)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', ctypes.c_uint), ('biWidth', ctypes.c_int),
                ('biHeight', ctypes.c_int), ('biPlanes', ctypes.c_ushort),
                ('biBitCount', ctypes.c_ushort), ('biCompression', ctypes.c_uint),
                ('biSizeImage', ctypes.c_uint), ('biXPelsPerMeter', ctypes.c_int),
                ('biYPelsPerMeter', ctypes.c_int), ('biClrUsed', ctypes.c_uint),
                ('biClrImportant', ctypes.c_uint)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', BITMAPINFOHEADER), ('bmiColors', ctypes.c_uint * 3)]


class NativeSystemCursorArt:
    """Caches actual Windows cursor pixels and their native click hotspots."""
    def __init__(self, canvas_size=64):
        self.size = canvas_size
        self.cache = {}
        self.base_pixels = {}
        self.available = sys.platform == 'win32'
        if not self.available:
            return
        self.u = ctypes.windll.user32
        self.g = ctypes.windll.gdi32
        self.u.LoadCursorW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.u.LoadCursorW.restype = ctypes.c_void_p
        self.u.GetIconInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(ICONINFO)]
        self.u.GetIconInfo.restype = ctypes.c_int
        self.u.DrawIconEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        self.u.DrawIconEx.restype = ctypes.c_int
        self.g.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        self.g.CreateCompatibleDC.restype = ctypes.c_void_p
        self.g.DeleteDC.argtypes = [ctypes.c_void_p]
        self.g.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.POINTER(BITMAPINFO),
                                            ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p),
                                            ctypes.c_void_p, ctypes.c_uint]
        self.g.CreateDIBSection.restype = ctypes.c_void_p
        self.g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.g.SelectObject.restype = ctypes.c_void_p
        self.g.DeleteObject.argtypes = [ctypes.c_void_p]
        self.g.DeleteObject.restype = ctypes.c_int

    def _draw(self, icon, fill):
        n = self.size
        dc = self.g.CreateCompatibleDC(None)
        if not dc:
            raise OSError('CreateCompatibleDC failed')
        bmp = None
        old = None
        try:
            info = BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info.bmiHeader.biWidth = n
            info.bmiHeader.biHeight = -n  # top-down DIB
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            info.bmiHeader.biCompression = 0
            bits = ctypes.c_void_p()
            bmp = self.g.CreateDIBSection(None, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
            if not bmp or not bits.value:
                raise OSError('CreateDIBSection failed')
            old = self.g.SelectObject(dc, bmp)
            ctypes.memset(bits.value, fill, n*n*4)
            if not self.u.DrawIconEx(dc, 0, 0, icon, 0, 0, 0, None, 0x0003):
                raise OSError('DrawIconEx failed')
            return ctypes.string_at(bits.value, n*n*4)
        finally:
            if old:
                self.g.SelectObject(dc, old)
            if bmp:
                self.g.DeleteObject(bmp)
            self.g.DeleteDC(dc)

    def get(self, name, color=None):
        color = normalize_cursor_color(color)
        key = (name, color)
        if key in self.cache:
            return self.cache[key]
        if not self.available:
            return None
        if name not in self.base_pixels:
            hcur = self.u.LoadCursorW(None, ctypes.c_void_p(CURSOR_IDS.get(name, 32512)))
            if not hcur:
                return None
            info = ICONINFO()
            if not self.u.GetIconInfo(hcur, ctypes.byref(info)):
                return None
            try:
                hotspot = (max(0, min(self.size-1, int(info.xHotspot))),
                           max(0, min(self.size-1, int(info.yHotspot))))
            finally:
                for handle in (info.hbmMask, info.hbmColor):
                    if handle:
                        self.g.DeleteObject(handle)
            black = self._draw(hcur, 0)
            white = self._draw(hcur, 255)
            pixels = bytearray(self.size*self.size*4)
            for i in range(0,len(pixels),4):
                pixels[i:i+4] = bytes(reconstruct_pixel(black[i:i+4], white[i:i+4]))
            self.base_pixels[name] = (bytes(pixels), hotspot)
        pixels, hotspot = self.base_pixels[name]
        pixels = tint_cursor_pixels(pixels, color)
        asset = (base64.b64encode(png_rgba(self.size,self.size,pixels)).decode('ascii'),hotspot)
        self.cache[key] = asset
        return asset


def paint_system_cursor(canvas, shape, color, renderer):
    """Native cursor first; retain previous colored canvas drawing as fallback."""
    try:
        native = renderer.get(shape, color)
        if native:
            import tkinter as tk
            payload, hotspot = native
            image = tk.PhotoImage(data=payload, master=canvas)
            canvas.delete('all')
            canvas.create_image(0, 0, anchor='nw', image=image)
            canvas._system_cursor_image = image  # Tk requires a Python reference
            return hotspot
    except Exception:
        pass
    from cursor_visuals import draw_shape
    return draw_shape(canvas, shape, color or '#FFFFFF')
