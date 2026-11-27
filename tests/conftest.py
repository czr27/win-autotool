"""Pytest configuration: isolate AuditLogger into a temp directory so tests never pollute real logs/."""

from pathlib import Path

import pytest

from autotool.core import audit_log
from autotool.core.audit_log import AuditLogger


@pytest.fixture(autouse=True)
def _isolate_audit_logger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit_log, "_DEFAULT_LOGGER", AuditLogger(logs_dir=tmp_path / "test_logs"))
