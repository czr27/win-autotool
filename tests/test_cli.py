"""CLI integration tests using Click CliRunner."""

import json
from pathlib import Path

from click.testing import CliRunner

from autotool.cli import main
from autotool.models.preset_store import PresetStore
from autotool.models.schema import ActionStep, LocatorStrategy, ParameterDef, Preset, Rect, ResolvedControl


def test_cli_list_show_and_dry_run(tmp_path: Path, monkeypatch) -> None:
    store = PresetStore(tmp_path)
    preset = Preset(
        name="cli_demo",
        description="CLI 测试预设",
        locator=LocatorStrategy(process_name="notepad.exe", automation_id="15"),
        parameters=[ParameterDef(name="text", default="hello")],
        actions=[
            ActionStep(type="left_click"),
            ActionStep(type="text_input", text="{{text}}"),
        ],
    )
    store.save(preset)

    runner = CliRunner()

    # list --json
    r_list = runner.invoke(main, ["--presets-dir", str(tmp_path), "list", "--json"])
    assert r_list.exit_code == 0
    items = json.loads(r_list.output)
    assert len(items) == 1
    assert items[0]["name"] == "cli_demo"

    # show
    r_show = runner.invoke(main, ["--presets-dir", str(tmp_path), "show", "cli_demo"])
    assert r_show.exit_code == 0
    assert '"cli_demo"' in r_show.output

    # run --dry-run --json (mock ControlResolver.resolve)
    from autotool.core.resolver import ControlResolver

    def _fake_resolve(self, strategy, timeout_override_ms=None, activate=True):
        return ResolvedControl(
            window_title="Notepad",
            process_name="notepad.exe",
            control_type="EditControl",
            rect=Rect(left=0, top=0, right=400, bottom=300),
            target_x=200,
            target_y=150,
            elapsed_ms=2,
        )

    monkeypatch.setattr(ControlResolver, "resolve", _fake_resolve)

    r_run = runner.invoke(
        main,
        [
            "--presets-dir",
            str(tmp_path),
            "run",
            "cli_demo",
            "-p",
            "text=来自外部程序",
            "--dry-run",
            "--json",
        ],
    )
    assert r_run.exit_code == 0
    out = json.loads(r_run.output)
    assert out["success"] is True
    assert out["dry_run"] is True
    assert out["steps"][0]["type"] == "resolve"
    assert any("来自外部程序" in s["detail"] for s in out["steps"])


def test_cli_missing_preset_exit_code_2(tmp_path: Path) -> None:
    runner = CliRunner()
    res = runner.invoke(main, ["--presets-dir", str(tmp_path), "run", "nonexistent", "--json"])
    assert res.exit_code == 2
    payload = json.loads(res.output)
    assert payload["success"] is False
