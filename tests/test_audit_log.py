"""Tests for full timestamped audit logging (.log + .jsonl) and per-step pass/fail traceability."""

import json
from pathlib import Path

from autotool.core.audit_log import AuditLogger
from autotool.core.runner import ActionRunner
from autotool.models.schema import (
    ActionStep,
    LocatorStrategy,
    Preset,
    Rect,
    ResolvedControl,
)


class _FakeResolver:
    def resolve(self, strategy, timeout_override_ms=None, activate=True) -> ResolvedControl:
        return ResolvedControl(
            window_title="AuditWin",
            process_name="audit.exe",
            control_type="ButtonControl",
            rect=Rect(left=0, top=0, right=100, bottom=40),
            target_x=50,
            target_y=20,
            elapsed_ms=4,
        )


class _FailingInput:
    def move_to(self, x: int, y: int, duration_ms: int = 0) -> None:
        pass

    def mouse_click(self, button: str = "left") -> None:
        pass

    def key_press(self, key: str) -> None:
        raise RuntimeError("boom_key_error")

    def release_all(self) -> None:
        pass


def test_audit_log_records_timestamps_all_steps_and_failure(tmp_path: Path) -> None:
    logger = AuditLogger(logs_dir=tmp_path)
    runner = ActionRunner(
        resolver=_FakeResolver(),
        input_driver=_FailingInput(),
        audit_logger=logger,
    )
    preset = Preset(
        name="audit_demo",
        locator=LocatorStrategy(process_name="audit.exe"),
        actions=[
            ActionStep(type="left_click"),
            ActionStep(type="key_press", key="enter"),
        ],
    )

    res = runner.run_preset(preset, params={"k": "v"}, source="cli")
    assert res.success is False
    assert res.run_id
    assert res.started_at and res.finished_at
    # Steps should include: 1=resolve (OK), 2=left_click (OK), 3=key_press (FAIL)
    assert len(res.steps) == 3
    assert res.steps[0].action_type == "resolve" and res.steps[0].success is True
    assert res.steps[1].action_type == "left_click" and res.steps[1].success is True
    assert res.steps[2].action_type == "key_press" and res.steps[2].success is False
    assert "boom_key_error" in res.steps[2].error
    assert all(s.timestamp for s in res.steps)

    # Check persisted .log and .jsonl files
    log_files = list(tmp_path.glob("autotool-*.log"))
    jsonl_files = list(tmp_path.glob("autotool-*.jsonl"))
    assert len(log_files) == 1
    assert len(jsonl_files) == 1

    text_content = log_files[0].read_text(encoding="utf-8")
    assert f"[RUN {res.run_id}]" in text_content
    assert "[START]" in text_content
    assert "[FAIL]" in text_content
    assert "boom_key_error" in text_content

    records = [json.loads(line) for line in jsonl_files[0].read_text(encoding="utf-8").splitlines()]
    events = [r["event"] for r in records]
    assert events == ["run_start", "step", "step", "step", "run_end"]
    assert records[-2]["success"] is False
    assert records[-1]["success"] is False

    # Verify AuditLogger.list_log_dates & load_runs for GUI log browser
    dates = logger.list_log_dates()
    assert len(dates) == 1
    runs = logger.load_runs(date_str=dates[0])
    assert len(runs) == 1
    assert runs[0]["run_id"] == res.run_id
    assert runs[0]["preset"] == "audit_demo"
    assert runs[0]["success"] is False
    assert len(runs[0]["steps"]) == 3
    assert len(logger.load_runs(status_filter="ok")) == 0
    assert len(logger.load_runs(status_filter="fail", keyword="boom_key")) == 1
