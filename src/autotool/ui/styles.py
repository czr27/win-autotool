"""Soft eye-friendly VS Code Light Modern QSS stylesheet for AutoTool floating UI."""

VSCODE_LIGHT_QSS = """
QWidget {
    background-color: #eceef2;
    color: #24292f;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 12px;
}

QLabel {
    background-color: transparent;
}

QGroupBox QWidget {
    background-color: transparent;
}

#RootFrame {
    background-color: #e8ebef;
    border: 1px solid #c5ccd6;
    border-radius: 8px;
}

#TitleBar {
    background-color: #dfe3e8;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    border-bottom: 1px solid #cbd2dc;
}

#MiniBar {
    background-color: #eef1f5;
    border: 1px solid #6ba4d9;
    border-radius: 18px;
}

QLabel#TitleLabel {
    font-weight: 700;
    font-size: 13px;
    color: #0958a6;
    background: transparent;
}

QLabel#SectionHint {
    color: #57606a;
    font-size: 11px;
    background: transparent;
}

QPushButton {
    background-color: #f6f8fa;
    color: #24292f;
    border: 1px solid #cbd2dc;
    border-radius: 5px;
    padding: 5px 10px;
}

QPushButton:hover {
    background-color: #eaeef3;
    border-color: #b0b9c6;
}

QPushButton:pressed {
    background-color: #dfe4eb;
}

QPushButton:checked {
    background-color: #e1eefb;
    color: #0958a6;
    border-color: #9ec5ea;
    font-weight: 600;
}

QPushButton:disabled {
    background-color: #eceff3;
    color: #959da5;
    border-color: #d8dee6;
}

QPushButton#PrimaryBtn {
    background-color: #e1eefb;
    color: #0958a6;
    border: 1px solid #9ec5ea;
    font-weight: 600;
}

QPushButton#PrimaryBtn:hover {
    background-color: #d3e5f8;
    border-color: #7eb3e6;
}

QPushButton#SuccessBtn {
    background-color: #e2f1e6;
    color: #186832;
    border: 1px solid #a6d5b3;
    font-weight: 600;
}

QPushButton#SuccessBtn:hover {
    background-color: #d3ead9;
    border-color: #84c596;
}

QPushButton#DangerBtn {
    background-color: #f7e4e3;
    color: #a12c25;
    border: 1px solid #eab6b3;
    font-weight: 600;
}

QPushButton#DangerBtn:hover {
    background-color: #f2d5d3;
    border-color: #df9995;
}

QPushButton#DangerBtn:disabled {
    background-color: #eceff3;
    color: #959da5;
    border-color: #d8dee6;
}

QPushButton#IconBtn {
    background-color: transparent;
    color: #3b434d;
    border: none;
    border-radius: 4px;
    padding: 3px 6px;
}

QPushButton#IconBtn:hover {
    background-color: #d2d8e0;
}

QTabWidget::pane {
    border: 1px solid #cbd2dc;
    border-radius: 6px;
    background-color: #f2f4f7;
    top: -1px;
}

QTabWidget > QStackedWidget > QWidget {
    background-color: #f2f4f7;
}

QTabBar::tab {
    background-color: #e3e7ec;
    color: #57606a;
    border: 1px solid #cbd2dc;
    border-bottom: none;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    padding: 6px 14px;
    margin-right: 3px;
}

QTabBar::tab:hover {
    background-color: #d9dfe6;
    color: #24292f;
}

QTabBar::tab:selected {
    background-color: #f2f4f7;
    color: #0958a6;
    font-weight: 600;
    border-bottom: 2px solid #3182ce;
}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QListWidget, QTableWidget {
    background-color: #f7f8fa;
    color: #24292f;
    border: 1px solid #cbd2dc;
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: #cde2f7;
    selection-color: #24292f;
}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {
    background-color: #fafbfc;
    border-color: #4c95db;
}

QComboBox::drop-down {
    border: none;
    width: 18px;
}

QComboBox QAbstractItemView {
    background-color: #f6f8fa;
    color: #24292f;
    border: 1px solid #cbd2dc;
    selection-background-color: #dfebf7;
    selection-color: #0958a6;
}

QListWidget::item {
    padding: 6px 8px;
    border-radius: 4px;
    margin: 1px 0;
}

QListWidget::item:hover {
    background-color: #ebeff4;
}

QListWidget::item:selected {
    background-color: #dfebf7;
    color: #0958a6;
    border: 1px solid #a8c9ea;
}

QHeaderView::section {
    background-color: #eaedf1;
    color: #57606a;
    border: none;
    border-bottom: 1px solid #cbd2dc;
    padding: 4px;
    font-weight: 600;
}

QGroupBox {
    background-color: #f2f4f7;
    border: 1px solid #d0d7e0;
    border-radius: 6px;
    margin-top: 9px;
    padding-top: 10px;
    font-weight: 600;
    color: #0958a6;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 5px;
    background-color: #f2f4f7;
}

QDialog {
    background-color: #f2f4f7;
}

QDialog QGroupBox::title {
    background-color: #f2f4f7;
}

QCheckBox {
    background: transparent;
    color: #24292f;
    spacing: 6px;
}

QSlider::groove:horizontal {
    height: 4px;
    background: #c8d0da;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    width: 12px;
    height: 12px;
    margin: -4px 0;
    background: #4c95db;
    border-radius: 6px;
}

QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background: #c0c8d2;
    min-height: 24px;
    border-radius: 4px;
}

QScrollBar::handle:vertical:hover {
    background: #9aa4b2;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
"""

# Backward-compatible alias
DARK_QSS = VSCODE_LIGHT_QSS
