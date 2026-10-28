"""把主库快照推到页面安装的备机，备机用本机库做认证和 /admin。"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import Vps
from app.services import ssh

logger = logging.getLogger("trojan-admin")
settings = get_settings()
_sync_lock = threading.Lock()

REMOTE_ROOT = "/opt/vps-trojan-node"
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
import os, sqlite3
from pathlib import Path
domain = os.environ["REPLICA_DOMAIN"]
incoming = Path("/var/lib/trojan-admin/ha-incoming/trojan.db")
live = Path("/opt/vps-trojan-node/backend/data/trojan.db")
live.parent.mkdir(parents=True, exist_ok=True)
if not incoming.is_file():
    raise SystemExit("缺少同步快照")
src = sqlite3.connect(incoming)
dst = sqlite3.connect(live, timeout=30)
try:
    src.backup(dst)
    dst.execute("UPDATE vps SET is_local=0")
    dst.execute(
        "UPDATE vps SET is_local=1, ssh_host='127.0.0.1', status='online' WHERE id=?",
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
    client = ssh.connect(vps.ssh_host, int(vps.ssh_port or 22), vps.ssh_user or "root")
    try:
        ssh.run(client, "mkdir -p /var/lib/trojan-admin/ha-incoming")
        ssh.sftp_put_bytes(client, snapshot.read_bytes(), INCOMING)
        key = ssh.private_key_path()
        pub = ssh.public_key_path()
        if key.is_file():
            remote_ssh = f"{REMOTE_ROOT}/backend/data/ssh"
            ssh.run(client, f"mkdir -p {remote_ssh} && chmod 700 {remote_ssh}")
            ssh.sftp_put_bytes(client, key.read_bytes(), f"{remote_ssh}/id_ed25519")
            ssh.run(client, f"chmod 600 {remote_ssh}/id_ed25519")
        if pub.is_file():
            remote_ssh = f"{REMOTE_ROOT}/backend/data/ssh"
            ssh.run(client, f"mkdir -p {remote_ssh}")
            ssh.sftp_put_bytes(client, pub.read_bytes(), f"{remote_ssh}/id_ed25519.pub")
        ssh.run(
            client,
            f"REPLICA_DOMAIN={vps.id} {_apply_script()}",
            timeout=120,
        )
    finally:
        client.close()


def _record_sync_error(db: Session, vps_id: str, message: str) -> None:
    try:
        row = db.get(Vps, vps_id)
        if row is None:
            return
        row.last_error = message
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("写入备机同步错误失败：%s", vps_id)


def sync_replicas() -> None:
    if not settings.is_control_primary:
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
            for row in rows:
                try:
                    push_replica(row, snapshot)
                    if row.last_error:
                        row.last_error = ""
                        db.commit()
                    logger.info("已同步备机 %s", row.id)
                except Exception as exc:
                    logger.exception("同步备机失败：%s", row.id)
                    _record_sync_error(db, row.id, str(exc))
        finally:
            db.close()


def push_replicas_after_acl_commit() -> None:
    """主库已提交后再调用。推送失败只记日志和 vps.last_error，不影响主库。"""
    if not settings.is_control_primary:
        return
    threading.Thread(target=_sync_replicas_safe, daemon=True, name="replica-acl-push").start()


def _sync_replicas_safe() -> None:
    try:
        sync_replicas()
    except Exception:
        logger.exception("即时同步备机失败")
