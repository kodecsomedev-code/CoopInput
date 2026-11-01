/*
 * coopinput_hook.c - DLL injetada no jogo (x64).
 * Faz o jogo acreditar que o mouse/teclado do jogador virtual sao "o" mouse/teclado:
 *  - posicao/botoes/teclas vem de memoria compartilhada (CoopInput_<pid>), nao do Windows
 *  - mensagens de mouse/teclado/raw input sao sinteticas; as reais sao descartadas
 *  - o jogo sempre acha que tem o foco (nao pausa ao perder o foco)
 *  - ClipCursor registra o estado da captura VIRTUAL (nao altera o cursor real do Windows)
 * Nao cobre DirectInput (so Win32 + Raw Input).
 * Fase 8: manter o isolamento do v7 e rastrear captura de cursor em jogos GLFW.
 * A aplicacao nao pode transformar scan codes do teclado em movimento do mouse.
 * e scroll horizontal. ESTA FONTE PRECISA SER RECOMPILADA para atualizar a DLL.
 *
 * Build: x86_64-w64-mingw32-gcc -shared -O2 -o coopinput_hook.dll coopinput_hook.c \
 *        minhook/src/(todos os .c) minhook/src/hde/hde64.c -Iminhook/include -luser32 -static-libgcc
 */
#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0A00
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include "MinHook.h"

#define RING 2048
#define MAGIC 0x504F4F43u
enum { EV_MOVE = 1, EV_BTN = 2, EV_WHEEL = 3, EV_KEY = 4, EV_EXIT = 99 };
#define HOOK_ABI_VERSION 8u
/* Game-specific virtual cursor state, not the shared Windows cursor. */
#define HOOK_CURSOR_HINT_VALID   0x0100u
#define HOOK_CURSOR_HINT_GRABBED 0x0200u
#define HOOK_MODE_RAW    0x02u
#define HOOK_MODE_LEGACY 0x04u

/* The high byte of Shared.pad[1] carries startup diagnostics. Its low bits
 * remain reserved for cursor-capture telemetry, preserving the v8 ABI. */
enum {
    BOOT_MEMORY_READY = 1, BOOT_WAIT_WINDOW = 2, BOOT_WINDOW_FOUND = 3,
    BOOT_INSTALL_HOOKS = 4, BOOT_READY = 5,
    BOOT_NO_WINDOW = 0x81, BOOT_MINHOOK_ERROR = 0x82,
    BOOT_CREATE_HOOK_ERROR = 0x83, BOOT_ENABLE_HOOK_ERROR = 0x84,
    BOOT_SUBCLASS_ERROR = 0x85
};


/* The controller refuses to attach a pre-v8 DLL. Keep this exact marker
 * in the binary even when optimized, so old DLLs cannot be mistaken for v6. */
__declspec(dllexport) const char CoopInputHookABI[] = "COOPINPUT-HOOK-ABI-8";
typedef struct { int32_t type, a, b, c; } Ev;
typedef struct {
    volatile uint32_t magic, head, dll_tick;
    volatile int32_t pos_x, pos_y;
    volatile uint32_t flags, pad[2];
    Ev ev[RING];
} Shared;

static HMODULE g_mod;
static Shared *g_sh;
static HWND g_hwnd;
static WNDPROC g_orig_proc;
static DWORD g_ui_tid;
static CRITICAL_SECTION g_cs;
static volatile LONG g_px, g_py;
static BYTE g_keys[256];
static volatile LONG g_exit;
static int g_cs_initialized, g_mh_initialized;

static void boot_status(unsigned code)
{
    if (!g_sh) return;
    volatile LONG *field = (volatile LONG *)&g_sh->pad[1];
    LONG old, updated;
    do {
        old = *field;
        updated = (old & 0x0000FFFFL) | ((LONG)(code & 0xFFu) << 16);
    } while (InterlockedCompareExchange(field, updated, old) != old);
}

/* ---------- fila de mensagens sinteticas ---------- */
#define QN 200
static MSG g_q[QN];
static int g_qh, g_qc;          /* head, count */
#define RAWN 256
static RAWINPUT g_raw[RAWN];
static int g_rawi;

