"""完整备控冷备份：把可从零恢复的控制面快照广播到在线完整备控。"""

from __future__ import annotations

import io
import logging
import sqlite3
import tarfile
import tempfile
import threading
from pathlib import Path

from app.config import get_settings
from app.models import Vps
from app.paths import repo_root
from app.services import ssh
from app.services.control_lock import is_switch_in_progress
from app.timeutil import now_local

logger = logging.getLogger("trojan-admin")
settings = get_settings()

REMOTE_BACKUP_ROOT = "/opt/data/trojan-backup"
RETENTION_COUNT = 14
DEBOUNCE_SECONDS = 10 * 60
_cold_lock = threading.Lock()
_timer_lock = threading.Lock()
_debounce_timer: threading.Timer | None = None


def _sqlite_backup(source_path: Path, dest_path: Path) -> None:
    """Use SQLite online backup so a live database is always consistent."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        dest_path.unlink()
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(dest_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    dest_path.chmod(0o600)


def _ha_env_path(root: Path) -> Path | None:
    candidates = (
        Path("/etc/trojan-admin/ha.env"),
        root / "ha.env",
        root / "deploy" / "ha.env",
    )
    return next((path for path in candidates if path.is_file()), None)


def _restore_guide() -> str:
    return """# Trojan 控制面冷备份恢复步骤

本包由主控生成，包含一致性的 `trojan.db`、管理 SSH 密钥、安装根 `.env`、可选 `ha.env`。
账号、规则、隧道和节点配置都在 SQLite 数据库中，无需额外导入。

## 从零恢复

1. 在新机器上按仓库说明完成基础安装（`install.sh`），并确认安装根目录为 `/opt/vps-trojan-service`；安装脚本会准备 nginx、Trojan、Python 环境和 systemd 服务。
2. 停止后台服务：
   ```bash
   sudo systemctl stop trojan-admin || true
   ```
3. 从备控取回对应的 `<stamp>.tar.gz`，在安装根目录解压本包（本文件位于包内）：
   ```bash
   sudo tar -xzf <本备份文件>.tar.gz -C /opt/vps-trojan-service
   sudo install -D -m 600 /opt/vps-trojan-service/trojan.db \
     /opt/vps-trojan-service/backend/data/trojan.db
   sudo rm -f /opt/vps-trojan-service/trojan.db
   ```
4. 确认密钥目录权限：
   ```bash
   sudo chmod 700 /opt/vps-trojan-service/backend/data/ssh
   sudo chmod 600 /opt/vps-trojan-service/backend/data/ssh/id_ed25519 2>/dev/null || true
   ```
5. 确认安装根 `.env` 中的 `DOMAIN`、`CONTROL_ROLE` 和路径配置；首次接管时设为 `CONTROL_ROLE=primary`，仅待命时设为 `standby`。
6. 若包内有 `ha.env`，复制到 `/etc/trojan-admin/ha.env` 并设为 600：
   ```bash
   sudo install -D -m 600 /opt/vps-trojan-service/ha.env /etc/trojan-admin/ha.env
   ```
7. 启动服务并检查 `/api/health`：
   ```bash
   sudo systemctl start trojan-admin
   curl -fsS http://127.0.0.1:8000/api/health
   ```

