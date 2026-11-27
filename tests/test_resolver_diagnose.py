"""Tests for step-by-step ControlResolver.diagnose() funnel reporting."""

from autotool.core import resolver as resolver_mod
from autotool.core.resolver import ControlResolver
from autotool.models.schema import LocatorStrategy


def test_diagnose_reports_pids_window_counts_and_title_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(resolver_mod, "_list_process_pids", lambda name, ign: [1001, 1002, 1003])
    monkeypatch.setattr(resolver_mod, "_get_proc_name", lambda pid: "Antigravity.exe")
    monkeypatch.setattr(
        resolver_mod,
        "enum_visible_top_windows",
        lambda: [
            {"hwnd": 11, "pid": 1001, "title": "ProjectA - Antigravity", "class_name": "Chrome_WidgetWin_1", "rect": (0, 0, 1200, 800)},
            {"hwnd": 12, "pid": 1002, "title": "ProjectB - Antigravity", "class_name": "Chrome_WidgetWin_1", "rect": (50, 50, 1250, 850)},
        ],
    )

    # Case 1: Title filter matches 0 windows
    strat_miss = LocatorStrategy(
        strategy_type="window_relative_ratio",
        process_name="Antigravity.exe",
        window_title="NonExistentTitle",
        window_title_match="contains",
    )
    d1 = ControlResolver().diagnose(strat_miss)
    assert d1.process_pids == [1001, 1002, 1003]
    assert d1.process_window_count == 2
    assert d1.title_matched_count == 0
    assert len(d1.control_matches) == 0
    rep1 = d1.format_report()
    assert "找到 3 个 PID" in rep1
    assert "可见顶层窗口 2 个" in rep1
    assert "符合 0 个" in rep1

    # Case 2: Title filter matches 2 windows -> reports 2 matches with ambiguity hint
    strat_hit = LocatorStrategy(
        strategy_type="window_relative_ratio",
        process_name="Antigravity.exe",
        window_title="Antigravity",
        window_title_match="contains",
        anchor_mode="relative_ratio",
        offset_x=0.5,
        offset_y=0.5,
    )
    d2 = ControlResolver().diagnose(strat_hit)
    assert d2.title_matched_count == 2
    assert len(d2.control_matches) == 2
    rep2 = d2.format_report()
    assert "符合 2 个" in rep2
    assert "不唯一！共2个" in rep2


def test_control_name_and_aid_match_modes() -> None:
    from autotool.core.resolver import _match_control_props

    class _DummyRect:
        left, top, right, bottom = 0, 0, 100, 30

    class _DummyCtrl:
        ControlTypeName = "ButtonControl"
        Name = "General Greeting (12:30)"
        AutomationId = "btn_submit_42"
        ClassName = "Button"
        BoundingRectangle = _DummyRect()

    ctrl = _DummyCtrl()

    # Exact fails for partial keyword, contains succeeds
    s_exact = LocatorStrategy(
        control_type="ButtonControl",
        control_name="Greeting",
        control_name_match="exact",
    )
    assert _match_control_props(ctrl, s_exact) is False

    s_contains = LocatorStrategy(
        control_type="ButtonControl",
        control_name="Greeting",
        control_name_match="contains",
        automation_id="submit",
        automation_id_match="contains",
    )
    assert _match_control_props(ctrl, s_contains) is True

    # Regex match on AutomationId and ignore on Name
    s_regex = LocatorStrategy(
        control_type="ButtonControl",
        control_name="anything",
        control_name_match="ignore",
        automation_id=r"^btn_submit_\d+$",
        automation_id_match="regex",
    )
    assert _match_control_props(ctrl, s_regex) is True
