"""Node-owned counters survive replacement of the control database."""

import sqlite3
from contextlib import closing

from app.config import get_settings
from app.timeutil import now_local

settings = get_settings()


def _connect() -> sqlite3.Connection:
    path = settings.sqlite_path.with_name("node-traffic.db")
    db = sqlite3.connect(path, timeout=10)
    path.chmod(0o600)
    return db


def init_node_traffic() -> None:
    with closing(_connect()) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            """CREATE TABLE IF NOT EXISTS counters (
                node_id TEXT NOT NULL, account_key TEXT NOT NULL, day TEXT NOT NULL,
                download INTEGER NOT NULL, upload INTEGER NOT NULL, last_used TEXT NOT NULL,
                PRIMARY KEY (node_id, account_key, day)
            )"""
        )
        db.commit()


def record_traffic(node_id: str, account_key: str, download: int, upload: int) -> None:
    if not (download or upload):
        return
    now = now_local()
    with closing(_connect()) as db, db:
        db.execute(
            """INSERT INTO counters VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (node_id, account_key, day) DO UPDATE SET
                download = download + excluded.download, upload = upload + excluded.upload,
                last_used = MAX(last_used, excluded.last_used)""",
            (node_id, account_key, now.date().isoformat(), download, upload, now.isoformat()),
        )


def export_traffic(node_id: str | None = None) -> list[tuple]:
    with closing(_connect()) as db:
        if node_id is not None:
            return db.execute(
                "SELECT node_id, account_key, day, download, upload, last_used FROM counters WHERE node_id = ?",
                (node_id,),
            ).fetchall()
        return db.execute(
            "SELECT node_id, account_key, day, download, upload, last_used FROM counters"
        ).fetchall()


def usage_for_account(account_key: str) -> int:
    with closing(_connect()) as db:
        row = db.execute(
            "SELECT COALESCE(SUM(download + upload), 0) FROM counters WHERE account_key = ?",
            (account_key,),
        ).fetchone()
        return int(row[0])
