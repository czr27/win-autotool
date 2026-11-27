"""Tests for global hotkey parsing (default shift+f2 and custom combos)."""

import pytest

from autotool.ui.hotkey import MOD_ALT, MOD_CONTROL, MOD_SHIFT, parse_global_hotkey


def test_parse_global_hotkey_valid_combos() -> None:
    mods, vk, label = parse_global_hotkey("shift+f2")
    assert mods == MOD_SHIFT
    assert vk == 0x71  # VK_F2
    assert label == "Shift+F2"

    mods2, vk2, label2 = parse_global_hotkey("ctrl+alt+f5")
    assert mods2 == (MOD_CONTROL | MOD_ALT)
    assert vk2 == 0x74  # VK_F5
    assert label2 == "Ctrl+Alt+F5"

    mods3, vk3, label3 = parse_global_hotkey("f8")
    assert mods3 == 0
    assert vk3 == 0x77  # VK_F8
    assert label3 == "F8"


def test_parse_global_hotkey_invalid() -> None:
    with pytest.raises(ValueError):
        parse_global_hotkey("")
    with pytest.raises(ValueError):
        parse_global_hotkey("ctrl+shift")
    with pytest.raises(ValueError):
        parse_global_hotkey("shift+unknownkey")
