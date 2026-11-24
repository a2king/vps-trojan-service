from hmac import compare_digest
from pathlib import Path
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_admin
from app.models import AdminAuditEvent, NodeObservation, Vps
from app.services.node_read import read_legacy_log, read_remote, read_token
from app.services.node_traffic import export_traffic
from app.services.observation import acl_version
from app.services.runtime_logs import read_chunk, tail_log
from app.timeutil import to_iso

router = APIRouter(prefix="/api")
settings = get_settings()


def require_node_token(x_node_read_token: str = Header(default="")) -> None:
    token = read_token()
    if not token or not compare_digest(token.encode(), x_node_read_token.encode()):
        raise HTTPException(status_code=401, detail="观测凭据无效")


def runtime_tail(source: str, limit: int) -> dict:
    paths = {"trojan": settings.trojan_log_path, "admin": "/var/log/trojan-admin.log"}
    path = paths.get(source)
    if not path:
        raise HTTPException(status_code=400, detail="未知日志类型")
    try:
        lines = tail_log(Path(path), limit)
    except FileNotFoundError:
        lines = []
    lines = [re.sub(r"(/config/)[^\s?]+", r"\1[已隐藏]", line) for line in lines]
    return {"lines": lines}


@router.get("/node-read/traffic", dependencies=[Depends(require_node_token)])
def node_traffic(db: Session = Depends(get_db)):
    return {"rows": export_traffic(settings.domain.lower()), "version": acl_version(lambda sql: db.execute(text(sql)))}


@router.get("/node-read/auth-log", dependencies=[Depends(require_node_token)])
def node_auth_log(inode: int = Query(ge=0), offset: int = Query(ge=0), anchor: str = Query(max_length=16)):
    try:
        return read_chunk(Path(settings.trojan_log_path), inode, offset, anchor)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="认证日志不存在") from exc


@router.get("/node-read/runtime-log", dependencies=[Depends(require_node_token)])
def node_runtime_log(source: str, limit: int = Query(default=200, ge=1, le=500)):
    return runtime_tail(source, limit)


@router.get("/observations", dependencies=[Depends(get_current_admin)])
def observations(db: Session = Depends(get_db)):
    version = acl_version(lambda sql: db.execute(text(sql)))
    states = {row.node_id: row for row in db.scalars(select(NodeObservation))}
    rows = []
    for node in db.scalars(select(Vps)):
        state = states.get(node.id)
        applied = version if node.is_local or not node.replica and node.status == "online" else state.applied_version if state else None
        rows.append({
            "node_id": node.id,
            "traffic_at": to_iso(state.traffic_at) if state else None,
            "traffic_error": state.traffic_error if state else None,
            "ips_at": to_iso(state.ips_at) if state else None,
            "ips_error": state.ips_error if state else None,
            "applied_at": to_iso(state.applied_at) if state else None,
            "applied_version": applied[:12] if applied else None,
            "sync_error": state.sync_error if state else None,
            "current": applied == version,
        })
    return {"version": version[:12], "nodes": rows}


@router.get("/observations/runtime-log/{node_id}", dependencies=[Depends(get_current_admin)])
def node_runtime(node_id: str, source: str, limit: int = Query(default=500, ge=1, le=500), db: Session = Depends(get_db)):
    node = db.get(Vps, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    if node.is_local:
        return runtime_tail(source, limit)
    if not node.replica and source != "trojan":
        raise HTTPException(status_code=404, detail="旧版代理节点只提供 Trojan 日志")
    try:
        if not node.replica:
            return read_legacy_log(node, limit=limit)
        return read_remote(node, "runtime-log", source=source, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="节点运行日志读取失败") from exc


@router.get("/observations/audit", dependencies=[Depends(get_current_admin)])
def audit(db: Session = Depends(get_db)):
    return [
        {"at": to_iso(row.occurred_at), "actor": row.actor, "event": row.event}
        for row in db.scalars(select(AdminAuditEvent).order_by(AdminAuditEvent.id.desc()).limit(200))
    ]
