"""Tests for composite Preset Groups: decoupled deletion, re-adding, pre-flight checks, delays, CLI & HTTP."""

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import urlopen

import pytest
from click.testing import CliRunner

from autotool.cli import main
from autotool.core.http_server import AutoToolHTTPServer
from autotool.core.runner import ActionRunner
from autotool.models.preset_store import PresetStore
from autotool.models.schema import (
    ActionStep,
    LocatorStrategy,
    ParameterDef,
    Preset,
    PresetGroupItem,
    Rect,
    ResolvedControl,
)


class _FakeResolver:
    def resolve(self, strategy, timeout_override_ms=None, activate=True) -> ResolvedControl:
        return ResolvedControl(
            window_title="TestWin",
            process_name="test.exe",
            control_type="EditControl",
            rect=Rect(left=0, top=0, right=200, bottom=50),
            target_x=100,
            target_y=25,
            elapsed_ms=2,
        )


class _RecordingInput:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def move_to(self, x: int, y: int, duration_ms: int = 0) -> None:
        self.events.append(("move", x, y))

    def mouse_click(self, button: str = "left") -> None:
        self.events.append(("click", button))

    def text_input(self, text: str, use_clipboard: bool = False) -> None:
        self.events.append(("text_input", text))

    def key_press(self, key: str) -> None:
        self.events.append(("key_press", key))

    def release_all(self) -> None:
        pass


def _make_child(name: str, step: ActionStep, params: list[ParameterDef] | None = None) -> Preset:
    return Preset(
        name=name,
        locator=LocatorStrategy(process_name="test.exe", automation_id=name),
        parameters=params or [],
        actions=[step],
    )


def test_group_survives_child_deletion_fails_preflight_then_recovers_on_readd(tmp_path: Path) -> None:
    store = PresetStore(tmp_path)
    store.save(_make_child("step_a", ActionStep(type="text_input", text="{{msg}}"), [ParameterDef(name="msg", default="hi")]))
    store.save(_make_child("step_b", ActionStep(type="key_press", key="enter")))

    group = Preset(
        name="combo_flow",
        kind="group",
        description="先输入再回车",
        group_items=[
            PresetGroupItem(preset_name="step_a", delay_after_ms=15),
            PresetGroupItem(preset_name="step_b", delay_after_ms=0),
        ],
    )
    store.save(group)

    # 1. Delete step_b — group preset itself must remain intact and loadable
    assert store.delete("step_b") is True
    loaded_group = store.load("combo_flow")
    assert len(loaded_group.group_items) == 2

    # 2. Attempt to run while step_b is missing -> MUST fail BEFORE executing step_a!
    rec = _RecordingInput()
    runner = ActionRunner(resolver=_FakeResolver(), input_driver=rec, store=store)
    res_fail = runner.run_preset(loaded_group, params={"msg": "不应该执行"})
    assert res_fail.success is False
    assert "step_b" in res_fail.error
    assert "执行前检查失败" in res_fail.error
    assert rec.events == []  # Zero actions executed!

    # 3. Re-add a preset with the same name `step_b` -> group now runs in order with interval!
    store.save(_make_child("step_b", ActionStep(type="key_press", key="enter")))
    res_ok = runner.run_preset(loaded_group, params={"msg": "恢复正常"})
    assert res_ok.success is True
    assert ("text_input", "恢复正常") in rec.events
    assert ("key_press", "enter") in rec.events
    # Verify interval log was recorded between step_a and step_b
    assert any("预设组间隔" in s.detail and "15ms" in s.detail for s in res_ok.steps)


def test_group_cycle_detection(tmp_path: Path) -> None:
    store = PresetStore(tmp_path)
    store.save(
        Preset(
            name="g1",
            kind="group",
            group_items=[PresetGroupItem(preset_name="g2", delay_after_ms=0)],
        )
    )
    store.save(
        Preset(
            name="g2",
            kind="group",
            group_items=[PresetGroupItem(preset_name="g1", delay_after_ms=0)],
        )
    )
    runner = ActionRunner(resolver=_FakeResolver(), input_driver=_RecordingInput(), store=store)
    res = runner.run_preset(store.load("g1"))
    assert res.success is False
    assert "循环引用" in res.error


def test_group_cli_and_http_invocation(tmp_path: Path) -> None:
    store = PresetStore(tmp_path)
    store.save(_make_child("p1", ActionStep(type="text_input", text="{{word}}")))
    store.save(
        Preset(
            name="my_group",
            kind="group",
            group_items=[
                PresetGroupItem(preset_name="p1", delay_after_ms=10),
                PresetGroupItem(preset_name="p2_missing", delay_after_ms=0),
            ],
        )
    )

    # CLI returns exit code 2 with clear missing-child error
    cli_runner = CliRunner()
    r_missing = cli_runner.invoke(
        main, ["--presets-dir", str(tmp_path), "run", "my_group", "--dry-run", "--json"]
    )
    assert r_missing.exit_code == 2
    payload = json.loads(r_missing.output)
    assert payload["success"] is False
    assert "p2_missing" in payload["error"]

    # HTTP returns 400 when child missing, then 200 after adding p2_missing
    server = AutoToolHTTPServer(
        store=store,
        host="127.0.0.1",
        port=0,
        runner_factory=lambda: ActionRunner(resolver=_FakeResolver(), store=store),
    )
    server.start()
    try:
        base = server.base_url
        with pytest.raises(HTTPError) as exc_info:
            urlopen(f"{base}/run/my_group?_dry_run=1")
        assert exc_info.value.code == 400
        err_body = json.loads(exc_info.value.read().decode("utf-8"))
        assert "p2_missing" in err_body["error"]

        # Re-add p2_missing -> HTTP GET succeeds!
        store.save(_make_child("p2_missing", ActionStep(type="key_press", key="enter")))
        qs = urlencode({"word": "组内传参", "_dry_run": "1"})
        with urlopen(f"{base}/run/{quote('my_group')}?{qs}") as resp:
            assert resp.status == 200
            out = json.loads(resp.read().decode("utf-8"))
            assert out["success"] is True
            assert any("组内传参" in s["detail"] for s in out["steps"])
    finally:
        server.stop()


def test_group_stops_immediately_on_child_failure(tmp_path: Path) -> None:
    store = PresetStore(tmp_path)
    store.save(_make_child("step_1", ActionStep(type="left_click")))
    store.save(_make_child("step_2_fail", ActionStep(type="text_input", text="{{missing_var}}")))
    store.save(_make_child("step_3_never", ActionStep(type="key_press", key="enter")))

    store.save(
        Preset(
            name="fail_fast_group",
            kind="group",
            group_items=[
                PresetGroupItem(preset_name="step_1", delay_after_ms=10),
                PresetGroupItem(preset_name="step_2_fail", delay_after_ms=10),
                PresetGroupItem(preset_name="step_3_never", delay_after_ms=0),
            ],
        )
    )

    rec = _RecordingInput()
    runner = ActionRunner(resolver=_FakeResolver(), input_driver=rec, store=store)
    res = runner.run_preset(store.load("fail_fast_group"))

    assert res.success is False
    assert "第 2/3 个子预设 [step_2_fail] 执行失败" in res.error
    assert "missing_var" in res.error
    # step_1 executed, step_3_never MUST NOT have executed
    assert ("click", "left") in rec.events
    assert ("key_press", "enter") not in rec.events
    assert not any("step_3_never" in s.detail for s in res.steps)
