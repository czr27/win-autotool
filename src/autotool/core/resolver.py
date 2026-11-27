"""Resolve a LocatorStrategy against live Windows UIA controls or screen image templates."""

from __future__ import annotations

import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import psutil
import uiautomation as auto

from autotool.core.dpi import bring_window_to_front, enum_visible_top_windows
from autotool.core.image_matcher import (
    find_all_templates_on_screen,
    find_template_on_screen,
    load_template_bgr,
)
from autotool.models.schema import LocatorStrategy, Rect, ResolvedControl


class ControlNotFoundError(RuntimeError):
    """Raised when no live control matches the LocatorStrategy within timeout."""


@dataclass
class LocatorDiagnostics:
    """Step-by-step match counts across process -> window title -> control layers."""

    strategy_type: str
    process_name: str = ""
    process_pids: list[int] = field(default_factory=list)
    process_window_count: int = 0
    process_window_titles: list[str] = field(default_factory=list)
    title_match_mode: str = "contains"
    window_title_query: str = ""
    title_matched_count: int = 0
    title_matched_titles: list[str] = field(default_factory=list)
    control_filter_desc: str = ""
    control_matches: list[ResolvedControl] = field(default_factory=list)
    elapsed_ms: int = 0
    error: str = ""

    def format_report(self) -> str:
        lines: list[str] = []
        # Stage 1: Process
        if self.process_name:
            pid_preview = ", ".join(str(p) for p in self.process_pids[:5])
            if len(self.process_pids) > 5:
                pid_preview += f"…共{len(self.process_pids)}个"
            icon1 = "✅" if self.process_pids else "❌"
            lines.append(
                f"{icon1} ① 进程 [{self.process_name}]：找到 {len(self.process_pids)} 个 PID"
                + (f" ({pid_preview})" if pid_preview else "")
                + f"，可见顶层窗口 {self.process_window_count} 个"
            )
        else:
            lines.append(f"ℹ ① 进程 [不限]：桌面可见顶层窗口 {self.process_window_count} 个")

        # Stage 2: Window title
        if self.strategy_type == "image_template" and not self.title_matched_count and not self.window_title_query and not self.process_name:
            lines.append("ℹ ② 窗口范围：全屏图像搜索")
        else:
            icon2 = "✅" if self.title_matched_count > 0 else "❌"
            q = self.window_title_query or "*"
            sample_t = ", ".join(f'"{t[:24]}"' for t in self.title_matched_titles[:2])
            if not sample_t and self.process_window_titles:
                sample_t = "候选标题: " + ", ".join(f'"{t[:20]}"' for t in self.process_window_titles[:2])
            lines.append(
                f"{icon2} ② 窗口标题 [{self.title_match_mode}: {q[:24]}]：符合 {self.title_matched_count} 个"
                + (f" ({sample_t})" if sample_t else "")
            )

        # Stage 3: Control / Image matches
        n = len(self.control_matches)
        icon3 = "✅" if n == 1 else ("⚠️" if n > 1 else "❌")
        lines.append(
            f"{icon3} ③ 控件匹配 [{self.control_filter_desc}]：符合 {n} 个 ({self.elapsed_ms}ms)"
        )
        if n >= 1:
            first = self.control_matches[0]
            score_s = f" 相似度={first.match_score:.1%}" if first.match_score is not None else ""
            lines.append(
                f"   ↳ 第1个命中：{first.control_type} {first.control_name or first.automation_id}"
                f"{score_s} {first.rect.width}×{first.rect.height} @落点({first.target_x}, {first.target_y})"
                + (f"（不唯一！共{n}个，建议提高相似度或加限定条件）" if n > 1 else "（唯一命中）")
            )
            if n > 1:
                others = []
                for idx, m in enumerate(self.control_matches[1:4], start=2):
                    ms = f" {m.match_score:.1%}" if m.match_score is not None else ""
                    others.append(f"#{idx}@({m.target_x},{m.target_y}){ms}")
                if n > 4:
                    others.append(f"…等共{n}处")
                lines.append("   ↳ 其他命中：" + ", ".join(others))
        elif self.error:
            lines.append(f"   ↳ 原因：{self.error}")
        return "\n".join(lines)


