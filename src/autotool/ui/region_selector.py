"""Full-desktop interactive screen region snipper for image-template localization."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from autotool.core.image_matcher import capture_region_png_bytes
from autotool.models.schema import Rect


def _logical_rect_to_physical(logical_rect: QRect) -> Rect:
    """Convert a Qt logical global QRect into physical screen pixels."""
    center = logical_rect.center()
    scr = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
    dpr = scr.devicePixelRatio() if scr else 1.0
    geo = scr.geometry() if scr else QRect(0, 0, 1920, 1080)
    phys_origin_x = int(round(geo.left() * dpr))
    phys_origin_y = int(round(geo.top() * dpr))

    left = phys_origin_x + int(round((logical_rect.left() - geo.left()) * dpr))
    top = phys_origin_y + int(round((logical_rect.top() - geo.top()) * dpr))
    right = phys_origin_x + int(round((logical_rect.right() + 1 - geo.left()) * dpr))
    bottom = phys_origin_y + int(round((logical_rect.bottom() + 1 - geo.top()) * dpr))
    return Rect(left=left, top=top, right=right, bottom=bottom)


class ScreenRegionSelector(QWidget):
    """Translucent fullscreen overlay allowing the user to drag-select a screen region."""

    region_captured = Signal(object, bytes)  # (physical Rect, png_bytes)
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._start: QPoint | None = None
        self._current: QPoint | None = None
        self._virtual_geo = QRect()

    def start_capture(self) -> None:
        """Cover all monitors and begin interactive drag selection."""
        union = QRect()
        for scr in QGuiApplication.screens():
            union = union.united(scr.geometry())
        self._virtual_geo = union
        self._start = None
        self._current = None
        self.setGeometry(union)
        self.show()
        self.raise_()
        self.activateWindow()

    def _selection_local_rect(self) -> QRect:
        if self._start is None or self._current is None:
            return QRect()
        return QRect(self._start, self._current).normalized()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            self.cancelled.emit()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self.hide()
            self.cancelled.emit()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.position().toPoint()
            self._current = self._start
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._start is not None:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        self._current = event.position().toPoint()
        local_rect = self._selection_local_rect()
        self._start = None
        self._current = None
        self.hide()

        if local_rect.width() < 6 or local_rect.height() < 6:
            self.cancelled.emit()
            return

        global_logical = QRect(
            self._virtual_geo.left() + local_rect.left(),
            self._virtual_geo.top() + local_rect.top(),
            local_rect.width(),
            local_rect.height(),
        )
        phys_rect = _logical_rect_to_physical(global_logical)
        # Wait briefly so the dark overlay fully disappears before taking the clean screenshot
        QTimer.singleShot(80, lambda r=phys_rect: self._finish_capture(r))

    def _finish_capture(self, phys_rect: Rect) -> None:
        try:
            png_bytes = capture_region_png_bytes(phys_rect)
        except Exception:
            self.cancelled.emit()
            return
        self.region_captured.emit(phys_rect, png_bytes)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Dimmed mask across full screen
        mask_color = QColor(0, 0, 0, 110)
        sel = self._selection_local_rect()
        if sel.isNull() or sel.width() < 2 or sel.height() < 2:
            painter.fillRect(self.rect(), mask_color)
            self._draw_hint(painter, "拖动鼠标框选目标图片区域（右键或 Esc 取消）")
            return

        # Draw 4 dimmed regions around the clear cutout
        w, h = self.width(), self.height()
        painter.fillRect(QRect(0, 0, w, sel.top()), mask_color)
        painter.fillRect(QRect(0, sel.bottom() + 1, w, h - sel.bottom() - 1), mask_color)
        painter.fillRect(QRect(0, sel.top(), sel.left(), sel.height()), mask_color)
        painter.fillRect(
            QRect(sel.right() + 1, sel.top(), w - sel.right() - 1, sel.height()), mask_color
        )

        # Selection border
        painter.setPen(QPen(QColor("#38bdf8"), 2))
        painter.drawRect(sel)

        # Size pill
        label = f"{sel.width()} × {sel.height()} px"
        font = QFont("Microsoft YaHei UI", 9, QFont.Weight.Bold)
        painter.setFont(font)
        fm = painter.fontMetrics()
        bw = fm.horizontalAdvance(label) + 14
        bx = sel.left()
        by = max(4, sel.top() - 24)
        badge = QRect(bx, by, bw, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0ea5e9"))
        painter.drawRoundedRect(badge, 4, 4)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)

    def _draw_hint(self, painter: QPainter, text: str) -> None:
        font = QFont("Microsoft YaHei UI", 11, QFont.Weight.Bold)
        painter.setFont(font)
        fm = painter.fontMetrics()
        bw = fm.horizontalAdvance(text) + 28
        bx = (self.width() - bw) // 2
        badge = QRect(bx, 28, bw, 32)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 23, 42, 220))
        painter.drawRoundedRect(badge, 8, 8)
        painter.setPen(QColor("#e2e8f0"))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
