# AutoTool — Windows 桌面控件抓捕与键鼠自动化工具箱

简体中文 | [English](./README_EN.md)

> 基于 **Python + PySide6 + Windows UI Automation + OpenCV + Win32 `SendInput`** 构建的轻量级 Windows 桌面控件抓捕、多策略智能定位与参数化键鼠自动化工具箱，支持悬浮窗 GUI、命令行 CLI 与本地 HTTP API 三端驱动。

## 功能特性

- **可视化控件抓捕与屏幕框选**
  - 支持拖拽靶心或全局热键实时高亮拾取任意 Windows 桌面程序控件，自动采集进程、窗口、控件树路径与几何坐标信息。
  - 对于自绘或无 UIA 句柄的界面（如 Canvas / WebView / 游戏窗口），支持直接在屏幕框选区域生成图像匹配模板。
- **7 类多策略智能定位与诊断**
  - 自动推演并支持 7 种定位策略：`process_automation_id`（进程+AutomationId）、`process_name_offset`（进程+控件名+相对位置）、`window_automation_id`（窗口标题+AutomationId）、`window_size_offset`（窗口+控件尺寸+相对位置）、`window_tree_path`（UIA 控件树路径）、`window_relative_ratio`（窗口相对比例兜底）及 `image_template`（OpenCV + MSS 图像模板匹配）。
  - 原生启用 Per-Monitor DPI Awareness V2 高分屏物理像素对齐，支持一键高亮验证落点与定位失败原因诊断。
- **参数化动作序列与预设组编排**
  - 支持鼠标移动、左/右键（单击、按下、抬起、像素偏移）、键盘 `key_press`/`key_down`/`key_up`、文本 `text_input`（Unicode 直输 / 剪贴板粘贴）、`text_press`（逐键敲击）、`clear_text`（`Ctrl+A + Backspace` 清空）及 `delay` 延时。
  - 支持在动作参数中嵌入 `{{param}}` 动态占位符，并支持将多个单步预设串联为 **预设组（Preset Group）** 按序执行（含循环依赖检测与子预设参数覆盖）。
- **GUI + CLI + 本地 HTTP 服务三端协同**
  - **悬浮友好界面**：支持置顶开关、透明度无级调节、一键折叠为迷你悬浮条。
  - **参数化 CLI**：支持外部脚本通过命令行传参执行、`--dry-run` 预演定位以及 `--json` 结构化结果输出。
  - **内置 HTTP 服务**：一键启动轻量级本地 HTTP API（默认 `127.0.0.1:18999`），通过简单的 `GET` URL 参数或 `POST` JSON 即可远程触发桌面自动化预设，全部执行明细自动落盘至本地审计日志。

## 环境要求

- **操作系统**：Windows 10 / 11
- **Python 版本**：Python 3.10 及以上（推荐使用 [`uv`](https://docs.astral.sh/uv/) 管理虚拟环境）

## 快速开始

### 1. 克隆仓库并安装依赖

使用 `uv`（推荐，可直接配合 `autotool.bat` 使用）：
```powershell
git clone https://github.com/czr27/win-autotool.git
cd win-autotool
uv sync --python 3.10 --extra dev
```

或使用标准 `venv` + `pip`：
```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
```

> **关于预设与运行数据**：运行时创建的个人自动化预设默认保存在本地 `presets/` 目录（预设结构可参考 [`examples/preset.example.json`](./examples/preset.example.json)，可按需复制到 `presets/` 目录体验），执行审计日志保存在 `logs/` 目录，二者均已通过 `.gitignore` 忽略，不会上传个人本地数据。

### 2. 启动悬浮图形界面（GUI）

双击运行 `run_gui.bat`，或在终端执行：
```powershell
.\autotool.bat gui
```

### 3. 通过命令行（CLI）调用预设

```powershell
# 查看所有已保存预设（支持 --json）
.\autotool.bat list

# 查看指定预设的 JSON 定义
.\autotool.bat show notepad_type_demo

# 传入动态参数执行预设（支持 --dry-run 仅验证定位、--json 输出结构化结果）
.\autotool.bat run notepad_type_demo -p text="你好世界" --json
```

### 4. 启动本地 HTTP API 服务

双击运行 `run_ser.bat`，或在终端执行：
```powershell
.\autotool.bat serve --host 127.0.0.1 --port 18999
```
常用接口：
- `GET http://127.0.0.1:18999/health` — 健康检查与可用端点列表
- `GET http://127.0.0.1:18999/presets` — 获取已保存预设及参数列表
- `GET http://127.0.0.1:18999/run/<preset_name>?text=hello&_dry_run=1` — 通过 URL Query 传参触发预设
- `POST http://127.0.0.1:18999/run/<preset_name>` — 通过 JSON Body 传参触发预设

## 运行测试

```powershell
.\.venv\Scripts\pytest
```

## 目录结构

```text
autotool/
├── src/autotool/
│   ├── cli.py                    # Click 命令行入口 (gui / list / show / run / serve)
│   ├── core/
│   │   ├── inspector.py          # Windows UIA 控件实时拾取器
│   │   ├── locator_generator.py  # 多策略智能定位生成器
│   │   ├── resolver.py           # UIA / 图像定位解析器与诊断器
│   │   ├── image_matcher.py      # MSS 截屏与 OpenCV 模板匹配引擎
│   │   ├── win32_input.py        # Win32 SendInput 物理键鼠与剪贴板注入
│   │   ├── runner.py             # 动作序列与复合预设组执行引擎
│   │   ├── http_server.py        # 内置多线程 HTTP API 服务
│   │   ├── audit_log.py          # 运行日志与 JSONL 审计记录
│   │   └── dpi.py                # Per-Monitor DPI Awareness V2 适配
│   ├── models/
│   │   ├── schema.py             # Pydantic 数据模型 (Preset / Locator / ActionStep)
│   │   └── preset_store.py       # 预设 JSON 持久化、排序与 {{param}} 模板渲染
│   └── ui/                       # PySide6 悬浮主窗口、靶心拾取器、框选器与编辑器组件
├── examples/
│   └── preset.example.json       # 单步参数化预设 JSON 示例模板
├── tests/                        # Pytest 单元测试套件
├── pyproject.toml                # 项目元数据与依赖声明
├── uv.lock                       # uv 锁定依赖版本
├── autotool.bat                  # CLI 快捷包装脚本
├── run_gui.bat                   # 一键启动悬浮 GUI
└── run_ser.bat                   # 一键启动本地 HTTP 服务
```

## 更新日志

### v0.1.0 (2026-11-27)
- 初始公开发布：支持 Windows UIA 控件靶心抓捕与屏幕图像模板匹配、7 种智能定位策略生成与诊断、参数化键鼠动作编排与预设组串联、PySide6 迷你折叠悬浮界面、参数化 CLI 以及内置本地 HTTP 触发服务。
