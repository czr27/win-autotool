"""Action sequence and parameter editor widget."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from autotool.models.preset_store import _PLACEHOLDER_RE
from autotool.models.schema import ActionStep, ActionType, ParameterDef

_MOUSE_QUICK_ACTIONS: list[tuple[str, ActionType]] = [
    ("移动", "mouse_move"),
    ("LeftClick", "left_click"),
    ("LeftDown", "left_down"),
    ("LeftUp", "left_up"),
    ("RightClick", "right_click"),
    ("RightDown", "right_down"),
    ("RightUp", "right_up"),
]

_KEY_QUICK_ACTIONS: list[tuple[str, ActionType]] = [
    ("按键Press", "key_press"),
    ("KeyDown", "key_down"),
    ("KeyUp", "key_up"),
    ("输入Input", "text_input"),
    ("敲入Press", "text_press"),
    ("清空文本", "clear_text"),
    ("延时Delay", "delay"),
]


class ActionEditorWidget(QWidget):
    """Visual editor for ordered mouse/keyboard/text/delay actions and preset parameters."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._steps: list[ActionStep] = []
        self._updating_form = False
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # Quick add buttons (2 compact rows)
        add_group = QGroupBox("添加操作步骤")
        add_layout = QVBoxLayout(add_group)
        add_layout.setContentsMargins(6, 6, 6, 6)
        add_layout.setSpacing(4)

        row1 = QHBoxLayout()
        row1.setSpacing(4)
        for label, atype in _MOUSE_QUICK_ACTIONS:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _checked=False, t=atype: self._add_step(t))
            row1.addWidget(btn)
        add_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(4)
        for label, atype in _KEY_QUICK_ACTIONS:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _checked=False, t=atype: self._add_step(t))
            row2.addWidget(btn)
        add_layout.addLayout(row2)
        root.addWidget(add_group)

        # Middle: step list + step property editor
        mid = QHBoxLayout()
        mid.setSpacing(6)

        left_col = QVBoxLayout()
        self.step_list = QListWidget()
        self.step_list.setMinimumHeight(140)
        self.step_list.currentRowChanged.connect(self._on_step_selected)
        left_col.addWidget(self.step_list)

        list_btns = QHBoxLayout()
        list_btns.setSpacing(4)
        for text, slot in (
            ("↑ 上移", self._move_up),
            ("↓ 下移", self._move_down),
            ("复制", self._duplicate_step),
            ("删除", self._delete_step),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            list_btns.addWidget(b)
        left_col.addLayout(list_btns)
        mid.addLayout(left_col, 5)

        # Step detail form
        detail_box = QGroupBox("步骤属性 (支持 {{参数名}})")
        self.form = QFormLayout(detail_box)
        self.form.setContentsMargins(6, 8, 6, 6)
        self.form.setSpacing(5)

        self.lbl_type = QLabel("未选中步骤")
        self.form.addRow("类型:", self.lbl_type)

        self.chk_use_target = QCheckBox("移动到定位控件落点")
        self.chk_use_target.stateChanged.connect(self._apply_form_to_step)
        self.form.addRow("坐标:", self.chk_use_target)

        offset_row = QHBoxLayout()
        self.spn_dx = QSpinBox()
        self.spn_dx.setRange(-2000, 2000)
        self.spn_dx.setPrefix("dx: ")
        self.spn_dx.valueChanged.connect(self._apply_form_to_step)
        self.spn_dy = QSpinBox()
        self.spn_dy.setRange(-2000, 2000)
        self.spn_dy.setPrefix("dy: ")
        self.spn_dy.valueChanged.connect(self._apply_form_to_step)
        offset_row.addWidget(self.spn_dx)
        offset_row.addWidget(self.spn_dy)
        self.offset_widget = QWidget()
        self.offset_widget.setLayout(offset_row)
        offset_row.setContentsMargins(0, 0, 0, 0)
        self.form.addRow("微调:", self.offset_widget)

        self.spn_duration = QSpinBox()
        self.spn_duration.setRange(0, 60000)
        self.spn_duration.setSingleStep(50)
        self.spn_duration.setSuffix(" ms")
        self.spn_duration.valueChanged.connect(self._apply_form_to_step)
        self.form.addRow("时长:", self.spn_duration)

        self.edt_key = QLineEdit()
        self.edt_key.setPlaceholderText("如 enter, ctrl+a, {{key}}")
        self.edt_key.textChanged.connect(self._apply_form_to_step)
        self.form.addRow("按键:", self.edt_key)

        self.edt_text = QLineEdit()
        self.edt_text.setPlaceholderText("如 Hello 或 {{text}}")
        self.edt_text.textChanged.connect(self._apply_form_to_step)
        self.form.addRow("文本:", self.edt_text)

        self.chk_clipboard = QCheckBox("使用剪贴板粘贴 (Ctrl+V)")
        self.chk_clipboard.stateChanged.connect(self._apply_form_to_step)
        self.form.addRow("模式:", self.chk_clipboard)

        self.spn_interval = QSpinBox()
        self.spn_interval.setRange(0, 2000)
        self.spn_interval.setSuffix(" ms")
        self.spn_interval.valueChanged.connect(self._apply_form_to_step)
        self.form.addRow("键间隔:", self.spn_interval)

        self.edt_ms = QLineEdit()
        self.edt_ms.setPlaceholderText("如 300 或 {{delay_ms}}")
        self.edt_ms.textChanged.connect(self._apply_form_to_step)
        self.form.addRow("延时ms:", self.edt_ms)

        mid.addWidget(detail_box, 5)
        root.addLayout(mid)

        # Parameters table
        param_box = QGroupBox("预设参数 (供外部调用传参 & 立即测试)")
        param_layout = QVBoxLayout(param_box)
        param_layout.setContentsMargins(6, 6, 6, 6)
        param_layout.setSpacing(4)

        param_bar = QHBoxLayout()
        btn_scan = QPushButton("✨ 自动识别 {{参数}}")
        btn_scan.clicked.connect(self.scan_placeholders)
        btn_add_p = QPushButton("+ 添加参数")
        btn_add_p.clicked.connect(lambda: self._add_param_row("text", "示例文本", "示例文本"))
        btn_del_p = QPushButton("- 删除参数")
        btn_del_p.clicked.connect(self._delete_param_row)
        param_bar.addWidget(btn_scan)
        param_bar.addWidget(btn_add_p)
        param_bar.addWidget(btn_del_p)
        param_bar.addStretch()
        param_layout.addLayout(param_bar)

        self.param_table = QTableWidget(0, 3)
        self.param_table.setHorizontalHeaderLabels(["参数名", "默认值", "本次测试值"])
        self.param_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.param_table.setMaximumHeight(110)
        self.param_table.cellChanged.connect(lambda _r, _c: self.changed.emit())
        param_layout.addWidget(self.param_table)
        root.addWidget(param_box)

        self._set_form_visibility(None)

    def _default_step(self, atype: ActionType) -> ActionStep:
        if atype == "key_press":
            return ActionStep(type=atype, key="enter")
        if atype in ("key_down", "key_up"):
            return ActionStep(type=atype, key="shift")
        if atype == "text_input":
            return ActionStep(type=atype, text="{{text}}")
        if atype == "text_press":
            return ActionStep(type=atype, text="{{text}}", interval_ms=25)
        if atype == "delay":
            return ActionStep(type=atype, ms="300")
        return ActionStep(type=atype)

    def _add_step(self, atype: ActionType) -> None:
        step = self._default_step(atype)
        row = self.step_list.currentRow()
        has_sel = bool(self.step_list.selectedItems()) and row >= 0
        insert_idx = row + 1 if has_sel else len(self._steps)
        self._steps.insert(insert_idx, step)
        self._refresh_list(select_row=insert_idx)
        self.scan_placeholders()
        self.changed.emit()

    def _move_up(self) -> None:
        row = self.step_list.currentRow()
        if row > 0:
            self._steps[row - 1], self._steps[row] = self._steps[row], self._steps[row - 1]
            self._refresh_list(select_row=row - 1)
            self.changed.emit()

    def _move_down(self) -> None:
        row = self.step_list.currentRow()
        if 0 <= row < len(self._steps) - 1:
            self._steps[row + 1], self._steps[row] = self._steps[row], self._steps[row + 1]
            self._refresh_list(select_row=row + 1)
            self.changed.emit()

    def _duplicate_step(self) -> None:
        row = self.step_list.currentRow()
        if 0 <= row < len(self._steps):
            cloned = self._steps[row].model_copy(deep=True)
            self._steps.insert(row + 1, cloned)
            self._refresh_list(select_row=row + 1)
            self.changed.emit()

    def _delete_step(self) -> None:
        row = self.step_list.currentRow()
        if 0 <= row < len(self._steps):
            self._steps.pop(row)
            next_row = min(row, len(self._steps) - 1)
            self._refresh_list(select_row=next_row)
            self.changed.emit()

    def _refresh_list(self, select_row: int = -1) -> None:
        self.step_list.blockSignals(True)
        self.step_list.clear()
        for idx, step in enumerate(self._steps, start=1):
            item = QListWidgetItem(f"{idx}. {step.summary()}")
            self.step_list.addItem(item)
        self.step_list.blockSignals(False)
        if 0 <= select_row < len(self._steps):
            self.step_list.setCurrentRow(select_row)
            self._on_step_selected(select_row)
        else:
            self._on_step_selected(-1)

    def _set_row_visible(self, field_widget: QWidget, visible: bool) -> None:
        label = self.form.labelForField(field_widget)
        field_widget.setVisible(visible)
        if label is not None:
            label.setVisible(visible)

    def _set_form_visibility(self, step: ActionStep | None) -> None:
        if step is None:
            self.lbl_type.setText("未选中步骤")
            for w in (
                self.chk_use_target,
                self.offset_widget,
                self.spn_duration,
                self.edt_key,
                self.edt_text,
                self.chk_clipboard,
                self.spn_interval,
                self.edt_ms,
            ):
                self._set_row_visible(w, False)
            return

        t = step.type
        self.lbl_type.setText(t)
        is_mouse_pos = t in ("mouse_move", "left_click", "left_down", "right_click", "right_down", "clear_text")
        has_dur = t == "mouse_move"
        is_key = t in ("key_press", "key_down", "key_up")
        is_text = t in ("text_input", "text_press")

        self._set_row_visible(self.chk_use_target, is_mouse_pos)
        self._set_row_visible(self.offset_widget, is_mouse_pos)
        self._set_row_visible(self.spn_duration, has_dur)
        self._set_row_visible(self.edt_key, is_key)
        self._set_row_visible(self.edt_text, is_text)
        self._set_row_visible(self.chk_clipboard, t == "text_input")
        self._set_row_visible(self.spn_interval, t == "text_press")
        self._set_row_visible(self.edt_ms, t == "delay")

    def _on_step_selected(self, row: int) -> None:
        if not (0 <= row < len(self._steps)):
            self._set_form_visibility(None)
            return
        step = self._steps[row]
        self._updating_form = True
        try:
            self._set_form_visibility(step)
            self.chk_use_target.setChecked(step.use_target)
            self.spn_dx.setValue(step.offset_dx)
            self.spn_dy.setValue(step.offset_dy)
            self.spn_duration.setValue(step.duration_ms)
            self.edt_key.setText(step.key)
            self.edt_text.setText(step.text)
            self.chk_clipboard.setChecked(step.use_clipboard)
            self.spn_interval.setValue(step.interval_ms)
            self.edt_ms.setText(step.ms)
        finally:
            self._updating_form = False

    def _apply_form_to_step(self) -> None:
        if self._updating_form:
            return
        row = self.step_list.currentRow()
        if not (0 <= row < len(self._steps)):
            return
        step = self._steps[row]
        step.use_target = self.chk_use_target.isChecked()
        step.offset_dx = self.spn_dx.value()
        step.offset_dy = self.spn_dy.value()
        step.duration_ms = self.spn_duration.value()
        step.key = self.edt_key.text()
        step.text = self.edt_text.text()
        step.use_clipboard = self.chk_clipboard.isChecked()
        step.interval_ms = self.spn_interval.value()
        step.ms = self.edt_ms.text()

        item = self.step_list.item(row)
        if item is not None:
            item.setText(f"{row + 1}. {step.summary()}")
        self.changed.emit()

    def _add_param_row(self, name: str, default: str = "", test_val: str = "") -> None:
        self.param_table.blockSignals(True)
        cur = self.param_table.currentRow()
        has_sel = bool(self.param_table.selectedItems()) and cur >= 0
        row = cur + 1 if has_sel else self.param_table.rowCount()
        self.param_table.insertRow(row)
        self.param_table.setItem(row, 0, QTableWidgetItem(name))
        self.param_table.setItem(row, 1, QTableWidgetItem(default))
        self.param_table.setItem(row, 2, QTableWidgetItem(test_val or default))
        self.param_table.selectRow(row)
        self.param_table.blockSignals(False)
        self.changed.emit()

    def _delete_param_row(self) -> None:
        row = self.param_table.currentRow()
        if row >= 0:
            self.param_table.removeRow(row)
            self.changed.emit()

    def scan_placeholders(self) -> None:
        existing = {
            (self.param_table.item(r, 0).text().strip() if self.param_table.item(r, 0) else "")
            for r in range(self.param_table.rowCount())
        }
        for step in self._steps:
            for field_val in (step.text, step.key, step.ms):
                for m in _PLACEHOLDER_RE.finditer(field_val or ""):
                    name = m.group(1)
                    if name not in existing:
                        existing.add(name)
                        self._add_param_row(name, "", "")

    def get_steps(self) -> list[ActionStep]:
        return [s.model_copy(deep=True) for s in self._steps]

    def set_steps(self, steps: list[ActionStep]) -> None:
        self._steps = [s.model_copy(deep=True) for s in steps]
        self._refresh_list(select_row=0 if self._steps else -1)

    def get_parameters(self) -> list[ParameterDef]:
        params: list[ParameterDef] = []
        for r in range(self.param_table.rowCount()):
            name = (self.param_table.item(r, 0).text() if self.param_table.item(r, 0) else "").strip()
            default = self.param_table.item(r, 1).text() if self.param_table.item(r, 1) else ""
            if name:
                params.append(ParameterDef(name=name, default=default))
        return params

    def set_parameters(self, params: list[ParameterDef]) -> None:
        self.param_table.blockSignals(True)
        self.param_table.setRowCount(0)
        for p in params:
            row = self.param_table.rowCount()
            self.param_table.insertRow(row)
            self.param_table.setItem(row, 0, QTableWidgetItem(p.name))
            self.param_table.setItem(row, 1, QTableWidgetItem(p.default))
            self.param_table.setItem(row, 2, QTableWidgetItem(p.default))
        self.param_table.blockSignals(False)

    def get_test_param_values(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for r in range(self.param_table.rowCount()):
            name = (self.param_table.item(r, 0).text() if self.param_table.item(r, 0) else "").strip()
            test_val = self.param_table.item(r, 2).text() if self.param_table.item(r, 2) else ""
            default = self.param_table.item(r, 1).text() if self.param_table.item(r, 1) else ""
            if name:
                values[name] = test_val if test_val != "" else default
        return values