static void q_push_locked(UINT m, WPARAM w, LPARAM l)
{
    if (g_qc == QN) { g_qh = (g_qh + 1) % QN; g_qc--; }   /* descarta o mais antigo */
    MSG *s = &g_q[(g_qh + g_qc) % QN];
    s->hwnd = g_hwnd; s->message = m; s->wParam = w; s->lParam = l;
    s->time = GetTickCount(); s->pt.x = g_px; s->pt.y = g_py;
    g_qc++;
}

/* Windows permits a message loop to filter by message range. The old code
 * ONLY tested the oldest queued event. A WM_MOUSEMOVE at the queue head could
 * indefinitely block keyboard, raw input and WM_CHAR requests by the game. */
static int take_synth(LPMSG out, HWND hw, UINT mn, UINT mx, BOOL remove)
{
    int ok = 0;
    EnterCriticalSection(&g_cs);
    for (int i = 0; i < g_qc; ++i) {
        MSG *s = &g_q[(g_qh + i) % QN];
        if ((hw && hw != s->hwnd) || ((mn || mx) && (s->message < mn || s->message > mx)))
            continue;
        *out = *s; ok = 1;
        if (remove) {
            /* Preserve remaining order. QN is bounded at 200. */
            for (int j = i; j < g_qc - 1; ++j)
                g_q[(g_qh + j) % QN] = g_q[(g_qh + j + 1) % QN];
            --g_qc;
        }
        break;
    }
    LeaveCriticalSection(&g_cs);
    return ok;
}

static int is_real_input(UINT m)
{
    return (m >= 0x200 && m <= 0x20E) || m == 0x100 || m == 0x101 || m == 0x104 ||
           m == 0x105 || m == 0xFF || m == 0x2A1 || m == 0x2A3;
}

/* ---------- hooks ---------- */
static BOOL (WINAPI *oGetCursorPos)(LPPOINT);
static BOOL (WINAPI *oSetCursorPos)(int, int);
static BOOL (WINAPI *oClipCursor)(const RECT *);
static BOOL (WINAPI *oGetCursorInfo)(PCURSORINFO);
static SHORT (WINAPI *oGetAsyncKeyState)(int);
static SHORT (WINAPI *oGetKeyState)(int);
static BOOL (WINAPI *oGetKeyboardState)(PBYTE);
static HWND (WINAPI *oGetForegroundWindow)(void);
static HWND (WINAPI *oGetActiveWindow)(void);
static HWND (WINAPI *oGetFocus)(void);
static BOOL (WINAPI *oSetForegroundWindow)(HWND);
static HWND (WINAPI *oSetFocus)(HWND);
static HWND (WINAPI *oSetActiveWindow)(HWND);
static UINT (WINAPI *oSendInput)(UINT, LPINPUT, int);
static DWORD (WINAPI *oGetMessagePos)(void);
static UINT (WINAPI *oGetRawInputData)(HRAWINPUT, UINT, LPVOID, PUINT, UINT);
static BOOL (WINAPI *oPeekMessageA)(LPMSG, HWND, UINT, UINT, UINT);
static BOOL (WINAPI *oPeekMessageW)(LPMSG, HWND, UINT, UINT, UINT);
static BOOL (WINAPI *oGetMessageA)(LPMSG, HWND, UINT, UINT);
static BOOL (WINAPI *oGetMessageW)(LPMSG, HWND, UINT, UINT);

static BOOL WINAPI hGetCursorPos(LPPOINT p) { if (!p) return FALSE; p->x = g_px; p->y = g_py; return TRUE; }
/* SetCursorPos must emulate a private cursor warp for legacy relative-look
 * engines, without sending WM_MOUSEMOVE or RAWMOUSE. Ignoring cursor warps
 * altogether makes those games repeatedly detect a stale offset and SPIN.
 * Mouse movement is synthesized ONLY inside EV_MOVE. */
