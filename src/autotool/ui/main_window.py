"""Main floating PySide6 window with collapsible mini-bar, control picker, locator tuning, and preset CLI export."""

from __future__ import annotations

import base64
import os
import re
import threading
from pathlib import Path
from urllib.parse import quote, urlencode

import uiautomation as auto
from PySide6.QtCore import QPoint, Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedLayout,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from autotool.core.audit_log import get_audit_logger, local_timestamp_ms
from autotool.core.http_server import AutoToolHTTPServer
from autotool.core.image_matcher import encode_png_b64
from autotool.core.inspector import UIAInspector
from autotool.core.locator_generator import generate_locator_strategies
from autotool.core.resolver import ControlResolver, LocatorDiagnostics
from autotool.core.runner import ActionRunner, RunResult, StepLog
from autotool.models.preset_store import PresetStore
from autotool.models.schema import (
    ActionStep,
    CapturedControl,
    LocatorStrategy,
    Preset,
    Rect,
    ResolvedControl,
)
from autotool.ui.action_editor import ActionEditorWidget
from autotool.ui.group_editor_dialog import PresetGroupDialog
from autotool.ui.hotkey import GlobalHotkeyManager
from autotool.ui.overlay import HighlightOverlay
from autotool.ui.picker import CrosshairButton, HoverCaptureController
from autotool.ui.region_selector import ScreenRegionSelector
from autotool.ui.styles import VSCODE_LIGHT_QSS


class _VerifyWorker(QThread):
    diagnosed = Signal(object)  # LocatorDiagnostics
    failed = Signal(str)

    def __init__(self, strategy: LocatorStrategy) -> None:
        super().__init__()
        self.strategy = strategy

    def run(self) -> None:
        with auto.UIAutomationInitializerInThread():
            try:
                diag = ControlResolver().diagnose(self.strategy)
                self.diagnosed.emit(diag)
            except Exception as exc:
                self.failed.emit(str(exc))


class _RunWorker(QThread):
    resolved_ok = Signal(object)  # ResolvedControl
    step_done = Signal(object)    # StepLog
    finished_run = Signal(object) # RunResult

    def __init__(
        self,
        preset: Preset,
        params: dict[str, str],
        stop_event: threading.Event,
        store: PresetStore | None = None,
    ) -> None:
        super().__init__()
        self.preset = preset
        self.params = params
        self.stop_event = stop_event
        self.store = store

    def run(self) -> None:
        with auto.UIAutomationInitializerInThread():
            runner = ActionRunner(store=self.store)
            result = runner.run_preset(
                self.preset,
                params=self.params,
                stop_event=self.stop_event,
                esc_abort=True,
                on_resolved=lambda r: self.resolved_ok.emit(r),
                on_step=lambda s: self.step_done.emit(s),
                store=self.store,
                source="gui",
            )
            self.finished_run.emit(result)


