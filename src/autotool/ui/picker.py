"""Control picker widgets: Spy++-style draggable crosshair and Ctrl-lock hover capture."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QPushButton

from autotool.core.dpi import get_cursor_pos, is_vk_pressed
from autotool.core.inspector import UIAInspector
from autotool.core.win32_input import VK_MAP
from autotool.models.schema import CapturedControl
from autotool.ui.overlay import HighlightOverlay


def _format_overlay_label(ctrl: CapturedControl) -> str:
    parts = [ctrl.control_type or "Control"]
    if ctrl.control_name:
        parts.append(f'"{ctrl.control_name[:22]}"')
    elif ctrl.automation_id:
        parts.append(f"#{ctrl.automation_id[:22]}")
    parts.append(f"{ctrl.control_rect.width}×{ctrl.control_rect.height}")
    return " | ".join(parts)


class CrosshairButton(QPushButton):
    """Press and drag this button onto any desktop control, then release to capture."""

    control_captured = Signal(object)  # CapturedControl
    hover_updated = Signal(object)     # CapturedControl | None

    def __init__(self, inspector: UIAInspector, overlay: HighlightOverlay, text: str = "🎯 按住拖拽抓捕") -> None:
        super().__init__(text)
        self.inspector = inspector
        self.overlay = overlay
        self._dragging = False
        self._last_pos: tuple[int, int] | None = None

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(75)
        self._poll_timer.timeout.connect(self._on_poll)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_pos = None
            self.setText("🎯 拖动到目标控件并松开…")
            self.grabMouse(Qt.CursorShape.CrossCursor)
            self._poll_timer.start()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._poll_timer.stop()
            self.releaseMouse()
            self.setText("🎯 按住拖拽抓捕")
            self.overlay.hide()

            x, y = get_cursor_pos()
            captured = self.inspector.inspect_point(x, y, include_tree_path=True)
            if captured is not None:
                self.overlay.show_rect(
                    captured.control_rect,
                    label="已捕获: " + _format_overlay_label(captured),
                    color="#10b981",
                    target_point=(captured.click_x, captured.click_y),
                    auto_hide_ms=900,
                )
                self.control_captured.emit(captured)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _on_poll(self) -> None:
        if not self._dragging:
            return
        pos = get_cursor_pos()
        if pos == self._last_pos:
            return
        self._last_pos = pos
        hovered = self.inspector.inspect_point(pos[0], pos[1], include_tree_path=False)
        self.hover_updated.emit(hovered)
        if hovered is not None:
            self.overlay.show_rect(
                hovered.control_rect,
                label=_format_overlay_label(hovered),
                color="#ff3b5c",
                target_point=pos,
            )
        else:
            self.overlay.hide()


class HoverCaptureController(QObject):
    """Toggleable hover-capture mode: hover freely, press Ctrl to lock or Esc to cancel."""

    control_captured = Signal(object)  # CapturedControl
    state_changed = Signal(bool)       # active

    def __init__(self, inspector: UIAInspector, overlay: HighlightOverlay) -> None:
        super().__init__()
        self.inspector = inspector
        self.overlay = overlay
        self._active = False
        self._last_pos: tuple[int, int] | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(70)
        self._timer.timeout.connect(self._tick)

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        self._last_pos = None
        self._timer.start()
        self.state_changed.emit(True)

    def stop(self) -> None:
        if not self._active:
            return
        self._active = False
        self._timer.stop()
        self.overlay.hide()
        self.state_changed.emit(False)

    def toggle(self) -> None:
        if self._active:
            self.stop()
        else:
            self.start()

    def _tick(self) -> None:
        if not self._active:
            return
        if is_vk_pressed(VK_MAP["esc"]):
            self.stop()
            return
        if is_vk_pressed(VK_MAP["ctrl"]):
            x, y = get_cursor_pos()
            self.stop()
            captured = self.inspector.inspect_point(x, y, include_tree_path=True)
            if captured is not None:
                self.overlay.show_rect(
                    captured.control_rect,
                    label="已捕获: " + _format_overlay_label(captured),
                    color="#10b981",
                    target_point=(captured.click_x, captured.click_y),
                    auto_hide_ms=900,
                )
                self.control_captured.emit(captured)
            return

        pos = get_cursor_pos()
        if pos == self._last_pos:
            return
        self._last_pos = pos
        hovered = self.inspector.inspect_point(pos[0], pos[1], include_tree_path=False)
        if hovered is not None:
            self.overlay.show_rect(
                hovered.control_rect,
                label="[按 Ctrl 锁定 | Esc 取消] " + _format_overlay_label(hovered),
                color="#38bdf8",
                target_point=pos,
            )
        else:
            self.overlay.hide()
