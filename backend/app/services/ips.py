from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Account, AccountIp, LogCursor, TrojanUser, Vps
from app.paths import repo_root
from app.services import ssh
from app.timeutil import TZ, now_local

logger = logging.getLogger("trojan-admin")
settings = get_settings()

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
AUTH_RE = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+\[(?:INFO|WARN|ALL)\]\s+"
    r"(\S+)\s+authenticated by authenticator \(([0-9a-f]{7})\)",
    re.IGNORECASE,
)
MAX_CHUNK = 8 * 1024 * 1024

REMOTE_READ_SCRIPT = r"""
import json, os, subprocess, sys
path = sys.argv[1]
offset = int(sys.argv[2])
full = len(sys.argv) > 3 and sys.argv[3] == "1"
inode = size = 0
chunk = ""
new_offset = offset
try:
    st = os.stat(path)
    inode = int(getattr(st, "st_ino", 0) or 0)
    size = int(st.st_size)
    if full or offset > size:
        offset = 0
    if size - offset > 8 * 1024 * 1024:
        offset = max(0, size - 8 * 1024 * 1024)
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        handle.seek(offset)
        chunk = handle.read()
        new_offset = handle.tell()
except OSError:
    pass
if "authenticated by authenticator" not in chunk:
    try:
        proc = subprocess.run(
            ["journalctl", "-u", "trojan", "--no-pager", "-o", "cat", "--since", "31 days ago"],
            capture_output=True, text=True, timeout=15,
        )
        extra = proc.stdout or ""
        if extra:
            chunk = (chunk + "\n" + extra) if chunk else extra
    except Exception:
        pass
print(json.dumps({"inode": inode, "size": size, "offset": new_offset, "chunk": chunk}, ensure_ascii=False))
"""


def _split_ip(endpoint: str) -> str:
    host = endpoint.rsplit(":", 1)[0].strip()
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    if host.startswith("::ffff:"):
        return host[7:]
    return host


def _parse_log_time(raw: str) -> datetime:
    parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=TZ).replace(tzinfo=None)


def _prefix_map(db: Session) -> dict[str, int]:
    mapping: dict[str, int] = {}
    collided: set[str] = set()
    for account in db.execute(select(Account)).scalars().all():
        prefix = account.password_sha224[:7].lower()
        if prefix in collided:
            continue
        if prefix in mapping:
            collided.add(prefix)
            mapping.pop(prefix, None)
            continue
        mapping[prefix] = account.id
    return mapping


def _ingest_context(db: Session) -> tuple[dict[str, int], dict[int, Account], dict[tuple[int, str], AccountIp]]:
    prefixes = _prefix_map(db)
    accounts = {row.id: row for row in db.execute(select(Account)).scalars().all()}
    existing = {
        (row.account_id, row.ip): row
        for row in db.execute(select(AccountIp)).scalars().all()
    }
    for obj in db.new:
        if isinstance(obj, AccountIp) and obj.account_id and obj.ip:
            existing[(obj.account_id, obj.ip)] = obj
    return prefixes, accounts, existing


def _apply_chunk(
    db: Session,
    chunk: str,
    prefixes: dict[str, int],
    accounts: dict[int, Account],
    existing: dict[tuple[int, str], AccountIp],
) -> int:
    latest_by_account: dict[int, datetime] = {}
    matched_count = 0
    for raw_line in chunk.splitlines():
        line = ANSI_RE.sub("", raw_line.strip())
        matched = AUTH_RE.search(line)
        if not matched:
            continue
        try:
            seen_at = _parse_log_time(matched.group(1))
        except ValueError:
            continue
        ip = _split_ip(matched.group(2))
        account_id = prefixes.get(matched.group(3).lower())
        if not ip or account_id is None:
            continue
        matched_count += 1
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
    return matched_count


def _cursor(db: Session, name: str, inode: int, size: int, force_full: bool) -> tuple[LogCursor, int]:
    cursor = db.get(LogCursor, name)
    if cursor is None:
        cursor = LogCursor(name=name, inode=inode, offset=0)
        db.add(cursor)
    offset = 0 if force_full else int(cursor.offset or 0)
    if cursor.inode != inode or offset > size:
        offset = 0
    if size - offset > MAX_CHUNK:
        offset = max(0, size - MAX_CHUNK)
    return cursor, offset