class FloatingMainWindow(QWidget):
    """Frameless, always-on-top, opacity-adjustable floating automation toolbox."""

    def __init__(self, store: PresetStore | None = None) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("AutoTool 桌面自动化工具箱")

        self.store = store or PresetStore()
        self.inspector = UIAInspector()
        self.overlay = HighlightOverlay()
        self.hover_picker = HoverCaptureController(self.inspector, self.overlay)
        self.region_selector = ScreenRegionSelector()
        self.region_selector.region_captured.connect(self._on_region_captured)
        self.region_selector.cancelled.connect(self._on_region_cancelled)

        self._drag_pos: QPoint | None = None
        self._collapsed = False
        self._expanded_size = (620, 760)
        self._pinned = True

        self._captured: CapturedControl | None = None
        self._strategies: list[LocatorStrategy] = []
        self._current_strategy: LocatorStrategy | None = None
        self._editing_preset_name: str | None = None
        self._updating_locator_ui = False

        self._verify_worker: _VerifyWorker | None = None
        self._run_worker: _RunWorker | None = None
        self._stop_event = threading.Event()
        self._http_server: AutoToolHTTPServer | None = None
        self._hotkey_mgr = GlobalHotkeyManager(parent=self)
        self._hotkey_mgr.triggered.connect(self._on_global_test_hotkey)

        self._build_ui()
        self.setStyleSheet(VSCODE_LIGHT_QSS)
        hint = self.minimumSizeHint()
        self.resize(max(self._expanded_size[0], hint.width()), max(self._expanded_size[1], hint.height()))
        self._refresh_preset_list()
        self._refresh_log_dates()
        self._refresh_log_runs()
        self._apply_test_hotkey("shift+f2", silent=True)

        # Default starter action so first-time users can test right away
        self.action_editor.set_steps([ActionStep(type="left_click")])

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.stack = QStackedLayout()
        outer.addLayout(self.stack)

        # Page 0: Full floating panel
        self.root_frame = QFrame()
        self.root_frame.setObjectName("RootFrame")
        frame_layout = QVBoxLayout(self.root_frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)

        frame_layout.addWidget(self._build_title_bar())

        body = QVBoxLayout()
        body.setContentsMargins(8, 8, 8, 8)
        body.setSpacing(6)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_capture_tab(), "1. 抓捕与定位")
        self.tabs.addTab(self._build_actions_tab(), "2. 操作与测试")
        self.tabs.addTab(self._build_presets_tab(), "3. 预设与CLI")
        self.tabs.addTab(self._build_logs_tab(), "4. 执行日志")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        body.addWidget(self.tabs)

        frame_layout.addLayout(body)
        self.stack.addWidget(self.root_frame)

        # Page 1: Compact mini floating pill
        self.mini_bar = self._build_mini_bar()
        self.stack.addWidget(self.mini_bar)
        self.stack.setCurrentIndex(0)

    def _build_title_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("TitleBar")
        bar.setFixedHeight(38)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 4, 8, 4)
        layout.setSpacing(6)

        title = QLabel("⚡ AutoTool 自动化工具箱")
        title.setObjectName("TitleLabel")
        layout.addWidget(title)
        layout.addStretch()

        lbl_op = QLabel("透明")
        lbl_op.setObjectName("SectionHint")
        layout.addWidget(lbl_op)

        self.slider_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacity.setRange(40, 100)
        self.slider_opacity.setValue(96)
        self.slider_opacity.setFixedWidth(68)
        self.slider_opacity.valueChanged.connect(lambda v: self.setWindowOpacity(v / 100.0))
        layout.addWidget(self.slider_opacity)

        self.btn_pin = QPushButton("📌")
        self.btn_pin.setObjectName("IconBtn")
        self.btn_pin.setCheckable(True)
        self.btn_pin.setChecked(True)
        self.btn_pin.setToolTip("置顶悬浮")
        self.btn_pin.toggled.connect(self._toggle_pin)
        layout.addWidget(self.btn_pin)

        btn_mini = QPushButton("悬浮条")
        btn_mini.setToolTip("折叠为迷你悬浮条")
        btn_mini.clicked.connect(lambda: self._set_collapsed(True))
        layout.addWidget(btn_mini)

        btn_close = QPushButton("✕")
        btn_close.setObjectName("IconBtn")
        btn_close.clicked.connect(self.close)
        layout.addWidget(btn_close)
        return bar

    def _build_mini_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("MiniBar")
        bar.setFixedHeight(40)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        drag_hint = QLabel("⚡")
        drag_hint.setObjectName("TitleLabel")
        layout.addWidget(drag_hint)

        self.mini_crosshair = CrosshairButton(self.inspector, self.overlay, text="🎯 抓捕")
        self.mini_crosshair.control_captured.connect(self._on_control_captured)
        layout.addWidget(self.mini_crosshair)

        btn_run = QPushButton("▶ 测试")
        btn_run.setObjectName("SuccessBtn")
        btn_run.clicked.connect(self._run_test)
        layout.addWidget(btn_run)

        btn_expand = QPushButton("展开")
        btn_expand.clicked.connect(lambda: self._set_collapsed(False))
        layout.addWidget(btn_expand)
        return bar

    def _build_capture_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Picker row
        pick_row = QHBoxLayout()
        self.btn_crosshair = CrosshairButton(self.inspector, self.overlay, text="🎯 按住拖拽抓捕控件")
        self.btn_crosshair.setObjectName("PrimaryBtn")
        self.btn_crosshair.setMinimumHeight(34)
        self.btn_crosshair.control_captured.connect(self._on_control_captured)
        pick_row.addWidget(self.btn_crosshair, 2)

        self.btn_hover_pick = QPushButton("⌨ 抓捕模式 (Ctrl)")
        self.btn_hover_pick.setCheckable(True)
        self.btn_hover_pick.setMinimumHeight(34)
        self.btn_hover_pick.clicked.connect(self.hover_picker.toggle)
        self.hover_picker.control_captured.connect(self._on_control_captured)
        self.hover_picker.state_changed.connect(self._on_hover_state_changed)
        pick_row.addWidget(self.btn_hover_pick, 2)

        self.btn_snip_image = QPushButton("🖼 框选图片定位")
        self.btn_snip_image.setMinimumHeight(34)
        self.btn_snip_image.setToolTip("在屏幕上框选一块区域作为图像模板，按相似度匹配定位")
        self.btn_snip_image.clicked.connect(self._start_region_snip)
        pick_row.addWidget(self.btn_snip_image, 2)
        layout.addLayout(pick_row)

        # Captured summary + optional template image thumbnail
        info_box = QGroupBox("已捕获控件 / 图片信息")
        info_layout = QHBoxLayout(info_box)
        info_layout.setContentsMargins(8, 6, 8, 6)
        self.lbl_captured_info = QLabel("尚未捕获。请拖拽靶心抓捕控件、按 Ctrl 锁定，或点击「框选图片定位」。")
        self.lbl_captured_info.setWordWrap(True)
        self.lbl_captured_info.setObjectName("SectionHint")
        info_layout.addWidget(self.lbl_captured_info, 1)

        self.lbl_img_preview = QLabel()
        self.lbl_img_preview.setFixedSize(92, 54)
        self.lbl_img_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_img_preview.setStyleSheet("border: 1px solid #cbd2dc; border-radius: 4px; background: #f7f8fa;")
        self.lbl_img_preview.setVisible(False)
        info_layout.addWidget(self.lbl_img_preview)
        layout.addWidget(info_box)

        # Strategy suggestions
        strat_box = QGroupBox("建议定位策略 (点击选择)")
        strat_layout = QVBoxLayout(strat_box)
        strat_layout.setContentsMargins(6, 6, 6, 6)
        self.strategy_list = QListWidget()
        self.strategy_list.setMinimumHeight(110)
        self.strategy_list.currentRowChanged.connect(self._on_strategy_selected)
        strat_layout.addWidget(self.strategy_list)
        layout.addWidget(strat_box)

        # Strategy fine-tuning
        tune_box = QGroupBox("定位微调与验证")
        tune_form = QFormLayout(tune_box)
        tune_form.setContentsMargins(8, 8, 8, 8)
        tune_form.setSpacing(5)

        self.edt_proc = QLineEdit()
        self.edt_proc.setPlaceholderText("进程名 如 notepad.exe (可留空)")
        self.edt_proc.textChanged.connect(self._apply_locator_tweaks)
        tune_form.addRow("进程名:", self.edt_proc)

        row_title = QHBoxLayout()
        self.edt_win_title = QLineEdit()
        self.edt_win_title.setPlaceholderText("窗口标题 (可留空)")
        self.edt_win_title.textChanged.connect(self._apply_locator_tweaks)
        self.cmb_title_match = QComboBox()
        self.cmb_title_match.addItems(["contains", "exact", "regex", "ignore"])
        self.cmb_title_match.currentTextChanged.connect(self._apply_locator_tweaks)
        row_title.addWidget(self.edt_win_title, 2)
        row_title.addWidget(QLabel("匹配:"))
        row_title.addWidget(self.cmb_title_match, 1)
        tune_form.addRow("窗口标题:", row_title)

        row_cname = QHBoxLayout()
        self.edt_ctrl_name = QLineEdit()
        self.edt_ctrl_name.setPlaceholderText("控件名称 / 关键字 (可留空)")
        self.edt_ctrl_name.textChanged.connect(self._apply_locator_tweaks)
        self.cmb_name_match = QComboBox()
        self.cmb_name_match.addItems(["exact", "contains", "regex", "ignore"])
        self.cmb_name_match.currentTextChanged.connect(self._apply_locator_tweaks)
        row_cname.addWidget(self.edt_ctrl_name, 2)
        row_cname.addWidget(QLabel("匹配:"))
        row_cname.addWidget(self.cmb_name_match, 1)
        tune_form.addRow("控件名称:", row_cname)

        row_aid = QHBoxLayout()
        self.edt_ctrl_aid = QLineEdit()
        self.edt_ctrl_aid.setPlaceholderText("AutomationId / 关键字 (可留空)")
        self.edt_ctrl_aid.textChanged.connect(self._apply_locator_tweaks)
        self.cmb_aid_match = QComboBox()
        self.cmb_aid_match.addItems(["exact", "contains", "regex", "ignore"])
        self.cmb_aid_match.currentTextChanged.connect(self._apply_locator_tweaks)
        row_aid.addWidget(self.edt_ctrl_aid, 2)
        row_aid.addWidget(QLabel("匹配:"))
        row_aid.addWidget(self.cmb_aid_match, 1)
        tune_form.addRow("控件 ID:", row_aid)

        row_anchor = QHBoxLayout()
        self.cmb_anchor = QComboBox()
        self.cmb_anchor.addItems(["center", "relative_ratio", "relative_px"])
        self.cmb_anchor.currentTextChanged.connect(self._apply_locator_tweaks)
        self.spn_ox = QDoubleSpinBox()
        self.spn_ox.setRange(-4000, 4000)
        self.spn_ox.setDecimals(3)
        self.spn_ox.setSingleStep(0.05)
        self.spn_ox.valueChanged.connect(self._apply_locator_tweaks)
        self.spn_oy = QDoubleSpinBox()
        self.spn_oy.setRange(-4000, 4000)
        self.spn_oy.setDecimals(3)
        self.spn_oy.setSingleStep(0.05)
        self.spn_oy.valueChanged.connect(self._apply_locator_tweaks)
        row_anchor.addWidget(self.cmb_anchor)
        row_anchor.addWidget(QLabel("X:"))
        row_anchor.addWidget(self.spn_ox)
        row_anchor.addWidget(QLabel("Y:"))
        row_anchor.addWidget(self.spn_oy)
        tune_form.addRow("落点锚点:", row_anchor)

        row_tol = QHBoxLayout()
        self.spn_size_tol = QSpinBox()
        self.spn_size_tol.setRange(0, 200)
        self.spn_size_tol.setValue(6)
        self.spn_size_tol.setSuffix(" px")
        self.spn_size_tol.valueChanged.connect(self._apply_locator_tweaks)
        self.spn_timeout = QSpinBox()
        self.spn_timeout.setRange(200, 30000)
        self.spn_timeout.setSingleStep(500)
        self.spn_timeout.setValue(3000)
        self.spn_timeout.setSuffix(" ms")
        self.spn_timeout.valueChanged.connect(self._apply_locator_tweaks)
        self.chk_activate = QCheckBox("前置窗口")
        self.chk_activate.setChecked(True)
        self.chk_activate.stateChanged.connect(self._apply_locator_tweaks)
        row_tol.addWidget(QLabel("尺寸容差:"))
        row_tol.addWidget(self.spn_size_tol)
        row_tol.addWidget(QLabel("超时:"))
        row_tol.addWidget(self.spn_timeout)
        row_tol.addWidget(self.chk_activate)
        tune_form.addRow("高级:", row_tol)

        # Image similarity controls (visible for image_template strategies)
        self.img_opts_widget = QWidget()
        img_row = QHBoxLayout(self.img_opts_widget)
        img_row.setContentsMargins(0, 0, 0, 0)
        img_row.setSpacing(6)
        self.spn_similarity = QSpinBox()
        self.spn_similarity.setRange(30, 100)
        self.spn_similarity.setValue(85)
        self.spn_similarity.setPrefix("> ")
        self.spn_similarity.setSuffix(" %")
        self.spn_similarity.valueChanged.connect(self._apply_locator_tweaks)
        self.chk_grayscale = QCheckBox("灰度匹配")
        self.chk_grayscale.stateChanged.connect(self._apply_locator_tweaks)
        self.chk_search_in_win = QCheckBox("仅在目标窗口内找图")
        self.chk_search_in_win.stateChanged.connect(self._apply_locator_tweaks)
        img_row.addWidget(QLabel("相似度:"))
        img_row.addWidget(self.spn_similarity)
        img_row.addWidget(self.chk_grayscale)
        img_row.addWidget(self.chk_search_in_win)
        img_row.addStretch()
        tune_form.addRow("图像匹配:", self.img_opts_widget)
        self.img_opts_widget.setVisible(False)
        img_lbl = tune_form.labelForField(self.img_opts_widget)
        if img_lbl is not None:
            img_lbl.setVisible(False)
        self._tune_form = tune_form

        layout.addWidget(tune_box)

        # Verify button
        verify_row = QHBoxLayout()
        self.btn_verify = QPushButton("🔍 逐级诊断并高亮控件")
        self.btn_verify.setObjectName("PrimaryBtn")
        self.btn_verify.setMinimumHeight(32)
        self.btn_verify.clicked.connect(self._verify_locator)
        verify_row.addWidget(self.btn_verify)

        btn_next = QPushButton("下一步：编排操作 →")
        btn_next.setMinimumHeight(32)
        btn_next.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        verify_row.addWidget(btn_next)
        layout.addLayout(verify_row)

        self.txt_verify_diag = QPlainTextEdit()
        self.txt_verify_diag.setReadOnly(True)
        self.txt_verify_diag.setFixedHeight(84)
        self.txt_verify_diag.setPlaceholderText("点击「逐级诊断并高亮控件」查看：① 进程PID/窗口数 → ② 窗口标题符合数 → ③ 控件符合数")
        layout.addWidget(self.txt_verify_diag)
        return w

    def _build_actions_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.action_editor = ActionEditorWidget()
        self.action_editor.changed.connect(self._update_cli_preview)
        layout.addWidget(self.action_editor, 1)

        run_row = QHBoxLayout()
        self.btn_test_run = QPushButton("▶ 立即测试执行 (Shift+F2)")
        self.btn_test_run.setObjectName("SuccessBtn")
        self.btn_test_run.setMinimumHeight(34)
        self.btn_test_run.clicked.connect(self._run_test)
        run_row.addWidget(self.btn_test_run, 2)

        self.btn_stop_run = QPushButton("⏹ 停止 (Esc)")
        self.btn_stop_run.setObjectName("DangerBtn")
        self.btn_stop_run.setMinimumHeight(34)
        self.btn_stop_run.setEnabled(False)
        self.btn_stop_run.clicked.connect(self._stop_test)
        run_row.addWidget(self.btn_stop_run, 1)

        btn_go_save = QPushButton("去保存预设 →")
        btn_go_save.setMinimumHeight(34)
        btn_go_save.clicked.connect(lambda: self.tabs.setCurrentIndex(2))
        run_row.addWidget(btn_go_save, 1)
        layout.addLayout(run_row)

        hk_row = QHBoxLayout()
        hk_row.addWidget(QLabel("全局测试快捷键:"))
        self.edt_test_hotkey = QLineEdit("shift+f2")
        self.edt_test_hotkey.setPlaceholderText("如 shift+f2, ctrl+f8")
        self.edt_test_hotkey.setMaximumWidth(140)
        hk_row.addWidget(self.edt_test_hotkey)
        btn_bind_hk = QPushButton("绑定")
        btn_bind_hk.clicked.connect(lambda: self._apply_test_hotkey(self.edt_test_hotkey.text()))
        hk_row.addWidget(btn_bind_hk)
        self.lbl_hk_status = QLabel("")
        self.lbl_hk_status.setObjectName("SectionHint")
        hk_row.addWidget(self.lbl_hk_status, 1)
        layout.addLayout(hk_row)

        log_bar = QHBoxLayout()
        lbl_log = QLabel("执行追溯日志（同步写入 logs/ 目录）")
        lbl_log.setObjectName("SectionHint")
        btn_open_logs = QPushButton("📂 打开日志目录")
        btn_open_logs.clicked.connect(self._open_logs_dir)
        log_bar.addWidget(lbl_log)
        log_bar.addStretch()
        log_bar.addWidget(btn_open_logs)
        layout.addLayout(log_bar)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(120)
        self.log_box.setPlaceholderText("带时间戳的每步拆解日志将显示在这里，并永久记录到 logs/ 目录…")
        layout.addWidget(self.log_box)
        return w

    def _build_presets_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        save_box = QGroupBox("保存当前配置为预设")
        save_form = QFormLayout(save_box)
        self.edt_preset_name = QLineEdit()
        self.edt_preset_name.setPlaceholderText("如 notepad_input（字母/数字/中文/下划线）")
        self.edt_preset_name.textChanged.connect(self._update_cli_preview)
        save_form.addRow("预设名称:", self.edt_preset_name)

        self.edt_preset_desc = QLineEdit()
        self.edt_preset_desc.setPlaceholderText("可选说明")
        save_form.addRow("描述:", self.edt_preset_desc)

        btn_save = QPushButton("💾 保存 / 更新预设")
        btn_save.setObjectName("PrimaryBtn")
        btn_save.setMinimumHeight(32)
        btn_save.clicked.connect(self._save_preset)
        save_form.addRow("", btn_save)
        layout.addWidget(save_box)

        list_box = QGroupBox("已保存预设（含预设组，支持拖拽排序）")
        list_layout = QVBoxLayout(list_box)
        self.preset_list = QListWidget()
        self.preset_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.preset_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.preset_list.setDragEnabled(True)
        self.preset_list.setAcceptDrops(True)
        self.preset_list.setDropIndicatorShown(True)
        self.preset_list.model().rowsMoved.connect(self._on_preset_order_changed)
        self.preset_list.itemDoubleClicked.connect(lambda _i: self._load_selected_preset())
        self.preset_list.currentItemChanged.connect(lambda _cur, _prev: self._update_cli_preview())
        list_layout.addWidget(self.preset_list)

        p_btns = QHBoxLayout()
        for label, slot in (
            ("🧩 新建预设组", self._create_preset_group),
            ("载入/编辑", self._load_selected_preset),
            ("创建副本", self._duplicate_selected_preset),
            ("▶ 直接运行", self._run_selected_preset),
            ("删除", self._delete_selected_preset),
            ("刷新", self._refresh_preset_list),
        ):
            b = QPushButton(label)
            b.clicked.connect(slot)
            p_btns.addWidget(b)
        list_layout.addLayout(p_btns)
        layout.addWidget(list_box, 1)

        cli_box = QGroupBox("外部程序调用 (CLI & HTTP GET)")
        cli_layout = QVBoxLayout(cli_box)
        cli_layout.setSpacing(5)

        cli_row = QHBoxLayout()
        self.edt_cli_cmd = QLineEdit()
        self.edt_cli_cmd.setReadOnly(True)
        btn_copy_cli = QPushButton("复制 CLI")
        btn_copy_cli.clicked.connect(self._copy_cli_command)
        cli_row.addWidget(self.edt_cli_cmd, 1)
        cli_row.addWidget(btn_copy_cli)
        cli_layout.addLayout(cli_row)

        http_ctrl = QHBoxLayout()
        http_ctrl.addWidget(QLabel("HTTP端口:"))
        self.spn_http_port = QSpinBox()
        self.spn_http_port.setRange(1024, 65535)
        self.spn_http_port.setValue(18999)
        self.spn_http_port.valueChanged.connect(self._update_cli_preview)
        http_ctrl.addWidget(self.spn_http_port)

        self.btn_http_toggle = QPushButton("▶ 启动 HTTP 服务")
        self.btn_http_toggle.setCheckable(True)
        self.btn_http_toggle.clicked.connect(self._toggle_http_server)
        http_ctrl.addWidget(self.btn_http_toggle)

        self.lbl_http_status = QLabel("未启动")
        self.lbl_http_status.setObjectName("SectionHint")
        http_ctrl.addWidget(self.lbl_http_status, 1)
        cli_layout.addLayout(http_ctrl)

        http_url_row = QHBoxLayout()
        self.edt_http_url = QLineEdit()
        self.edt_http_url.setReadOnly(True)
        btn_copy_url = QPushButton("复制 URL")
        btn_copy_url.clicked.connect(self._copy_http_url)
        http_url_row.addWidget(self.edt_http_url, 1)
        http_url_row.addWidget(btn_copy_url)
        cli_layout.addLayout(http_url_row)

        layout.addWidget(cli_box)
        return w

    def _build_logs_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(5)
        self.cmb_log_date = QComboBox()
        self.cmb_log_date.currentIndexChanged.connect(self._refresh_log_runs)
        filter_row.addWidget(self.cmb_log_date)

        self.cmb_log_status = QComboBox()
        self.cmb_log_status.addItem("全部状态", "all")
        self.cmb_log_status.addItem("仅失败", "fail")
        self.cmb_log_status.addItem("仅成功", "ok")
        self.cmb_log_status.currentIndexChanged.connect(self._refresh_log_runs)
        filter_row.addWidget(self.cmb_log_status)

        self.edt_log_search = QLineEdit()
        self.edt_log_search.setPlaceholderText("过滤预设名/错误信息…")
        self.edt_log_search.textChanged.connect(self._refresh_log_runs)
        filter_row.addWidget(self.edt_log_search, 1)

        btn_refresh_logs = QPushButton("刷新")
        btn_refresh_logs.clicked.connect(self._on_refresh_logs_clicked)
        filter_row.addWidget(btn_refresh_logs)

        btn_dir = QPushButton("📂 目录")
        btn_dir.clicked.connect(self._open_logs_dir)
        filter_row.addWidget(btn_dir)
        layout.addLayout(filter_row)

        runs_box = QGroupBox("执行记录（双击任意记录 → 直接编辑对应预设）")
        runs_layout = QVBoxLayout(runs_box)
        runs_layout.setContentsMargins(6, 6, 6, 6)
        self.log_run_list = QListWidget()
        self.log_run_list.currentRowChanged.connect(self._on_log_run_selected)
        self.log_run_list.itemDoubleClicked.connect(self._on_log_run_double_clicked)
        runs_layout.addWidget(self.log_run_list)
        layout.addWidget(runs_box, 1)

        steps_box = QGroupBox("步骤拆解详情（双击子预设步骤可直接编辑该子预设）")
        steps_layout = QVBoxLayout(steps_box)
        steps_layout.setContentsMargins(6, 6, 6, 6)
        self.log_step_list = QListWidget()
        self.log_step_list.itemDoubleClicked.connect(self._on_log_step_double_clicked)
        steps_layout.addWidget(self.log_step_list)
        layout.addWidget(steps_box, 1)

        bottom_row = QHBoxLayout()
        self.lbl_log_hint = QLabel("提示：双击上方运行记录或下方步骤即可跳转到预设编辑。")
        self.lbl_log_hint.setObjectName("SectionHint")
        bottom_row.addWidget(self.lbl_log_hint, 1)
        btn_edit_from_log = QPushButton("✏ 编辑选中日志对应预设")
        btn_edit_from_log.setObjectName("PrimaryBtn")
        btn_edit_from_log.clicked.connect(self._edit_preset_from_selected_log)
        bottom_row.addWidget(btn_edit_from_log)
        layout.addLayout(bottom_row)

        self._log_runs_cache: list[dict] = []
        return w

    # --- Floating window drag & collapse ---

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_pos = None

    def _toggle_pin(self, checked: bool) -> None:
        self._pinned = checked
        pos = self.pos()
        flags = Qt.WindowType.FramelessWindowHint
        if checked:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.move(pos)
        self.show()

    def _set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        if collapsed:
            self._expanded_size = (self.width(), self.height())
            self.stack.setCurrentIndex(1)
            self.setFixedSize(260, 42)
        else:
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self.stack.setCurrentIndex(0)
            hint = self.minimumSizeHint()
            self.resize(
                max(self._expanded_size[0], hint.width()),
                max(self._expanded_size[1], hint.height()),
            )

    # --- Capture & Locator Logic ---

    def _on_hover_state_changed(self, active: bool) -> None:
        self.btn_hover_pick.setChecked(active)
        self.btn_hover_pick.setText("⏹ 正在抓捕… (Ctrl锁定 / Esc退出)" if active else "⌨ 抓捕模式 (Ctrl锁定)")

    def _on_control_captured(self, captured: CapturedControl) -> None:
        self._captured = captured
        r = captured.control_rect
        self.lbl_captured_info.setText(
            f"进程: {captured.process_name or '-'} (PID {captured.process_id}) | "
            f"窗口: {captured.window_title[:36] or '-'}\n"
            f"控件: {captured.control_type} | 名称: {captured.control_name[:28] or '-'} | "
            f"ID: {captured.automation_id or '-'}\n"
            f"尺寸: {r.width}×{r.height} @({r.left},{r.top}) | "
            f"相对落点: ({captured.rel_x}px, {captured.rel_y}px) / ({captured.ratio_x:.0%}, {captured.ratio_y:.0%})"
        )

        self._strategies = generate_locator_strategies(captured)
        self.strategy_list.blockSignals(True)
        self.strategy_list.clear()
        badge_map = {"high": "★ 推荐", "medium": "● 备选", "low": "○ 兜底"}
        for st in self._strategies:
            item = QListWidgetItem(f"[{badge_map[st.confidence]}] {st.label}\n    {st.description}")
            self.strategy_list.addItem(item)
        self.strategy_list.blockSignals(False)

        if self._strategies:
            self.strategy_list.setCurrentRow(0)
            self._on_strategy_selected(0)

        if self._collapsed:
            self._set_collapsed(False)
        self.tabs.setCurrentIndex(0)

    def _on_strategy_selected(self, row: int) -> None:
        if not (0 <= row < len(self._strategies)):
            return
        st = self._strategies[row].model_copy(deep=True)
        self._current_strategy = st
        self._sync_locator_ui_from_strategy(st)

    def _start_region_snip(self) -> None:
        self.hover_picker.stop()
        self.hide()
        self.region_selector.start_capture()

    def _on_region_cancelled(self) -> None:
        self.show()
        self.raise_()

    def _on_region_captured(self, phys_rect: Rect, png_bytes: bytes) -> None:
        self.show()
        self.raise_()
        b64 = encode_png_b64(png_bytes)

        # Also inspect window under the center of the snipped region to offer window-scoped image matching
        under = self.inspector.inspect_point(phys_rect.center_x, phys_rect.center_y, include_tree_path=False)
        proc_name = under.process_name if under else ""
        win_title = under.window_title if under else ""

        strategies: list[LocatorStrategy] = [
            LocatorStrategy(
                id="image_template_fullscreen",
                label=f"全屏图像匹配 ({phys_rect.width}×{phys_rect.height}, 相似度 > 85%)",
                description="在全屏范围内搜索与所框选图片相似度高于阈值的区域，不受控件树限制。",
                confidence="high",
                strategy_type="image_template",
                process_name=proc_name,
                window_title=win_title,
                window_title_match="contains" if win_title else "ignore",
                template_image_b64=b64,
                similarity_threshold=0.85,
                image_grayscale=False,
                search_in_window=False,
                anchor_mode="center",
            )
        ]
        if proc_name or win_title:
            strategies.append(
                LocatorStrategy(
                    id="image_template_window",
                    label=f"窗口内图像匹配 ({proc_name or win_title[:18]}, 相似度 > 85%)",
                    description=f"仅在目标窗口 ({proc_name or win_title[:24]}) 区域内找图，速度更快且避免误匹配。",
                    confidence="high",
                    strategy_type="image_template",
                    process_name=proc_name,
                    window_title=win_title,
                    window_title_match="contains" if win_title else "ignore",
                    template_image_b64=b64,
                    similarity_threshold=0.85,
                    image_grayscale=False,
                    search_in_window=True,
                    anchor_mode="center",
                )
            )

        self._strategies = strategies
        self.lbl_captured_info.setText(
            f"已框选图像模板：{phys_rect.width}×{phys_rect.height} px @({phys_rect.left}, {phys_rect.top})\n"
            f"所在进程: {proc_name or '-'} | 窗口: {win_title[:36] or '-'}"
        )
        self._update_image_preview(b64)

        self.strategy_list.blockSignals(True)
        self.strategy_list.clear()
        for st in self._strategies:
            self.strategy_list.addItem(f"[★ 图像] {st.label}\n    {st.description}")
        self.strategy_list.blockSignals(False)
        self.strategy_list.setCurrentRow(0)
        self._on_strategy_selected(0)

        self.overlay.show_rect(
            phys_rect,
            label=f"已截取模板 {phys_rect.width}×{phys_rect.height}",
            color="#10b981",
            target_point=(phys_rect.center_x, phys_rect.center_y),
            auto_hide_ms=900,
        )
        if self._collapsed:
            self._set_collapsed(False)
        self.tabs.setCurrentIndex(0)

    def _update_image_preview(self, b64_str: str) -> None:
        if not b64_str:
            self.lbl_img_preview.clear()
            self.lbl_img_preview.setVisible(False)
            return
        try:
            pix = QPixmap()
            pix.loadFromData(base64.b64decode(b64_str))
            if not pix.isNull():
                scaled = pix.scaled(
                    self.lbl_img_preview.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.lbl_img_preview.setPixmap(scaled)
                self.lbl_img_preview.setVisible(True)
                return
        except Exception:
            pass
        self.lbl_img_preview.setVisible(False)

    def _sync_locator_ui_from_strategy(self, st: LocatorStrategy) -> None:
        self._updating_locator_ui = True
        try:
            self.edt_proc.setText(st.process_name)
            self.edt_win_title.setText(st.window_title)
            self.cmb_title_match.setCurrentText(st.window_title_match)
            self.edt_ctrl_name.setText(st.control_name)
            self.cmb_name_match.setCurrentText(st.control_name_match)
            self.edt_ctrl_aid.setText(st.automation_id)
            self.cmb_aid_match.setCurrentText(st.automation_id_match)
            self.cmb_anchor.setCurrentText(st.anchor_mode)
            self.spn_ox.setValue(st.offset_x)
            self.spn_oy.setValue(st.offset_y)
            self.spn_size_tol.setValue(st.size_tolerance)
            self.spn_timeout.setValue(st.timeout_ms)
            self.chk_activate.setChecked(st.activate_window)

            is_img = st.strategy_type == "image_template"
            self.img_opts_widget.setVisible(is_img)
            img_lbl = self._tune_form.labelForField(self.img_opts_widget)
            if img_lbl is not None:
                img_lbl.setVisible(is_img)
            if is_img:
                self.spn_similarity.setValue(int(round(st.similarity_threshold * 100)))
                self.chk_grayscale.setChecked(st.image_grayscale)
                self.chk_search_in_win.setChecked(st.search_in_window)
                self._update_image_preview(st.template_image_b64)
            else:
                self._update_image_preview("")
        finally:
            self._updating_locator_ui = False

    def _apply_locator_tweaks(self) -> None:
        if self._updating_locator_ui or self._current_strategy is None:
            return
        st = self._current_strategy
        st.process_name = self.edt_proc.text().strip()
        st.window_title = self.edt_win_title.text()
        st.window_title_match = self.cmb_title_match.currentText()  # type: ignore[assignment]
        st.control_name = self.edt_ctrl_name.text()
        st.control_name_match = self.cmb_name_match.currentText()  # type: ignore[assignment]
        st.automation_id = self.edt_ctrl_aid.text().strip()
        st.automation_id_match = self.cmb_aid_match.currentText()  # type: ignore[assignment]
        st.anchor_mode = self.cmb_anchor.currentText()  # type: ignore[assignment]
        st.offset_x = self.spn_ox.value()
        st.offset_y = self.spn_oy.value()
        st.size_tolerance = self.spn_size_tol.value()
        st.timeout_ms = self.spn_timeout.value()
        st.activate_window = self.chk_activate.isChecked()
        if st.strategy_type == "image_template":
            st.similarity_threshold = round(self.spn_similarity.value() / 100.0, 2)
            st.image_grayscale = self.chk_grayscale.isChecked()
            st.search_in_window = self.chk_search_in_win.isChecked()

    def _verify_locator(self) -> None:
        if self._current_strategy is None:
            QMessageBox.information(self, "提示", "请先抓捕控件或载入一个预设。")
            return
        self.btn_verify.setEnabled(False)
        self.txt_verify_diag.setPlainText("正在逐级诊断：进程 → 窗口标题 → 控件匹配…")
        self._verify_worker = _VerifyWorker(self._current_strategy.model_copy(deep=True))
        self._verify_worker.diagnosed.connect(self._on_verify_diagnosed)
        self._verify_worker.failed.connect(self._on_verify_failed)
        self._verify_worker.start()

    def _on_verify_diagnosed(self, diag: LocatorDiagnostics) -> None:
        self.btn_verify.setEnabled(True)
        self.txt_verify_diag.setPlainText(diag.format_report())
        if diag.control_matches:
            first = diag.control_matches[0]
            score_str = f" 相似度 {first.match_score:.1%}" if first.match_score is not None else ""
            count_str = f" (共{len(diag.control_matches)}个)" if len(diag.control_matches) > 1 else ""
            self.overlay.show_rect(
                first.rect,
                label=f"✅ 命中{count_str} ({diag.elapsed_ms}ms){score_str}",
                color="#10b981",
                target_point=(first.target_x, first.target_y),
                auto_hide_ms=1600,
            )

    def _on_verify_failed(self, err: str) -> None:
        self.btn_verify.setEnabled(True)
        self.txt_verify_diag.setPlainText(f"❌ 诊断异常：{err}")

    # --- Test Run ---

    def _build_current_preset(self, name: str = "__test__") -> Preset:
        if self._current_strategy is None:
            raise ValueError("尚未选择定位策略，请先抓捕控件或载入预设。")
        self._apply_locator_tweaks()
        return Preset(
            name=name,
            description=self.edt_preset_desc.text().strip(),
            locator=self._current_strategy.model_copy(deep=True),
            parameters=self.action_editor.get_parameters(),
            actions=self.action_editor.get_steps(),
        )

    def _append_log(self, line: str) -> None:
        self.log_box.appendPlainText(line)

    def _run_test(self) -> None:
        try:
            preset = self._build_current_preset(self.edt_preset_name.text().strip() or "__test__")
        except ValueError as exc:
            QMessageBox.warning(self, "无法测试", str(exc))
            return

        if not preset.actions:
            QMessageBox.information(self, "提示", "请至少添加一个操作步骤。")
            return

        params = self.action_editor.get_test_param_values()
        self._start_run_worker(preset, params)

    def _start_run_worker(self, preset: Preset, params: dict[str, str]) -> None:
        self.log_box.clear()
        if preset.kind == "group":
            self._append_log(f"▶ 开始执行预设组 [{preset.name}]，共 {len(preset.group_items)} 个子预设…")
        else:
            self._append_log(f"▶ 开始测试预设 [{preset.name}]，共 {len(preset.actions)} 步…")
        self.btn_test_run.setEnabled(False)
        self.btn_stop_run.setEnabled(True)
        self._stop_event.clear()

        self._run_worker = _RunWorker(preset, params, self._stop_event, store=self.store)
        self._run_worker.resolved_ok.connect(self._on_run_resolved)
        self._run_worker.step_done.connect(self._on_run_step)
        self._run_worker.finished_run.connect(self._on_run_finished)
        self._run_worker.start()

    def _stop_test(self) -> None:
        self._stop_event.set()
        self._append_log("⏹ 正在请求停止…")

    def _open_logs_dir(self) -> None:
        logs_dir = get_audit_logger().logs_dir
        logs_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(logs_dir))
        except Exception as exc:
            QMessageBox.information(self, "日志目录", f"日志路径：\n{logs_dir}\n({exc})")

    def _on_run_resolved(self, res: ResolvedControl) -> None:
        self.overlay.show_rect(
            res.rect,
            label="▶ 执行目标",
            color="#10b981",
            target_point=(res.target_x, res.target_y),
            auto_hide_ms=900,
        )

    def _on_run_step(self, log: StepLog) -> None:
        mark = "✓ OK  " if log.success else "✗ FAIL"
        err_part = f" | 错误: {log.error}" if log.error else ""
        self._append_log(
            f"[{log.timestamp}] [{mark}] #{log.index} ({log.action_type}) {log.detail} ({log.elapsed_ms}ms){err_part}"
        )

    def _on_run_finished(self, result: RunResult) -> None:
        self.btn_test_run.setEnabled(True)
        self.btn_stop_run.setEnabled(False)
        ts = result.finished_at or local_timestamp_ms()
        if result.success:
            self._append_log(f"[{ts}] [RUN {result.run_id}] ✅ 执行成功，总耗时 {result.total_ms}ms")
        else:
            self._append_log(f"[{ts}] [RUN {result.run_id}] ❌ 执行失败 ({result.total_ms}ms): {result.error}")
        self._refresh_log_dates()
        self._refresh_log_runs()

    # --- Presets & CLI ---

    def _save_preset(self) -> None:
        name = self.edt_preset_name.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入预设名称。")
            return
        try:
            preset = self._build_current_preset(name)
            old_name = self._editing_preset_name
            path = self.store.save_or_rename(preset, old_name=old_name)
            self._editing_preset_name = preset.name
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._refresh_preset_list()
        for i in range(self.preset_list.count()):
            it = self.preset_list.item(i)
            if it is not None and it.data(Qt.ItemDataRole.UserRole) == preset.name:
                self.preset_list.setCurrentRow(i)
                break
        self._update_cli_preview()
        get_audit_logger().log_event(
            "preset_save",
            f"保存预设 [{preset.name}] (策略={preset.locator.strategy_type if preset.locator else 'group'})",
            success=True,
        )
        if old_name and old_name != preset.name:
            QMessageBox.information(
                self, "已重命名并保存", f"预设已从 [{old_name}] 更名为 [{preset.name}]：\n{path}"
            )
        else:
            QMessageBox.information(self, "已保存", f"预设 [{preset.name}] 已保存到：\n{path}")

    def _on_preset_order_changed(self, *_args) -> None:
        names: list[str] = []
        for i in range(self.preset_list.count()):
            item = self.preset_list.item(i)
            if item is not None:
                n = item.data(Qt.ItemDataRole.UserRole)
                if n:
                    names.append(str(n))
        if names:
            self.store.save_order(names)

    def _create_preset_group(self) -> None:
        dlg = PresetGroupDialog(self.store, existing=None, parent=self)
        if dlg.exec() == PresetGroupDialog.DialogCode.Accepted:
            self._refresh_preset_list()

    def _refresh_preset_list(self) -> None:
        self.preset_list.clear()
        all_presets = self.store.list_presets()
        existing_names = {p.name for p in all_presets}
        for p in all_presets:
            if p.kind == "group":
                missing = [x.preset_name for x in p.group_items if x.preset_name not in existing_names]
                warn = f" | ⚠缺: {', '.join(missing)}" if missing else ""
                members = " → ".join(x.preset_name for x in p.group_items)
                text = f"[预设组] {p.name}  ({len(p.group_items)}项{warn})\n    {members}"
            else:
                param_names = ", ".join(x.name for x in p.parameters) or "无参数"
                text = (
                    f"{p.name}  ({len(p.actions)}步 | 参数: {param_names})"
                    + (f"\n    {p.description}" if p.description else "")
                )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, p.name)
            self.preset_list.addItem(item)
        self._update_cli_preview()

    def _selected_preset_name(self) -> str | None:
        item = self.preset_list.currentItem()
        if item is None:
            return None
        return str(item.data(Qt.ItemDataRole.UserRole))

    def _load_selected_preset(self) -> None:
        name = self._selected_preset_name()
        if not name:
            return
        self._load_preset_by_name(name)

    def _load_preset_by_name(self, name: str) -> None:
        try:
            preset = self.store.load(name)
        except Exception as exc:
            QMessageBox.warning(self, "载入失败", str(exc))
            return

        if preset.kind == "group":
            dlg = PresetGroupDialog(self.store, existing=preset, parent=self)
            if dlg.exec() == PresetGroupDialog.DialogCode.Accepted:
                self._refresh_preset_list()
            return

        if preset.locator is None:
            QMessageBox.warning(self, "载入失败", "该预设缺少定位策略。")
            return

        self._current_strategy = preset.locator.model_copy(deep=True)
        self._strategies = [self._current_strategy]
        self.strategy_list.blockSignals(True)
        self.strategy_list.clear()
        self.strategy_list.addItem(f"[已载入] {preset.locator.label}\n    {preset.locator.description}")
        self.strategy_list.setCurrentRow(0)
        self.strategy_list.blockSignals(False)
        self._sync_locator_ui_from_strategy(self._current_strategy)

        self._editing_preset_name = preset.name
        self.edt_preset_name.setText(preset.name)
        self.edt_preset_desc.setText(preset.description)
        self.action_editor.set_steps(preset.actions)
        self.action_editor.set_parameters(preset.parameters)
        self.lbl_captured_info.setText(
            f"已载入预设 [{preset.name}]：{preset.locator.process_name or '*'} / "
            f"{preset.locator.window_title or '*'} / {preset.locator.control_type or '*'}"
        )
        self._update_cli_preview()
        self.tabs.setCurrentIndex(1)

    def _run_selected_preset(self) -> None:
        name = self._selected_preset_name()
        if not name:
            return
        try:
            preset = self.store.load(name)
        except Exception as exc:
            QMessageBox.warning(self, "运行失败", str(exc))
            return
        if preset.kind == "group":
            self.tabs.setCurrentIndex(1)
            self._start_run_worker(preset, self.action_editor.get_test_param_values())
            return
        self._load_selected_preset()
        if self._current_strategy is not None:
            self._run_test()

    def _duplicate_selected_preset(self) -> None:
        name = self._selected_preset_name()
        if not name:
            QMessageBox.information(self, "提示", "请先选中一个预设或预设组。")
            return
        suggested = self.store.suggest_copy_name(name)
        new_name, ok = QInputDialog.getText(
            self,
            "创建副本",
            f"为 [{name}] 创建副本，请输入新名称：",
            text=suggested,
        )
        if not ok or not new_name.strip():
            return
        try:
            copied = self.store.duplicate(name, new_name.strip())
        except Exception as exc:
            QMessageBox.warning(self, "创建副本失败", str(exc))
            return
        self._refresh_preset_list()
        for i in range(self.preset_list.count()):
            it = self.preset_list.item(i)
            if it is not None and it.data(Qt.ItemDataRole.UserRole) == copied.name:
                self.preset_list.setCurrentRow(i)
                break

    def _delete_selected_preset(self) -> None:
        name = self._selected_preset_name()
        if not name:
            return
        if (
            QMessageBox.question(self, "确认删除", f"确定删除预设 [{name}] 吗？")
            == QMessageBox.StandardButton.Yes
        ):
            self.store.delete(name)
            self._refresh_preset_list()

    def _update_cli_preview(self) -> None:
        name = self.edt_preset_name.text().strip() or self._selected_preset_name() or "<preset_name>"
        bat_path = Path(__file__).resolve().parents[3] / "autotool.bat"
        cmd_parts = [f'"{bat_path}"', "run", name]
        query_pairs: list[tuple[str, str]] = []
        for p in self.action_editor.get_parameters():
            val = p.default or "示例内容"
            cmd_parts.append(f'-p {p.name}="{val}"')
            query_pairs.append((p.name, val))
        self.edt_cli_cmd.setText(" ".join(cmd_parts))

        port = self.spn_http_port.value()
        encoded_name = quote(name, safe="<>_")
        base = f"http://127.0.0.1:{port}/run/{encoded_name}"
        if query_pairs:
            qs = urlencode(query_pairs)
            self.edt_http_url.setText(f"{base}?{qs}")
        else:
            self.edt_http_url.setText(base)

    def _copy_cli_command(self) -> None:
        QApplication.clipboard().setText(self.edt_cli_cmd.text())

    def _copy_http_url(self) -> None:
        QApplication.clipboard().setText(self.edt_http_url.text())

    def _toggle_http_server(self) -> None:
        if self._http_server is not None and self._http_server.is_running:
            self._http_server.stop()
            self._http_server = None
            self.btn_http_toggle.setChecked(False)
            self.btn_http_toggle.setText("▶ 启动 HTTP 服务")
            self.lbl_http_status.setText("已停止")
            self.spn_http_port.setEnabled(True)
            return

        port = self.spn_http_port.value()
        try:
            srv = AutoToolHTTPServer(store=self.store, host="127.0.0.1", port=port)
            srv.start()
        except Exception as exc:
            self.btn_http_toggle.setChecked(False)
            QMessageBox.warning(self, "HTTP 服务启动失败", f"端口 {port} 启动失败：\n{exc}")
            return

        self._http_server = srv
        self.btn_http_toggle.setChecked(True)
        self.btn_http_toggle.setText("⏹ 停止 HTTP 服务")
        self.lbl_http_status.setText(f"● 运行中 ({srv.base_url})")
        self.spn_http_port.setEnabled(False)
        self._update_cli_preview()

    def _apply_test_hotkey(self, spec: str, silent: bool = False) -> None:
        ok, info = self._hotkey_mgr.register(spec)
        if ok:
            self.btn_test_run.setText(f"▶ 立即测试执行 ({info})")
            self.lbl_hk_status.setText(f"✅ 全局生效：{info}")
        else:
            self.btn_test_run.setText("▶ 立即测试执行")
            self.lbl_hk_status.setText(f"⚠ {info}")
            if not silent:
                QMessageBox.warning(self, "快捷键绑定失败", info)

    def _on_global_test_hotkey(self) -> None:
        if self._run_worker is not None and self._run_worker.isRunning():
            return
        self._run_test()

    # --- Log Browser ---

    def _on_tab_changed(self, index: int) -> None:
        if index == 3:
            self._refresh_log_dates()
            self._refresh_log_runs()

    def _on_refresh_logs_clicked(self) -> None:
        self._refresh_log_dates()
        self._refresh_log_runs()

    def _refresh_log_dates(self) -> None:
        cur = self.cmb_log_date.currentData()
        self.cmb_log_date.blockSignals(True)
        self.cmb_log_date.clear()
        dates = get_audit_logger().list_log_dates()
        for d in dates:
            self.cmb_log_date.addItem(d, d)
        self.cmb_log_date.addItem("全部日期", "all")
        if cur:
            idx = self.cmb_log_date.findData(cur)
            if idx >= 0:
                self.cmb_log_date.setCurrentIndex(idx)
        self.cmb_log_date.blockSignals(False)

    def _refresh_log_runs(self) -> None:
        date_val = self.cmb_log_date.currentData() or "all"
        status_val = self.cmb_log_status.currentData() or "all"
        kw = self.edt_log_search.text()
        runs = get_audit_logger().load_runs(
            date_str=str(date_val),
            status_filter=str(status_val),
            keyword=kw,
            limit=200,
        )
        self._log_runs_cache = runs
        self.log_run_list.blockSignals(True)
        self.log_run_list.clear()
        for r in runs:
            if r["success"] is True:
                icon = "✅"
            elif r["success"] is False:
                icon = "❌"
            else:
                icon = "⏳"
            ts_short = str(r.get("started_at") or "")[5:19]  # MM-DD HH:MM:SS
            src = r.get("source") or "-"
            dry = " [演练]" if r.get("dry_run") else ""
            err_line = f"\n    原因: {str(r.get('error') or '')[:80]}" if r.get("error") else ""
            text = (
                f"{icon} [{ts_short}] [{src}]{dry} {r['preset']} "
                f"({len(r['steps'])}步 · {r['total_ms']}ms){err_line}"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, r["preset"])
            self.log_run_list.addItem(item)
        self.log_run_list.blockSignals(False)
        if runs:
            self.log_run_list.setCurrentRow(0)
            self._on_log_run_selected(0)
        else:
            self.log_step_list.clear()

    def _on_log_run_selected(self, row: int) -> None:
        self.log_step_list.clear()
        if not (0 <= row < len(self._log_runs_cache)):
            return
        run = self._log_runs_cache[row]
        for s in run.get("steps", []):
            ok = s.get("success", True)
            mark = "✓" if ok else "✗"
            ts_full = str(s.get("timestamp") or "")
            ts_part = ts_full[11:23] if len(ts_full) >= 23 else ts_full
            err = f" | 错误: {s.get('error')}" if s.get("error") else ""
            line = (
                f"{mark} [{ts_part}] #{s.get('index', '?')} ({s.get('type', '')}) "
                f"{s.get('detail', '')} ({s.get('elapsed_ms', 0)}ms){err}"
            )
            it = QListWidgetItem(line)
            it.setData(Qt.ItemDataRole.UserRole, str(s.get("detail") or ""))
            self.log_step_list.addItem(it)

    def _on_log_run_double_clicked(self, item: QListWidgetItem) -> None:
        preset_name = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if preset_name:
            self._load_preset_by_name(preset_name)

    def _on_log_step_double_clicked(self, item: QListWidgetItem) -> None:
        detail = str(item.data(Qt.ItemDataRole.UserRole) or "")
        # If step comes from a preset group child like `[agv_select_claude] ...`, jump straight to it
        m = re.match(r"^\[([^\[\]]+)\]\s", detail)
        if m:
            candidate = m.group(1).strip()
            if candidate not in ("预设组间隔",) and self.store.path_for(candidate).exists():
                self._load_preset_by_name(candidate)
                return
        self._edit_preset_from_selected_log()

    def _edit_preset_from_selected_log(self) -> None:
        row = self.log_run_list.currentRow()
        if not (0 <= row < len(self._log_runs_cache)):
            QMessageBox.information(self, "提示", "请先选中一条执行日志。")
            return
        preset_name = str(self._log_runs_cache[row].get("preset") or "").strip()
        if preset_name:
            self._load_preset_by_name(preset_name)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._hotkey_mgr.cleanup()
        if self._http_server is not None:
            self._http_server.stop()
            self._http_server = None
        self.hover_picker.stop()
        self.region_selector.close()
        self.overlay.close()
        super().closeEvent(event)
