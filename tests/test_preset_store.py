"""Unit tests for PresetStore persistence and parameter template rendering."""

from pathlib import Path

import pytest

from autotool.models.preset_store import (
    PresetStore,
    extract_placeholders,
    render_template,
    resolve_params,
)
from autotool.models.schema import ActionStep, LocatorStrategy, ParameterDef, Preset


def _make_preset(name: str = "demo_preset") -> Preset:
    return Preset(
        name=name,
        description="向记事本输入内容",
        locator=LocatorStrategy(
            strategy_type="process_automation_id",
            process_name="notepad.exe",
            automation_id="15",
        ),
        parameters=[
            ParameterDef(name="text", default="默认内容"),
            ParameterDef(name="submit_key", default="enter"),
        ],
        actions=[
            ActionStep(type="left_click"),
            ActionStep(type="delay", ms="{{wait_ms}}"),
            ActionStep(type="text_input", text="{{text}}"),
            ActionStep(type="key_press", key="{{submit_key}}"),
        ],
    )


def test_save_load_list_delete(tmp_path: Path) -> None:
    store = PresetStore(tmp_path)
    preset = _make_preset("记事本_输入-1")

    saved_path = store.save(preset)
    assert saved_path.exists()

    loaded = store.load("记事本_输入-1")
    assert loaded.name == "记事本_输入-1"
    assert len(loaded.actions) == 4
    assert loaded.locator.automation_id == "15"

    listed = store.list_presets()
    assert [p.name for p in listed] == ["记事本_输入-1"]

    assert store.delete("记事本_输入-1") is True
    assert store.list_presets() == []


def test_invalid_preset_name_rejected(tmp_path: Path) -> None:
    store = PresetStore(tmp_path)
    with pytest.raises(ValueError):
        store.validate_name("../evil")
    with pytest.raises(ValueError):
        store.validate_name("has space")


def test_template_rendering_and_placeholders() -> None:
    preset = _make_preset()
    assert extract_placeholders(preset) == ["wait_ms", "text", "submit_key"]

    merged = resolve_params(preset, {"text": "你好CLI", "wait_ms": "150"})
    assert render_template("输入: {{text}}", merged) == "输入: 你好CLI"
    assert render_template("{{submit_key}}", merged) == "enter"

    with pytest.raises(KeyError):
        render_template("{{missing_param}}", merged)


def test_preset_custom_order_persistence(tmp_path: Path) -> None:
    store = PresetStore(tmp_path)
    store.save(_make_preset("alpha"))
    store.save(_make_preset("beta"))
    store.save(_make_preset("gamma"))

    # Default alphabetical order
    assert [p.name for p in store.list_presets()] == ["alpha", "beta", "gamma"]

    # Save custom dragged order
    store.save_order(["gamma", "alpha", "beta"])
    assert [p.name for p in store.list_presets()] == ["gamma", "alpha", "beta"]

    # Newly saved preset appends at the end without breaking custom order
    store.save(_make_preset("delta"))
    assert [p.name for p in store.list_presets()] == ["gamma", "alpha", "beta", "delta"]

    # Deleting removes it from order cleanly
    store.delete("alpha")
    assert [p.name for p in store.list_presets()] == ["gamma", "beta", "delta"]


def test_duplicate_preset_and_group(tmp_path: Path) -> None:
    from autotool.models.schema import PresetGroupItem

    store = PresetStore(tmp_path)
    store.save(_make_preset("alpha"))
    store.save(
        Preset(
            name="grp",
            kind="group",
            group_items=[PresetGroupItem(preset_name="alpha", delay_after_ms=200)],
        )
    )

    copy1 = store.duplicate("alpha")
    assert copy1.name == "alpha_副本"
    assert len(copy1.actions) == 4
    # Placed right after alpha
    assert [p.name for p in store.list_presets()] == ["alpha", "alpha_副本", "grp"]

    copy2 = store.duplicate("alpha")
    assert copy2.name == "alpha_副本2"

    grp_copy = store.duplicate("grp", "grp_copy")
    assert grp_copy.kind == "group"
    assert grp_copy.group_items[0].preset_name == "alpha"
    assert [p.name for p in store.list_presets()] == ["alpha", "alpha_副本2", "alpha_副本", "grp", "grp_copy"]


def test_save_or_rename_removes_old_name_and_preserves_order(tmp_path: Path) -> None:
    store = PresetStore(tmp_path)
    store.save(_make_preset("first"))
    store.save(_make_preset("old_name"))
    store.save(_make_preset("last"))
    store.save_order(["first", "old_name", "last"])

    updated = _make_preset("new_name")
    store.save_or_rename(updated, old_name="old_name")

    assert not (tmp_path / "old_name.json").exists()
    assert (tmp_path / "new_name.json").exists()
    assert [p.name for p in store.list_presets()] == ["first", "new_name", "last"]