def _read_journal(unit: str = "trojan") -> str:
    try:
        result = subprocess.run(
            [
                "journalctl",
                "-u",
                unit,
                "--no-pager",
                "-o",
                "cat",
                "--since",
                f"{max(1, settings.log_retention_days)} days ago",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        return result.stdout or ""
    except Exception:
        return ""


def ingest_local_ips(
    db: Session,
    force_full: bool = False,
    prefixes: dict[str, int] | None = None,
    accounts: dict[int, Account] | None = None,
    existing: dict[tuple[int, str], AccountIp] | None = None,
) -> int:
    path = Path(settings.trojan_log_path)
    if prefixes is None or accounts is None or existing is None:
        prefixes, accounts, existing = _ingest_context(db)
    matched = 0
    chunk = ""
    if path.is_file():
        try:
            stat = path.stat()
            inode = int(getattr(stat, "st_ino", 0) or 0)
            size = int(stat.st_size)
            cursor, offset = _cursor(db, "trojan", inode, size, force_full)
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                chunk = handle.read()
                new_offset = handle.tell()
            matched += _apply_chunk(db, chunk, prefixes, accounts, existing)
            cursor.inode = inode
            cursor.offset = new_offset
        except OSError:
            logger.exception("读取本机 Trojan 日志失败：%s", path)
    if matched == 0:
        journal = _read_journal()
        if journal:
            matched += _apply_chunk(db, journal, prefixes, accounts, existing)
    return matched


def ingest_remote_ips(
    db: Session,
    vps: Vps,
    force_full: bool = False,
    prefixes: dict[str, int] | None = None,
    accounts: dict[int, Account] | None = None,
    existing: dict[tuple[int, str], AccountIp] | None = None,
) -> int:
    if vps.is_local or vps.status != "online":
        return 0
    client = ssh.connect(vps.ssh_host, int(vps.ssh_port or 22), vps.ssh_user or "root", timeout=12)
    try:
        cursor_name = f"trojan:{vps.id}"
        cursor = db.get(LogCursor, cursor_name)
        offset = 0 if force_full else (int(cursor.offset or 0) if cursor is not None else 0)
        path = "/var/log/trojan.log"
        command = (
            f"python3 -c {shlex.quote(REMOTE_READ_SCRIPT)} "
            f"{shlex.quote(path)} {int(offset)} {1 if force_full else 0}"
        )
        _stdin, stdout, stderr = client.exec_command(command, timeout=30)
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
    cursor, _ignored = _cursor(db, f"trojan:{vps.id}", inode, size, force_full)
    if prefixes is None or accounts is None or existing is None:
        prefixes, accounts, existing = _ingest_context(db)
    matched = _apply_chunk(db, chunk, prefixes, accounts, existing)
    cursor.inode = inode
    cursor.offset = new_offset
    return matched


def ingest_trojan_ips(db: Session, force_full: bool = False) -> None:
    prefixes, accounts, existing = _ingest_context(db)
    ingest_local_ips(db, force_full=force_full, prefixes=prefixes, accounts=accounts, existing=existing)
    remotes = list(db.execute(select(Vps).where(Vps.is_local == 0, Vps.status == "online")).scalars().all())
    for vps in remotes:
        vps_id = vps.id
        try:
            ingest_remote_ips(
                db,
                vps,
                force_full=force_full,
                prefixes=prefixes,
                accounts=accounts,
                existing=existing,
            )
        except SQLAlchemyError:
            db.rollback()
            logger.exception("读取节点 %s 日志失败", vps_id)
            prefixes, accounts, existing = _ingest_context(db)
        except Exception:
            logger.exception("读取节点 %s 日志失败", vps_id)
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


def _account_has_traffic(db: Session, account: Account) -> bool:
    trojan = db.execute(select(TrojanUser).where(TrojanUser.password == account.password_sha224)).scalar_one_or_none()
    if trojan is None:
        return False
    return int(trojan.download or 0) > 0 or int(trojan.upload or 0) > 0


def _safe_ingest(db: Session, force_full: bool = False) -> None:
    try:
        ingest_trojan_ips(db, force_full=force_full)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("采集 Trojan IP 失败")


def list_account_ips(db: Session, account_id: int) -> list[AccountIp]:
    _safe_ingest(db)
    rows = list(
        db.execute(
            select(AccountIp)
            .where(AccountIp.account_id == account_id)
            .order_by(AccountIp.last_seen_at.desc())
        ).scalars().all()
    )
    if rows:
        return rows
    account = db.get(Account, account_id)
    if account is None or not _account_has_traffic(db, account):
        return rows
    _safe_ingest(db, force_full=True)
    return list(
        db.execute(
            select(AccountIp)
            .where(AccountIp.account_id == account_id)
            .order_by(AccountIp.last_seen_at.desc())
        ).scalars().all()
    )
