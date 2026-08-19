from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Account, AccountIp, LogCursor
from app.timeutil import TZ, now_local

settings = get_settings()

# [2026-08-19 12:37:07] [INFO] 1.2.3.4:54321 authenticated by authenticator (a1b2c3d)
AUTH_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[INFO\] (.+) authenticated by authenticator \(([0-9a-f]{7})\)"
)


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


def ingest_trojan_ips(db: Session) -> None:
    path = Path(settings.trojan_log_path)
    if not path.is_file():
        return

    stat = path.stat()
    inode = int(getattr(stat, "st_ino", 0) or 0)
    size = int(stat.st_size)
    cursor = db.get(LogCursor, "trojan")
    if cursor is None:
        cursor = LogCursor(name="trojan", inode=inode, offset=0)
        db.add(cursor)
        db.flush()

    offset = int(cursor.offset or 0)
    if cursor.inode != inode or offset > size:
        offset = 0

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
    latest_by_account: dict[int, datetime] = {}

    for line in chunk.splitlines():
        matched = AUTH_RE.search(line.strip())
        if not matched:
            continue
        seen_at = _parse_log_time(matched.group(1))
        ip = _split_ip(matched.group(2))
        account_id = prefixes.get(matched.group(3).lower())
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

    cutoff = now_local() - timedelta(days=max(1, settings.log_retention_days))
    db.query(AccountIp).filter(AccountIp.last_seen_at < cutoff).delete()

    cursor.inode = inode
    cursor.offset = new_offset


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