恢复后请按需更新 SSH 主机地址、DNS 和 `ha.env` 中的主备参数，再启用主备同步服务。
"""


def build_archive() -> tuple[str, bytes]:
    """Return ``(stamp, tar.gz bytes)`` for one local cold snapshot."""
    root = repo_root()
    env_file = root / ".env"
    if not env_file.is_file():
        raise RuntimeError(f"冷备份缺少安装根 .env：{env_file}")
    source_db = settings.sqlite_path
    if not source_db.is_file():
        raise RuntimeError(f"冷备份缺少 SQLite 数据库：{source_db}")
    ssh_dir = source_db.parent / "ssh"
    ha_env = _ha_env_path(root)
    stamp = now_local().strftime("%Y%m%d-%H%M%S")

    with tempfile.TemporaryDirectory(prefix="trojan-cold-backup-") as tmp:
        snapshot = Path(tmp) / "trojan.db"
        _sqlite_backup(source_db, snapshot)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            archive.add(snapshot, arcname="trojan.db", recursive=False)
            # Keep the directory itself in the archive even when a fresh install
            # has no generated key yet.
            if ssh_dir.is_dir():
                archive.add(ssh_dir, arcname="backend/data/ssh", recursive=True)
            else:
                info = tarfile.TarInfo("backend/data/ssh")
                info.type = tarfile.DIRTYPE
                info.mode = 0o700
                archive.addfile(info)
            archive.add(env_file, arcname=".env", recursive=False)
            if ha_env is not None:
                archive.add(ha_env, arcname="ha.env", recursive=False)
            restore = _restore_guide().encode("utf-8")
            info = tarfile.TarInfo("RESTORE.md")
            info.size = len(restore)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(restore))
        return stamp, buffer.getvalue()


def _remote_prepare_command() -> str:
    return f"""python3 - <<'PY'
from pathlib import Path
root = Path({REMOTE_BACKUP_ROOT!r})
root.mkdir(parents=True, exist_ok=True)
root.chmod(0o700)
PY"""


def _remote_prune_command() -> str:
    return f"""python3 - <<'PY'
from pathlib import Path
import shutil
root = Path({REMOTE_BACKUP_ROOT!r})
entries = [
    path for path in root.iterdir()
    if path.name.startswith('20') and (path.name.endswith('.tar.gz') or path.is_dir())
]
entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
for path in entries[{RETENTION_COUNT}:]:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except FileNotFoundError:
        pass
root.chmod(0o700)
PY"""


def _online_replicas() -> list[Vps]:
    # Import lazily to keep this module usable by startup/tests without opening
    # a SQLAlchemy session until a backup is actually requested.
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        return list(
            db.query(Vps)
            .filter(Vps.is_local == 0, Vps.replica == 1, Vps.status == "online")
            .all()
        )
    finally:
        db.close()


def broadcast_cold_backup() -> None:
    """Create one package and broadcast it to all online complete replicas."""
    if not settings.is_control_primary or is_switch_in_progress():
        return
    with _cold_lock:
        rows = _online_replicas()
        if not rows:
            return
        stamp, payload = build_archive()
        remote_name = f"{REMOTE_BACKUP_ROOT}/{stamp}.tar.gz"
        for row in rows:
            client = None
            try:
                client = ssh.connect(row.ssh_host, int(row.ssh_port or 22), row.ssh_user or "root")
                ssh.run(client, _remote_prepare_command(), timeout=60)
                ssh.sftp_put_bytes(client, payload, remote_name)
                ssh.run(client, f"chmod 600 {remote_name}", timeout=30)
                ssh.run(client, _remote_prune_command(), timeout=60)
                logger.info("已广播冷备份 %s 到完整备控 %s", stamp, row.id)
            except Exception:
                logger.exception("广播冷备份到备控失败：%s", row.id)
            finally:
                if client is not None:
                    client.close()


def _broadcast_safe() -> None:
    global _debounce_timer
    with _timer_lock:
        _debounce_timer = None
    try:
        broadcast_cold_backup()
    except Exception:
        logger.exception("冷备份广播失败")


def schedule_cold_backup() -> None:
    """Debounce write-triggered cold backup by about ten minutes."""
    global _debounce_timer
    if not settings.is_control_primary:
        return
    with _timer_lock:
        if _debounce_timer is not None:
            _debounce_timer.cancel()
        _debounce_timer = threading.Timer(DEBOUNCE_SECONDS, _broadcast_safe)
        _debounce_timer.daemon = True
        _debounce_timer.name = "cold-backup-debounce"
        _debounce_timer.start()


def job_cold_backup() -> None:
    """Daily APScheduler entrypoint."""
    _broadcast_safe()
