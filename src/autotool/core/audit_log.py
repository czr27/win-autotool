"""Thread-safe persistent audit logger writing daily human-readable .log and machine .jsonl files."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


def local_timestamp_ms() -> str:
    """Return local wall-clock timestamp with millisecond precision: YYYY-MM-DD HH:MM:SS.fff."""
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"


def default_logs_dir() -> Path:
    """Return the default `logs/` directory at the project root."""
    return Path(__file__).resolve().parents[3] / "logs"


class AuditLogger:
    """Records every run, broken-down step, and key system event with timestamps and pass/fail status."""

    def __init__(self, logs_dir: Path | str | None = None) -> None:
        self.logs_dir = Path(logs_dir) if logs_dir else default_logs_dir()
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @staticmethod
    def new_run_id() -> str:
        return uuid.uuid4().hex[:8]

    def _today_paths(self) -> tuple[Path, Path]:
        day = datetime.now().strftime("%Y-%m-%d")
        return self.logs_dir / f"autotool-{day}.log", self.logs_dir / f"autotool-{day}.jsonl"

    def _append(self, text_line: str, json_record: dict[str, Any]) -> None:
        log_path, jsonl_path = self._today_paths()
        json_line = json.dumps(json_record, ensure_ascii=False)
        with self._lock:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(text_line + "\n")
            with jsonl_path.open("a", encoding="utf-8") as f:
                f.write(json_line + "\n")

    def log_run_start(
        self,
        run_id: str,
        source: str,
        preset_name: str,
        kind: str,
        params: dict[str, Any],
        dry_run: bool,
        timestamp: str | None = None,
    ) -> None:
        ts = timestamp or local_timestamp_ms()
        params_str = json.dumps(params, ensure_ascii=False)
        line = (
            f"[{ts}] [RUN {run_id}] [START] source={source} preset={preset_name} "
            f"kind={kind} dry_run={dry_run} params={params_str}"
        )
        self._append(
            line,
            {
                "timestamp": ts,
                "event": "run_start",
                "run_id": run_id,
                "source": source,
                "preset": preset_name,
                "kind": kind,
                "dry_run": dry_run,
                "params": params,
            },
        )

    def log_step(
        self,
        run_id: str,
        index: int,
        action_type: str,
        detail: str,
        success: bool,
        elapsed_ms: int,
        error: str = "",
        timestamp: str | None = None,
    ) -> None:
        ts = timestamp or local_timestamp_ms()
        status = "OK  " if success else "FAIL"
        err_part = f" error={error!r}" if error else ""
        line = (
            f"[{ts}] [RUN {run_id}] [{status}] step={index} type={action_type} "
            f"detail={detail!r} elapsed={elapsed_ms}ms{err_part}"
        )
        self._append(
            line,
            {
                "timestamp": ts,
                "event": "step",
                "run_id": run_id,
                "index": index,
                "type": action_type,
                "detail": detail,
                "success": success,
                "elapsed_ms": elapsed_ms,
                "error": error,
            },
        )

    def log_run_end(
        self,
        run_id: str,
        preset_name: str,
        success: bool,
        total_ms: int,
        step_count: int,
        error: str = "",
        timestamp: str | None = None,
    ) -> None:
        ts = timestamp or local_timestamp_ms()
        status = "OK  " if success else "FAIL"
        err_part = f" error={error!r}" if error else ""
        line = (
            f"[{ts}] [RUN {run_id}] [END {status}] preset={preset_name} "
            f"steps={step_count} total={total_ms}ms{err_part}"
        )
        self._append(
            line,
            {
                "timestamp": ts,
                "event": "run_end",
                "run_id": run_id,
                "preset": preset_name,
                "success": success,
                "steps": step_count,
                "total_ms": total_ms,
                "error": error,
            },
        )

    def log_event(
        self,
        category: str,
        detail: str,
        success: bool = True,
        error: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        ts = local_timestamp_ms()
        status = "OK  " if success else "FAIL"
        err_part = f" error={error!r}" if error else ""
        line = f"[{ts}] [{category.upper()}] [{status}] {detail}{err_part}"
        record: dict[str, Any] = {
            "timestamp": ts,
            "event": category,
            "success": success,
            "detail": detail,
            "error": error,
        }
        if extra:
            record["extra"] = extra
        self._append(line, record)

    def list_log_dates(self) -> list[str]:
        """Return available log date strings (YYYY-MM-DD) sorted newest first."""
        dates: list[str] = []
        if not self.logs_dir.exists():
            return dates
        for p in self.logs_dir.glob("autotool-*.jsonl"):
            stem = p.stem
            if stem.startswith("autotool-"):
                dates.append(stem[len("autotool-") :])
        return sorted(set(dates), reverse=True)

    def load_runs(
        self,
        date_str: str | None = None,
        status_filter: str = "all",
        keyword: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Parse `.jsonl` audit logs into grouped run records (newest first)."""
        if not self.logs_dir.exists():
            return []

        if date_str and date_str != "all":
            files = [self.logs_dir / f"autotool-{date_str}.jsonl"]
        else:
            files = sorted(self.logs_dir.glob("autotool-*.jsonl"), reverse=True)

        runs_by_id: dict[str, dict[str, Any]] = {}
        ordered_ids: list[str] = []

        for path in files:
            if not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for raw in lines:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except Exception:
                    continue
                ev = rec.get("event")
                rid = str(rec.get("run_id") or "")
                if not rid or ev not in ("run_start", "step", "run_end"):
                    continue

                run = runs_by_id.get(rid)
                if run is None:
                    run = {
                        "run_id": rid,
                        "started_at": rec.get("timestamp", ""),
                        "finished_at": "",
                        "source": rec.get("source", ""),
                        "preset": rec.get("preset", ""),
                        "kind": rec.get("kind", "single"),
                        "dry_run": bool(rec.get("dry_run", False)),
                        "params": rec.get("params") or {},
                        "success": None,
                        "total_ms": 0,
                        "error": "",
                        "steps": [],
                    }
                    runs_by_id[rid] = run
                    ordered_ids.append(rid)

                if ev == "run_start":
                    run["started_at"] = rec.get("timestamp", run["started_at"])
                    run["source"] = rec.get("source", run["source"])
                    run["preset"] = rec.get("preset", run["preset"])
                    run["kind"] = rec.get("kind", run["kind"])
                    run["dry_run"] = bool(rec.get("dry_run", False))
                    run["params"] = rec.get("params") or {}
                elif ev == "step":
                    run["steps"].append(rec)
                elif ev == "run_end":
                    run["finished_at"] = rec.get("timestamp", "")
                    run["success"] = bool(rec.get("success", False))
                    run["total_ms"] = int(rec.get("total_ms", 0) or 0)
                    run["error"] = str(rec.get("error") or "")

        # Newest first
        runs = [runs_by_id[rid] for rid in ordered_ids]
        runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)

        kw = keyword.strip().lower()
        filtered: list[dict[str, Any]] = []
        for r in runs:
            if status_filter == "ok" and r["success"] is not True:
                continue
            if status_filter == "fail" and r["success"] is not False:
                continue
            if kw:
                hay = f"{r['preset']} {r['source']} {r['error']} {r['run_id']}".lower()
                if kw not in hay and not any(kw in str(s.get("detail", "")).lower() for s in r["steps"]):
                    continue
            filtered.append(r)
            if limit > 0 and len(filtered) >= limit:
                break
        return filtered


_DEFAULT_LOGGER: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    global _DEFAULT_LOGGER
    if _DEFAULT_LOGGER is None:
        _DEFAULT_LOGGER = AuditLogger()
    return _DEFAULT_LOGGER
