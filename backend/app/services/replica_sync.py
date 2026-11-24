"""把主库快照推到页面安装的备机，备机用本机库做认证和 /admin。"""

from __future__ import annotations

import logging
import shlex
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import Vps
from app.services import ssh
from app.services.control_lock import is_switch_in_progress
from app.services.observation import acl_version, update_status
from app.timeutil import now_local

logger = logging.getLogger("trojan-admin")
settings = get_settings()
_sync_lock = threading.Lock()

INCOMING = "/var/lib/trojan-admin/ha-incoming/trojan.db"


def backup_sqlite(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    source = sqlite3.connect(settings.sqlite_path)
    target = sqlite3.connect(dest)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    dest.chmod(0o600)


def _apply_script() -> str:
    return """python3 - <<'PY'
import json, os, sqlite3, subprocess
from pathlib import Path
from urllib.request import urlopen
domain = os.environ["REPLICA_DOMAIN"]
incoming = Path("/var/lib/trojan-admin/ha-incoming/trojan.db")
backend = Path(subprocess.check_output(
    ["systemctl", "show", "-p", "WorkingDirectory", "--value", "trojan-admin"], text=True,
).strip())
live = backend / "data" / "trojan.db"
if not backend.is_absolute() or not live.is_file() or not incoming.is_file():
    raise SystemExit("找不到备机数据库或同步快照")
with urlopen("http://127.0.0.1:8000/api/health", timeout=5) as response:
    if json.load(response).get("control_role") != "standby":
        raise SystemExit("节点当前角色不是 standby")
src = sqlite3.connect(incoming)
dst = sqlite3.connect(live, timeout=30)
try:
    if src.execute("PRAGMA quick_check").fetchone() != ("ok",):
        raise SystemExit("同步快照完整性校验失败")
    src.backup(dst)
    dst.execute("UPDATE vps SET ssh_host=id, replica=1 WHERE is_local=1 AND ssh_host IN ('127.0.0.1', 'localhost', '')")
    dst.execute("UPDATE vps SET is_local=0")
    dst.execute(
        "UPDATE vps SET is_local=1, replica=0, ssh_host='127.0.0.1', status='online' WHERE id=?",
        (domain,),
    )
    dst.commit()
finally:
    dst.close()
    src.close()
live.chmod(0o600)
print("applied")
PY
"""


def push_replica(vps: Vps, snapshot: Path) -> None:
    client = ssh.connect_node(vps)
    try:
        ssh.run(client, "install -d -m 700 /var/lib/trojan-admin/ha-incoming")
        ssh.sftp_put_bytes(client, snapshot.read_bytes(), f"{INCOMING}.pending")
        ssh.run(client, f"chmod 600 {INCOMING}.pending && mv -f {INCOMING}.pending {INCOMING}")
        ssh.run(
            client,
            f"REPLICA_DOMAIN={shlex.quote(vps.id)} {_apply_script()}",
            timeout=120,
        )
    finally:
        client.close()


def _record_sync_error(db: Session, vps_id: str, message: str) -> None:
    try:
        row = db.get(Vps, vps_id)
        if row is None:
            return
        update_status(db, vps_id, sync_error=message[:400])
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("写入备机同步错误失败：%s", vps_id)


def sync_replicas() -> None:
    if not settings.is_control_primary or is_switch_in_progress():
        return
    with _sync_lock:
        db = SessionLocal()
        try:
            rows = (
                db.query(Vps)
                .filter(Vps.is_local == 0, Vps.replica == 1, Vps.status == "online")
                .all()
            )
            if not rows:
                return
            snapshot = settings.sqlite_path.parent / "replica-snapshot.db"
            backup_sqlite(snapshot)
            with closing(sqlite3.connect(snapshot)) as snapshot_db:
                version = acl_version(snapshot_db.execute)
            for row in rows:
                try:
                    push_replica(row, snapshot)
                    update_status(db, row.id, applied_version=version, applied_at=now_local(), sync_error=None)
                    db.commit()
                    logger.info("已同步备机 %s", row.id)
                except Exception as exc:
                    logger.exception("同步备机失败：%s", row.id)
                    _record_sync_error(db, row.id, str(exc))
        finally:
            db.close()


def push_replicas_after_acl_commit() -> None:
    """主库已提交后再调用。热同步立即推送，冷备份走十分钟防抖。"""
    if not settings.is_control_primary:
        return
    threading.Thread(target=_sync_replicas_safe, daemon=True, name="replica-acl-push").start()
    # Cold snapshots are intentionally debounced: one write burst produces one
    # package, while the existing hot replica sync remains immediate.
    from app.services.cold_backup import schedule_cold_backup

    schedule_cold_backup()


def _sync_replicas_safe() -> None:
    try:
        sync_replicas()
    except Exception:
        logger.exception("即时同步备机失败")
