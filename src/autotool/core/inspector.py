"""Windows UI Automation control inspector for mouse-based element capture."""

from __future__ import annotations

import os
from typing import Optional

import psutil
import uiautomation as auto

from autotool.core.dpi import get_root_hwnd
from autotool.models.schema import CapturedControl, Rect, TreePathNode

_PID_NAME_CACHE: dict[int, str] = {}


def _get_process_name(pid: int) -> str:
    if pid <= 0:
        return ""
    cached = _PID_NAME_CACHE.get(pid)
    if cached is not None:
        return cached
    try:
        name = psutil.Process(pid).name()
    except Exception:
        name = ""
    if len(_PID_NAME_CACHE) > 256:
        _PID_NAME_CACHE.clear()
    _PID_NAME_CACHE[pid] = name
    return name


def _rect_from_uia(control: auto.Control) -> Rect:
    r = control.BoundingRectangle
    if r is None:
        return Rect(left=0, top=0, right=0, bottom=0)
    return Rect(left=int(r.left), top=int(r.top), right=int(r.right), bottom=int(r.bottom))


def _same_element(a: auto.Control, b: auto.Control) -> bool:
    try:
        ra = a.GetRuntimeId()
        rb = b.GetRuntimeId()
        if ra and rb:
            return tuple(ra) == tuple(rb)
    except Exception:
        pass
    try:
        return (
            a.NativeWindowHandle != 0
            and a.NativeWindowHandle == b.NativeWindowHandle
            and a.ControlTypeName == b.ControlTypeName
        )
    except Exception:
        return False


def _find_sibling_index(parent: auto.Control, child: auto.Control, max_scan: int = 80) -> int:
    """Compute 0-based index of `child` among siblings sharing the same ControlTypeName."""
    target_type = child.ControlTypeName
    idx = 0
    try:
        siblings = parent.GetChildren()
    except Exception:
        return 0
    for i, sib in enumerate(siblings):
        if i >= max_scan:
            break
        if _same_element(sib, child):
            return idx
        try:
            if sib.ControlTypeName == target_type:
                idx += 1
        except Exception:
            continue
    return idx


class UIAInspector:
    """Inspects live Windows UIA controls under a physical screen point."""

    def __init__(self, ignore_pid: int | None = None) -> None:
        self.ignore_pid = ignore_pid if ignore_pid is not None else os.getpid()

    def inspect_point(
        self,
        x: int,
        y: int,
        include_tree_path: bool = True,
    ) -> Optional[CapturedControl]:
        """Inspect the UIA control at physical coordinates (x, y).

        Returns None if the point belongs to this process or cannot be inspected.
        """
        try:
            ctrl = auto.ControlFromPoint(x, y)
        except Exception:
            return None

        if ctrl is None:
            return None

        try:
            pid = int(ctrl.ProcessId or 0)
        except Exception:
            pid = 0

        if pid == self.ignore_pid:
            return None

        # Walk up to top-level window (direct child of desktop root; allow deep Electron/Chromium trees)
        root = auto.GetRootControl()
        chain: list[auto.Control] = [ctrl]
        cur = ctrl
        for _ in range(128):
            try:
                parent = cur.GetParentControl()
            except Exception:
                break
            if parent is None or _same_element(parent, root):
                break
            chain.append(parent)
            cur = parent

        top_window = chain[-1]
        # Ensure we truly reached the OS top-level window via GA_ROOT if an HWND is available
        try:
            top_hwnd = int(top_window.NativeWindowHandle or 0)
            if not top_hwnd:
                for node in reversed(chain):
                    h = int(node.NativeWindowHandle or 0)
                    if h:
                        top_hwnd = h
                        break
            if top_hwnd:
                root_hwnd = get_root_hwnd(top_hwnd)
                if root_hwnd and root_hwnd != top_hwnd:
                    real_top = auto.ControlFromHandle(root_hwnd)
                    if real_top is not None:
                        top_window = real_top
        except Exception:
            pass

        try:
            top_pid = int(top_window.ProcessId or pid)
        except Exception:
            top_pid = pid
        if top_pid == self.ignore_pid:
            return None

        ctrl_rect = _rect_from_uia(ctrl)
        win_rect = _rect_from_uia(top_window)

        rel_x = x - ctrl_rect.left
        rel_y = y - ctrl_rect.top
        ratio_x = round(rel_x / ctrl_rect.width, 4) if ctrl_rect.width > 0 else 0.5
        ratio_y = round(rel_y / ctrl_rect.height, 4) if ctrl_rect.height > 0 else 0.5
        ratio_x = min(max(ratio_x, 0.0), 1.0)
        ratio_y = min(max(ratio_y, 0.0), 1.0)

        win_ratio_x = (
            round((x - win_rect.left) / win_rect.width, 4) if win_rect.width > 0 else 0.5
        )
        win_ratio_y = (
            round((y - win_rect.top) / win_rect.height, 4) if win_rect.height > 0 else 0.5
        )
        win_ratio_x = min(max(win_ratio_x, 0.0), 1.0)
        win_ratio_y = min(max(win_ratio_y, 0.0), 1.0)

        tree_path: list[TreePathNode] = []
        if include_tree_path and len(chain) > 1:
            # chain is [target, ..., top_window]; reverse to go top_window -> target
            top_down = list(reversed(chain))
            for i in range(1, len(top_down)):
                parent_node = top_down[i - 1]
                child_node = top_down[i]
                sib_idx = _find_sibling_index(parent_node, child_node)
                tree_path.append(
                    TreePathNode(
                        control_type=str(child_node.ControlTypeName or ""),
                        index=sib_idx,
                        name=str(child_node.Name or "")[:60],
                        automation_id=str(child_node.AutomationId or ""),
                        class_name=str(child_node.ClassName or ""),
                    )
                )

        return CapturedControl(
            process_name=_get_process_name(top_pid),
            process_id=top_pid,
            window_title=str(top_window.Name or ""),
            window_class=str(top_window.ClassName or ""),
            window_rect=win_rect,
            control_type=str(ctrl.ControlTypeName or ""),
            control_name=str(ctrl.Name or ""),
            automation_id=str(ctrl.AutomationId or ""),
            class_name=str(ctrl.ClassName or ""),
            control_rect=ctrl_rect,
            tree_path=tree_path,
            click_x=x,
            click_y=y,
            rel_x=rel_x,
            rel_y=rel_y,
            ratio_x=ratio_x,
            ratio_y=ratio_y,
            win_ratio_x=win_ratio_x,
            win_ratio_y=win_ratio_y,
        )
