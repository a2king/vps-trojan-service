from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AccountGroupTunnel, TunnelProxy, Vps
from app.services.nodes import get_vps, list_vps, normalize_domain, vps_proxy_name
from app.timeutil import now_local, to_iso

TUNNEL_PREFIX = "tunnel:"
PROTOCOLS = {"http", "https", "socks5"}


def assign_id(tunnel_id: int) -> str:
    return f"{TUNNEL_PREFIX}{int(tunnel_id)}"


def parse_assign_id(raw: str) -> int | None:
    value = (raw or "").strip()
    if not value.startswith(TUNNEL_PREFIX):
        return None
    try:
        return int(value[len(TUNNEL_PREFIX) :])
    except ValueError:
        return None


def normalize_protocol(raw: str) -> str:
    value = (raw or "").strip().lower().replace("_", "")
    if value in {"sock5", "socks", "socks5"}:
        return "socks5"
    if value in {"http", "https"}:
        return value
    raise ValueError("协议仅支持 http、https、socks5")


def list_tunnels(db: Session) -> list[TunnelProxy]:
    return list(db.execute(select(TunnelProxy).order_by(TunnelProxy.id.desc())).scalars().all())


def get_tunnel(db: Session, tunnel_id: int) -> TunnelProxy | None:
    return db.get(TunnelProxy, int(tunnel_id))


def _clash_names(db: Session, exclude_id: int | None = None) -> set[str]:
    nodes = list_vps(db)
    names = {vps_proxy_name(item, nodes) for item in nodes}
    names.update(item.id for item in nodes)
    for row in list_tunnels(db):
        if exclude_id is not None and row.id == exclude_id:
            continue
        names.add(row.name)
    return names


def _require_vps(db: Session, vps_id: str) -> Vps:
    row = get_vps(db, vps_id)
    if row is None:
        raise KeyError("跳转节点不存在")
    return row


def create_tunnel(
    db: Session,
    *,
    name: str,
    protocol: str,
    server: str,
    port: int,
    username: str,
    password: str,
    vps_id: str,
) -> TunnelProxy:
    display = name.strip()
    if not display:
        raise ValueError("名称不能为空")
    proto = normalize_protocol(protocol)
    host = (server or "").strip()
    if not host:
        raise ValueError("服务器不能为空")
    user = username.strip()
    secret = password.strip()
    if not user or not secret:
        raise ValueError("用户名和密码不能为空")
    vps = _require_vps(db, vps_id)
    if display in _clash_names(db):
        raise ValueError("名称与现有节点或隧道重复")
    row = TunnelProxy(
        name=display,
        protocol=proto,
        server=host,
        port=int(port),
        username=user,
        password=secret,
        vps_id=vps.id,
        created_at=now_local(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_tunnel(db: Session, row: TunnelProxy, payload: dict) -> TunnelProxy:
    if "name" in payload and payload["name"] is not None:
        display = str(payload["name"]).strip()
        if not display:
            raise ValueError("名称不能为空")
        if display != row.name and display in _clash_names(db, exclude_id=row.id):
            raise ValueError("名称与现有节点或隧道重复")
        row.name = display
    if "protocol" in payload and payload["protocol"] is not None:
        row.protocol = normalize_protocol(str(payload["protocol"]))
    if "server" in payload and payload["server"] is not None:
        host = str(payload["server"]).strip()
        if not host:
            raise ValueError("服务器不能为空")
        row.server = host
    if "port" in payload and payload["port"] is not None:
        row.port = int(payload["port"])
    if "username" in payload and payload["username"] is not None:
        user = str(payload["username"]).strip()
        if not user:
            raise ValueError("用户名不能为空")
        row.username = user
    if "password" in payload and payload["password"] is not None:
        secret = str(payload["password"]).strip()
        if not secret:
            raise ValueError("密码不能为空")
        row.password = secret
    if "vps_id" in payload and payload["vps_id"] is not None:
        vps = _require_vps(db, str(payload["vps_id"]))
        row.vps_id = vps.id
    db.commit()
    db.refresh(row)
    return row


def delete_tunnel(db: Session, row: TunnelProxy) -> None:
    db.query(AccountGroupTunnel).filter(AccountGroupTunnel.tunnel_id == row.id).delete()
    db.delete(row)
    db.commit()


def tunnels_using_vps(db: Session, vps_id: str) -> list[TunnelProxy]:
    node_id = normalize_domain(vps_id)
    return list(db.execute(select(TunnelProxy).where(TunnelProxy.vps_id == node_id)).scalars().all())


def to_tunnel_out(row: TunnelProxy, vps: Vps | None = None, all_vps: list[Vps] | None = None) -> dict:
    hop = vps
    display = hop.id if hop is not None else row.vps_id
    if hop is not None:
        display = vps_proxy_name(hop, all_vps) if all_vps is not None else (hop.name or hop.id)
    return {
        "id": row.id,
        "assign_id": assign_id(row.id),
        "name": row.name,
        "protocol": row.protocol,
        "server": row.server,
        "port": int(row.port),
        "username": row.username,
        "vps_id": row.vps_id,
        "vps_name": display,
        "created_at": to_iso(row.created_at) or "",
    }


def clash_proxy_items(row: TunnelProxy, dialer_name: str) -> list[tuple[str, object]]:
    clash_type = "socks5" if row.protocol == "socks5" else "http"
    items: list[tuple[str, object]] = [
        ("name", row.name),
        ("type", clash_type),
        ("server", row.server),
        ("port", int(row.port)),
        ("username", row.username),
        ("password", row.password),
        ("udp", False),
        ("dialer-proxy", dialer_name),
    ]
    if row.protocol == "https":
        items.extend([("tls", True), ("skip-cert-verify", True)])
    return items
