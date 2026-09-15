import asyncio
import logging
import re

from mysql_mimic import MysqlServer, Session
from sqlalchemy import text

from app.config import get_settings
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


def _account_allowed(password: str, vps_id: str) -> bool:
    if not vps_id:
        return True
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id, enabled FROM accounts WHERE password_sha224 = :password"),
            {"password": password},
        ).first()
        if row is None or int(row[1] or 0) != 1:
            return False
        assigned = conn.execute(
            text(
                "SELECT 1 FROM account_group_vps WHERE account_id = :account_id AND vps_id = :vps_id LIMIT 1"
            ),
            {"account_id": int(row[0]), "vps_id": vps_id},
        ).first()
        if assigned is not None:
            return True
        hop = conn.execute(
            text(
                "SELECT 1 FROM account_group_tunnels t "
                "JOIN tunnel_proxies p ON p.id = t.tunnel_id "
                "WHERE t.account_id = :account_id AND p.vps_id = :vps_id LIMIT 1"
            ),
            {"account_id": int(row[0]), "vps_id": vps_id},
        ).first()
        if hop is not None:
            return True
        any_row = conn.execute(
            text("SELECT 1 FROM account_group_vps WHERE account_id = :account_id LIMIT 1"),
            {"account_id": int(row[0])},
        ).first()
        any_tunnel = conn.execute(
            text("SELECT 1 FROM account_group_tunnels WHERE account_id = :account_id LIMIT 1"),
            {"account_id": int(row[0])},
        ).first()
        local_id = (settings.domain or "").strip().lower()
        return any_row is None and any_tunnel is None and vps_id == local_id


def handle_trojan_sql(sql: str, mysql_user: str = "") -> tuple[list, list[str]]:
    statement = sql.strip().rstrip(";")
    vps_id = _vps_id_for_mysql_user(mysql_user)
    matched = SELECT_RE.search(statement)
    if matched:
        password = matched.group(1)
        if not _account_allowed(password, vps_id):
            return [], ["quota", "download + upload"]
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT quota, download + upload FROM users WHERE password = :password"),
                {"password": password},
            ).first()
        if row is None:
            return [], ["quota", "download + upload"]
        return [(int(row[0]), int(row[1]))], ["quota", "download + upload"]

    matched = UPDATE_RE.search(statement)
    if matched:
        download = int(matched.group(1))
        upload = int(matched.group(2))
        password = matched.group(3)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE users SET download = download + :download, upload = upload + :upload "
                    "WHERE password = :password"
                ),
                {"download": download, "upload": upload, "password": password},
            )
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