static BOOL WINAPI hSetCursorPos(int x, int y)
{
    InterlockedExchange(&g_px, x);
    InterlockedExchange(&g_py, y);
    if (g_sh) { g_sh->pos_x = x; g_sh->pos_y = y; }
    return TRUE;
}
/* GLFW (including Minecraft Java) calls ClipCursor(rect) when the camera
 * grabs the pointer and ClipCursor(NULL) in GUI screens. Mirror ONLY this
 * intent into the per-game mapping. NEVER clip or hide the real OS pointer. */
static BOOL WINAPI hClipCursor(const RECT *r)
{
    if (g_sh) {
        volatile LONG *status = (volatile LONG *)&g_sh->pad[1];
        if (r) InterlockedOr(status, HOOK_CURSOR_HINT_VALID | HOOK_CURSOR_HINT_GRABBED);
        else {
            InterlockedOr(status, HOOK_CURSOR_HINT_VALID);
            InterlockedAnd(status, ~(LONG)HOOK_CURSOR_HINT_GRABBED);
        }
    }
    return TRUE;
}
static BOOL WINAPI hGetCursorInfo(PCURSORINFO ci)
{
    BOOL r = oGetCursorInfo(ci);
    if (r && ci) { ci->ptScreenPos.x = g_px; ci->ptScreenPos.y = g_py; }
    return r;
}
static SHORT WINAPI hGetAsyncKeyState(int vk) { return (vk >= 0 && vk < 256 && (g_keys[vk] & 0x80)) ? (SHORT)0x8000 : 0; }
static SHORT WINAPI hGetKeyState(int vk)
{
    if (vk < 0 || vk >= 256) return 0;
    return (SHORT)((g_keys[vk] & 0x80 ? 0x8000 : 0) | (g_keys[vk] & 1));
}
static BOOL WINAPI hGetKeyboardState(PBYTE b)
{
    if (!b) return FALSE;
    memcpy(b, g_keys, sizeof(g_keys));
    return TRUE;
}
static HWND WINAPI hGetForegroundWindow(void) { return g_hwnd ? g_hwnd : oGetForegroundWindow(); }
static HWND WINAPI hGetActiveWindow(void) { return g_hwnd ? g_hwnd : oGetActiveWindow(); }
static HWND WINAPI hGetFocus(void) { return g_hwnd ? g_hwnd : oGetFocus(); }
static BOOL WINAPI hSetForegroundWindow(HWND h) { (void)h; return TRUE; }
static HWND WINAPI hSetFocus(HWND h) { (void)h; return g_hwnd; }
static HWND WINAPI hSetActiveWindow(HWND h) { (void)h; return g_hwnd; }
static UINT WINAPI hSendInput(UINT n, LPINPUT in, int cb) { (void)in; (void)cb; return n; }
static DWORD WINAPI hGetMessagePos(void) { return MAKELONG((short)g_px, (short)g_py); }

static UINT WINAPI hGetRawInputData(HRAWINPUT h, UINT cmd, LPVOID data, PUINT size, UINT hdr)
{
    RAWINPUT *r = (RAWINPUT *)h;
    if (r >= g_raw && r < g_raw + RAWN) {
        UINT need = (cmd == RID_HEADER) ? (UINT)sizeof(RAWINPUTHEADER) : r->header.dwSize;
        if (!data) { *size = need; return 0; }
        if (*size < need) { SetLastError(ERROR_INSUFFICIENT_BUFFER); return (UINT)-1; }
        EnterCriticalSection(&g_cs);
        memcpy(data, r, need);
        LeaveCriticalSection(&g_cs);
        return need;
    }
    return oGetRawInputData(h, cmd, data, size, hdr);
}

