"""Command-line interface for AutoTool: launch floating GUI or run saved presets with parameters."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from autotool.core.dpi import enable_per_monitor_dpi_awareness
from autotool.models.preset_store import PresetStore


def _parse_kv_params(items: tuple[str, ...], params_json: str | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if params_json:
        try:
            parsed = json.loads(params_json)
        except json.JSONDecodeError as exc:
            raise click.ClickException(f"--params-json 不是合法 JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise click.ClickException("--params-json 必须是 JSON 对象")
        merged.update(parsed)

    for raw in items:
        if "=" not in raw:
            raise click.ClickException(f"参数格式错误 {raw!r}，应为 key=value")
        key, val = raw.split("=", 1)
        merged[key.strip()] = val
    return merged


@click.group(invoke_without_command=True)
@click.option(
    "--presets-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="自定义预设目录路径（默认使用项目 presets/ 目录）",
)
@click.pass_context
def main(ctx: click.Context, presets_dir: Path | None) -> None:
    """AutoTool — Windows 桌面控件自动化工具箱。"""
    ctx.ensure_object(dict)
    ctx.obj["store"] = PresetStore(presets_dir)
    if ctx.invoked_subcommand is None:
        ctx.invoke(gui)


@main.command()
@click.pass_context
def gui(ctx: click.Context) -> None:
    """启动悬浮图形界面。"""
    from PySide6.QtWidgets import QApplication

    # Create QApplication before importing uiautomation so Qt sets Per-Monitor V2 DPI first
    app = QApplication(sys.argv)

    from autotool.ui.main_window import FloatingMainWindow

    win = FloatingMainWindow(store=ctx.obj["store"])
    win.show()
    sys.exit(app.exec())


@main.command(name="list")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 格式输出")
@click.pass_context
def list_cmd(ctx: click.Context, as_json: bool) -> None:
    """列出所有已保存的预设。"""
    store: PresetStore = ctx.obj["store"]
    presets = store.list_presets()
    if as_json:
        payload = [
            {
                "name": p.name,
                "kind": p.kind,
                "description": p.description,
                "strategy_type": p.locator.strategy_type if p.locator else None,
                "steps": len(p.group_items) if p.kind == "group" else len(p.actions),
                "group_items": [x.model_dump() for x in p.group_items] if p.kind == "group" else [],
                "parameters": [x.model_dump() for x in p.parameters],
                "updated_at": p.updated_at,
            }
            for p in presets
        ]
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not presets:
        click.echo("当前没有任何已保存的预设。")
        return
    for p in presets:
        if p.kind == "group":
            members = " -> ".join(x.preset_name for x in p.group_items)
            click.echo(f"- [预设组] {p.name} ({len(p.group_items)} 个子预设: {members}) {p.description}")
        else:
            params = ", ".join(x.name for x in p.parameters) or "无"
            click.echo(f"- {p.name} ({len(p.actions)} 步 | 参数: {params}) {p.description}")


@main.command()
@click.argument("preset_name")
@click.pass_context
def show(ctx: click.Context, preset_name: str) -> None:
    """查看指定预设的完整 JSON 定义。"""
    store: PresetStore = ctx.obj["store"]
    try:
        preset = store.load(preset_name)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"错误: {exc}", err=True)
        sys.exit(2)
    click.echo(json.dumps(preset.model_dump(mode="json"), ensure_ascii=False, indent=2))


@main.command()
@click.argument("preset_name")
@click.option("-p", "--param", "params", multiple=True, help="传入参数，格式 key=value，可重复指定")
@click.option("--params-json", default=None, help="通过 JSON 字符串批量传入参数")
@click.option("--timeout", type=int, default=None, help="覆盖控件定位超时时间 (ms)")
@click.option("--dry-run", is_flag=True, help="仅定位控件与渲染步骤，不发送实际键鼠操作")
@click.option("--json", "as_json", is_flag=True, help="以结构化 JSON 输出执行结果")
@click.pass_context
def run(
    ctx: click.Context,
    preset_name: str,
    params: tuple[str, ...],
    params_json: str | None,
    timeout: int | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """按名称执行已保存预设，并支持注入参数。"""
    enable_per_monitor_dpi_awareness()
    store: PresetStore = ctx.obj["store"]
    try:
        preset = store.load(preset_name)
        merged_params = _parse_kv_params(params, params_json)
    except (FileNotFoundError, ValueError, click.ClickException) as exc:
        if as_json:
            click.echo(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        else:
            click.echo(f"参数/预设错误: {exc}", err=True)
        sys.exit(2)

    from autotool.core.runner import ActionRunner

    runner = ActionRunner(store=store)
    result = runner.run_preset(
        preset,
        params=merged_params,
        timeout_override_ms=timeout,
        dry_run=dry_run,
        store=store,
        source="cli",
    )

    if as_json:
        payload = {
            "run_id": result.run_id,
            "source": result.source,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "preset": result.preset_name,
            "success": result.success,
            "dry_run": result.dry_run,
            "total_ms": result.total_ms,
            "error": result.error,
            "resolved": result.resolved.model_dump() if result.resolved else None,
            "steps": [
                {
                    "index": s.index,
                    "timestamp": s.timestamp,
                    "type": s.action_type,
                    "detail": s.detail,
                    "elapsed_ms": s.elapsed_ms,
                    "success": s.success,
                    "error": s.error,
                }
                for s in result.steps
            ],
        }
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        click.echo(f"[{result.started_at}] [RUN {result.run_id}] 预设={result.preset_name}")
        for s in result.steps:
            mark = "OK  " if s.success else "FAIL"
            err_txt = f" | 错误: {s.error}" if s.error else ""
            click.echo(f"  [{s.timestamp}] [{mark}] [{s.index}] ({s.action_type}) {s.detail} ({s.elapsed_ms}ms){err_txt}")
        if result.success:
            click.echo(f"[{result.finished_at}] 执行成功 (总耗时 {result.total_ms}ms)")
        else:
            click.echo(f"[{result.finished_at}] 执行失败 ({result.total_ms}ms): {result.error}", err=True)

    if not result.success:
        if "未找到匹配控件" in result.error:
            sys.exit(3)
        if (
            "缺少必需参数" in result.error
            or "执行前检查失败" in result.error
            or "循环引用" in result.error
        ):
            sys.exit(2)
        sys.exit(4)


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="监听地址")
@click.option("--port", type=int, default=18999, show_default=True, help="监听端口")
@click.pass_context
def serve(ctx: click.Context, host: str, port: int) -> None:
    """启动本地 HTTP 服务，支持通过 GET URL 带参数调用预设。"""
    enable_per_monitor_dpi_awareness()
    from autotool.core.http_server import AutoToolHTTPServer

    server = AutoToolHTTPServer(store=ctx.obj["store"], host=host, port=port)
    click.echo(f"AutoTool HTTP 服务已启动: http://{host}:{port}")
    click.echo(f"  调用示例: GET http://{host}:{port}/run/<预设名>?text=你好")
    click.echo(f"  查看预设: GET http://{host}:{port}/presets")
    click.echo("按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        click.echo("\nHTTP 服务已停止。")
