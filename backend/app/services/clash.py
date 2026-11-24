from __future__ import annotations

import json
from copy import deepcopy
from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.defaults import DEFAULT_CLASH_SETTINGS, DEFAULT_RULES
from app.models import Account, ClashRule, ClashSetting

settings = get_settings()

NODE_PLACEHOLDER = "__NODE__"
BUILTIN_POLICIES = {"DIRECT", "REJECT"}
RULE_TYPES = {
    "DOMAIN",
    "DOMAIN-SUFFIX",
    "DOMAIN-KEYWORD",
    "GEOSITE",
    "IP-CIDR",
    "IP-CIDR6",
    "GEOIP",
    "MATCH",
    "SRC-IP-CIDR",
    "PROCESS-NAME",
    "PROCESS-PATH",
    "DST-PORT",
    "SRC-PORT",
    "RULE-SET",
    "AND",
    "OR",
    "NOT",
    "NETWORK",
    "UID",
    "IN-TYPE",
}


def normalize_rule(raw: object) -> str:
    if not isinstance(raw, str):
        raw = str(raw)
    line = raw.strip()
    if line.startswith("- "):
        line = line[2:].strip()
    if (line.startswith('"') and line.endswith('"')) or (line.startswith("'") and line.endswith("'")):
        line = line[1:-1].strip()
    parts = [part.strip() for part in line.split(",") if part.strip()]
    return ",".join(parts)


def is_valid_rule(line: str) -> bool:
    if not line or line.startswith("#") or "," not in line:
        return False
    kind = line.split(",", 1)[0].strip().upper()
    return kind in RULE_TYPES


def rule_policy(line: str) -> str:
    parts = [part.strip() for part in line.split(",") if part.strip()]
    if len(parts) < 2:
        return ""
    if parts[-1].lower() == "no-resolve" and len(parts) >= 3:
        return parts[-2]
    return parts[-1]


def clash_subscribe_url(password: str) -> str:
    return f"https://{settings.domain}/config/{quote(password, safe='')}"


def default_node_name() -> str:
    host = (settings.domain or "trojan").split(".")[0]
    return host or "trojan"


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        inner = ", ".join(_yaml_scalar(item) for item in value)
        return f"[{inner}]"
    return json.dumps(str(value), ensure_ascii=False)


def _flow_map(items: list[tuple[str, Any]]) -> str:
    body = ", ".join(f"{key}: {_yaml_scalar(value)}" for key, value in items)
    return "{ " + body + " }"


def get_clash_settings(db: Session) -> dict[str, Any]:
    row = db.get(ClashSetting, 1)
    data = deepcopy(DEFAULT_CLASH_SETTINGS)
    if row is not None:
        try:
            stored = json.loads(row.data_json)
        except json.JSONDecodeError:
            stored = {}
        if isinstance(stored, dict):
            data.update(stored)
    if not str(data.get("node_name") or "").strip():
        data["node_name"] = default_node_name()
    return data


