"""Windows global hotkey manager using RegisterHotKey + Qt nativeEventFilter."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal
from PySide6.QtWidgets import QApplication

from autotool.core.win32_input import VK_MAP

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_MOD_MAP: dict[str, int] = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}


def parse_global_hotkey(spec: str) -> tuple[int, int, str]:
    """Parse a hotkey string like 'shift+f2' into (modifiers_mask, vk_code, display_label)."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError("快捷键不能为空")

    mods = 0
    main_key: str | None = None
    display_parts: list[str] = []

    for p in parts:
        if p in _MOD_MAP:
            mods |= _MOD_MAP[p]
            label = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}[p]
            if label not in display_parts:
                display_parts.append(label)
        else:
            if main_key is not None:
                raise ValueError(f"只能包含一个主键，发现多个：{main_key} 和 {p}")
            main_key = p

    if main_key is None:
        raise ValueError("快捷键缺少主键（如 F2、R 等）")

    if main_key in VK_MAP:
        vk = VK_MAP[main_key]
    else:
        raise ValueError(f"不支持的按键名称：{main_key}")

    display_parts.append(main_key.upper())
    return mods, vk, "+".join(display_parts)


class _WinHotkeyEventFilter(QAbstractNativeEventFilter):
    def __init__(self, hotkey_id: int, callback) -> None:
        super().__init__()
        self.hotkey_id = hotkey_id
        self.callback = callback

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if event_type in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == self.hotkey_id:
                self.callback()
                return True, 0
        return False, 0


class GlobalHotkeyManager(QObject):
    """Registers a single system-wide Windows hotkey and emits `triggered` on press."""

    triggered = Signal()

    def __init__(self, hotkey_id: int = 0x5A01, parent=None) -> None:
        super().__init__(parent)
        self._hotkey_id = hotkey_id
        self._registered = False
        self._display_label = ""
        self._filter = _WinHotkeyEventFilter(hotkey_id, self.triggered.emit)
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._filter)

    @property
    def display_label(self) -> str:
        return self._display_label

    def register(self, spec: str) -> tuple[bool, str]:
        try:
            mods, vk, label = parse_global_hotkey(spec)
        except ValueError as exc:
            return False, str(exc)

        self.unregister()
        ok = bool(
            ctypes.windll.user32.RegisterHotKey(
                None, self._hotkey_id, mods | MOD_NOREPEAT, vk
            )
        )
        if not ok:
            return False, f"全局快捷键 {label} 注册失败（可能已被其他程序占用）"

        self._registered = True
        self._display_label = label
        return True, label

    def unregister(self) -> None:
        if self._registered:
            try:
                ctypes.windll.user32.UnregisterHotKey(None, self._hotkey_id)
            except Exception:
                pass
            self._registered = False

    def cleanup(self) -> None:
        self.unregister()
        app = QApplication.instance()
        if app is not None:
            app.removeNativeEventFilter(self._filter)
