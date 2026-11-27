"""Lightweight built-in HTTP server for invoking AutoTool presets via GET URL parameters (or POST JSON)."""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

import uiautomation as auto

from autotool.core.runner import ActionRunner
from autotool.models.preset_store import PresetStore


def _run_result_to_dict(result) -> dict[str, Any]:
    return {
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


def _make_handler_class(
    store: PresetStore,
    runner_factory: Callable[[], ActionRunner],
    exec_lock: threading.Lock,
) -> type[BaseHTTPRequestHandler]:
    class AutoToolRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # Silence default stderr logging; callers/GUI can observe via JSON responses
            return

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query_multi = parse_qs(parsed.query, keep_blank_values=True)
            query = {k: vals[-1] for k, vals in query_multi.items()}

            if path in ("/", "/health"):
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "service": "autotool",
                        "endpoints": [
                            "GET /health",
                            "GET /presets",
                            "GET /run/<preset_name>?param=value",
                            "GET /run?preset=<preset_name>&param=value",
                            "POST /run/<preset_name>",
                        ],
                    },
                )
                return

            if path == "/presets":
                presets = store.list_presets()
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "presets": [
                            {
                                "name": p.name,
                                "kind": p.kind,
                                "description": p.description,
                                "strategy_type": p.locator.strategy_type if p.locator else None,
                                "steps": len(p.group_items) if p.kind == "group" else len(p.actions),
                                "group_items": (
                                    [x.model_dump() for x in p.group_items]
                                    if p.kind == "group"
                                    else []
                                ),
                                "parameters": [x.model_dump() for x in p.parameters],
                            }
                            for p in presets
                        ]
                    },
                )
                return

            if path == "/run":
                preset_name = query.pop("preset", "").strip()
                if not preset_name:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"success": False, "error": "缺少 preset 参数，例如 /run?preset=demo&text=hello"},
                    )
                    return
                self._handle_run(preset_name, query)
                return

            if path.startswith("/run/"):
                preset_name = unquote(path[len("/run/") :]).strip()
                self._handle_run(preset_name, query)
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"success": False, "error": f"未知路径: {path}"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            query_multi = parse_qs(parsed.query, keep_blank_values=True)
            params: dict[str, Any] = {k: vals[-1] for k, vals in query_multi.items()}

            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > 0:
                raw = self.rfile.read(length)
                try:
                    body_json = json.loads(raw.decode("utf-8"))
                except Exception as exc:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST, {"success": False, "error": f"无效 JSON: {exc}"}
                    )
                    return
                if not isinstance(body_json, dict):
                    self._send_json(
                        HTTPStatus.BAD_REQUEST, {"success": False, "error": "请求体必须是 JSON 对象"}
                    )
                    return
                params.update(body_json)

            if path == "/run":
                preset_name = str(params.pop("preset", "")).strip()
            elif path.startswith("/run/"):
                preset_name = unquote(path[len("/run/") :]).strip()
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"success": False, "error": f"未知路径: {path}"})
                return

            if not preset_name:
                self._send_json(HTTPStatus.BAD_REQUEST, {"success": False, "error": "缺少预设名称"})
                return
            self._handle_run(preset_name, params)

        def _handle_run(self, preset_name: str, raw_params: dict[str, Any]) -> None:
            params = dict(raw_params)
            timeout_raw = params.pop("_timeout", None)
            dry_run_raw = str(params.pop("_dry_run", "")).strip().lower()
            dry_run = dry_run_raw in ("1", "true", "yes")

            timeout_override: int | None = None
            if timeout_raw not in (None, ""):
                try:
                    timeout_override = int(timeout_raw)
                except ValueError:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"success": False, "error": f"_timeout 必须是整数毫秒: {timeout_raw!r}"},
                    )
                    return

            try:
                preset = store.load(preset_name)
            except FileNotFoundError as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"success": False, "error": str(exc)})
                return
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"success": False, "error": str(exc)})
                return

            with exec_lock:
                with auto.UIAutomationInitializerInThread():
                    runner = runner_factory()
                    result = runner.run_preset(
                        preset,
                        params=params,
                        timeout_override_ms=timeout_override,
                        dry_run=dry_run,
                        store=store,
                        source="http",
                    )

            payload = _run_result_to_dict(result)
            if result.success:
                self._send_json(HTTPStatus.OK, payload)
            elif "未找到匹配控件" in result.error:
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, payload)
            elif (
                "缺少必需参数" in result.error
                or "执行前检查失败" in result.error
                or "循环引用" in result.error
            ):
                self._send_json(HTTPStatus.BAD_REQUEST, payload)
            else:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, payload)

    return AutoToolRequestHandler


class AutoToolHTTPServer:
    """Managed background HTTP server wrapping ThreadingHTTPServer."""

    def __init__(
        self,
        store: PresetStore | None = None,
        host: str = "127.0.0.1",
        port: int = 18999,
        runner_factory: Callable[[], ActionRunner] | None = None,
    ) -> None:
        self.store = store or PresetStore()
        self.host = host
        self.port = port
        self.runner_factory = runner_factory or ActionRunner
        self._exec_lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._httpd is not None

    @property
    def actual_port(self) -> int:
        if self._httpd is not None:
            return int(self._httpd.server_address[1])
        return self.port

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.actual_port}"

    def start(self) -> None:
        if self._httpd is not None:
            return
        handler_cls = _make_handler_class(self.store, self.runner_factory, self._exec_lock)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler_cls)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="AutoToolHTTPServer",
            daemon=True,
        )
        self._thread.start()

    def serve_forever(self) -> None:
        """Blocking serve for CLI `autotool serve`."""
        handler_cls = _make_handler_class(self.store, self.runner_factory, self._exec_lock)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler_cls)
        try:
            self._httpd.serve_forever()
        finally:
            self._httpd.server_close()
            self._httpd = None

    def stop(self) -> None:
        if self._httpd is None:
            return
        httpd = self._httpd
        self._httpd = None
        httpd.shutdown()
        httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
