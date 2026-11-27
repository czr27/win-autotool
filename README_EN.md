# AutoTool — Windows Desktop UI Automation Toolbox

[简体中文](./README.md) | English

> A lightweight Windows desktop UI control inspector, multi-strategy smart locator, and parameterized mouse/keyboard automation toolbox built with **Python + PySide6 + Windows UI Automation + OpenCV + Win32 `SendInput`**, featuring a floating GUI, CLI, and built-in local HTTP API server.

## Features

- **Visual Control Picker & Screen Region Capture**
  - Drag the crosshair or use global hotkeys to highlight and inspect any Windows desktop UI control in real time, capturing process, window, UIA tree path, and bounding-box geometry.
  - Capture screen regions directly for custom-rendered or non-UIA surfaces (Canvas, WebViews, games) via OpenCV + MSS template matching.
- **7 Smart Locator Strategies & Diagnostics**
  - Automatically generates and evaluates 7 locator strategies: `process_automation_id`, `process_name_offset`, `window_automation_id`, `window_size_offset`, `window_tree_path`, `window_relative_ratio`, and `image_template`.
  - Built-in Per-Monitor DPI Awareness V2 for pixel-accurate targeting across multi-DPI displays, with one-click highlight verification and step-by-step locator failure diagnostics.
- **Parameterized Action Sequences & Preset Groups**
  - Supports mouse move, left/right click/down/up with pixel offsets, `key_press`/`key_down`/`key_up`, `text_input` (Unicode `SendInput` or clipboard paste), `text_press` (per-key typing), `clear_text` (`Ctrl+A + Backspace`), and `delay`.
  - Embed `{{param}}` template placeholders in actions and chain multiple presets into **Preset Groups** with cycle detection and per-step parameter overrides.
- **Floating GUI + CLI + Local HTTP API**
  - **Floating GUI**: Always-on-top pin toggle, opacity slider, and one-click collapse into a compact floating bar.
  - **Parameterized CLI**: Trigger presets from external scripts with `-p key=value`, `--dry-run` locator verification, and `--json` structured output.
  - **Built-in HTTP Server**: Launch a lightweight local HTTP server (default `127.0.0.1:18999`) to trigger presets via `GET` query parameters or `POST` JSON payloads, with automatic JSONL execution audit logs.

## Requirements

- **OS**: Windows 10 / 11
- **Python**: Python 3.10+ ([`uv`](https://docs.astral.sh/uv/) recommended)

## Quick Start

### 1. Clone & Install Dependencies

Using `uv` (recommended, works out-of-the-box with `autotool.bat`):
```powershell
git clone https://github.com/czr27/win-autotool.git
cd win-autotool
uv sync --python 3.10 --extra dev
```

Or using standard `venv` + `pip`:
```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
```

> **Presets & Local Data**: Saved automation presets are stored locally under `presets/` (see [`examples/preset.example.json`](./examples/preset.example.json) for a sample preset you can copy into `presets/`), and execution logs are written to `logs/`. Both directories are ignored by `.gitignore` to keep personal workflows private.

### 2. Launch Floating GUI

Double-click `run_gui.bat` or run:
```powershell
.\autotool.bat gui
```

### 3. Run via CLI

```powershell
# List all saved presets (supports --json)
.\autotool.bat list

# Show full JSON definition of a preset
.\autotool.bat show notepad_type_demo

# Execute a preset with template parameters (supports --dry-run and --json)
.\autotool.bat run notepad_type_demo -p text="Hello World" --json
```

### 4. Start Local HTTP API Server

Double-click `run_ser.bat` or run:
```powershell
.\autotool.bat serve --host 127.0.0.1 --port 18999
```
Available endpoints:
- `GET http://127.0.0.1:18999/health` — Health check & endpoint list
- `GET http://127.0.0.1:18999/presets` — List saved presets and parameter schemas
- `GET http://127.0.0.1:18999/run/<preset_name>?text=hello&_dry_run=1` — Trigger preset via URL query parameters
- `POST http://127.0.0.1:18999/run/<preset_name>` — Trigger preset via JSON body

## Running Tests

```powershell
.\.venv\Scripts\pytest
```

## Project Structure

```text
autotool/
├── src/autotool/
│   ├── cli.py                    # Click CLI entry point (gui / list / show / run / serve)
│   ├── core/
│   │   ├── inspector.py          # Live Windows UIA control inspector
│   │   ├── locator_generator.py  # Multi-strategy smart locator generator
│   │   ├── resolver.py           # UIA & image template resolver with diagnostics
│   │   ├── image_matcher.py      # MSS screen capture & OpenCV template matcher
│   │   ├── win32_input.py        # Win32 SendInput mouse/keyboard & clipboard driver
│   │   ├── runner.py             # Action sequence & composite preset group runner
│   │   ├── http_server.py        # Built-in multi-threaded HTTP API server
│   │   ├── audit_log.py          # Execution log & JSONL audit writer
│   │   └── dpi.py                # Per-Monitor DPI Awareness V2 helper
│   ├── models/
│   │   ├── schema.py             # Pydantic models (Preset / Locator / ActionStep)
│   │   └── preset_store.py       # Preset JSON storage, ordering & {{param}} renderer
│   └── ui/                       # PySide6 floating window, crosshair picker, region selector & editors
├── examples/
│   └── preset.example.json       # Sample parameterized preset template
├── tests/                        # Pytest unit test suite
├── pyproject.toml                # Package metadata & dependencies
├── uv.lock                       # Locked dependency graph
├── autotool.bat                  # CLI launcher wrapper
├── run_gui.bat                   # Quick-launch script for floating GUI
└── run_ser.bat                   # Quick-launch script for local HTTP server
```

## Changelog

### v0.1.0 (2026-11-27)
- Initial public release featuring Windows UIA crosshair control picking and OpenCV screen region template matching, 7 smart locator strategies with diagnostics, parameterized mouse/keyboard action sequences and composite preset groups, a collapsible PySide6 floating GUI, parameterized CLI, and a built-in local HTTP trigger server.
