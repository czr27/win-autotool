"""Pydantic data schemas for captured controls, locator strategies, actions, and presets."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Rect(BaseModel):
    """Screen bounding rectangle in physical pixels."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def center_x(self) -> int:
        return self.left + self.width // 2

    @property
    def center_y(self) -> int:
        return self.top + self.height // 2


class TreePathNode(BaseModel):
    """Single hop in a UIA control tree path from the top-level window down to target."""

    control_type: str
    index: int = 0
    name: str = ""
    automation_id: str = ""
    class_name: str = ""


class CapturedControl(BaseModel):
    """Full snapshot of a control captured via mouse hover/pick."""

    process_name: str = ""
    process_id: int = 0
    window_title: str = ""
    window_class: str = ""
    window_rect: Rect
    control_type: str = ""
    control_name: str = ""
    automation_id: str = ""
    class_name: str = ""
    control_rect: Rect
    tree_path: list[TreePathNode] = Field(default_factory=list)
    click_x: int = 0
    click_y: int = 0
    rel_x: int = 0
    rel_y: int = 0
    ratio_x: float = 0.5
    ratio_y: float = 0.5
    win_ratio_x: float = 0.5
    win_ratio_y: float = 0.5


StrategyType = Literal[
    "process_automation_id",
    "process_name_offset",
    "window_automation_id",
    "window_size_offset",
    "window_tree_path",
    "window_relative_ratio",
    "image_template",
]

AnchorMode = Literal["center", "relative_ratio", "relative_px"]
TitleMatchMode = Literal["exact", "contains", "regex", "ignore"]
NameMatchMode = Literal["exact", "contains", "regex", "ignore"]


class LocatorStrategy(BaseModel):
    """A concrete strategy for locating a UI control and computing its target point."""

    id: str = "custom"
    label: str = "自定义策略"
    description: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"
    strategy_type: StrategyType = "process_automation_id"

    process_name: str = ""
    window_title: str = ""
    window_title_match: TitleMatchMode = "contains"
    window_class: str = ""

    control_type: str = ""
    automation_id: str = ""
    automation_id_match: TitleMatchMode = "exact"
    control_name: str = ""
    control_name_match: NameMatchMode = "exact"
    class_name: str = ""

    target_width: Optional[int] = None
    target_height: Optional[int] = None
    size_tolerance: int = 6

    tree_path: list[TreePathNode] = Field(default_factory=list)

    # Image template matching fields (used when strategy_type == "image_template")
    template_image_b64: str = ""
    template_image_path: str = ""
    similarity_threshold: float = 0.85
    image_grayscale: bool = False
    search_in_window: bool = False

    anchor_mode: AnchorMode = "center"
    offset_x: float = 0.5
    offset_y: float = 0.5

    timeout_ms: int = 3000
    activate_window: bool = True

    def compute_point(self, rect: Rect) -> tuple[int, int]:
        """Compute target physical screen coordinates inside the matched rect."""
        if self.anchor_mode == "center":
            return rect.center_x, rect.center_y
        if self.anchor_mode == "relative_ratio":
            rx = min(max(self.offset_x, 0.0), 1.0)
            ry = min(max(self.offset_y, 0.0), 1.0)
            return rect.left + int(round(rect.width * rx)), rect.top + int(round(rect.height * ry))
        # relative_px
        return rect.left + int(round(self.offset_x)), rect.top + int(round(self.offset_y))


ActionType = Literal[
    "mouse_move",
    "left_click",
    "left_down",
    "left_up",
    "right_click",
    "right_down",
    "right_up",
    "key_press",
    "key_down",
    "key_up",
    "text_input",
    "text_press",
    "clear_text",
    "delay",
]


class ActionStep(BaseModel):
    """Single mouse, keyboard, text, or delay action in a preset sequence."""

    type: ActionType
    use_target: bool = True
    offset_dx: int = 0
    offset_dy: int = 0
    duration_ms: int = 0
    key: str = ""
    text: str = ""
    use_clipboard: bool = False
    interval_ms: int = 25
    ms: str = "200"
    note: str = ""

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_mouse_types(cls, data: object) -> object:
        if isinstance(data, dict):
            t = data.get("type")
            btn = str(data.get("button", "left")).lower()
            prefix = "right" if btn == "right" else "left"
            if t == "mouse_click":
                data = {**data, "type": f"{prefix}_click"}
            elif t == "mouse_down":
                data = {**data, "type": f"{prefix}_down"}
            elif t == "mouse_up":
                data = {**data, "type": f"{prefix}_up"}
        return data

    def summary(self) -> str:
        """Short human-readable description for UI lists and logs."""
        if self.type == "mouse_move":
            target = "控件落点" if self.use_target else "当前位置"
            extra = f" ({self.duration_ms}ms)" if self.duration_ms > 0 else ""
            return f"鼠标移动 → {target}{extra}"
        if self.type == "left_click":
            return "LeftClick" + (" @控件" if self.use_target else " @当前")
        if self.type == "left_down":
            return "LeftDown" + (" @控件" if self.use_target else " @当前")
        if self.type == "left_up":
            return "LeftUp"
        if self.type == "right_click":
            return "RightClick" + (" @控件" if self.use_target else " @当前")
        if self.type == "right_down":
            return "RightDown" + (" @控件" if self.use_target else " @当前")
        if self.type == "right_up":
            return "RightUp"
        if self.type == "key_press":
            return f"键盘敲击 Press: {self.key}"
        if self.type == "key_down":
            return f"键盘按下 Down: {self.key}"
        if self.type == "key_up":
            return f"键盘抬起 Up: {self.key}"
        if self.type == "text_input":
            mode = "剪贴板" if self.use_clipboard else "Unicode"
            return f"文本输入 ({mode}): {self.text}"
        if self.type == "text_press":
            return f"逐键敲入 Press ({self.interval_ms}ms): {self.text}"
        if self.type == "clear_text":
            return "清空文本 (Ctrl+A + Backspace)" + (" @控件" if self.use_target else " @当前")
        if self.type == "delay":
            return f"延时 Delay: {self.ms}ms"
        return self.type


class ParameterDef(BaseModel):
    """Named parameter exposed by a preset for external invocation."""

    name: str
    param_type: Literal["string", "key", "number"] = "string"
    default: str = ""
    description: str = ""


class PresetGroupItem(BaseModel):
    """Single child preset entry inside a composite preset group."""

    preset_name: str
    delay_after_ms: int = 300
    param_overrides: dict[str, str] = Field(default_factory=dict)


PresetKind = Literal["single", "group"]


class Preset(BaseModel):
    """Saved automation preset (either a single control flow or a composite preset group)."""

    name: str
    kind: PresetKind = "single"
    description: str = ""
    locator: Optional[LocatorStrategy] = None
    parameters: list[ParameterDef] = Field(default_factory=list)
    actions: list[ActionStep] = Field(default_factory=list)
    group_items: list[PresetGroupItem] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class ResolvedControl(BaseModel):
    """Result of resolving a LocatorStrategy on the live desktop."""

    window_title: str = ""
    process_name: str = ""
    control_type: str = ""
    control_name: str = ""
    automation_id: str = ""
    rect: Rect
    target_x: int
    target_y: int
    elapsed_ms: int = 0
    match_score: Optional[float] = None
