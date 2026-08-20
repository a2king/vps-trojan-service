from __future__ import annotations

import threading
from pathlib import Path

from app.config import get_settings
from app.timeutil import now_local

settings = get_settings()
_lock = threading.Lock()
_logs: dict[str, list[str]] = {}


def log_dir() -> Path:
    path = settings.sqlite_path.parent / "vps-logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_file(vps_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in ".-" else "_" for ch in vps_id)
    return log_dir() / f"{safe}.log"


def clear_log(vps_id: str) -> None:
    with _lock:
        _logs[vps_id] = []
    path = log_file(vps_id)
    if path.exists():
        path.write_text("", encoding="utf-8")


def append_log(vps_id: str, line: str) -> None:
    stamp = now_local().strftime("%H:%M:%S")
    text = f"[{stamp}] {line.rstrip()}"
    with _lock:
        _logs.setdefault(vps_id, []).append(text)
    path = log_file(vps_id)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text + "\n")


def read_log(vps_id: str) -> str:
    path = log_file(vps_id)
    if path.is_file():
        return path.read_text(encoding="utf-8", errors="replace")
    with _lock:
        return "\n".join(_logs.get(vps_id) or [])
