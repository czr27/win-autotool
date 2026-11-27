"""Unit tests for ActionRunner execution order, parameter injection, and safety release."""

from autotool.core.runner import ActionRunner
from autotool.models.schema import (
    ActionStep,
    LocatorStrategy,
    ParameterDef,
    Preset,
    Rect,
    ResolvedControl,
)


class _FakeResolver:
    def resolve(self, strategy, timeout_override_ms=None, activate=True) -> ResolvedControl:
        return ResolvedControl(
            window_title="TestWin",
            process_name="test.exe",
            control_type="EditControl",
            rect=Rect(left=100, top=100, right=300, bottom=200),
            target_x=200,
            target_y=150,
            elapsed_ms=5,
        )


class _FakeInput:
    def __init__(self, fail_on_key: str | None = None) -> None:
        self.events: list[tuple] = []
        self.released = False
        self.fail_on_key = fail_on_key

    def move_to(self, x: int, y: int, duration_ms: int = 0) -> None:
        self.events.append(("move", x, y, duration_ms))

    def mouse_click(self, button: str = "left") -> None:
        self.events.append(("click", button))

    def mouse_double_click(self, button: str = "left") -> None:
        self.events.append(("dblclick", button))

    def mouse_down(self, button: str = "left") -> None:
        self.events.append(("mouse_down", button))

    def mouse_up(self, button: str = "left") -> None:
        self.events.append(("mouse_up", button))

    def key_down(self, key: str) -> None:
        self.events.append(("key_down", key))

    def key_up(self, key: str) -> None:
        self.events.append(("key_up", key))

    def key_press(self, key: str) -> None:
        if self.fail_on_key and key == self.fail_on_key:
            raise RuntimeError("simulated key error")
        self.events.append(("key_press", key))

    def text_input(self, text: str, use_clipboard: bool = False) -> None:
        self.events.append(("text_input", text, use_clipboard))

    def text_press(self, text: str, interval_ms: int = 25) -> None:
        self.events.append(("text_press", text, interval_ms))

    def release_all(self) -> None:
        self.released = True


def test_runner_executes_all_action_types_with_params() -> None:
    fake_input = _FakeInput()
    runner = ActionRunner(resolver=_FakeResolver(), input_driver=fake_input)

    preset = Preset(
        name="full_flow",
        locator=LocatorStrategy(),
        parameters=[
            ParameterDef(name="msg", default="hi"),
            ParameterDef(name="typed", default="abc"),
        ],
        actions=[
            ActionStep(type="mouse_move", duration_ms=10),
            ActionStep(type="left_click"),
            ActionStep(type="left_down"),
            ActionStep(type="left_up"),
            ActionStep(type="right_click", offset_dx=5, offset_dy=-5),
            ActionStep(type="right_down"),
            ActionStep(type="right_up"),
            ActionStep(type="clear_text"),
            ActionStep(type="text_input", text="{{msg}}"),
            ActionStep(type="text_press", text="{{typed}}", interval_ms=5),
            ActionStep(type="key_down", key="shift"),
            ActionStep(type="key_press", key="enter"),
            ActionStep(type="key_up", key="shift"),
            ActionStep(type="delay", ms="10"),
        ],
    )

    res = runner.run_preset(preset, params={"msg": "你好世界", "typed": "xyz"})
    assert res.success is True
    assert len(res.steps) == 15  # 1 resolve + 14 action steps
    assert fake_input.released is True
    assert ("key_press", "ctrl+a") in fake_input.events
    assert ("key_press", "backspace") in fake_input.events
    assert ("click", "left") in fake_input.events
    assert ("mouse_down", "left") in fake_input.events
    assert ("mouse_up", "left") in fake_input.events
    assert ("click", "right") in fake_input.events
    assert ("mouse_down", "right") in fake_input.events
    assert ("mouse_up", "right") in fake_input.events
    assert ("text_input", "你好世界", False) in fake_input.events
    assert ("text_press", "xyz", 5) in fake_input.events
    assert ("move", 205, 145, 0) in fake_input.events


def test_runner_releases_held_keys_on_error() -> None:
    fake_input = _FakeInput(fail_on_key="boom")
    runner = ActionRunner(resolver=_FakeResolver(), input_driver=fake_input)
    preset = Preset(
        name="err_flow",
        locator=LocatorStrategy(),
        actions=[
            ActionStep(type="key_down", key="ctrl"),
            ActionStep(type="key_press", key="boom"),
        ],
    )
    res = runner.run_preset(preset)
    assert res.success is False
    assert "simulated key error" in res.error
    assert fake_input.released is True
