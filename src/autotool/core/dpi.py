"""Windows DPI awareness and Win32 screen/cursor helper functions."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_DPI_INITIALIZED = False


def enable_per_monitor_dpi_awareness() -> None:
    """Enable Per-Monitor V2 DPI awareness so UIA, Win32, and Qt coordinates align."""
    global _DPI_INITIALIZED
    if _DPI_INITIALIZED:
        return
    _DPI_INITIALIZED = True
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = (DPI_AWARENESS_CONTEXT)-4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def get_cursor_pos() -> tuple[int, int]:
    """Return physical screen cursor coordinates (x, y)."""
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def get_virtual_screen_bounds() -> tuple[int, int, int, int]:
    """Return (left, top, width, height) of the entire virtual desktop in physical pixels."""
    user32 = ctypes.windll.user32
    left = user32.GetSystemMetrics(76)    # SM_XVIRTUALSCREEN
    top = user32.GetSystemMetrics(77)     # SM_YVIRTUALSCREEN
    width = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
    height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
    if width <= 0 or height <= 0:
        width = user32.GetSystemMetrics(0)
        height = user32.GetSystemMetrics(1)
        left, top = 0, 0
    return int(left), int(top), int(width), int(height)


def is_vk_pressed(vk_code: int) -> bool:
    """Check whether a virtual key is currently held down."""
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk_code) & 0x8000)


def bring_window_to_front(hwnd: int) -> None:
    """Restore if minimized and attempt to bring the window to the foreground."""
    if not hwnd:
        return
    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def get_root_hwnd(hwnd: int) -> int:
    """Return the top-level root window HWND for any child HWND (GA_ROOT = 2)."""
    if not hwnd:
        return 0
    try:
        root = int(ctypes.windll.user32.GetAncestor(int(hwnd), 2) or 0)
        return root or int(hwnd)
    except Exception:
        return int(hwnd)


def get_window_rect_win32(hwnd: int) -> tuple[int, int, int, int]:
    """Return (left, top, right, bottom) physical rect of a window HWND."""
    rect = wintypes.RECT()
    if ctypes.windll.user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    return 0, 0, 0, 0


def enum_visible_top_windows() -> list[dict]:
    """Enumerate all visible top-level desktop windows via Win32 EnumWindows.

    Much faster and more reliable than UIA GetRootControl().GetChildren(), especially for
    Electron/Chromium apps and worker threads.
    """
    user32 = ctypes.windll.user32
    results: list[dict] = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return True

        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        title_len = user32.GetWindowTextLengthW(hwnd)
        title_buf = ctypes.create_unicode_buffer(title_len + 1)
        if title_len > 0:
            user32.GetWindowTextW(hwnd, title_buf, title_len + 1)

        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)

        results.append(
            {
                "hwnd": int(hwnd),
                "pid": int(pid.value),
                "title": title_buf.value,
                "class_name": cls_buf.value,
                "rect": (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)),
            }
        )
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return results
