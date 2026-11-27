"""JSON preset storage and template parameter rendering."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from autotool.models.schema import Preset, utc_now_iso

_VALID_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff]+$")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


def default_presets_dir() -> Path:
    """Return the default `presets` directory at the project root."""
    # src/autotool/models/preset_store.py -> project root is 4 levels up
    root = Path(__file__).resolve().parents[3]
    return root / "presets"


def render_template(value: str, params: dict[str, Any]) -> str:
    """Replace `{{param_name}}` placeholders using provided params."""
    if not value or "{{" not in value:
        return value

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in params:
            raise KeyError(f"缺少必需参数: {key}")
        return str(params[key])

    return _PLACEHOLDER_RE.sub(_replace, value)


def extract_placeholders(preset: Preset) -> list[str]:
    """Find all unique `{{param}}` names referenced inside a preset's actions."""
    found: list[str] = []
    seen: set[str] = set()
    for step in preset.actions:
        for field_val in (step.text, step.key, step.ms):
            for match in _PLACEHOLDER_RE.finditer(field_val or ""):
                name = match.group(1)
                if name not in seen:
                    seen.add(name)
                    found.append(name)
    return found


def resolve_params(preset: Preset, overrides: dict[str, Any] | None = None) -> dict[str, str]:
    """Merge preset defaults with caller-supplied overrides."""
    merged: dict[str, str] = {}
    for pdef in preset.parameters:
        if pdef.default != "":
            merged[pdef.name] = pdef.default
    if overrides:
        for k, v in overrides.items():
            merged[k] = str(v)
    return merged


class PresetStore:
    """Manages reading/writing Preset JSON files and their display order on disk."""

    ORDER_FILENAME = "_order.json"

    def __init__(self, presets_dir: Path | str | None = None) -> None:
        self.presets_dir = Path(presets_dir) if presets_dir else default_presets_dir()
        self.presets_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _order_path(self) -> Path:
        return self.presets_dir / self.ORDER_FILENAME

    def load_order(self) -> list[str]:
        if not self._order_path.exists():
            return []
        try:
            data = json.loads(self._order_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [str(x) for x in data if isinstance(x, str)]
        except Exception:
            pass
        return []

    def save_order(self, names: list[str]) -> None:
        seen: set[str] = set()
        deduped: list[str] = []
        for n in names:
            if n and n not in seen:
                seen.add(n)
                deduped.append(n)
        self._order_path.write_text(
            json.dumps(deduped, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def validate_name(name: str) -> str:
        cleaned = name.strip()
        if not cleaned or cleaned == "_order" or not _VALID_NAME_RE.match(cleaned):
            raise ValueError("预设名称只能包含字母、数字、中文、下划线(_)或连字符(-)")
        return cleaned

    def path_for(self, name: str) -> Path:
        cleaned = self.validate_name(name)
        return self.presets_dir / f"{cleaned}.json"

    def save(self, preset: Preset) -> Path:
        cleaned = self.validate_name(preset.name)
        preset.name = cleaned
        preset.updated_at = utc_now_iso()
        path = self.path_for(cleaned)
        if path.exists():
            try:
                existing = self.load(cleaned)
                preset.created_at = existing.created_at
            except Exception:
                pass
        path.write_text(
            json.dumps(preset.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        order = self.load_order()
        if order and cleaned not in order:
            order.append(cleaned)
            self.save_order(order)
        return path

    def save_or_rename(self, preset: Preset, old_name: str | None = None) -> Path:
        """Save `preset`, and if `old_name` differs from `preset.name`, remove `old_name` and keep its list position."""
        cleaned = self.validate_name(preset.name)
        preset.name = cleaned
        if not old_name or old_name == cleaned:
            return self.save(preset)

        old_cleaned = old_name.strip()
        old_path = self.presets_dir / f"{old_cleaned}.json"
        if old_path.exists():
            try:
                existing = self.load(old_cleaned)
                preset.created_at = existing.created_at
            except Exception:
                pass

        current_names = [p.name for p in self.list_presets()]
        path = self.save(preset)

        if old_cleaned in current_names:
            new_order: list[str] = []
            for n in current_names:
                if n == old_cleaned:
                    if cleaned not in new_order:
                        new_order.append(cleaned)
                elif n != cleaned:
                    new_order.append(n)
            self.save_order(new_order)

        if old_path.exists():
            old_path.unlink()

        return path

    def load(self, name: str) -> Preset:
        path = self.path_for(name)
        if not path.exists():
            raise FileNotFoundError(f"找不到预设: {name}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return Preset.model_validate(data)

    def delete(self, name: str) -> bool:
        path = self.path_for(name)
        if path.exists():
            path.unlink()
            order = self.load_order()
            if name in order:
                self.save_order([x for x in order if x != name])
            return True
        return False

    def suggest_copy_name(self, source_name: str) -> str:
        """Generate a non-conflicting copy name like '<name>_副本' or '<name>_副本2'."""
        base = f"{source_name}_副本"
        if not self.path_for(base).exists():
            return base
        idx = 2
        while self.path_for(f"{base}{idx}").exists():
            idx += 1
        return f"{base}{idx}"

    def duplicate(self, source_name: str, target_name: str | None = None) -> Preset:
        """Create an independent copy of `source_name` (works for both single presets and groups)."""
        src = self.load(source_name)
        new_name = self.validate_name(target_name) if target_name else self.suggest_copy_name(src.name)
        if self.path_for(new_name).exists():
            raise FileExistsError(f"预设名称已存在: {new_name}")

        now = utc_now_iso()
        copy_preset = src.model_copy(deep=True)
        copy_preset.name = new_name
        copy_preset.created_at = now
        copy_preset.updated_at = now
        self.save(copy_preset)

        # Place the copy right after the source item in display order
        existing_names = [p.name for p in self.list_presets() if p.name != new_name]
        if src.name in existing_names:
            idx = existing_names.index(src.name) + 1
            existing_names.insert(idx, new_name)
        else:
            existing_names.append(new_name)
        self.save_order(existing_names)
        return copy_preset

    def list_presets(self) -> list[Preset]:
        by_name: dict[str, Preset] = {}
        if not self.presets_dir.exists():
            return []
        for file in sorted(self.presets_dir.glob("*.json")):
            if file.name == self.ORDER_FILENAME:
                continue
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                p = Preset.model_validate(data)
                by_name[p.name] = p
            except Exception:
                continue

        order = self.load_order()
        ordered: list[Preset] = []
        used: set[str] = set()
        for name in order:
            if name in by_name and name not in used:
                ordered.append(by_name[name])
                used.add(name)
        for name in sorted(by_name.keys()):
            if name not in used:
                ordered.append(by_name[name])
        return ordered
