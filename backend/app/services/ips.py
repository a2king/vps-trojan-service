from __future__ import annotations

import json
import logging
import re
import shlex
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Account, AccountIp, LogCursor, Vps
from app.paths import repo_root
from app.services import ssh
from app.timeutil import TZ, now_local

logger = logging.getLogger("trojan-admin")
settings = get_settings()

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
AUTH_RE = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+\[INFO\]\s+"
    r"(.+?)\s+authenticated(?:\s+by authenticator \(|\s+as\s+)([0-9a-f]{7,56})\)?",
    re.IGNORECASE,
)

REMOTE_READ_SCRIPT = r"""
import json, os, sys
path = sys.argv[1]
offset = int(sys.argv[2])
try:
    st = os.stat(path)
except OSError:
    print(json.dumps({"inode": 0, "size": 0, "offset": 0, "chunk": ""}))
    raise SystemExit(0)
inode = int(getattr(st, "st_ino", 0) or 0)
size = int(st.st_size)
if offset > size:
    offset = 0
with open(path, "r", encoding="utf-8", errors="replace") as handle:
    handle.seek(offset)
    chunk = handle.read()
    new_offset = handle.tell()
print(json.dumps({"inode": inode, "size": size, "offset": new_offset, "chunk": chunk}, ensure_ascii=False))
"""


def _split_ip(endpoint: str) -> str:
    host = endpoint.rsplit(":", 1)[0].strip()
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    return host


def _parse_log_time(raw: str) -> datetime:
    parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=TZ).replace(tzinfo=None)


def _prefix_map(db: Session) -> dict[str, int]:
    mapping: dict[str, int] = {}
    collided: set[str] = set()
    for account in db.execute(select(Account)).scalars().all():
        prefix = account.password_sha224[:7]
        if prefix in collided:
            continue
        if prefix in mapping:
            collided.add(prefix)
            mapping.pop(prefix, None)
            continue
        mapping[prefix] = account.id
    return mapping


def _apply_chunk(
    db: Session,
    chunk: str,
    prefixes: dict[str, int],
    accounts: dict[int, Account],
    existing: dict[tuple[int, str], AccountIp],
) -> None:
    latest_by_account: dict[int, datetime] = {}
    for line in chunk.splitlines():
        matched = AUTH_RE.search(ANSI_RE.sub("", line.strip()))
        if not matched:
            continue
        seen_at = _parse_log_time(matched.group(1))
        ip = _split_ip(matched.group(2))
        token = matched.group(3).lower()
        account_id = prefixes.get(token[:7])
        if not ip or account_id is None:
            continue
        key = (account_id, ip)
        row = existing.get(key)
        if row is None:
            row = AccountIp(account_id=account_id, ip=ip, last_seen_at=seen_at)
            db.add(row)
            existing[key] = row
        elif seen_at > row.last_seen_at:
            row.last_seen_at = seen_at
        prev = latest_by_account.get(account_id)
        if prev is None or seen_at > prev:
            latest_by_account[account_id] = seen_at

    for account_id, seen_at in latest_by_account.items():
        account = accounts.get(account_id)
        if account is None:
            continue
        if account.last_seen_at is None or seen_at > account.last_seen_at:
            account.last_seen_at = seen_at


def _cursor(db: Session, name: str, inode: int, size: int) -> tuple[LogCursor, int]:
    cursor = db.get(LogCursor, name)
    if cursor is None:
        cursor = LogCursor(name=name, inode=inode, offset=0)
        db.add(cursor)
        db.flush()
    offset = int(cursor.offset or 0)
    if cursor.inode != inode or offset > size:
        offset = 0
    return cursor, offset


def ingest_local_ips(db: Session) -> None:
    path = Path(settings.trojan_log_path)
    if not path.is_file():
        return
    stat = path.stat()
    inode = int(getattr(stat, "st_ino", 0) or 0)
    size = int(stat.st_size)
    cursor, offset = _cursor(db, "trojan", inode, size)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            chunk = handle.read()
            new_offset = handle.tell()
    except OSError:
        return
    prefixes = _prefix_map(db)
    accounts = {row.id: row for row in db.execute(select(Account)).scalars().all()}
    existing = {
        (row.account_id, row.ip): row
        for row in db.execute(select(AccountIp)).scalars().all()
    }
    _apply_chunk(db, chunk, prefixes, accounts, existing)
    cursor.inode = inode
    cursor.offset = new_offset


def ingest_remote_ips(db: Session, vps: Vps) -> None:
    if vps.is_local or vps.status != "online":
        return
    client = ssh.connect(vps.ssh_host, int(vps.ssh_port or 22), vps.ssh_user or "root", timeout=12)
    try:
        cursor_name = f"trojan:{vps.id}"
        cursor = db.get(LogCursor, cursor_name)
        offset = int(cursor.offset or 0) if cursor is not None else 0
        path = "/var/log/trojan.log"
        command = f"python3 - {shlex.quote(path)} {offset}"
        stdin, stdout, stderr = client.exec_command(command, timeout=30)
        stdin.write(REMOTE_READ_SCRIPT)
        stdin.channel.shutdown_write()
        raw = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        if code != 0:
            raise RuntimeError(err.strip() or f"exit {code}")
        payload = json.loads(raw or "{}")
    finally:
        client.close()

    inode = int(payload.get("inode") or 0)
    size = int(payload.get("size") or 0)
    new_offset = int(payload.get("offset") or 0)
    chunk = str(payload.get("chunk") or "")
    cursor, _ignored = _cursor(db, f"trojan:{vps.id}", inode, size)
    prefixes = _prefix_map(db)
    accounts = {row.id: row for row in db.execute(select(Account)).scalars().all()}
    existing = {
        (row.account_id, row.ip): row
        for row in db.execute(select(AccountIp)).scalars().all()
    }
    _apply_chunk(db, chunk, prefixes, accounts, existing)
    cursor.inode = inode
    cursor.offset = new_offset


def ingest_trojan_ips(db: Session) -> None:
    ingest_local_ips(db)
    remotes = list(db.execute(select(Vps).where(Vps.is_local == 0, Vps.status == "online")).scalars().all())
    for vps in remotes:
        try:
            ingest_remote_ips(db, vps)
        except Exception:
            logger.exception("读取节点 %s 日志失败", vps.id)
    cutoff = now_local() - timedelta(days=max(1, settings.log_retention_days))
    db.query(AccountIp).filter(AccountIp.last_seen_at < cutoff).delete()


def collect_remote_system(vps: Vps) -> dict:
    script = (repo_root() / "deploy" / "node_stats.py").read_text(encoding="utf-8")
    client = ssh.connect(vps.ssh_host, int(vps.ssh_port or 22), vps.ssh_user or "root", timeout=12)
    try:
        stdin, stdout, stderr = client.exec_command("python3 -", timeout=20)
        stdin.write(script)
        stdin.channel.shutdown_write()
        raw = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        if code != 0:
            raise RuntimeError(err.strip() or "远端采集失败")
        return json.loads(raw)
    finally:
        client.close()


def list_account_ips(db: Session, account_id: int) -> list[AccountIp]:
    ingest_trojan_ips(db)
    db.commit()
    return list(
        db.execute(
            select(AccountIp)
            .where(AccountIp.account_id == account_id)
            .order_by(AccountIp.last_seen_at.desc())
        ).scalars().all()
    )
