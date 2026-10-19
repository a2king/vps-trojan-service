from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Account, AccountGroupTunnel, AccountGroupVps, TunnelProxy, Vps
from app.services.clash import NODE_PLACEHOLDER, default_node_name, get_clash_settings
from app.timeutil import now_local, to_iso

settings = get_settings()

DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-z0-9-]+(?:\.[a-z0-9-]+)+$",
    re.IGNORECASE,
)


def normalize_domain(raw: str) -> str:
    value = (raw or "").strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = value.split("/")[0].split(":")[0].strip(".")
    return value


def is_valid_domain(value: str) -> bool:
    return bool(DOMAIN_RE.match(value or ""))


def local_vps_id() -> str:
    return normalize_domain(settings.domain)


def placeholder_group_names(db: Session) -> list[str]:
    data = get_clash_settings(db)
    names: list[str] = []
    seen: set[str] = set()
    for group in data.get("proxy_groups") or []:
        proxies = [str(item) for item in (group.get("proxies") or [])]
        if NODE_PLACEHOLDER not in proxies:
            continue
        name = str(group.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def list_vps(db: Session) -> list[Vps]:
    return list(db.execute(select(Vps).order_by(Vps.is_local.desc(), Vps.created_at, Vps.id)).scalars().all())


def get_vps(db: Session, vps_id: str) -> Vps | None:
    return db.get(Vps, normalize_domain(vps_id))


def vps_proxy_name(vps: Vps, all_vps: list[Vps] | None = None) -> str:
    name = (vps.name or "").strip()
    if not name:
        return vps.id
    peers = all_vps if all_vps is not None else [vps]
    collisions = [item for item in peers if (item.name or "").strip() == name]
    if len(collisions) > 1:
        return vps.id
    return name


def seed_local_vps(db: Session) -> Vps | None:
    domain = local_vps_id()
    if not domain or not is_valid_domain(domain):
        return None
    row = db.get(Vps, domain)
    if row is None:
        clash = get_clash_settings(db)
        name = str(clash.get("node_name") or "").strip() or default_node_name()
        row = Vps(
            id=domain,
            name=name,
            ssh_host="127.0.0.1",
            ssh_port=22,
            ssh_user="root",
            is_local=1,
            hidden=0,
            status="online",
            last_error="",
            created_at=now_local(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    elif not (row.name or "").strip():
        row.name = default_node_name()
        db.commit()
        db.refresh(row)
    retarget_local_role(db)
    backfill_missing_assignments(db)
    return row


def retarget_local_role(db: Session) -> None:
    """备机上把本机域名标成本地节点，避免沿用主库里的 is_local。"""
    if settings.is_control_primary:
        return
    domain = local_vps_id()
    if not domain:
        return
    changed = False
    for row in list_vps(db):
        if row.id == domain:
            if row.is_local != 1 or (row.ssh_host or "") != "127.0.0.1":
                row.is_local = 1
                row.ssh_host = "127.0.0.1"
                row.status = "online"
                changed = True
        elif row.is_local:
            row.is_local = 0
            changed = True
    if changed:
        db.commit()


def assign_default_nodes(db: Session, account_id: int, *, include_hidden: bool = False) -> None:
    groups = placeholder_group_names(db)
    if not groups:
        return
    query = select(Vps)
    if not include_hidden:
        query = query.where(Vps.hidden == 0)
    nodes = list(db.execute(query).scalars().all())
    existing = {
        (row.group_name, row.vps_id)
        for row in db.execute(select(AccountGroupVps).where(AccountGroupVps.account_id == account_id)).scalars().all()
    }
    for vps in nodes:
        for group in groups:
            key = (group, vps.id)
            if key in existing:
                continue
            db.add(AccountGroupVps(account_id=account_id, group_name=group, vps_id=vps.id))
            existing.add(key)
    db.flush()


def backfill_missing_assignments(db: Session) -> None:
    groups = placeholder_group_names(db)
    if not groups:
        return
    assigned_ids = {
        int(row[0])
        for row in db.execute(select(AccountGroupVps.account_id).distinct()).all()
    }
    accounts = list(db.execute(select(Account)).scalars().all())
    changed = False
    for account in accounts:
        if account.id in assigned_ids:
            continue
        assign_default_nodes(db, account.id)
        changed = True
    if changed:
        db.commit()


def group_nodes_map(db: Session, account_id: int) -> dict[str, list[str]]:
    from app.services.tunnels import assign_id

    rows = list(
        db.execute(
            select(AccountGroupVps)
            .where(AccountGroupVps.account_id == account_id)
            .order_by(AccountGroupVps.group_name, AccountGroupVps.vps_id)
        ).scalars().all()
    )
    result: dict[str, list[str]] = {}
    for row in rows:
        result.setdefault(row.group_name, []).append(row.vps_id)
    tunnels = list(
        db.execute(
            select(AccountGroupTunnel)
            .where(AccountGroupTunnel.account_id == account_id)
            .order_by(AccountGroupTunnel.group_name, AccountGroupTunnel.tunnel_id)
        ).scalars().all()
    )
    for row in tunnels:
        result.setdefault(row.group_name, []).append(assign_id(row.tunnel_id))
    return result


def replace_group_nodes(db: Session, account_id: int, groups: list[tuple[str, list[str]]]) -> dict[str, list[str]]:
    from app.services.tunnels import parse_assign_id

    known = {item.id for item in list_vps(db)}
    known_tunnels = {int(item) for item in db.execute(select(TunnelProxy.id)).scalars().all()}
    db.query(AccountGroupVps).filter(AccountGroupVps.account_id == account_id).delete()
    db.query(AccountGroupTunnel).filter(AccountGroupTunnel.account_id == account_id).delete()
    seen: set[tuple[str, str]] = set()
    seen_tunnels: set[tuple[str, int]] = set()
    for group_name, vps_ids in groups:
        name = group_name.strip()
        if not name:
            continue
        for raw in vps_ids:
            tunnel_id = parse_assign_id(raw)
            if tunnel_id is not None:
                if tunnel_id not in known_tunnels or (name, tunnel_id) in seen_tunnels:
                    continue
                db.add(AccountGroupTunnel(account_id=account_id, group_name=name, tunnel_id=tunnel_id))
                seen_tunnels.add((name, tunnel_id))
                continue
            vps_id = normalize_domain(raw)
            if vps_id not in known or (name, vps_id) in seen:
                continue
            db.add(AccountGroupVps(account_id=account_id, group_name=name, vps_id=vps_id))
            seen.add((name, vps_id))
    db.flush()
    db.commit()
    return group_nodes_map(db, account_id)


def assigned_vps_ids(db: Session, account_id: int) -> list[str]:
    rows = db.execute(
        select(AccountGroupVps.vps_id).where(AccountGroupVps.account_id == account_id).distinct()
    ).all()
    ids = [str(item[0]) for item in rows]
    if ids:
        return ids
    hops = db.execute(
        select(TunnelProxy.vps_id)
        .join(AccountGroupTunnel, AccountGroupTunnel.tunnel_id == TunnelProxy.id)
        .where(AccountGroupTunnel.account_id == account_id)
        .distinct()
    ).all()
    return [str(item[0]) for item in hops]


def account_allowed_on_vps(db: Session, password_sha224: str, vps_id: str) -> bool:
    account = db.execute(select(Account).where(Account.password_sha224 == password_sha224)).scalar_one_or_none()
    if account is None or not account.enabled:
        return False
    node_id = normalize_domain(vps_id)
    row = db.execute(
        select(AccountGroupVps.id).where(
            AccountGroupVps.account_id == account.id,
            AccountGroupVps.vps_id == node_id,
        ).limit(1)
    ).first()
    if row is not None:
        return True
    hop = db.execute(
        select(AccountGroupTunnel.id)
        .join(TunnelProxy, TunnelProxy.id == AccountGroupTunnel.tunnel_id)
        .where(
            AccountGroupTunnel.account_id == account.id,
            TunnelProxy.vps_id == node_id,
        )
        .limit(1)
    ).first()
    if hop is not None:
        return True
    any_row = db.execute(
        select(AccountGroupVps.id).where(AccountGroupVps.account_id == account.id).limit(1)
    ).first()
    any_tunnel = db.execute(
        select(AccountGroupTunnel.id).where(AccountGroupTunnel.account_id == account.id).limit(1)
    ).first()
    return any_row is None and any_tunnel is None and node_id == local_vps_id()


def to_vps_out(row: Vps) -> dict:
    return {
        "id": row.id,
        "name": row.name or row.id,
        "ssh_host": row.ssh_host or "",
        "ssh_port": int(row.ssh_port or 22),
        "ssh_user": row.ssh_user or "root",
        "is_local": bool(row.is_local),
        "hidden": bool(row.hidden),
        "status": row.status or "pending",
        "last_error": row.last_error or "",
        "created_at": to_iso(row.created_at) or "",
    }
