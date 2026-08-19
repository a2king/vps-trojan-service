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


def add_rules(db: Session, lines: list[str], *, before_match: bool = True) -> list[ClashRule]:
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
        return []

    insert_at = match_insert_index(current) if before_match else len(current)
    created: list[ClashRule] = []
    for line in incoming:
        row = ClashRule(line=line, sort_order=0)
        db.add(row)
        db.flush()
        created.append(row)
        current.insert(insert_at, row)
        insert_at += 1
    for index, rule in enumerate(current, start=1):
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


def _resolve_group_proxies(items: list[Any], node_name: str) -> list[str]:
    resolved: list[str] = []
    seen: set[str] = set()
    for item in items:
        name = node_name if str(item) == NODE_PLACEHOLDER else str(item)
        if name in seen:
            continue
        seen.add(name)
        resolved.append(name)
    return resolved or [node_name]


def render_clash_yaml(account: Account, db: Session) -> str:
    data = get_clash_settings(db)
    node_name = str(data.get("node_name") or default_node_name())
    alpn = data.get("alpn") or ["h2", "http/1.1"]
    dns = data.get("dns") or DEFAULT_CLASH_SETTINGS["dns"]
    groups = data.get("proxy_groups") or DEFAULT_CLASH_SETTINGS["proxy_groups"]
    rules = [rule.line for rule in list_rules(db)]
    if not rules:
        rules = list(DEFAULT_RULES)

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
        "  - "
        + _flow_map(
            [
                ("name", node_name),
                ("type", "trojan"),
                ("server", settings.domain),
                ("port", int(settings.trojan_port)),
                ("alpn", list(alpn)),
                ("password", account.password),
                ("udp", bool(data.get("udp", True))),
            ]
        ),
        "",
        "proxy-groups:",
    ]
    for group in groups:
        name = str(group.get("name") or "PROXY")
        proxies = _resolve_group_proxies(list(group.get("proxies") or [NODE_PLACEHOLDER]), node_name)
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
