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


def handle_trojan_sql(sql: str) -> tuple[list, list[str]]:
    statement = sql.strip().rstrip(";")
    matched = SELECT_RE.search(statement)
    if matched:
        password = matched.group(1)
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
        return await asyncio.to_thread(handle_trojan_sql, sql)

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
