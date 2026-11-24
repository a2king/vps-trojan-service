import hashlib
import json
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert

from app.database import SessionLocal
from app.models import NodeObservation, NodeTrafficDaily, NodeTrafficMonthly, Vps
from app.services.node_read import read_remote
from app.services.node_traffic import export_traffic
from app.timeutil import now_local

POLICY_QUERIES = (
    "SELECT id, password_sha224, enabled FROM accounts ORDER BY id",
    "SELECT account_id, group_name, vps_id FROM account_group_vps ORDER BY account_id, group_name, vps_id",
    "SELECT account_id, group_name, tunnel_id FROM account_group_tunnels ORDER BY account_id, group_name, tunnel_id",
    "SELECT id, vps_id FROM tunnel_proxies ORDER BY id",
)


def acl_version(query) -> str:
    values = [[tuple(row) for row in query(sql)] for sql in POLICY_QUERIES]
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def update_status(db, node_id: str, **values) -> None:
    statement = insert(NodeObservation).values(node_id=node_id, **values)
    db.execute(statement.on_conflict_do_update(
        index_elements=[NodeObservation.node_id], set_=values,
    ))


def import_traffic(db, rows, expected_node: str | None = None) -> None:
    months = set()
    for node_id, account_key, day, download, upload, last_used in rows:
        if expected_node is not None and node_id != expected_node:
            raise ValueError("上报节点标识不匹配")
        data = {
            "node_id": node_id,
            "account_key": account_key,
            "stat_date": date.fromisoformat(day),
            "download_bytes": download,
            "upload_bytes": upload,
            "last_used_at": datetime.fromisoformat(last_used),
        }
        statement = insert(NodeTrafficDaily).values(**data)
        result = db.execute(statement.on_conflict_do_update(
            index_elements=["node_id", "account_key", "stat_date"],
            set_={
                "download_bytes": func.max(NodeTrafficDaily.download_bytes, statement.excluded.download_bytes),
                "upload_bytes": func.max(NodeTrafficDaily.upload_bytes, statement.excluded.upload_bytes),
                "last_used_at": func.max(NodeTrafficDaily.last_used_at, statement.excluded.last_used_at),
            },
            where=(NodeTrafficDaily.download_bytes < statement.excluded.download_bytes)
            | (NodeTrafficDaily.upload_bytes < statement.excluded.upload_bytes)
            | (NodeTrafficDaily.last_used_at < statement.excluded.last_used_at),
        ))
        month = data["stat_date"].replace(day=1)
        if result.rowcount or db.get(NodeTrafficMonthly, (node_id, account_key, month)) is None:
            months.add((node_id, account_key, month))
    for node_id, account_key, month in months:
        next_month = (month + timedelta(days=32)).replace(day=1)
        download, upload = db.execute(select(
            func.sum(NodeTrafficDaily.download_bytes), func.sum(NodeTrafficDaily.upload_bytes),
        ).where(
            NodeTrafficDaily.node_id == node_id, NodeTrafficDaily.account_key == account_key,
            NodeTrafficDaily.stat_date >= month, NodeTrafficDaily.stat_date < next_month,
        )).one()
        statement = insert(NodeTrafficMonthly).values(
            node_id=node_id, account_key=account_key, month=month,
            download_bytes=download, upload_bytes=upload,
        )
        db.execute(statement.on_conflict_do_update(
            index_elements=["node_id", "account_key", "month"],
            set_={"download_bytes": download, "upload_bytes": upload},
        ))


def collect_node_traffic() -> None:
    with SessionLocal() as db:
        nodes = db.query(Vps).filter(Vps.status == "online").all()
        for node in nodes:
            try:
                policy = None
                if node.is_local:
                    rows = export_traffic()
                elif node.replica:
                    payload = read_remote(node, "traffic")
                    rows = payload["rows"]
                    policy = payload.get("version")
                else:
                    update_status(db, node.id, traffic_error="旧节点通过主控计数，暂无独立采集时间")
                    db.commit()
                    continue
                import_traffic(db, rows, expected_node=None if node.is_local else node.id)
                update_status(db, node.id, traffic_at=now_local(), traffic_error=None)
                state = db.get(NodeObservation, node.id)
                if policy and state is not None and policy != state.applied_version:
                    update_status(db, node.id, applied_version=policy, applied_at=now_local())
                db.commit()
            except Exception as exc:
                db.rollback()
                update_status(db, node.id, traffic_error=str(exc)[:400])
                db.commit()
