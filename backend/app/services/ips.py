import base64
import ipaddress
import json
import logging
import re
import shlex
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from app.config import get_settings
from app.models import Account, AccountAuthPrefix, AccountIp, AccountNodeIp, LogCursor, NodeObservation, Vps
from app.paths import repo_root
from app.services import ssh
from app.services.node_read import read_legacy_log, read_remote
from app.services.observation import update_status
from app.services.runtime_logs import read_chunk
from app.timeutil import now_local

logger = logging.getLogger("trojan-admin")
settings = get_settings()

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
AUTH_RE = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+\[(?:INFO|WARN|ALL)\]\s+"
    r"(\S+)\s+authenticated by authenticator \(([0-9a-f]{7})\)",
    re.IGNORECASE,
)


def remember_auth_prefix(db, account: Account) -> None:
    db.execute(insert(AccountAuthPrefix).values(
        prefix=account.password_sha224[:7].lower(), account_key=account.telemetry_key,
    ).on_conflict_do_nothing())


def _prefix_map(db) -> dict[str, int]:
    prefix_accounts: dict[str, set[str]] = {}
    for prefix, account_key in db.execute(select(AccountAuthPrefix.prefix, AccountAuthPrefix.account_key)):
        prefix_accounts.setdefault(prefix, set()).add(account_key)
    accounts = {key: account_id for account_id, key in db.execute(select(Account.id, Account.telemetry_key))}
    return {
        prefix: accounts[key]
        for prefix, candidates in prefix_accounts.items()
        if len(candidates) == 1
        for key in candidates
        if key in accounts
    }


def _events(content: str, prefixes: dict, cutoff: datetime) -> dict:
    result = {}
    for raw in content.splitlines():
        match = AUTH_RE.search(ANSI_RE.sub("", raw))
        if not match:
            continue
        timestamp, endpoint, prefix = match.groups()
        account_id = prefixes.get(prefix.lower())
        if account_id is None:
            continue
        try:
            seen_at = datetime.fromisoformat(timestamp)
            address = ipaddress.ip_address(endpoint.rsplit(":", 1)[0].strip("[]"))
        except ValueError:
            continue
        if seen_at < cutoff:
            continue
        ip = str(getattr(address, "ipv4_mapped", None) or address)
        key = (account_id, ip)
        first, last = result.get(key, (seen_at, seen_at))
        result[key] = (min(first, seen_at), max(last, seen_at))
    return result


def ingest_trojan_ips(db) -> None:
    prefixes = _prefix_map(db)
    cutoff = now_local() - timedelta(days=max(1, settings.log_retention_days))
    nodes = db.query(Vps).filter(Vps.status == "online").all()
    for node in nodes:
        try:
            cursor_name = f"auth:{node.id}"
            cursor = db.get(LogCursor, cursor_name)
            arguments = {
                "inode": cursor.inode if cursor else 0,
                "offset": cursor.offset if cursor else 0,
                "anchor": cursor.anchor if cursor else "",
            }
            if node.is_local:
                chunk = read_chunk(Path(settings.trojan_log_path), **arguments)
            elif node.replica:
                chunk = read_remote(node, "auth-log", **arguments)
            else:
                chunk = read_legacy_log(node, cursor=arguments)
            content = base64.b64decode(chunk["content"]).decode("utf-8", errors="replace")
            for (account_id, ip), (first, last) in _events(content, prefixes, cutoff).items():
                row = db.get(AccountNodeIp, (account_id, node.id, ip))
                if row is None:
                    db.add(AccountNodeIp(account_id=account_id, node_id=node.id, ip=ip, first_seen_at=first, last_seen_at=last))
                else:
                    row.first_seen_at = min(row.first_seen_at, first)
                    row.last_seen_at = max(row.last_seen_at, last)
            db.merge(LogCursor(name=cursor_name, inode=chunk["inode"], offset=chunk["offset"], anchor=chunk["anchor"]))
            observation = db.get(NodeObservation, node.id)
            previous_error = observation.ips_error if observation else None
            gap = "轮转日志之间有缺口，部分认证记录可能缺失"
            update_status(
                db, node.id, ips_at=now_local(),
                ips_error=gap if chunk.get("gap") or previous_error == gap else None,
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            update_status(db, node.id, ips_error=str(exc)[:400])
            db.commit()
            logger.warning("节点 %s 的认证日志采集失败", node.id)
    db.query(AccountNodeIp).filter(AccountNodeIp.last_seen_at < cutoff).delete()
    db.query(AccountIp).filter(AccountIp.last_seen_at < cutoff).delete()


def collect_remote_system(vps: Vps) -> dict:
    client = ssh.connect_node(vps, timeout=12)
    try:
        script = (repo_root() / "deploy" / "node_stats.py").read_text(encoding="utf-8")
        return json.loads(ssh.run(client, f"python3 -c {shlex.quote(script)}", timeout=20))
    finally:
        client.close()


def list_account_ips(db, account_id: int) -> list[dict]:
    observed = db.scalars(select(AccountNodeIp).where(AccountNodeIp.account_id == account_id)).all()
    legacy = db.scalars(select(AccountIp).where(AccountIp.account_id == account_id)).all()
    latest = {}
    for row in observed:
        latest[row.ip] = max(latest.get(row.ip, row.last_seen_at), row.last_seen_at)
    results = [
        dict(ip=row.ip, node_id=row.node_id, first_seen_at=row.first_seen_at, last_seen_at=row.last_seen_at)
        for row in observed
    ]
    results.extend(
        dict(ip=row.ip, node_id=None, first_seen_at=None, last_seen_at=row.last_seen_at)
        for row in legacy if row.ip not in latest or row.last_seen_at > latest[row.ip]
    )
    return sorted(results, key=lambda row: row["last_seen_at"], reverse=True)
