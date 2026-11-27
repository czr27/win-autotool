"""Tests for the built-in AutoTool HTTP server (GET query params & POST JSON)."""

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import pytest

from autotool.core.http_server import AutoToolHTTPServer
from autotool.core.runner import ActionRunner
from autotool.models.preset_store import PresetStore
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
            window_title="DemoWin",
            process_name="demo.exe",
            control_type="EditControl",
            rect=Rect(left=10, top=20, right=210, bottom=60),
            target_x=110,
            target_y=40,
            elapsed_ms=3,
        )


def test_http_get_endpoints_and_query_params(tmp_path: Path) -> None:
    store = PresetStore(tmp_path)
    store.save(
        Preset(
            name="输入测试",
            description="HTTP GET 调用测试",
            locator=LocatorStrategy(process_name="demo.exe", automation_id="1"),
            parameters=[
                ParameterDef(name="text", default="默认"),
                ParameterDef(name="key", default="enter"),
            ],
            actions=[
                ActionStep(type="left_click"),
                ActionStep(type="text_input", text="{{text}}"),
                ActionStep(type="key_press", key="{{key}}"),
            ],
        )
    )

    server = AutoToolHTTPServer(
        store=store,
        host="127.0.0.1",
        port=0,
        runner_factory=lambda: ActionRunner(resolver=_FakeResolver()),
    )
    server.start()
    try:
        base = server.base_url

        # 1. GET /health
        with urlopen(f"{base}/health") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["ok"] is True

        # 2. GET /presets
        with urlopen(f"{base}/presets") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert [p["name"] for p in data["presets"]] == ["输入测试"]

        # 3. GET /run/<preset_name>?text=...&key=...&_dry_run=1
        qs = urlencode({"text": "你好HTTP", "key": "tab", "_dry_run": "1"})
        url = f"{base}/run/{quote('输入测试')}?{qs}"
        with urlopen(url) as resp:
            assert resp.status == 200
            out = json.loads(resp.read().decode("utf-8"))
            assert out["success"] is True
            assert out["dry_run"] is True
            assert any("你好HTTP" in s["detail"] for s in out["steps"])
            assert any("tab" in s["detail"] for s in out["steps"])

        # 4. GET /run?preset=<preset_name>&text=...&_dry_run=1
        qs2 = urlencode({"preset": "输入测试", "text": "方式二", "_dry_run": "1"})
        with urlopen(f"{base}/run?{qs2}") as resp:
            assert resp.status == 200
            out2 = json.loads(resp.read().decode("utf-8"))
            assert out2["success"] is True
            assert any("方式二" in s["detail"] for s in out2["steps"])

        # 5. POST /run/<preset_name> with JSON
        req = Request(
            f"{base}/run/{quote('输入测试')}",
            data=json.dumps({"text": "来自POST", "_dry_run": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req) as resp:
            assert resp.status == 200
            out3 = json.loads(resp.read().decode("utf-8"))
            assert out3["success"] is True
            assert any("来自POST" in s["detail"] for s in out3["steps"])

        # 6. 404 for unknown preset
        with pytest.raises(HTTPError) as exc_info:
            urlopen(f"{base}/run/does_not_exist")
        assert exc_info.value.code == 404
    finally:
        server.stop()
