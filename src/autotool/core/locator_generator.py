"""Generate ranked locator strategy suggestions from a captured UIA control."""

from __future__ import annotations

from autotool.models.schema import CapturedControl, LocatorStrategy


def generate_locator_strategies(captured: CapturedControl) -> list[LocatorStrategy]:
    """Produce multiple practical locator strategies ranked from most to least stable."""
    strategies: list[LocatorStrategy] = []

    proc = captured.process_name
    win_title = captured.window_title
    win_cls = captured.window_class
    ctype = captured.control_type
    cname = captured.control_name
    aid = captured.automation_id
    ccls = captured.class_name
    cw = captured.control_rect.width
    ch = captured.control_rect.height

    # 1. Process -> AutomationId (highest stability for WPF/WinForms/UWP/modern apps)
    if aid:
        strategies.append(
            LocatorStrategy(
                id="process_automation_id",
                label="进程 → AutomationId",
                description=(
                    f"在进程 {proc or '(任意)'} 中通过 AutomationId='{aid}' "
                    f"({ctype}) 精确定位，不受窗口标题或文字变化影响。"
                ),
                confidence="high",
                strategy_type="process_automation_id",
                process_name=proc,
                window_title=win_title,
                window_title_match="ignore" if proc else "contains",
                window_class=win_cls,
                control_type=ctype,
                automation_id=aid,
                anchor_mode="center",
                offset_x=captured.ratio_x,
                offset_y=captured.ratio_y,
            )
        )

    # 2. Process -> ControlType + ControlName -> Relative position
    if cname:
        strategies.append(
            LocatorStrategy(
                id="process_name_offset",
                label="进程 → 控件名称 → 相对位置",
                description=(
                    f"在进程 {proc or '(任意)'} 中按控件类型 {ctype} 与名称 '{cname[:32]}' "
                    f"定位，并点击控件相对位置 ({captured.ratio_x:.0%}, {captured.ratio_y:.0%})。"
                ),
                confidence="high" if proc else "medium",
                strategy_type="process_name_offset",
                process_name=proc,
                window_title=win_title,
                window_title_match="ignore" if proc else "contains",
                window_class=win_cls,
                control_type=ctype,
                control_name=cname,
                control_name_match="exact",
                anchor_mode="relative_ratio",
                offset_x=captured.ratio_x,
                offset_y=captured.ratio_y,
            )
        )

    # 3. Window Title -> AutomationId / ControlName (best when multiple windows of same process exist)
    if win_title and (aid or cname):
        key_desc = f"AutomationId='{aid}'" if aid else f"名称='{cname[:32]}'"
        strategies.append(
            LocatorStrategy(
                id="window_automation_id",
                label="窗口名称 → 控件ID/名称",
                description=(
                    f"先匹配标题包含 '{win_title[:32]}' 的窗口，再通过 {key_desc} 定位，"
                    f"适合同一程序打开多个窗口的场景。"
                ),
                confidence="high" if aid else "medium",
                strategy_type="window_automation_id",
                process_name=proc,
                window_title=win_title,
                window_title_match="contains",
                window_class=win_cls,
                control_type=ctype,
                automation_id=aid,
                control_name="" if aid else cname,
                control_name_match="ignore" if aid else "exact",
                anchor_mode="center",
                offset_x=captured.ratio_x,
                offset_y=captured.ratio_y,
            )
        )

    # 4. Window / Process -> ControlType + Specific Size (W x H) -> Relative position
    if cw > 0 and ch > 0:
        strategies.append(
            LocatorStrategy(
                id="window_size_offset",
                label=f"窗口 → 控件特定大小 ({cw}×{ch}) → 相对位置",
                description=(
                    f"在目标窗口中查找类型为 {ctype or '任意'}、尺寸约为 {cw}×{ch} (±6px) 的控件，"
                    f"再按相对位置 ({captured.ratio_x:.0%}, {captured.ratio_y:.0%}) 操作，适合无名称/无ID控件。"
                ),
                confidence="medium",
                strategy_type="window_size_offset",
                process_name=proc,
                window_title=win_title,
                window_title_match="contains" if win_title else "ignore",
                window_class=win_cls,
                control_type=ctype,
                class_name=ccls,
                target_width=cw,
                target_height=ch,
                size_tolerance=6,
                anchor_mode="relative_ratio",
                offset_x=captured.ratio_x,
                offset_y=captured.ratio_y,
            )
        )

    # 5. Window -> UIA Tree Path -> Relative position
    if captured.tree_path and len(captured.tree_path) <= 12:
        path_preview = " > ".join(
            f"{n.control_type}[{n.index}]" for n in captured.tree_path[-3:]
        )
        strategies.append(
            LocatorStrategy(
                id="window_tree_path",
                label="窗口 → 控件层级路径 → 相对位置",
                description=(
                    f"沿 UI 树路径 (… > {path_preview}) 逐层定位到目标控件，"
                    f"适合静态布局但缺少稳定名称/ID的界面。"
                ),
                confidence="medium",
                strategy_type="window_tree_path",
                process_name=proc,
                window_title=win_title,
                window_title_match="contains" if win_title else "ignore",
                window_class=win_cls,
                control_type=ctype,
                tree_path=list(captured.tree_path),
                anchor_mode="relative_ratio",
                offset_x=captured.ratio_x,
                offset_y=captured.ratio_y,
            )
        )

    # 6. Fallback: Window Relative Ratio (works even on canvas / DirectX / custom UI)
    strategies.append(
        LocatorStrategy(
            id="window_relative_ratio",
            label="窗口相对比例兜底",
            description=(
                f"直接定位到顶层窗口 ({proc or win_title[:24]})，按窗口内相对比例 "
                f"({captured.win_ratio_x:.1%}, {captured.win_ratio_y:.1%}) 操作，适合自绘/游戏/画布界面。"
            ),
            confidence="low",
            strategy_type="window_relative_ratio",
            process_name=proc,
            window_title=win_title,
            window_title_match="contains" if win_title else "ignore",
            window_class=win_cls,
            anchor_mode="relative_ratio",
            offset_x=captured.win_ratio_x,
            offset_y=captured.win_ratio_y,
        )
    )

    return strategies
