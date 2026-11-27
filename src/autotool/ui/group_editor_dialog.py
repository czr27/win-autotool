"""Dialog for creating and editing composite Preset Groups (ordered child presets + intervals)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from autotool.models.preset_store import PresetStore
from autotool.models.schema import Preset, PresetGroupItem
from autotool.ui.styles import VSCODE_LIGHT_QSS


class PresetGroupDialog(QDialog):
    """Modal editor for a composite Preset (`kind='group'`)."""

    def __init__(
        self,
        store: PresetStore,
        existing: Preset | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.existing = existing
        self._items: list[PresetGroupItem] = (
            [it.model_copy(deep=True) for it in existing.group_items] if existing else []
        )
        self._available_names = {p.name for p in self.store.list_presets()}
        self.saved_preset: Preset | None = None

        self.setWindowTitle("编辑预设组" if existing else "新建预设组")
        self.setMinimumSize(480, 470)
        self.setStyleSheet(VSCODE_LIGHT_QSS)
        self._build_ui()
        self._refresh_list(select_row=0 if self._items else -1)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Basic metadata
        meta_form = QFormLayout()
        self.edt_name = QLineEdit(self.existing.name if self.existing else "")
        self.edt_name.setPlaceholderText("预设组名称，如 login_flow（外部通过此名称调用）")
        meta_form.addRow("预设组名:", self.edt_name)

        self.edt_desc = QLineEdit(self.existing.description if self.existing else "")
        self.edt_desc.setPlaceholderText("可选描述")
        meta_form.addRow("描述:", self.edt_desc)
        root.addLayout(meta_form)

        # Add child preset bar
        add_box = QGroupBox("添加子预设（按名称引用，子预设删除不影响本组，执行时校验存在性）")
        add_row = QHBoxLayout(add_box)
        add_row.setContentsMargins(8, 8, 8, 8)
        add_row.setSpacing(6)

        self.cmb_child = QComboBox()
        self.cmb_child.setEditable(True)
        self.cmb_child.setPlaceholderText("选择已有预设或直接输入预设名")
        cur_name = self.existing.name if self.existing else ""
        for p in self.store.list_presets():
            if p.name != cur_name:
                self.cmb_child.addItem(p.name)
        add_row.addWidget(self.cmb_child, 3)

        add_row.addWidget(QLabel("后置间隔:"))
        self.spn_add_delay = QSpinBox()
        self.spn_add_delay.setRange(0, 60000)
        self.spn_add_delay.setSingleStep(100)
        self.spn_add_delay.setValue(300)
        self.spn_add_delay.setSuffix(" ms")
        add_row.addWidget(self.spn_add_delay, 1)

        btn_add = QPushButton("+ 添加")
        btn_add.setObjectName("PrimaryBtn")
        btn_add.clicked.connect(self._add_item)
        add_row.addWidget(btn_add)
        root.addWidget(add_box)

        # Ordered list of child presets
        list_box = QGroupBox("执行顺序与间隔设置（支持拖拽排序）")
        list_layout = QVBoxLayout(list_box)
        list_layout.setContentsMargins(8, 8, 8, 8)
        list_layout.setSpacing(6)

        self.item_list = QListWidget()
        self.item_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.item_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.item_list.setDropIndicatorShown(True)
        self.item_list.model().rowsMoved.connect(self._on_items_reordered)
        self.item_list.currentRowChanged.connect(self._on_row_selected)
        list_layout.addWidget(self.item_list, 1)

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(5)
        for text, slot in (
            ("↑ 上移", self._move_up),
            ("↓ 下移", self._move_down),
            ("删除", self._delete_item),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            ctrl_row.addWidget(b)

        ctrl_row.addStretch()
        ctrl_row.addWidget(QLabel("选中项后置间隔:"))
        self.spn_item_delay = QSpinBox()
        self.spn_item_delay.setRange(0, 60000)
        self.spn_item_delay.setSingleStep(100)
        self.spn_item_delay.setSuffix(" ms")
        self.spn_item_delay.valueChanged.connect(self._on_delay_changed)
        ctrl_row.addWidget(self.spn_item_delay)
        list_layout.addLayout(ctrl_row)

        root.addWidget(list_box, 1)

        # Save / Cancel
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("💾 保存预设组")
        btn_save.setObjectName("SuccessBtn")
        btn_save.setMinimumHeight(32)
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_save)
        root.addLayout(btn_row)

    def _add_item(self) -> None:
        name = self.cmb_child.currentText().strip()
        if not name:
            QMessageBox.information(self, "提示", "请选择或输入一个子预设名称。")
            return
        row = self.item_list.currentRow()
        has_sel = bool(self.item_list.selectedItems()) and row >= 0
        insert_idx = row + 1 if has_sel else len(self._items)
        self._items.insert(
            insert_idx,
            PresetGroupItem(preset_name=name, delay_after_ms=self.spn_add_delay.value()),
        )
        self._refresh_list(select_row=insert_idx)

    def _move_up(self) -> None:
        row = self.item_list.currentRow()
        if row > 0:
            self._items[row - 1], self._items[row] = self._items[row], self._items[row - 1]
            self._refresh_list(select_row=row - 1)

    def _move_down(self) -> None:
        row = self.item_list.currentRow()
        if 0 <= row < len(self._items) - 1:
            self._items[row + 1], self._items[row] = self._items[row], self._items[row + 1]
            self._refresh_list(select_row=row + 1)

    def _delete_item(self) -> None:
        row = self.item_list.currentRow()
        if 0 <= row < len(self._items):
            self._items.pop(row)
            self._refresh_list(select_row=min(row, len(self._items) - 1))

    def _on_row_selected(self, row: int) -> None:
        if 0 <= row < len(self._items):
            self.spn_item_delay.blockSignals(True)
            self.spn_item_delay.setEnabled(True)
            self.spn_item_delay.setValue(self._items[row].delay_after_ms)
            self.spn_item_delay.blockSignals(False)
        else:
            self.spn_item_delay.setEnabled(False)

    def _on_delay_changed(self, value: int) -> None:
        row = self.item_list.currentRow()
        if 0 <= row < len(self._items):
            self._items[row].delay_after_ms = value
            item = self.item_list.item(row)
            if item is not None:
                item.setText(self._format_item(row, self._items[row]))

    def _format_item(self, idx: int, entry: PresetGroupItem) -> str:
        exists = entry.preset_name in self._available_names
        status = "✓ 存在" if exists else "⚠ 当前缺失"
        suffix = f"  → 间隔 {entry.delay_after_ms}ms" if idx < len(self._items) - 1 else "  (最后一步)"
        return f"{idx + 1}. {entry.preset_name}  [{status}]{suffix}"

    def _on_items_reordered(self, _parent, start: int, _end: int, _dest, row: int) -> None:
        if not (0 <= start < len(self._items)):
            return
        moved = self._items.pop(start)
        target_idx = row if row <= start else row - 1
        target_idx = max(0, min(target_idx, len(self._items)))
        self._items.insert(target_idx, moved)
        self._refresh_list(select_row=target_idx)

    def _refresh_list(self, select_row: int = -1) -> None:
        self.item_list.blockSignals(True)
        self.item_list.clear()
        for idx, entry in enumerate(self._items):
            self.item_list.addItem(QListWidgetItem(self._format_item(idx, entry)))
        self.item_list.blockSignals(False)
        if 0 <= select_row < len(self._items):
            self.item_list.setCurrentRow(select_row)
            self._on_row_selected(select_row)
        else:
            self._on_row_selected(-1)

    def _save(self) -> None:
        name = self.edt_name.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入预设组名称。")
            return
        if not self._items:
            QMessageBox.warning(self, "提示", "请至少添加一个子预设。")
            return
        if any(it.preset_name == name for it in self._items):
            QMessageBox.warning(self, "提示", "预设组不能引用自身。")
            return

        preset = Preset(
            name=name,
            kind="group",
            description=self.edt_desc.text().strip(),
            locator=None,
            group_items=list(self._items),
        )
        try:
            old_name = self.existing.name if self.existing is not None else None
            self.store.save_or_rename(preset, old_name=old_name)
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return

        self.saved_preset = preset
        self.accept()