#define PEEK_HOOK(NAME) \
static BOOL WINAPI h##NAME(LPMSG m, HWND hw, UINT mn, UINT mx, UINT rm) \
{ \
    if (GetCurrentThreadId() != g_ui_tid) return o##NAME(m, hw, mn, mx, rm); \
    if (take_synth(m, hw, mn, mx, (rm & PM_REMOVE) != 0)) return TRUE; \
    BOOL r = o##NAME(m, hw, mn, mx, rm); \
    if (r && is_real_input(m->message)) m->message = WM_NULL; \
    return r; \
}
#define GET_HOOK(NAME) \
static BOOL WINAPI h##NAME(LPMSG m, HWND hw, UINT mn, UINT mx) \
{ \
    if (GetCurrentThreadId() != g_ui_tid) return o##NAME(m, hw, mn, mx); \
    if (take_synth(m, hw, mn, mx, TRUE)) return TRUE; \
    BOOL r = o##NAME(m, hw, mn, mx); \
    if (r > 0 && is_real_input(m->message)) m->message = WM_NULL; \
    return r; \
}
PEEK_HOOK(PeekMessageA)
PEEK_HOOK(PeekMessageW)
GET_HOOK(GetMessageA)
GET_HOOK(GetMessageW)

/* subclass: o jogo nunca "perde o foco" */
static LRESULT CALLBACK SubProc(HWND h, UINT m, WPARAM w, LPARAM l)
{
    switch (m) {
    case WM_KILLFOCUS: return 0;
    case WM_ACTIVATEAPP: if (!w) return 0; break;
    case WM_ACTIVATE: if (LOWORD(w) == WA_INACTIVE) return 0; break;
    case WM_NCACTIVATE: if (!w) return TRUE; break;
    }
    return CallWindowProcW(g_orig_proc, h, m, w, l);
}

/* ---------- janela do jogo ---------- */
typedef struct { HWND hwnd; int64_t area; } WindowCandidate;
static BOOL CALLBACK enum_cb(HWND h, LPARAM lp)
{
    DWORD pid; GetWindowThreadProcessId(h, &pid);
    if (pid != GetCurrentProcessId() || !IsWindowVisible(h) || GetWindow(h, GW_OWNER)) return TRUE;
    if (GetWindowLongW(h, GWL_EXSTYLE) & WS_EX_TOOLWINDOW) return TRUE;
    RECT r; GetClientRect(h, &r);
    int64_t area = (int64_t)(r.right - r.left) * (r.bottom - r.top);
    WindowCandidate *best = (WindowCandidate *)lp;
    if (area > best->area) { best->hwnd = h; best->area = area; }
    return TRUE;
}
static HWND find_game_window(void)
{
    WindowCandidate best = { 0 };
    EnumWindows(enum_cb, (LPARAM)&best);
    return best.hwnd;
}

static void client_rect_screen(RECT *rc)
{
    RECT r; GetClientRect(g_hwnd, &r);
    POINT a = { r.left, r.top }, b = { r.right, r.bottom };
    ClientToScreen(g_hwnd, &a); ClientToScreen(g_hwnd, &b);
    rc->left = a.x; rc->top = a.y; rc->right = b.x; rc->bottom = b.y;
}

/* ---------- processa eventos do controlador ---------- */
static int mk_flags(void)
{
    return (g_keys[VK_LBUTTON] & 0x80 ? MK_LBUTTON : 0) |
           (g_keys[VK_RBUTTON] & 0x80 ? MK_RBUTTON : 0) |
           (g_keys[VK_MBUTTON] & 0x80 ? MK_MBUTTON : 0) |
           (g_keys[VK_XBUTTON1] & 0x80 ? MK_XBUTTON1 : 0) |
           (g_keys[VK_XBUTTON2] & 0x80 ? MK_XBUTTON2 : 0) |
           (g_keys[VK_SHIFT] & 0x80 ? MK_SHIFT : 0) |
           (g_keys[VK_CONTROL] & 0x80 ? MK_CONTROL : 0);
}
static LPARAM client_lparam(void)
{
    POINT p = { g_px, g_py };
    ScreenToClient(g_hwnd, &p);
    return MAKELPARAM((short)p.x, (short)p.y);
}
static RAWINPUT *new_raw(DWORD type)
{
    RAWINPUT *r = &g_raw[g_rawi++ % RAWN];
    memset(r, 0, sizeof(*r));
    r->header.dwType = type;
    r->header.dwSize = sizeof(RAWINPUTHEADER) + (type == RIM_TYPEMOUSE ? sizeof(RAWMOUSE) : sizeof(RAWKEYBOARD));
    r->header.hDevice = (HANDLE)(uintptr_t)(type == RIM_TYPEMOUSE ? 0x10001 : 0x10002);
    return r;
}
static void set_key(int vk, int down)
{
    if (vk < 0 || vk > 255) return;
    int was_down = (g_keys[vk] & 0x80) != 0;
    BYTE toggled = g_keys[vk] & 1;
    if (down && !was_down && (vk == VK_CAPITAL || vk == VK_NUMLOCK || vk == VK_SCROLL))
        toggled ^= 1;
    g_keys[vk] = (BYTE)((down ? 0x80 : 0) | toggled);
    if (vk == VK_LSHIFT || vk == VK_RSHIFT) g_keys[VK_SHIFT] = (g_keys[VK_LSHIFT] | g_keys[VK_RSHIFT]) & 0x80;
    if (vk == VK_LCONTROL || vk == VK_RCONTROL) g_keys[VK_CONTROL] = (g_keys[VK_LCONTROL] | g_keys[VK_RCONTROL]) & 0x80;
    if (vk == VK_LMENU || vk == VK_RMENU) g_keys[VK_MENU] = (g_keys[VK_LMENU] | g_keys[VK_RMENU]) & 0x80;
}