def _get_proc_name(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        return psutil.Process(pid).name()
    except Exception:
        return ""


def _rect_of(ctrl: auto.Control) -> Rect:
    r = ctrl.BoundingRectangle
    if r is None:
        return Rect(left=0, top=0, right=0, bottom=0)
    return Rect(left=int(r.left), top=int(r.top), right=int(r.right), bottom=int(r.bottom))


def _match_text(actual: str, expected: str, mode: str) -> bool:
    if mode == "ignore" or not expected:
        return True
    if mode == "exact":
        return actual == expected
    if mode == "contains":
        return expected.lower() in actual.lower()
    if mode == "regex":
        try:
            return re.search(expected, actual) is not None
        except re.error:
            return False
    return actual == expected


def _match_window(win: auto.Control, strategy: LocatorStrategy, self_pid: int) -> tuple[bool, str]:
    try:
        pid = int(win.ProcessId or 0)
    except Exception:
        pid = 0
    if pid == self_pid:
        return False, ""

    proc_name = _get_proc_name(pid)
    if strategy.process_name:
        if proc_name.lower() != strategy.process_name.lower():
            return False, proc_name

    if strategy.window_class and strategy.strategy_type in ("window_relative_ratio",):
        try:
            if str(win.ClassName or "") != strategy.window_class:
                return False, proc_name
        except Exception:
            return False, proc_name

    try:
        title = str(win.Name or "")
    except Exception:
        title = ""

    if not _match_text(title, strategy.window_title, strategy.window_title_match):
        return False, proc_name

    rect = _rect_of(win)
    if rect.width <= 0 or rect.height <= 0:
        return False, proc_name

    return True, proc_name


def _match_control_props(ctrl: auto.Control, strategy: LocatorStrategy) -> bool:
    if strategy.control_type:
        try:
            if str(ctrl.ControlTypeName or "") != strategy.control_type:
                return False
        except Exception:
            return False

    if strategy.automation_id and strategy.automation_id_match != "ignore":
        try:
            actual_aid = str(ctrl.AutomationId or "")
        except Exception:
            actual_aid = ""
        if not _match_text(actual_aid, strategy.automation_id, strategy.automation_id_match):
            return False

    if strategy.control_name and strategy.control_name_match != "ignore":
        try:
            actual_name = str(ctrl.Name or "")
        except Exception:
            actual_name = ""
        if not _match_text(actual_name, strategy.control_name, strategy.control_name_match):
            return False

    if strategy.strategy_type == "window_size_offset":
        if strategy.class_name:
            try:
                if str(ctrl.ClassName or "") != strategy.class_name:
                    return False
            except Exception:
                return False
        rect = _rect_of(ctrl)
        tol = max(0, int(strategy.size_tolerance))
        if strategy.target_width is not None:
            if abs(rect.width - strategy.target_width) > tol:
                return False
        if strategy.target_height is not None:
            if abs(rect.height - strategy.target_height) > tol:
                return False

    rect = _rect_of(ctrl)
    return rect.width > 0 and rect.height > 0


def _resolve_by_tree_path(
    win: auto.Control,
    strategy: LocatorStrategy,
) -> Optional[auto.Control]:
    cur = win
    for node in strategy.tree_path:
        try:
            children = cur.GetChildren()
        except Exception:
            return None
        same_type = [
            c for c in children if str(getattr(c, "ControlTypeName", "") or "") == node.control_type
        ]
        if node.index < len(same_type):
            cur = same_type[node.index]
        else:
            return None
    rect = _rect_of(cur)
    if rect.width <= 0 or rect.height <= 0:
        return None
    return cur


_TREE_SCOPE_DESCENDANTS = 4  # UIA TreeScope_Descendants


def _build_native_condition(strategy: LocatorStrategy):
    """Build a native C++ IUIAutomationCondition for fast, unlimited-depth descendant search."""
    from uiautomation.uiautomation import _AutomationClient

    client = _AutomationClient.instance().IUIAutomation
    conds = []

    if strategy.control_type:
        ct_id = getattr(auto.ControlType, strategy.control_type, None)
        if isinstance(ct_id, int):
            conds.append(client.CreatePropertyCondition(auto.PropertyId.ControlTypeProperty, ct_id))

    if strategy.automation_id and strategy.automation_id_match == "exact":
        conds.append(
            client.CreatePropertyCondition(
                auto.PropertyId.AutomationIdProperty, strategy.automation_id
            )
        )

    if strategy.control_name and strategy.control_name_match == "exact":
        conds.append(
            client.CreatePropertyCondition(auto.PropertyId.NameProperty, strategy.control_name)
        )

    if strategy.strategy_type == "window_size_offset" and strategy.class_name:
        conds.append(
            client.CreatePropertyCondition(auto.PropertyId.ClassNameProperty, strategy.class_name)
        )

    if not conds:
        return client.CreateTrueCondition()

    combined = conds[0]
    for extra in conds[1:]:
        combined = client.CreateAndCondition(combined, extra)
    return combined


def _search_descendants_native(
    win: auto.Control,
    strategy: LocatorStrategy,
) -> Optional[auto.Control]:
    """Use native C++ UIA FindFirst/FindAll so deep Electron/Chromium trees are searched in milliseconds."""
    cond = _build_native_condition(strategy)
    needs_python_filter = (
        strategy.strategy_type == "window_size_offset"
        or (bool(strategy.control_name) and strategy.control_name_match in ("contains", "regex"))
        or (bool(strategy.automation_id) and strategy.automation_id_match in ("contains", "regex"))
    )

    if not needs_python_filter:
        el = win.Element.FindFirst(_TREE_SCOPE_DESCENDANTS, cond)
        if el is None:
            return None
        ctrl = auto.Control.CreateControlFromElement(el)
        rect = _rect_of(ctrl)
        if rect.width > 0 and rect.height > 0:
            return ctrl
        # If the first match has 0x0 rect (hidden duplicate), scan FindAll for a visible one
        arr = win.Element.FindAll(_TREE_SCOPE_DESCENDANTS, cond)
        if arr is None:
            return None
        for i in range(arr.Length):
            cand = auto.Control.CreateControlFromElement(arr.GetElement(i))
            r = _rect_of(cand)
            if r.width > 0 and r.height > 0:
                return cand
        return None

    arr = win.Element.FindAll(_TREE_SCOPE_DESCENDANTS, cond)
    if arr is None:
        return None
    for i in range(arr.Length):
        cand = auto.Control.CreateControlFromElement(arr.GetElement(i))
        if _match_control_props(cand, strategy):
            return cand
    return None


def _search_descendants_bfs(
    win: auto.Control,
    strategy: LocatorStrategy,
    max_depth: int = 16,
    max_nodes: int = 2500,
) -> Optional[auto.Control]:
    try:
        hit = _search_descendants_native(win, strategy)
        if hit is not None:
            return hit
        return None
    except Exception:
        pass

    if _match_control_props(win, strategy):
        return win

    queue: deque[tuple[auto.Control, int]] = deque([(win, 0)])
    visited = 0
    while queue and visited < max_nodes:
        node, depth = queue.popleft()
        if depth >= max_depth:
            continue
        try:
            children = node.GetChildren()
        except Exception:
            continue
        for child in children:
            visited += 1
            if _match_control_props(child, strategy):
                return child
            if visited >= max_nodes:
                break
            queue.append((child, depth + 1))
    return None


def _find_all_descendants_native(
    win: auto.Control,
    strategy: LocatorStrategy,
    limit: int | None = None,
) -> list[auto.Control]:
    """Return ALL visible matching controls inside `win` using native C++ UIA FindAll."""
    cond = _build_native_condition(strategy)
    needs_python_filter = (
        strategy.strategy_type == "window_size_offset"
        or (bool(strategy.control_name) and strategy.control_name_match in ("contains", "regex"))
        or (bool(strategy.automation_id) and strategy.automation_id_match in ("contains", "regex"))
    )
    arr = win.Element.FindAll(_TREE_SCOPE_DESCENDANTS, cond)
    if arr is None:
        return []
    hits: list[auto.Control] = []
    for i in range(arr.Length):
        cand = auto.Control.CreateControlFromElement(arr.GetElement(i))
        if needs_python_filter:
            if not _match_control_props(cand, strategy):
                continue
        else:
            r = _rect_of(cand)
            if r.width <= 0 or r.height <= 0:
                continue
        hits.append(cand)
        if limit is not None and len(hits) >= limit:
            break
    return hits


def _describe_control_filter(strategy: LocatorStrategy) -> str:
    if strategy.strategy_type == "image_template":
        return f"图像模板 >{strategy.similarity_threshold:.0%}"
    if strategy.strategy_type == "window_relative_ratio":
        return f"窗口相对比例 ({strategy.offset_x:.1%}, {strategy.offset_y:.1%})"
    parts: list[str] = []
    if strategy.control_type:
        parts.append(strategy.control_type)
    if strategy.automation_id and strategy.automation_id_match != "ignore":
        mode_tag = "" if strategy.automation_id_match == "exact" else f"[{strategy.automation_id_match}]"
        parts.append(f"ID{mode_tag}={strategy.automation_id}")
    if strategy.control_name and strategy.control_name_match != "ignore":
        mode_tag = "" if strategy.control_name_match == "exact" else f"[{strategy.control_name_match}]"
        parts.append(f"名称{mode_tag}={strategy.control_name[:20]}")
    if strategy.strategy_type == "window_size_offset" and strategy.target_width and strategy.target_height:
        parts.append(f"尺寸={strategy.target_width}×{strategy.target_height}±{strategy.size_tolerance}")
    if strategy.strategy_type == "window_tree_path":
        parts.append(f"树路径({len(strategy.tree_path)}层)")
    return " | ".join(parts) or strategy.strategy_type


def _list_process_pids(process_name: str, ignore_pid: int) -> list[int]:
    if not process_name:
        return []
    target = process_name.lower()
    pids: list[int] = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pid = int(proc.info["pid"])
            name = str(proc.info["name"] or "")
            if pid != ignore_pid and name.lower() == target:
                pids.append(pid)
        except Exception:
            continue
    return pids


class ControlResolver:
    """Finds a live control on the Windows desktop using a LocatorStrategy."""

    def __init__(self, ignore_pid: int | None = None) -> None:
        self.ignore_pid = ignore_pid if ignore_pid is not None else os.getpid()

    def resolve(
        self,
        strategy: LocatorStrategy,
        timeout_override_ms: int | None = None,
        activate: bool = True,
    ) -> ResolvedControl:
        """Resolve `strategy` within timeout, or raise ControlNotFoundError."""
        timeout_ms = (
            timeout_override_ms if timeout_override_ms is not None else strategy.timeout_ms
        )
        deadline = time.monotonic() + max(0, timeout_ms) / 1000.0
        start = time.monotonic()

        while True:
            found = self._resolve_once(strategy, activate=activate)
            if found is not None:
                found.elapsed_ms = int((time.monotonic() - start) * 1000)
                return found
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)

        if strategy.strategy_type == "image_template":
            raise ControlNotFoundError(
                f"在 {timeout_ms}ms 内未找到匹配控件 "
                f"(策略=image_template, 相似度阈值>{strategy.similarity_threshold:.0%}, "
                f"限定窗口={strategy.search_in_window})"
            )

        raise ControlNotFoundError(
            f"在 {timeout_ms}ms 内未找到匹配控件 "
            f"(策略={strategy.strategy_type}, 进程={strategy.process_name or '*'}, "
            f"窗口={strategy.window_title or '*'}, ID={strategy.automation_id or '*'}, "
            f"名称={strategy.control_name or '*'})"
        )

    def diagnose(self, strategy: LocatorStrategy) -> LocatorDiagnostics:
        """Run an exhaustive step-by-step diagnostic funnel across ALL matching windows and occurrences."""
        t0 = time.monotonic()
        diag = LocatorDiagnostics(
            strategy_type=strategy.strategy_type,
            process_name=strategy.process_name,
            title_match_mode=strategy.window_title_match,
            window_title_query=strategy.window_title,
            control_filter_desc=_describe_control_filter(strategy),
        )

        # Stage 1: Process PIDs & visible windows belonging to process
        if strategy.process_name:
            diag.process_pids = _list_process_pids(strategy.process_name, self.ignore_pid)

        all_wins = enum_visible_top_windows()
        proc_wins: list[dict] = []
        for info in all_wins:
            pid = info["pid"]
            if pid == self.ignore_pid:
                continue
            pname = _get_proc_name(pid)
            if strategy.process_name and pname.lower() != strategy.process_name.lower():
                continue
            info["proc_name"] = pname
            proc_wins.append(info)

        diag.process_window_count = len(proc_wins)
        diag.process_window_titles = [w["title"] for w in proc_wins if w["title"]]

        # Stage 2: Window title filter
        title_wins = [
            w for w in proc_wins if _match_text(w["title"], strategy.window_title, strategy.window_title_match)
        ]
        diag.title_matched_count = len(title_wins)
        diag.title_matched_titles = [w["title"] for w in title_wins if w["title"]]

        # Stage 3: Exhaustive Control / Image matching across ALL candidates
        try:
            if strategy.strategy_type == "image_template":
                template_bgr = load_template_bgr(
                    b64_str=strategy.template_image_b64,
                    file_path=strategy.template_image_path,
                )
                if strategy.search_in_window and (strategy.process_name or strategy.window_title):
                    for info in title_wins:
                        l, t, r, b = info["rect"]
                        wrect = Rect(left=l, top=t, right=r, bottom=b)
                        if wrect.width <= 0 or wrect.height <= 0:
                            continue
                        hits = find_all_templates_on_screen(
                            template_bgr=template_bgr,
                            threshold=strategy.similarity_threshold,
                            search_rect=wrect,
                            grayscale=strategy.image_grayscale,
                        )
                        for mrect, score in hits:
                            tx, ty = strategy.compute_point(mrect)
                            diag.control_matches.append(
                                ResolvedControl(
                                    window_title=info["title"],
                                    process_name=info["proc_name"],
                                    control_type="ImageTemplate",
                                    control_name=f"相似度 {score:.1%}",
                                    rect=mrect,
                                    target_x=tx,
                                    target_y=ty,
                                    match_score=round(score, 4),
                                )
                            )
                else:
                    hits = find_all_templates_on_screen(
                        template_bgr=template_bgr,
                        threshold=strategy.similarity_threshold,
                        search_rect=None,
                        grayscale=strategy.image_grayscale,
                    )
                    for mrect, score in hits:
                        tx, ty = strategy.compute_point(mrect)
                        diag.control_matches.append(
                            ResolvedControl(
                                window_title="(全屏图像匹配)",
                                process_name="",
                                control_type="ImageTemplate",
                                control_name=f"相似度 {score:.1%}",
                                rect=mrect,
                                target_x=tx,
                                target_y=ty,
                                match_score=round(score, 4),
                            )
                        )
            elif strategy.strategy_type == "window_relative_ratio":
                for info in title_wins:
                    l, t, r, b = info["rect"]
                    wrect = Rect(left=l, top=t, right=r, bottom=b)
                    tx, ty = strategy.compute_point(wrect)
                    diag.control_matches.append(
                        ResolvedControl(
                            window_title=info["title"],
                            process_name=info["proc_name"],
                            control_type="WindowControl",
                            control_name=info["title"],
                            rect=wrect,
                            target_x=tx,
                            target_y=ty,
                        )
                    )
            else:
                for info in title_wins:
                    try:
                        win = auto.ControlFromHandle(info["hwnd"])
                    except Exception:
                        continue
                    if win is None:
                        continue
                    if strategy.strategy_type == "window_tree_path":
                        target = _resolve_by_tree_path(win, strategy)
                        cands = [target] if target is not None else []
                    else:
                        cands = _find_all_descendants_native(win, strategy, limit=None)
                    for ctrl in cands:
                        rect = _rect_of(ctrl)
                        if rect.width <= 0 or rect.height <= 0:
                            continue
                        tx, ty = strategy.compute_point(rect)
                        diag.control_matches.append(
                            ResolvedControl(
                                window_title=info["title"],
                                process_name=info["proc_name"],
                                control_type=str(getattr(ctrl, "ControlTypeName", "") or ""),
                                control_name=str(getattr(ctrl, "Name", "") or ""),
                                automation_id=str(getattr(ctrl, "AutomationId", "") or ""),
                                rect=rect,
                                target_x=tx,
                                target_y=ty,
                            )
                        )
        except Exception as exc:
            diag.error = str(exc)

        diag.elapsed_ms = int((time.monotonic() - t0) * 1000)
        return diag

    def _find_matching_win32_windows(self, strategy: LocatorStrategy) -> list[dict]:
        """Find visible top-level windows matching process_name & window_title via Win32 EnumWindows."""
        matched: list[dict] = []
        for info in enum_visible_top_windows():
            pid = info["pid"]
            if pid == self.ignore_pid:
                continue
            proc_name = _get_proc_name(pid)
            if strategy.process_name and proc_name.lower() != strategy.process_name.lower():
                continue
            if not _match_text(info["title"], strategy.window_title, strategy.window_title_match):
                continue
            info["proc_name"] = proc_name
            matched.append(info)
        return matched

    def _resolve_once(
        self,
        strategy: LocatorStrategy,
        activate: bool = True,
    ) -> Optional[ResolvedControl]:
        if strategy.strategy_type == "image_template":
            return self._resolve_image_template(strategy, activate=activate)

        win_infos = self._find_matching_win32_windows(strategy)
        for info in win_infos:
            hwnd = info["hwnd"]
            proc_name = info["proc_name"]
            l, t, r, b = info["rect"]
            win_rect = Rect(left=l, top=t, right=r, bottom=b)

            # Pure window relative ratio needs no UIA traversal at all!
            if strategy.strategy_type == "window_relative_ratio":
                if activate and strategy.activate_window and hwnd:
                    bring_window_to_front(hwnd)
                    time.sleep(0.05)
                tx, ty = strategy.compute_point(win_rect)
                return ResolvedControl(
                    window_title=info["title"],
                    process_name=proc_name,
                    control_type="WindowControl",
                    control_name=info["title"],
                    automation_id="",
                    rect=win_rect,
                    target_x=tx,
                    target_y=ty,
                )

            try:
                win = auto.ControlFromHandle(hwnd)
            except Exception:
                continue
            if win is None:
                continue

            if strategy.strategy_type == "window_tree_path":
                target = _resolve_by_tree_path(win, strategy)
            else:
                target = _search_descendants_bfs(win, strategy)

            if target is None:
                continue

            if activate and strategy.activate_window and hwnd:
                bring_window_to_front(hwnd)
                time.sleep(0.05)

            rect = _rect_of(target)
            if rect.width <= 0 or rect.height <= 0:
                continue

            tx, ty = strategy.compute_point(rect)
            return ResolvedControl(
                window_title=info["title"],
                process_name=proc_name,
                control_type=str(getattr(target, "ControlTypeName", "") or ""),
                control_name=str(getattr(target, "Name", "") or ""),
                automation_id=str(getattr(target, "AutomationId", "") or ""),
                rect=rect,
                target_x=tx,
                target_y=ty,
            )

        return None

    def _resolve_image_template(
        self,
        strategy: LocatorStrategy,
        activate: bool = True,
    ) -> Optional[ResolvedControl]:
        template_bgr = load_template_bgr(
            b64_str=strategy.template_image_b64,
            file_path=strategy.template_image_path,
        )

        search_rect: Optional[Rect] = None
        matched_win_title = ""
        matched_proc_name = ""

        if strategy.search_in_window and (strategy.process_name or strategy.window_title):
            win_infos = self._find_matching_win32_windows(strategy)
            if not win_infos:
                return None
            info = win_infos[0]
            matched_proc_name = info["proc_name"]
            matched_win_title = info["title"]
            hwnd = info["hwnd"]
            if activate and strategy.activate_window and hwnd:
                bring_window_to_front(hwnd)
                time.sleep(0.06)
            l, t, r, b = info["rect"]
            search_rect = Rect(left=l, top=t, right=r, bottom=b)
            if search_rect.width <= 0 or search_rect.height <= 0:
                return None

        match = find_template_on_screen(
            template_bgr=template_bgr,
            threshold=strategy.similarity_threshold,
            search_rect=search_rect,
            grayscale=strategy.image_grayscale,
        )
        if match is None:
            return None

        rect, score = match
        tx, ty = strategy.compute_point(rect)
        return ResolvedControl(
            window_title=matched_win_title or "(全屏图像匹配)",
            process_name=matched_proc_name,
            control_type="ImageTemplate",
            control_name=f"相似度 {score:.1%}",
            automation_id="",
            rect=rect,
            target_x=tx,
            target_y=ty,
            match_score=round(score, 4),
        )
