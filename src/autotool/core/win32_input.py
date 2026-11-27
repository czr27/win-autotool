"""Native Win32 SendInput driver for mouse, keyboard, and Unicode/keystroke text input."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from autotool.core.dpi import get_cursor_pos, get_virtual_screen_bounds

# Input types
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

# Mouse flags
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000

# Keyboard flags
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("_union",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_union", _INPUT_UNION),
    ]


_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_SendInput = _user32.SendInput
_SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_SendInput.restype = wintypes.UINT

# Virtual-Key lookup table
VK_MAP: dict[str, int] = {
    "backspace": 0x08,
    "bs": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "menu": 0x12,
    "pause": 0x13,
    "capslock": 0x14,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pgup": 0x21,
    "pagedown": 0x22,
    "pgdn": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "printscreen": 0x2C,
    "insert": 0x2D,
    "ins": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
    "win": 0x5B,
    "lwin": 0x5B,
    "rwin": 0x5C,
    "lshift": 0xA0,
    "rshift": 0xA1,
    "lctrl": 0xA2,
    "rctrl": 0xA3,
    "lalt": 0xA4,
    "ralt": 0xA5,
    ";": 0xBA,
    "=": 0xBB,
    ",": 0xBC,
    "-": 0xBD,
    ".": 0xBE,
    "/": 0xBF,
    "`": 0xC0,
    "[": 0xDB,
    "\\": 0xDC,
    "]": 0xDD,
    "'": 0xDE,
}
for _d in range(10):
    VK_MAP[str(_d)] = 0x30 + _d
for _i in range(26):
    VK_MAP[chr(ord("a") + _i)] = 0x41 + _i
for _f in range(1, 13):
    VK_MAP[f"f{_f}"] = 0x6F + _f

EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0xA3, 0xA5}

_MOUSE_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


def parse_key_combo(key_spec: str) -> list[int]:
    """Parse a key or combo like 'ctrl+shift+a' or 'enter' into VK codes."""
    parts = [p.strip().lower() for p in key_spec.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"无效按键名称: {key_spec!r}")
    vks: list[int] = []
    for p in parts:
        if p in VK_MAP:
            vks.append(VK_MAP[p])
        elif len(p) == 1:
            res = _user32.VkKeyScanW(ord(p))
            if res == -1:
                raise ValueError(f"无法识别按键: {p!r}")
            vks.append(res & 0xFF)
        else:
            raise ValueError(f"未知按键名称: {p!r}")
    return vks


def _send_inputs(items: list[INPUT]) -> None:
    if not items:
        return
    arr = (INPUT * len(items))(*items)
    _SendInput(len(items), arr, ctypes.sizeof(INPUT))


def _set_clipboard_text(text: str) -> None:
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    _kernel32.GlobalAlloc.restype = ctypes.c_void_p
    _kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    _kernel32.GlobalLock.restype = ctypes.c_void_p
    _kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    _user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]

    encoded = (text + "\0").encode("utf-16-le")
    if not _user32.OpenClipboard(None):
        return
    try:
        _user32.EmptyClipboard()
        h_mem = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        if h_mem:
            ptr = _kernel32.GlobalLock(h_mem)
            if ptr:
                ctypes.memmove(ptr, encoded, len(encoded))
                _kernel32.GlobalUnlock(h_mem)
            _user32.SetClipboardData(CF_UNICODETEXT, h_mem)
    finally:
        _user32.CloseClipboard()


class Win32Input:
    """Stateful Win32 input driver with safety tracking for held keys/buttons."""

    def __init__(self) -> None:
        self._held_buttons: set[str] = set()
        self._held_vks: set[int] = set()

    def _to_abs_coords(self, x: int, y: int) -> tuple[int, int]:
        vleft, vtop, vwidth, vheight = get_virtual_screen_bounds()
        nx = int(round(((x - vleft) * 65535) / max(1, vwidth - 1)))
        ny = int(round(((y - vtop) * 65535) / max(1, vheight - 1)))
        return max(0, min(65535, nx)), max(0, min(65535, ny))

    def move_to(self, x: int, y: int, duration_ms: int = 0) -> None:
        if duration_ms > 0:
            sx, sy = get_cursor_pos()
            steps = max(2, min(60, duration_ms // 12))
            step_sleep = (duration_ms / 1000.0) / steps
            for i in range(1, steps + 1):
                t = i / steps
                cx = int(round(sx + (x - sx) * t))
                cy = int(round(sy + (y - sy) * t))
                self._move_instant(cx, cy)
                time.sleep(step_sleep)
        else:
            self._move_instant(x, y)

    def _move_instant(self, x: int, y: int) -> None:
        nx, ny = self._to_abs_coords(x, y)
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(
            dx=nx,
            dy=ny,
            mouseData=0,
            dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
            time=0,
            dwExtraInfo=0,
        )
        _send_inputs([inp])

    def mouse_down(self, button: str = "left") -> None:
        down_flag, _ = _MOUSE_FLAGS[button]
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=down_flag, time=0, dwExtraInfo=0)
        _send_inputs([inp])
        self._held_buttons.add(button)

    def mouse_up(self, button: str = "left") -> None:
        _, up_flag = _MOUSE_FLAGS[button]
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=up_flag, time=0, dwExtraInfo=0)
        _send_inputs([inp])
        self._held_buttons.discard(button)

    def mouse_click(self, button: str = "left") -> None:
        self.mouse_down(button)
        time.sleep(0.02)
        self.mouse_up(button)

    def mouse_double_click(self, button: str = "left") -> None:
        self.mouse_click(button)
        time.sleep(0.06)
        self.mouse_click(button)

    def mouse_hold(self, button: str = "left", duration_ms: int = 500) -> None:
        self.mouse_down(button)
        try:
            time.sleep(max(0, duration_ms) / 1000.0)
        finally:
            self.mouse_up(button)

    def _send_vk(self, vk: int, key_up: bool = False) -> None:
        scan = _user32.MapVirtualKeyW(vk, 0)
        flags = 0
        if vk in EXTENDED_VKS:
            flags |= KEYEVENTF_EXTENDEDKEY
        if key_up:
            flags |= KEYEVENTF_KEYUP
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
        _send_inputs([inp])

    def key_down(self, key_spec: str) -> None:
        for vk in parse_key_combo(key_spec):
            self._send_vk(vk, key_up=False)
            self._held_vks.add(vk)

    def key_up(self, key_spec: str) -> None:
        for vk in reversed(parse_key_combo(key_spec)):
            self._send_vk(vk, key_up=True)
            self._held_vks.discard(vk)

    def key_press(self, key_spec: str) -> None:
        vks = parse_key_combo(key_spec)
        for vk in vks:
            self._send_vk(vk, key_up=False)
            self._held_vks.add(vk)
        time.sleep(0.02)
        for vk in reversed(vks):
            self._send_vk(vk, key_up=True)
            self._held_vks.discard(vk)

    def text_input(self, text: str, use_clipboard: bool = False) -> None:
        """Input arbitrary Unicode text via SendInput(KEYEVENTF_UNICODE) or clipboard Ctrl+V."""
        if not text:
            return
        if use_clipboard:
            _set_clipboard_text(text)
            time.sleep(0.03)
            self.key_press("ctrl+v")
            return

        inputs: list[INPUT] = []
        for ch in text:
            if ch == "\n":
                if inputs:
                    _send_inputs(inputs)
                    inputs.clear()
                self.key_press("enter")
                continue
            if ch == "\r":
                continue
            # Encode to UTF-16 code units to support full Unicode (including emojis)
            utf16 = ch.encode("utf-16-le")
            for i in range(0, len(utf16), 2):
                code_unit = int.from_bytes(utf16[i : i + 2], "little")
                down = INPUT(type=INPUT_KEYBOARD)
                down.ki = KEYBDINPUT(
                    wVk=0, wScan=code_unit, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=0
                )
                up = INPUT(type=INPUT_KEYBOARD)
                up.ki = KEYBDINPUT(
                    wVk=0,
                    wScan=code_unit,
                    dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                    time=0,
                    dwExtraInfo=0,
                )
                inputs.append(down)
                inputs.append(up)
        if inputs:
            _send_inputs(inputs)

    def text_press(self, text: str, interval_ms: int = 25) -> None:
        """Simulate physical key-by-key typing for a string."""
        delay_s = max(0, interval_ms) / 1000.0
        for ch in text:
            if ch == "\n":
                self.key_press("enter")
            elif ch == "\t":
                self.key_press("tab")
            elif ch == "\r":
                continue
            else:
                res = _user32.VkKeyScanW(ord(ch))
                if res == -1:
                    # Fallback to Unicode for non-ASCII characters (e.g. Chinese)
                    self.text_input(ch, use_clipboard=False)
                else:
                    vk = res & 0xFF
                    shift_state = (res >> 8) & 0xFF
                    mods: list[int] = []
                    if shift_state & 1:
                        mods.append(VK_MAP["shift"])
                    if shift_state & 2:
                        mods.append(VK_MAP["ctrl"])
                    if shift_state & 4:
                        mods.append(VK_MAP["alt"])
                    for m in mods:
                        self._send_vk(m, key_up=False)
                        self._held_vks.add(m)
                    self._send_vk(vk, key_up=False)
                    time.sleep(0.01)
                    self._send_vk(vk, key_up=True)
                    for m in reversed(mods):
                        self._send_vk(m, key_up=True)
                        self._held_vks.discard(m)
            if delay_s > 0:
                time.sleep(delay_s)

    def release_all(self) -> None:
        """Release any keys or mouse buttons still held down (safety cleanup)."""
        for btn in list(self._held_buttons):
            try:
                self.mouse_up(btn)
            except Exception:
                pass
        for vk in list(self._held_vks):
            try:
                self._send_vk(vk, key_up=True)
            except Exception:
                pass
            self._held_vks.discard(vk)