static void handle(const Ev *e)
{
    RECT rc;
    EnterCriticalSection(&g_cs);
    switch (e->type) {
    case EV_MOVE: {
        client_rect_screen(&rc);
        LONG x = g_px + e->a, y = g_py + e->b;
        if (x < rc.left) x = rc.left;
        if (x >= rc.right) x = rc.right - 1;
        if (y < rc.top) y = rc.top;
        if (y >= rc.bottom) y = rc.bottom - 1;
        InterlockedExchange(&g_px, x); InterlockedExchange(&g_py, y);
        /* Coalesce up to the previous raw-mouse movement event. Previously
         * every move queued a fresh WM_MOUSEMOVE because the last event was
         * often WM_INPUT, adding latency when mice poll at 500-1000 Hz. */
        MSG *move = NULL;
        if (g_qc > 0) {
            MSG *end = &g_q[(g_qh + g_qc - 1) % QN];
            if (end->message == WM_MOUSEMOVE) move = end;
            else if (g_qc >= 2 && end->message == WM_INPUT) {
                RAWINPUT *r = (RAWINPUT *)end->lParam;
                if (r >= g_raw && r < g_raw + RAWN &&
                    r->header.dwType == RIM_TYPEMOUSE && !r->data.mouse.ulButtons) {
                    MSG *prev = &g_q[(g_qh + g_qc - 2) % QN];
                    if (prev->message == WM_MOUSEMOVE) move = prev;
                }
            }
        }
        if (g_sh->flags & HOOK_MODE_LEGACY) {
            if (move) { move->lParam = client_lparam(); move->wParam = mk_flags(); }
            else q_push_locked(WM_MOUSEMOVE, mk_flags(), client_lparam());
        }
        /* Raw mouse input must be generated ONLY for EV_MOVE from a mouse. */
        if (g_sh->flags & HOOK_MODE_RAW) {
        RAWINPUT *last = NULL;
        for (int i = g_qc - 1; i >= 0 && i >= g_qc - 3; i--) {
            MSG *s = &g_q[(g_qh + i) % QN];
            if (s->message == 0xFF) {
                RAWINPUT *r = (RAWINPUT *)s->lParam;
                if (r->header.dwType == RIM_TYPEMOUSE && !r->data.mouse.ulButtons) last = r;
                break;
            }
        }
        if (last) { last->data.mouse.lLastX += e->a; last->data.mouse.lLastY += e->b; }
        else {
            RAWINPUT *r = new_raw(RIM_TYPEMOUSE);
            r->data.mouse.lLastX = e->a; r->data.mouse.lLastY = e->b;
            q_push_locked(0xFF, RIM_INPUT, (LPARAM)r);
        }
        }
        g_sh->pos_x = g_px; g_sh->pos_y = g_py;
        break; }
    case EV_BTN: {
        static const UINT dn[5] = { WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN,
                                    WM_XBUTTONDOWN, WM_XBUTTONDOWN };
        static const int vk[5] = { VK_LBUTTON, VK_RBUTTON, VK_MBUTTON, VK_XBUTTON1, VK_XBUTTON2 };
        static const USHORT rdn[5] = { 0x01, 0x04, 0x10, 0x40, 0x100 };
        int i = e->a; if (i < 0 || i >= 5) break;
        set_key(vk[i], e->b ? 1 : 0);
        WPARAM wp = (WPARAM)mk_flags();
        if (i >= 3) wp = MAKEWPARAM(mk_flags(), i == 3 ? XBUTTON1 : XBUTTON2);
        if (g_sh->flags & HOOK_MODE_LEGACY)
            q_push_locked(dn[i] + (e->b ? 0 : 1), wp, client_lparam());
        if (g_sh->flags & HOOK_MODE_RAW) {
        RAWINPUT *r = new_raw(RIM_TYPEMOUSE);
        if (i >= 3) {
            r->data.mouse.usButtonFlags = e->b ? RI_MOUSE_BUTTON_4_DOWN : RI_MOUSE_BUTTON_4_UP;
            if (i == 4) r->data.mouse.usButtonFlags = e->b ? RI_MOUSE_BUTTON_5_DOWN : RI_MOUSE_BUTTON_5_UP;
        } else r->data.mouse.usButtonFlags = e->b ? rdn[i] : rdn[i] << 1;
        q_push_locked(0xFF, RIM_INPUT, (LPARAM)r);
        }
        break; }
    case EV_WHEEL: {
        POINT p = { g_px, g_py };
        int horizontal = e->b != 0;
        if (g_sh->flags & HOOK_MODE_LEGACY)
            q_push_locked(horizontal ? WM_MOUSEHWHEEL : WM_MOUSEWHEEL,
                          MAKEWPARAM(mk_flags(), (short)e->a), MAKELPARAM((short)p.x, (short)p.y));
        if (g_sh->flags & HOOK_MODE_RAW) {
        RAWINPUT *r = new_raw(RIM_TYPEMOUSE);
        r->data.mouse.usButtonFlags = horizontal ? RI_MOUSE_HWHEEL : RI_MOUSE_WHEEL;
        r->data.mouse.usButtonData = (USHORT)(short)e->a;
        q_push_locked(0xFF, RIM_INPUT, (LPARAM)r);
        }
        break; }
    case EV_KEY: {
        int vkc = e->a, down = e->b, sc = e->c & 0xFF;
        /* Strict channel separation: only physical mouse input may touch
         * VK_LBUTTON..VK_XBUTTON2 or the virtual mouse state. */
        if (vkc >= VK_LBUTTON && vkc <= VK_XBUTTON2) break;
        int ext = (e->c >> 8) & 1, e1 = (e->c >> 9) & 1;
        int was_down = (vkc >= 0 && vkc < 256) ? g_keys[vkc] : 0;
        set_key(vkc, down);
        int alt = g_keys[VK_MENU] != 0;
        int sys = alt || vkc == VK_MENU || vkc == VK_LMENU || vkc == VK_RMENU;
        UINT msg = sys ? (down ? WM_SYSKEYDOWN : WM_SYSKEYUP) : (down ? WM_KEYDOWN : WM_KEYUP);
        LPARAM lp = 1 | ((LPARAM)sc << 16) | ((LPARAM)ext << 24) |
                    ((LPARAM)alt << 29) | (was_down ? (1LL << 30) : 0) |
                    (down ? 0 : 0xC0000000);
        q_push_locked(msg, (WPARAM)vkc, lp);
        /* IMPORTANT v7: Never inject a synthetic RAWKEYBOARD WM_INPUT by default.
         * Many games (including GLFW-based Minecraft builds) register raw
         * MOUSE input only. GLFW's WM_INPUT mouse branch may read the mouse
         * union without checking header.dwType. That interprets a keyboard
         * MakeCode (e.g. W=0x11 or S=0x1F) as RAWMOUSE.usFlags, producing
         * absolute cursor movement / camera spin on key presses. A/D happen
         * to have even set-1 scan codes (0x1E/0x20) and behaved differently.
         * Standard WM_KEYDOWN/WM_KEYUP above remains for all keyboard keys.
         * A future raw-keyboard option must require opt-in AND confirmation
         * that the receiving window registered keyboard raw input separately.
         */
        break; }
    case EV_EXIT: InterlockedExchange(&g_exit, 1); break;
    }
    LeaveCriticalSection(&g_cs);
}

