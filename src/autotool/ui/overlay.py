"""Click-through transparent overlay window to highlight captured or resolved controls."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from autotool.models.schema import Rect


def _physical_to_logical(x: int, y: int) -> tuple[int, int, float]:
    """Convert physical Win32 screen coordinates into Qt logical coordinates."""
    screens = QGuiApplication.screens()
    for scr in screens:
        dpr = scr.devicePixelRatio() or 1.0
        geo = scr.geometry()
        phys_left = int(round(geo.left() * dpr))
        phys_top = int(round(geo.top() * dpr))
        phys_right = phys_left + int(round(geo.width() * dpr))
        phys_bottom = phys_top + int(round(geo.height() * dpr))
        if phys_left <= x <= phys_right and phys_top <= y <= phys_bottom:
            lx = geo.left() + int(round((x - phys_left) / dpr))
            ly = geo.top() + int(round((y - phys_top) / dpr))
            return lx, ly, dpr
    primary = QGuiApplication.primaryScreen()
    dpr = primary.devicePixelRatio() if primary else 1.0
    return int(round(x / dpr)), int(round(y / dpr)), dpr


class HighlightOverlay(QWidget):
    """Topmost click-through highlight box with info badge and optional target crosshair."""

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._border_color = QColor("#ff3b5c")
        self._label = ""
        self._box_rect = QRect()
        self._crosshair: QPoint | None = None

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_rect(
        self,
        rect: Rect,
        label: str = "",
        color: str = "#ff3b5c",
        target_point: tuple[int, int] | None = None,
        auto_hide_ms: int = 0,
    ) -> None:
        if rect.width <= 0 or rect.height <= 0:
            self.hide()
            return

        lx1, ly1, dpr = _physical_to_logical(rect.left, rect.top)
        lw = max(4, int(round(rect.width / dpr)))
        lh = max(4, int(round(rect.height / dpr)))

        pad_top = 26 if label else 4
        pad = 4
        win_x = lx1 - pad
        win_y = ly1 - pad_top
        win_w = max(lw + pad * 2, 240 if label else lw + pad * 2)
        win_h = lh + pad_top + pad

        self._border_color = QColor(color)
        self._label = label
        self._box_rect = QRect(pad, pad_top, lw, lh)

        if target_point is not None:
            tx, ty, _ = _physical_to_logical(target_point[0], target_point[1])
            self._crosshair = QPoint(tx - win_x, ty - win_y)
        else:
            self._crosshair = None

        self.setGeometry(win_x, win_y, win_w, win_h)
        self.show()
        self.raise_()
        self.update()

        self._hide_timer.stop()
        if auto_hide_ms > 0:
            self._hide_timer.start(auto_hide_ms)

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._box_rect.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        fill = QColor(self._border_color)
        fill.setAlpha(28)
        painter.fillRect(self._box_rect, fill)

        pen = QPen(self._border_color, 2.5)
        painter.setPen(pen)
        painter.drawRect(self._box_rect)

        if self._label:
            font = QFont("Microsoft YaHei UI", 9, QFont.Weight.Bold)
            painter.setFont(font)
            fm = painter.fontMetrics()
            text_w = min(self.width() - 8, fm.horizontalAdvance(self._label) + 14)
            badge = QRect(self._box_rect.left(), 2, max(60, text_w), 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._border_color)
            painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(
                badge.adjusted(6, 0, -6, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                fm.elidedText(self._label, Qt.TextElideMode.ElideRight, badge.width() - 12),
            )

        if self._crosshair is not None:
            cx, cy = self._crosshair.x(), self._crosshair.y()
            c_pen = QPen(QColor("#22c55e"), 2.0)
            painter.setPen(c_pen)
            painter.drawLine(cx - 9, cy, cx + 9, cy)
            painter.drawLine(cx, cy - 9, cx, cy + 9)
            painter.drawEllipse(QPoint(cx, cy), 4, 4)
