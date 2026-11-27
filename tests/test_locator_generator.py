"""Unit tests for multi-strategy locator suggestion generation."""

from autotool.core.locator_generator import generate_locator_strategies
from autotool.models.schema import CapturedControl, Rect, TreePathNode


def _sample_captured(**overrides) -> CapturedControl:
    base = dict(
        process_name="notepad.exe",
        process_id=1234,
        window_title="无标题 - 记事本",
        window_class="Notepad",
        window_rect=Rect(left=100, top=100, right=900, bottom=700),
        control_type="EditControl",
        control_name="文本编辑器",
        automation_id="15",
        class_name="RichEditD2DPT",
        control_rect=Rect(left=110, top=160, right=890, bottom=680),
        tree_path=[
            TreePathNode(control_type="PaneControl", index=0),
            TreePathNode(control_type="EditControl", index=0, automation_id="15"),
        ],
        click_x=300,
        click_y=290,
        rel_x=190,
        rel_y=130,
        ratio_x=0.2436,
        ratio_y=0.25,
        win_ratio_x=0.25,
        win_ratio_y=0.3167,
    )
    base.update(overrides)
    return CapturedControl(**base)


def test_generates_full_strategy_suite_when_all_attrs_present() -> None:
    captured = _sample_captured()
    strategies = generate_locator_strategies(captured)
    types = [s.strategy_type for s in strategies]

    assert types[0] == "process_automation_id"
    assert "process_name_offset" in types
    assert "window_automation_id" in types
    assert "window_size_offset" in types
    assert "window_tree_path" in types
    assert types[-1] == "window_relative_ratio"

    # Verify size strategy carries exact captured dimensions
    size_strat = next(s for s in strategies if s.strategy_type == "window_size_offset")
    assert size_strat.target_width == 780
    assert size_strat.target_height == 520
    assert size_strat.anchor_mode == "relative_ratio"


def test_omits_id_and_name_strategies_for_anonymous_custom_control() -> None:
    captured = _sample_captured(automation_id="", control_name="")
    strategies = generate_locator_strategies(captured)
    types = [s.strategy_type for s in strategies]

    assert "process_automation_id" not in types
    assert "process_name_offset" not in types
    assert "window_automation_id" not in types
    assert "window_size_offset" in types
    assert "window_relative_ratio" in types


def test_compute_point_modes() -> None:
    captured = _sample_captured()
    strategies = generate_locator_strategies(captured)
    rect = Rect(left=100, top=200, right=300, bottom=400)

    s_center = strategies[0]
    s_center.anchor_mode = "center"
    assert s_center.compute_point(rect) == (200, 300)

    s_ratio = strategies[0].model_copy(
        update={"anchor_mode": "relative_ratio", "offset_x": 0.25, "offset_y": 0.75}
    )
    assert s_ratio.compute_point(rect) == (150, 350)

    s_px = strategies[0].model_copy(
        update={"anchor_mode": "relative_px", "offset_x": 12, "offset_y": 34}
    )
    assert s_px.compute_point(rect) == (112, 234)