/* ---------- boot ---------- */
/* Before the shared mapping opens, status bytes cannot reach the controller.
 * Write a small, non-sensitive diagnostic in the same user profile instead. */
static void startup_diagnostic(const char *phase, DWORD winerr)
{
    char folder[MAX_PATH], path[MAX_PATH], line[192];
    DWORD n = GetEnvironmentVariableA("LOCALAPPDATA", folder, sizeof(folder));
    if (!n || n >= sizeof(folder)) return;
    int path_len = snprintf(path, sizeof(path), "%s\\CoopInput\\hook_startup_%lu.log",
                            folder, GetCurrentProcessId());
    if (path_len < 0 || path_len >= (int)sizeof(path)) return;
    HANDLE f = CreateFileA(path, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                           NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return;
    int count = snprintf(line, sizeof(line), "pid=%lu phase=%s winerr=%lu\r\n",
                         GetCurrentProcessId(), phase, winerr);
    if (count > 0 && count < (int)sizeof(line)) {
        DWORD written;
        WriteFile(f, line, (DWORD)count, &written, NULL);
    }
    CloseHandle(f);
}

static void fail_boot(unsigned stage, HANDLE fm)
{
    if (g_sh && g_sh->magic == MAGIC) boot_status(stage);
    if (g_mh_initialized) {
        MH_DisableHook(MH_ALL_HOOKS);
        MH_Uninitialize();
    }
    if (g_cs_initialized) DeleteCriticalSection(&g_cs);
    if (g_sh) { UnmapViewOfFile(g_sh); g_sh = NULL; }
    if (fm) CloseHandle(fm);
    /* Unload on failure: a second Attach must be able to restart boot(). */
    FreeLibraryAndExitThread(g_mod, stage);
}

#define HK(NAME) do { \
    if (MH_CreateHookApi(L"user32", #NAME, (LPVOID)h##NAME, (LPVOID *)&o##NAME) != MH_OK) \
        fail_boot(BOOT_CREATE_HOOK_ERROR, NULL); \
} while (0)

static DWORD WINAPI boot(LPVOID unused)
{
    (void)unused;
    char name[64];
    startup_diagnostic("thread-started", 0);
    snprintf(name, sizeof name, "CoopInput_%lu", GetCurrentProcessId());
    /* The hook needs read/write access, not standard rights such as WRITE_DAC.
     * Requesting ALL_ACCESS can fail on mappings created by other processes. */
    HANDLE fm = OpenFileMappingA(FILE_MAP_READ | FILE_MAP_WRITE, FALSE, name);
    if (!fm) {
        startup_diagnostic("OpenFileMappingA", GetLastError());
        fail_boot(0x86, NULL);
    }
    g_sh = (Shared *)MapViewOfFile(fm, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, sizeof(Shared));
    if (!g_sh) {
        startup_diagnostic("MapViewOfFile", GetLastError());
        fail_boot(0x87, fm);
    }
    if (g_sh->magic != MAGIC) {
        startup_diagnostic("mapping-magic-mismatch", 0);
        fail_boot(0x87, fm);
    }
    CloseHandle(fm);  /* The mapped view keeps the shared section alive. */
    startup_diagnostic("shared-memory-ready", 0);
    g_sh->pad[1] = HOOK_MODE_RAW | HOOK_MODE_LEGACY;
    boot_status(BOOT_MEMORY_READY);
    InitializeCriticalSection(&g_cs);
    g_cs_initialized = 1;

    boot_status(BOOT_WAIT_WINDOW);
    for (int i = 0; i < 100 && !g_hwnd; i++) { g_hwnd = find_game_window(); if (!g_hwnd) Sleep(100); }
    if (!g_hwnd) fail_boot(BOOT_NO_WINDOW, NULL);
    boot_status(BOOT_WINDOW_FOUND);
    g_ui_tid = GetWindowThreadProcessId(g_hwnd, NULL);

    RECT rc; client_rect_screen(&rc);
    g_px = (rc.left + rc.right) / 2; g_py = (rc.top + rc.bottom) / 2;
    g_sh->pos_x = g_px; g_sh->pos_y = g_py;

    /* Initialize telemetry BEFORE any hook can receive ClipCursor calls. */
    if (MH_Initialize() != MH_OK) fail_boot(BOOT_MINHOOK_ERROR, NULL);
    g_mh_initialized = 1;
    boot_status(BOOT_INSTALL_HOOKS);
    HK(GetCursorPos); HK(SetCursorPos); HK(ClipCursor); HK(GetCursorInfo);
    HK(GetAsyncKeyState); HK(GetKeyState); HK(GetKeyboardState);
    HK(GetForegroundWindow); HK(GetActiveWindow); HK(GetFocus);
    HK(SetForegroundWindow); HK(SetFocus); HK(SetActiveWindow);
    HK(SendInput); HK(GetMessagePos); HK(GetRawInputData);
    HK(PeekMessageA); HK(PeekMessageW); HK(GetMessageA); HK(GetMessageW);
    if (MH_EnableHook(MH_ALL_HOOKS) != MH_OK) fail_boot(BOOT_ENABLE_HOOK_ERROR, NULL);
    SetLastError(ERROR_SUCCESS);
    g_orig_proc = (WNDPROC)SetWindowLongPtrW(g_hwnd, GWLP_WNDPROC, (LONG_PTR)SubProc);
    if (!g_orig_proc) fail_boot(BOOT_SUBCLASS_ERROR, NULL);

    EnterCriticalSection(&g_cs);
    q_push_locked(WM_ACTIVATEAPP, 1, g_ui_tid);
    q_push_locked(WM_ACTIVATE, WA_ACTIVE, 0);
    q_push_locked(WM_SETFOCUS, 0, 0);
    LeaveCriticalSection(&g_cs);

    uint32_t tail = g_sh->head;
    DWORD last_beat = 0, last_wake = 0;
    g_sh->pad[0] = HOOK_ABI_VERSION; /* handshake, set only after hooks installed */
    g_sh->dll_tick = GetTickCount();
    boot_status(BOOT_READY);
    g_sh->flags |= 1;
    while (!g_exit) {
        int got = 0;
        while (tail != g_sh->head) {
            if ((uint32_t)(g_sh->head - tail) > RING) tail = g_sh->head - RING / 2;
            Ev e = g_sh->ev[tail % RING]; tail++;
            handle(&e); got = 1;
        }
        DWORD now = GetTickCount();
        if (now - last_beat > 250) { g_sh->dll_tick = now; last_beat = now; }
        if ((got || g_qc) && now - last_wake > 2) { PostThreadMessageW(g_ui_tid, WM_NULL, 0, 0); last_wake = now; }
        Sleep(1);
    }
    /* desanexar */
    MH_DisableHook(MH_ALL_HOOKS);
    Sleep(300);
    if (g_orig_proc) SetWindowLongPtrW(g_hwnd, GWLP_WNDPROC, (LONG_PTR)g_orig_proc);
    g_sh->flags &= ~1u;
    g_sh->pad[0] = 0;
    Sleep(300);
    MH_Uninitialize();
    UnmapViewOfFile(g_sh);
    g_sh = NULL;
    DeleteCriticalSection(&g_cs);
    FreeLibraryAndExitThread(g_mod, 0);
    return 0;
}

BOOL APIENTRY DllMain(HINSTANCE h, DWORD reason, LPVOID res)
{
    (void)res;
    if (reason == DLL_PROCESS_ATTACH) {
        g_mod = h;
        DisableThreadLibraryCalls(h);
        HANDLE thread = CreateThread(NULL, 0, boot, NULL, 0, NULL);
        if (!thread) return FALSE;
        CloseHandle(thread);
    }
    return TRUE;
}
