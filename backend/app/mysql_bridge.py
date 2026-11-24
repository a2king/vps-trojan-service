import asyncio
import logging
import re

from mysql_mimic import MysqlServer, Session
from sqlalchemy import text

from app.config import get_settings
from app.services.node_traffic import record_traffic, usage_for_account
from app.database import engine

logger = logging.getLogger("trojan-admin")
settings = get_settings()

SELECT_RE = re.compile(
    r"SELECT\s+quota\s*,\s*download\s*\+\s*upload\s+FROM\s+users\s+WHERE\s+password\s*=\s*'([0-9a-f]{56})'",
    re.IGNORECASE,
)
UPDATE_RE = re.compile(
    r"UPDATE\s+users\s+SET\s+download\s*=\s*download\s*\+\s*(\d+)\s*,\s*upload\s*=\s*upload\s*\+\s*(\d+)\s+WHERE\s+password\s*=\s*'([0-9a-f]{56})'",
    re.IGNORECASE,
)


def _mysql_username(session: object) -> str:
    name = str(getattr(session, "username", "") or "")
    if name:
        return name
    identity = getattr(session, "identity", None)
    if identity is not None:
        return str(getattr(identity, "username", "") or "")
    connection = getattr(session, "connection", None) or getattr(session, "_connection", None)
    if connection is not None:
        return str(getattr(connection, "username", "") or "")
    return ""


def _vps_id_for_mysql_user(username: str) -> str:
    name = (username or "").strip().lower()
    if not name or name == "trojan":
        return (settings.domain or "").strip().lower()
    return name


AUTH_OK = "ok"
AUTH_MISSING_HASH = "missing_hash"
AUTH_DISABLED = "disabled"
AUTH_ACL_DENY = "acl_deny"


def _account_auth_reason(password: str, vps_id: str) -> str:
    if not vps_id:
        return AUTH_OK
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id, enabled FROM accounts WHERE password_sha224 = :password"),
            {"password": password},
        ).first()
        if row is None:
            return AUTH_MISSING_HASH
        if int(row[1] or 0) != 1:
            return AUTH_DISABLED
        assigned = conn.execute(
            text(
                "SELECT 1 FROM account_group_vps WHERE account_id = :account_id AND vps_id = :vps_id LIMIT 1"
            ),
            {"account_id": int(row[0]), "vps_id": vps_id},
        ).first()
        if assigned is not None:
            return AUTH_OK
        hop = conn.execute(
            text(
                "SELECT 1 FROM account_group_tunnels t "
                "JOIN tunnel_proxies p ON p.id = t.tunnel_id "
                "WHERE t.account_id = :account_id AND p.vps_id = :vps_id LIMIT 1"
            ),
            {"account_id": int(row[0]), "vps_id": vps_id},
        ).first()
        if hop is not None:
            return AUTH_OK
        any_row = conn.execute(
            text("SELECT 1 FROM account_group_vps WHERE account_id = :account_id LIMIT 1"),
            {"account_id": int(row[0])},
        ).first()
        any_tunnel = conn.execute(
            text("SELECT 1 FROM account_group_tunnels WHERE account_id = :account_id LIMIT 1"),
            {"account_id": int(row[0])},
        ).first()
        local_id = (settings.domain or "").strip().lower()
        if any_row is None and any_tunnel is None and vps_id == local_id:
            return AUTH_OK
        return AUTH_ACL_DENY


def _log_auth_deny(reason: str, password: str, vps_id: str) -> None:
    digest = (password or "")[:8]
    if reason == AUTH_ACL_DENY:
        logger.info("Trojan 拒绝：节点 ACL 未授权 vps=%s hash=%s…", vps_id, digest)
    elif reason == AUTH_MISSING_HASH:
        logger.info("Trojan 拒绝：密码哈希不存在 vps=%s hash=%s…", vps_id, digest)
    elif reason == AUTH_DISABLED:
        logger.info("Trojan 拒绝：账号已禁用 vps=%s hash=%s…", vps_id, digest)
    else:
        logger.info("Trojan 拒绝：%s vps=%s hash=%s…", reason, vps_id, digest)


def handle_trojan_sql(sql: str, mysql_user: str = "") -> tuple[list, list[str]]:
    statement = sql.strip().rstrip(";")
    vps_id = _vps_id_for_mysql_user(mysql_user)
    matched = SELECT_RE.search(statement)
    if matched:
        password = matched.group(1)
        reason = _account_auth_reason(password, vps_id)
        if reason != AUTH_OK:
            _log_auth_deny(reason, password, vps_id)
            return [], ["quota", "download + upload"]
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT users.quota, users.download + users.upload, accounts.telemetry_key "
                    "FROM users LEFT JOIN accounts ON accounts.password_sha224 = users.password "
                    "WHERE users.password = :password"
                ),
                {"password": password},
            ).first()
        if row is None:
            _log_auth_deny(AUTH_MISSING_HASH, password, vps_id)
            return [], ["quota", "download + upload"]
        quota, usage, account_key = row
        if quota > 0 and account_key:
            usage += usage_for_account(account_key)
        return [(int(quota), int(usage))], ["quota", "download + upload"]

    matched = UPDATE_RE.search(statement)
    if matched:
        download = int(matched.group(1))
        upload = int(matched.group(2))
        password = matched.group(3)
        with engine.begin() as conn:
            account_key = conn.execute(
                text("SELECT telemetry_key FROM accounts WHERE password_sha224 = :password"),
                {"password": password},
            ).scalar_one_or_none()
        if account_key:
            record_traffic(vps_id or settings.domain, account_key, download, upload)
        return [], []

    logger.debug("忽略 Trojan MySQL 语句: %s", statement[:200])
    return [], []


class TrojanMysqlSession(Session):
    async def query(self, expression, sql: str, attrs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(handle_trojan_sql, sql, _mysql_username(self))

    async def schema(self):  # type: ignore[no-untyped-def]
        return {
            "users": {
                "id": "INT",
                "username": "TEXT",
                "password": "TEXT",
                "quota": "BIGINT",
                "download": "BIGINT",
                "upload": "BIGINT",
            }
        }


def create_mysql_server() -> MysqlServer:
    return MysqlServer(session_factory=TrojanMysqlSession)