def save_clash_settings(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    current = get_clash_settings(db)
    merged = deepcopy(current)
    for key, value in payload.items():
        if key in DEFAULT_CLASH_SETTINGS or key in merged:
            merged[key] = value
    row = db.get(ClashSetting, 1)
    blob = json.dumps(merged, ensure_ascii=False)
    if row is None:
        db.add(ClashSetting(id=1, data_json=blob))
    else:
        row.data_json = blob
    db.commit()
    return get_clash_settings(db)


def list_rules(db: Session) -> list[ClashRule]:
    return list(db.execute(select(ClashRule).order_by(ClashRule.sort_order, ClashRule.id)).scalars().all())


def next_sort_order(db: Session) -> int:
    rows = list_rules(db)
    if not rows:
        return 10
    return int(rows[-1].sort_order) + 10


def match_insert_index(rules: list[ClashRule]) -> int:
    for index, rule in enumerate(rules):
        if rule.line.upper().startswith("MATCH,"):
            return index
    return len(rules)


def seed_clash(db: Session) -> None:
    if db.get(ClashSetting, 1) is None:
        db.add(ClashSetting(id=1, data_json=json.dumps(DEFAULT_CLASH_SETTINGS, ensure_ascii=False)))
    existing = db.execute(select(ClashRule.id).limit(1)).first()
    if existing is None:
        seen: set[str] = set()
        order = 10
        for raw in DEFAULT_RULES:
            line = normalize_rule(raw)
            if not is_valid_rule(line) or line in seen:
                continue
            seen.add(line)
            db.add(ClashRule(line=line, sort_order=order))
            order += 10
    db.commit()


def add_rules(db: Session, lines: list[str]) -> list[ClashRule]:
    # Lock writes before reading the current order so parallel edits cannot reuse it.
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    current = list_rules(db)
    existing = {rule.line for rule in current}
    incoming: list[str] = []
    seen: set[str] = set()
    for raw in lines:
        line = normalize_rule(raw)
        if not is_valid_rule(line) or line in existing or line in seen:
            continue
        seen.add(line)
        incoming.append(line)
    if not incoming:
        db.rollback()
        return []

    created = [ClashRule(line=line) for line in incoming]
    db.add_all(created)
    priority_rules: list[ClashRule] = []
    fallback_rules: list[ClashRule] = []
    for rule in created:
        if rule.line.upper().startswith("MATCH,"):
            fallback_rules.append(rule)
        else:
            priority_rules.append(rule)
    fallback_index = match_insert_index(current)
    current[fallback_index:fallback_index] = fallback_rules
    for index, rule in enumerate(priority_rules + current, start=1):
        rule.sort_order = index * 10
    _ensure_groups_for_policies(db, incoming)
    db.commit()
    for row in created:
        db.refresh(row)
    return created


def replace_rule(db: Session, rule_id: int, line: str) -> ClashRule:
    row = db.get(ClashRule, rule_id)
    if row is None:
        raise KeyError("规则不存在")
    normalized = normalize_rule(line)
    if not is_valid_rule(normalized):
        raise ValueError("规则格式无效")
    other = db.execute(
        select(ClashRule).where(ClashRule.line == normalized, ClashRule.id != rule_id)
    ).scalar_one_or_none()
    if other is not None:
        raise ValueError("规则已存在")
    row.line = normalized
    db.commit()
    db.refresh(row)
    return row


def delete_rule(db: Session, rule_id: int) -> None:
    row = db.get(ClashRule, rule_id)
    if row is None:
        raise KeyError("规则不存在")
    db.delete(row)
    db.commit()


def reorder_rules(db: Session, ids: list[int]) -> list[ClashRule]:
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    rows = {rule.id: rule for rule in list_rules(db)}
    if set(ids) != set(rows):
        raise ValueError("排序列表与现有规则不一致")
    for index, rule_id in enumerate(ids, start=1):
        rows[rule_id].sort_order = index * 10
    db.commit()
    return list_rules(db)


def _ensure_groups_for_policies(db: Session, lines: list[str]) -> None:
    data = get_clash_settings(db)
    groups: list[dict[str, Any]] = list(data.get("proxy_groups") or [])
    names = {str(group.get("name")) for group in groups}
    changed = False
    for line in lines:
        policy = rule_policy(line)
        if not policy or policy in BUILTIN_POLICIES or policy in names:
            continue
        groups.append({"name": policy, "type": "select", "proxies": [NODE_PLACEHOLDER]})
        names.add(policy)
        changed = True
    if changed:
        data["proxy_groups"] = groups
        row = db.get(ClashSetting, 1)
        blob = json.dumps(data, ensure_ascii=False)
        if row is None:
            db.add(ClashSetting(id=1, data_json=blob))
        else:
            row.data_json = blob


def _resolve_group_proxies(items: list[Any], replacements: list[str], fallback: str) -> list[str]:
    resolved: list[str] = []
    seen: set[str] = set()
    for item in items:
        names = replacements if str(item) == NODE_PLACEHOLDER else [str(item)]
        for name in names:
            if not name or name in seen:
                continue
            seen.add(name)
            resolved.append(name)
    return resolved or ([fallback] if fallback else [])


def render_clash_yaml(account: Account, db: Session) -> str:
    from app.services.nodes import group_nodes_map, list_vps, local_vps_id, vps_proxy_name
    from app.services.tunnels import clash_proxy_items, list_tunnels, parse_assign_id

    data = get_clash_settings(db)
    alpn = data.get("alpn") or ["h2", "http/1.1"]
    dns = data.get("dns") or DEFAULT_CLASH_SETTINGS["dns"]
    groups = data.get("proxy_groups") or DEFAULT_CLASH_SETTINGS["proxy_groups"]
    rules = [rule.line for rule in list_rules(db)]
    if not rules:
        rules = list(DEFAULT_RULES)

    all_nodes = list_vps(db)
    by_id = {item.id: item for item in all_nodes}
    all_tunnels = list_tunnels(db)
    tunnels_by_id = {item.id: item for item in all_tunnels}
    assignments = group_nodes_map(db, account.id)
    assigned_ids: list[str] = []
    assigned_tunnels: list = []
    seen_ids: set[str] = set()
    seen_tunnels: set[int] = set()
    explicit_keys: list[str] = []
    seen_explicit: set[str] = set()
    for keys in assignments.values():
        for key in keys:
            if key not in seen_explicit:
                seen_explicit.add(key)
                explicit_keys.append(key)
            tunnel_id = parse_assign_id(key)
            if tunnel_id is not None:
                tunnel = tunnels_by_id.get(tunnel_id)
                if tunnel is None or tunnel.id in seen_tunnels:
                    continue
                seen_tunnels.add(tunnel.id)
                assigned_tunnels.append(tunnel)
                if tunnel.vps_id in by_id and tunnel.vps_id not in seen_ids:
                    seen_ids.add(tunnel.vps_id)
                    assigned_ids.append(tunnel.vps_id)
                continue
            if key in seen_ids or key not in by_id:
                continue
            seen_ids.add(key)
            assigned_ids.append(key)
    if not assigned_ids and not assigned_tunnels:
        local_id = local_vps_id()
        if local_id in by_id:
            assigned_ids = [local_id]
        elif all_nodes:
            assigned_ids = [all_nodes[0].id]

    assigned = [by_id[vps_id] for vps_id in assigned_ids if vps_id in by_id]
    fallback_name = ""
    if assigned:
        fallback_name = vps_proxy_name(assigned[0], all_nodes)
    elif assigned_tunnels:
        fallback_name = assigned_tunnels[0].name
    else:
        fallback_name = default_node_name()

    lines: list[str] = [
        f"mixed-port: {int(data.get('mixed_port') or 7890)}",
        f"socks-port: {int(data.get('socks_port') or 7891)}",
        f"allow-lan: {_yaml_scalar(bool(data.get('allow_lan', True)))}",
        f"bind-address: {_yaml_scalar(str(data.get('bind_address') or '*'))}",
        f"mode: {data.get('mode') or 'rule'}",
        f"log-level: {data.get('log_level') or 'info'}",
        f"external-controller: {_yaml_scalar(str(data.get('external_controller') or '127.0.0.1:9090'))}",
        "dns:",
        f"  {_flow_map(list(dns.items()))}",
        "proxies:",
    ]
    for node in assigned:
        proxy_name = vps_proxy_name(node, all_nodes)
        lines.append(
            "  - "
            + _flow_map(
                [
                    ("name", proxy_name),
                    ("type", "trojan"),
                    ("server", node.id),
                    ("port", int(settings.trojan_port)),
                    ("alpn", list(alpn)),
                    ("password", account.password),
                    ("udp", bool(data.get("udp", True))),
                ]
            )
        )
    for tunnel in assigned_tunnels:
        hop = by_id.get(tunnel.vps_id)
        if hop is None:
            continue
        dialer = vps_proxy_name(hop, all_nodes)
        lines.append("  - " + _flow_map(clash_proxy_items(tunnel, dialer)))
    lines.append("")
    lines.append("proxy-groups:")
    for group in groups:
        name = str(group.get("name") or "PROXY")
        selected_keys = assignments.get(name) or []
        if not selected_keys:
            selected_keys = explicit_keys or list(assigned_ids)
        selected_names: list[str] = []
        for key in selected_keys:
            tunnel_id = parse_assign_id(key)
            if tunnel_id is not None:
                tunnel = tunnels_by_id.get(tunnel_id)
                if tunnel is not None:
                    selected_names.append(tunnel.name)
                continue
            if key in by_id:
                selected_names.append(vps_proxy_name(by_id[key], all_nodes))
        proxies = _resolve_group_proxies(list(group.get("proxies") or [NODE_PLACEHOLDER]), selected_names, fallback_name)
        lines.append(
            "  - "
            + _flow_map(
                [
                    ("name", name),
                    ("type", str(group.get("type") or "select")),
                    ("proxies", proxies),
                ]
            )
        )
    lines.append("rules:")
    for rule in rules:
        lines.append(f"  - {rule}")

    bypass = list(data.get("cfw_bypass") or [])
    if bypass:
        lines.extend(["", "cfw-bypass:"])
        for item in bypass:
            lines.append(f"  - {_yaml_scalar(item)}")
    timeout = data.get("cfw_latency_timeout")
    if timeout:
        lines.append(f"cfw-latency-timeout: {int(timeout)}")
    lines.append("")
    return "\n".join(lines)
