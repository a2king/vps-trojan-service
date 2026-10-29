from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import threading

from app.config import get_settings

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows development fallback
    fcntl = None

_thread_lock = threading.Lock()


def lock_path() -> Path:
    settings = get_settings()
    if os.name == "nt":
        settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        return settings.sqlite_path.parent / "primary-switch.lock"
    configured = Path("/var/lib/trojan-admin")
    try:
        configured.mkdir(parents=True, exist_ok=True)
        return configured / "primary-switch.lock"
    except OSError:
        settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        return settings.sqlite_path.parent / "primary-switch.lock"


def marker_path() -> Path:
    return lock_path().with_name("primary-switch.in-progress")


def mark_switch_in_progress() -> None:
    marker_path().write_text("switching\n", encoding="utf-8")


def clear_switch_marker() -> None:
    try:
        marker_path().unlink()
    except FileNotFoundError:
        pass


def is_switch_in_progress() -> bool:
    return marker_path().is_file()


@contextmanager
def primary_switch_lock():
    if not _thread_lock.acquire(blocking=False):
        raise RuntimeError("已有主节点切换正在进行")
    handle = None
    try:
        handle = lock_path().open("a+")
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("已有主节点切换正在进行") from exc
        yield
    finally:
        if handle is not None:
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()
        _thread_lock.release()
