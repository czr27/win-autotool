"""Action sequence runner combining ControlResolver, template rendering, and Win32Input."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from autotool.core.audit_log import AuditLogger, get_audit_logger, local_timestamp_ms
from autotool.core.dpi import is_vk_pressed
from autotool.core.resolver import ControlResolver
from autotool.core.win32_input import VK_MAP, Win32Input
from autotool.models.preset_store import PresetStore, render_template, resolve_params
from autotool.models.schema import ActionStep, Preset, ResolvedControl


class ExecutionAbortedError(RuntimeError):
    """Raised when execution is cancelled by user or Esc safety hotkey."""


class GroupValidationError(RuntimeError):
    """Raised when a preset group fails pre-flight validation (missing child or cycle)."""


@dataclass
class StepLog:
    index: int
    action_type: str
    detail: str
    elapsed_ms: int
    success: bool = True
    error: str = ""
    timestamp: str = field(default_factory=local_timestamp_ms)


@dataclass
class RunResult:
    preset_name: str
    success: bool
    run_id: str = ""
    source: str = "internal"
    started_at: str = ""
    finished_at: str = ""
    resolved: Optional[ResolvedControl] = None
    steps: list[StepLog] = field(default_factory=list)
    total_ms: int = 0
    error: str = ""
    dry_run: bool = False


class ActionRunner:
    """Executes a Preset's locator resolution and action steps (or a composite preset group)."""

    def __init__(
        self,
        resolver: ControlResolver | None = None,
        input_driver: Win32Input | None = None,
        store: PresetStore | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.resolver = resolver or ControlResolver()
        self.input_driver = input_driver or Win32Input()
        self.store = store
        self.audit_logger = audit_logger or get_audit_logger()

    def _check_abort(self, stop_event: threading.Event | None, esc_abort: bool) -> None:
        if stop_event is not None and stop_event.is_set():
            raise ExecutionAbortedError("执行已被手动停止")
        if esc_abort and is_vk_pressed(VK_MAP["esc"]):
            raise ExecutionAbortedError("检测到按下 Esc，已紧急停止执行")

    def _sleep_interruptible(
        self,
        ms: int,
        stop_event: threading.Event | None,
        esc_abort: bool,
    ) -> None:
        end = time.monotonic() + max(0, ms) / 1000.0
        while True:
            self._check_abort(stop_event, esc_abort)
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.05, remaining))

    def validate_group_tree(
        self,
        preset: Preset,
        store: PresetStore,
        _stack: tuple[str, ...] = (),
    ) -> dict[str, Preset]:
        """Recursively verify that every child preset in a group exists and has no cycles.

        Returns a dict mapping preset_name -> loaded Preset.
        Raises GroupValidationError if any child is missing or a cycle is detected.
        """
        if not preset.group_items:
            raise GroupValidationError(f"预设组 [{preset.name}] 中没有任何子预设")

        chain = _stack + (preset.name,)
        loaded: dict[str, Preset] = {}
        missing: list[str] = []

        for item in preset.group_items:
            cname = item.preset_name.strip()
            if cname in chain:
                cycle_str = " -> ".join(chain + (cname,))
                raise GroupValidationError(f"预设组存在循环引用：{cycle_str}")
            if cname in loaded:
                continue
            try:
                child = store.load(cname)
            except (FileNotFoundError, ValueError):
                if cname not in missing:
                    missing.append(cname)
                continue
            loaded[cname] = child

        if missing:
            raise GroupValidationError(
                f"预设组 [{preset.name}] 执行前检查失败：以下子预设不存在：{', '.join(missing)}"
            )

        # Recursively validate any nested groups
        nested_missing: list[str] = []
        for cname, child in list(loaded.items()):
            if child.kind == "group":
                try:
                    sub_loaded = self.validate_group_tree(child, store, _stack=chain)
                    loaded.update(sub_loaded)
                except GroupValidationError as exc:
                    nested_missing.append(str(exc))

        if nested_missing:
            raise GroupValidationError("；".join(nested_missing))

        return loaded

    def run_preset(
        self,
        preset: Preset,
        params: dict[str, Any] | None = None,
        timeout_override_ms: int | None = None,
        dry_run: bool = False,
        stop_event: threading.Event | None = None,
        esc_abort: bool = False,
        on_resolved: Callable[[ResolvedControl], None] | None = None,
        on_step: Callable[[StepLog], None] | None = None,
        store: PresetStore | None = None,
        source: str = "internal",
        _run_id: str | None = None,
    ) -> RunResult:
        is_top_level = _run_id is None
        run_id = _run_id or self.audit_logger.new_run_id()

        if preset.kind == "group":
            return self._run_group(
                preset=preset,
                params=params,
                timeout_override_ms=timeout_override_ms,
                dry_run=dry_run,
                stop_event=stop_event,
                esc_abort=esc_abort,
                on_resolved=on_resolved,
                on_step=on_step,
                store=store or self.store or PresetStore(),
                source=source,
                run_id=run_id,
                is_top_level=is_top_level,
            )

        t0 = time.monotonic()
        started_at = local_timestamp_ms()
        merged_params = resolve_params(preset, params)
        result = RunResult(
            preset_name=preset.name,
            success=False,
            run_id=run_id,
            source=source,
            started_at=started_at,
            dry_run=dry_run,
        )

        if is_top_level:
            self.audit_logger.log_run_start(
                run_id=run_id,
                source=source,
                preset_name=preset.name,
                kind=preset.kind,
                params=merged_params,
                dry_run=dry_run,
                timestamp=started_at,
            )

        def _record_step(log: StepLog) -> None:
            result.steps.append(log)
            if is_top_level:
                self.audit_logger.log_step(
                    run_id=run_id,
                    index=log.index,
                    action_type=log.action_type,
                    detail=log.detail,
                    success=log.success,
                    elapsed_ms=log.elapsed_ms,
                    error=log.error,
                    timestamp=log.timestamp,
                )
            if on_step:
                on_step(log)

        step_num = 0
        try:
            self._check_abort(stop_event, esc_abort)
            if preset.locator is None:
                raise ValueError(f"预设 [{preset.name}] 缺少定位策略配置")

            st_res = time.monotonic()
            try:
                resolved = self.resolver.resolve(
                    preset.locator,
                    timeout_override_ms=timeout_override_ms,
                    activate=not dry_run,
                )
            except Exception as exc:
                step_num += 1
                _record_step(
                    StepLog(
                        index=step_num,
                        action_type="resolve",
                        detail=f"定位控件 ({preset.locator.strategy_type})",
                        elapsed_ms=int((time.monotonic() - st_res) * 1000),
                        success=False,
                        error=str(exc),
                    )
                )
                raise

            result.resolved = resolved
            step_num += 1
            score_txt = f" 相似度={resolved.match_score:.1%}" if resolved.match_score is not None else ""
            _record_step(
                StepLog(
                    index=step_num,
                    action_type="resolve",
                    detail=(
                        f"定位成功: {resolved.process_name or 'Screen'} | {resolved.control_type}"
                        f"{score_txt} @({resolved.target_x}, {resolved.target_y})"
                    ),
                    elapsed_ms=resolved.elapsed_ms,
                    success=True,
                )
            )
            if on_resolved:
                on_resolved(resolved)

            for step in preset.actions:
                self._check_abort(stop_event, esc_abort)
                st = time.monotonic()
                step_num += 1
                try:
                    detail = self._execute_step(
                        step=step,
                        resolved=resolved,
                        params=merged_params,
                        dry_run=dry_run,
                        stop_event=stop_event,
                        esc_abort=esc_abort,
                    )
                except Exception as exc:
                    _record_step(
                        StepLog(
                            index=step_num,
                            action_type=step.type,
                            detail=step.summary(),
                            elapsed_ms=int((time.monotonic() - st) * 1000),
                            success=False,
                            error=str(exc),
                        )
                    )
                    raise
                _record_step(
                    StepLog(
                        index=step_num,
                        action_type=step.type,
                        detail=detail,
                        elapsed_ms=int((time.monotonic() - st) * 1000),
                        success=True,
                    )
                )

            result.success = True
        except Exception as exc:
            result.error = str(exc)
            result.success = False
        finally:
            if not dry_run:
                self.input_driver.release_all()
            result.total_ms = int((time.monotonic() - t0) * 1000)
            result.finished_at = local_timestamp_ms()
            if is_top_level:
                self.audit_logger.log_run_end(
                    run_id=run_id,
                    preset_name=preset.name,
                    success=result.success,
                    total_ms=result.total_ms,
                    step_count=len(result.steps),
                    error=result.error,
                    timestamp=result.finished_at,
                )

        return result

    def _run_group(
        self,
        preset: Preset,
        params: dict[str, Any] | None,
        timeout_override_ms: int | None,
        dry_run: bool,
        stop_event: threading.Event | None,
        esc_abort: bool,
        on_resolved: Callable[[ResolvedControl], None] | None,
        on_step: Callable[[StepLog], None] | None,
        store: PresetStore,
        source: str,
        run_id: str,
        is_top_level: bool,
    ) -> RunResult:
        t0 = time.monotonic()
        started_at = local_timestamp_ms()
        merged_group_params = resolve_params(preset, params)
        result = RunResult(
            preset_name=preset.name,
            success=False,
            run_id=run_id,
            source=source,
            started_at=started_at,
            dry_run=dry_run,
        )

        if is_top_level:
            self.audit_logger.log_run_start(
                run_id=run_id,
                source=source,
                preset_name=preset.name,
                kind="group",
                params=merged_group_params,
                dry_run=dry_run,
                timestamp=started_at,
            )

        step_counter = 0

        def _record_group_step(log: StepLog) -> None:
            result.steps.append(log)
            if is_top_level:
                self.audit_logger.log_step(
                    run_id=run_id,
                    index=log.index,
                    action_type=log.action_type,
                    detail=log.detail,
                    success=log.success,
                    elapsed_ms=log.elapsed_ms,
                    error=log.error,
                    timestamp=log.timestamp,
                )
            if on_step:
                on_step(log)

        try:
            self._check_abort(stop_event, esc_abort)
            # Pre-flight check: ALL referenced child presets must exist before executing anything
            st_pre = time.monotonic()
            try:
                loaded_children = self.validate_group_tree(preset, store)
            except Exception as exc:
                step_counter += 1
                _record_group_step(
                    StepLog(
                        index=step_counter,
                        action_type="preflight",
                        detail=f"预设组 [{preset.name}] 执行前子预设检查",
                        elapsed_ms=int((time.monotonic() - st_pre) * 1000),
                        success=False,
                        error=str(exc),
                    )
                )
                raise

            step_counter += 1
            _record_group_step(
                StepLog(
                    index=step_counter,
                    action_type="preflight",
                    detail=f"预设组 [{preset.name}] 校验通过 ({len(preset.group_items)} 个子预设)",
                    elapsed_ms=int((time.monotonic() - st_pre) * 1000),
                    success=True,
                )
            )

            total_items = len(preset.group_items)
            for idx, item in enumerate(preset.group_items):
                self._check_abort(stop_event, esc_abort)
                child = loaded_children[item.preset_name.strip()]

                child_params = dict(merged_group_params)
                for k, v in item.param_overrides.items():
                    child_params[k] = render_template(v, merged_group_params)

                def _forward_step(child_log: StepLog, _cname: str = child.name) -> None:
                    nonlocal step_counter
                    step_counter += 1
                    wrapped = StepLog(
                        index=step_counter,
                        action_type=child_log.action_type,
                        detail=f"[{_cname}] {child_log.detail}",
                        elapsed_ms=child_log.elapsed_ms,
                        success=child_log.success,
                        error=child_log.error,
                        timestamp=child_log.timestamp,
                    )
                    _record_group_step(wrapped)

                child_res = self.run_preset(
                    child,
                    params=child_params,
                    timeout_override_ms=timeout_override_ms,
                    dry_run=dry_run,
                    stop_event=stop_event,
                    esc_abort=esc_abort,
                    on_resolved=on_resolved,
                    on_step=_forward_step,
                    store=store,
                    source=source,
                    _run_id=run_id,
                )
                if child_res.resolved is not None:
                    result.resolved = child_res.resolved

                if not child_res.success:
                    raise RuntimeError(
                        f"第 {idx + 1}/{total_items} 个子预设 [{child.name}] 执行失败，已停止后续预设：{child_res.error}"
                    )

                if idx < total_items - 1 and item.delay_after_ms > 0:
                    st_d = time.monotonic()
                    if not dry_run:
                        self._sleep_interruptible(item.delay_after_ms, stop_event, esc_abort)
                    step_counter += 1
                    _record_group_step(
                        StepLog(
                            index=step_counter,
                            action_type="group_delay",
                            detail=f"[预设组间隔] 等待 {item.delay_after_ms}ms",
                            elapsed_ms=int((time.monotonic() - st_d) * 1000),
                            success=True,
                        )
                    )

            result.success = True
        except Exception as exc:
            result.error = str(exc)
            result.success = False
        finally:
            if not dry_run:
                self.input_driver.release_all()
            result.total_ms = int((time.monotonic() - t0) * 1000)
            result.finished_at = local_timestamp_ms()
            if is_top_level:
                self.audit_logger.log_run_end(
                    run_id=run_id,
                    preset_name=preset.name,
                    success=result.success,
                    total_ms=result.total_ms,
                    step_count=len(result.steps),
                    error=result.error,
                    timestamp=result.finished_at,
                )

        return result

    def _execute_step(
        self,
        step: ActionStep,
        resolved: ResolvedControl,
        params: dict[str, str],
        dry_run: bool,
        stop_event: threading.Event | None,
        esc_abort: bool,
    ) -> str:
        tx = resolved.target_x + step.offset_dx
        ty = resolved.target_y + step.offset_dy

        if step.type == "mouse_move":
            detail = f"鼠标移动至 ({tx}, {ty})" if step.use_target else "鼠标保持当前位置"
            if not dry_run and step.use_target:
                self.input_driver.move_to(tx, ty, duration_ms=step.duration_ms)
            return detail

        if step.type == "left_click":
            detail = "LeftClick" + (f" @({tx}, {ty})" if step.use_target else "")
            if not dry_run:
                if step.use_target:
                    self.input_driver.move_to(tx, ty)
                self.input_driver.mouse_click("left")
            return detail

        if step.type == "left_down":
            detail = "LeftDown" + (f" @({tx}, {ty})" if step.use_target else "")
            if not dry_run:
                if step.use_target:
                    self.input_driver.move_to(tx, ty)
                self.input_driver.mouse_down("left")
            return detail

        if step.type == "left_up":
            detail = "LeftUp"
            if not dry_run:
                self.input_driver.mouse_up("left")
            return detail

        if step.type == "right_click":
            detail = "RightClick" + (f" @({tx}, {ty})" if step.use_target else "")
            if not dry_run:
                if step.use_target:
                    self.input_driver.move_to(tx, ty)
                self.input_driver.mouse_click("right")
            return detail

        if step.type == "right_down":
            detail = "RightDown" + (f" @({tx}, {ty})" if step.use_target else "")
            if not dry_run:
                if step.use_target:
                    self.input_driver.move_to(tx, ty)
                self.input_driver.mouse_down("right")
            return detail

        if step.type == "right_up":
            detail = "RightUp"
            if not dry_run:
                self.input_driver.mouse_up("right")
            return detail

        if step.type == "key_press":
            key_val = render_template(step.key, params)
            detail = f"键盘敲击 Press: {key_val}"
            if not dry_run:
                self.input_driver.key_press(key_val)
            return detail

        if step.type == "key_down":
            key_val = render_template(step.key, params)
            detail = f"键盘按下 Down: {key_val}"
            if not dry_run:
                self.input_driver.key_down(key_val)
            return detail

        if step.type == "key_up":
            key_val = render_template(step.key, params)
            detail = f"键盘抬起 Up: {key_val}"
            if not dry_run:
                self.input_driver.key_up(key_val)
            return detail

        if step.type == "text_input":
            text_val = render_template(step.text, params)
            mode = "剪贴板" if step.use_clipboard else "Unicode"
            detail = f"文本输入 ({mode}): {text_val!r}"
            if not dry_run:
                self.input_driver.text_input(text_val, use_clipboard=step.use_clipboard)
            return detail

        if step.type == "text_press":
            text_val = render_template(step.text, params)
            detail = f"逐键敲入 Press ({step.interval_ms}ms): {text_val!r}"
            if not dry_run:
                self.input_driver.text_press(text_val, interval_ms=step.interval_ms)
            return detail

        if step.type == "clear_text":
            detail = "清空文本 (Ctrl+A + Backspace)" + (f" @({tx}, {ty})" if step.use_target else "")
            if not dry_run:
                if step.use_target:
                    self.input_driver.move_to(tx, ty)
                    self.input_driver.mouse_click("left")
                    self._sleep_interruptible(30, stop_event, esc_abort)
                self.input_driver.key_press("ctrl+a")
                self._sleep_interruptible(30, stop_event, esc_abort)
                self.input_driver.key_press("backspace")
            return detail

        if step.type == "delay":
            ms_str = render_template(step.ms, params)
            ms_val = int(float(ms_str))
            detail = f"延时 {ms_val}ms"
            if not dry_run:
                self._sleep_interruptible(ms_val, stop_event, esc_abort)
            return detail

        raise ValueError(f"不支持的操作类型: {step.type}")
