from __future__ import annotations

from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_admin
from app.models import ClashRule
from app.schemas import (
    ClashImportConfirm,
    ClashImportPreview,
    ClashImportedRule,
    ClashReorder,
    ClashRuleCreate,
    ClashRuleOut,
    ClashSettingsOut,
)
from app.services.clash import (
    add_rules,
    delete_rule,
    get_clash_settings,
    is_valid_rule,
    list_rules,
    normalize_rule,
    reorder_rules,
    replace_rule,
    save_clash_settings,
)

router = APIRouter(prefix="/api/clash", tags=["clash"], dependencies=[Depends(get_current_admin)])


def _to_out(row: ClashRule) -> ClashRuleOut:
    return ClashRuleOut(id=row.id, line=row.line, sort_order=row.sort_order)


@router.get("/settings", response_model=ClashSettingsOut)
def read_settings(db: Session = Depends(get_db)) -> ClashSettingsOut:
    return ClashSettingsOut(settings=get_clash_settings(db))


@router.put("/settings", response_model=ClashSettingsOut)
def update_settings(body: ClashSettingsOut, db: Session = Depends(get_db)) -> ClashSettingsOut:
    return ClashSettingsOut(settings=save_clash_settings(db, body.settings))


@router.get("/rules", response_model=list[ClashRuleOut])
def read_rules(q: str = "", db: Session = Depends(get_db)) -> list[ClashRuleOut]:
    keyword = q.strip().lower()
    rows = list_rules(db)
    if keyword:
        rows = [row for row in rows if keyword in row.line.lower()]
    return [_to_out(row) for row in rows]


@router.post("/rules", response_model=ClashRuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(body: ClashRuleCreate, db: Session = Depends(get_db)) -> ClashRuleOut:
    created = add_rules(db, [body.line], before_match=True)
    if not created:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="规则已存在或格式无效")
    return _to_out(created[0])


@router.patch("/rules/{rule_id}", response_model=ClashRuleOut)
def update_rule(rule_id: int, body: ClashRuleCreate, db: Session = Depends(get_db)) -> ClashRuleOut:
    try:
        row = replace_rule(db, rule_id, body.line)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _to_out(row)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_rule(rule_id: int, db: Session = Depends(get_db)) -> Response:
    try:
        delete_rule(db, rule_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/rules/reorder", response_model=list[ClashRuleOut])
def sort_rules(body: ClashReorder, db: Session = Depends(get_db)) -> list[ClashRuleOut]:
    try:
        rows = reorder_rules(db, body.ids)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return [_to_out(row) for row in rows]


def _extract_rule_lines(text: str) -> tuple[list[str], list[str]]:
    invalid: list[str] = []
    parsed: Any = None
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        parsed = None

    raw_items: list[Any] = []
    if isinstance(parsed, dict):
        rules_field = parsed.get("rules")
        raw_items = rules_field if isinstance(rules_field, list) else []
    elif isinstance(parsed, list):
        raw_items = parsed
    else:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            raw_items.append(stripped)

    rules: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        line = normalize_rule(item)
        if not is_valid_rule(line):
            invalid.append(str(item))
            continue
        if line in seen:
            continue
        seen.add(line)
        rules.append(line)
    return rules, invalid


@router.post("/rules/import/preview", response_model=ClashImportPreview)
def preview_import(body: ClashImportConfirm, db: Session = Depends(get_db)) -> ClashImportPreview:
    source = body.yaml or ""
    if not source.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请粘贴或上传 Clash 配置")
    imported, invalid = _extract_rule_lines(source)
    existing = {row.line for row in list_rules(db)}
    new_rules = [ClashImportedRule(line=line, selected=True) for line in imported if line not in existing]
    duplicated = len(imported) - len(new_rules)
    return ClashImportPreview(
        total=len(imported),
        duplicated=duplicated,
        invalid=invalid,
        new_rules=new_rules,
    )


@router.post("/rules/import", response_model=list[ClashRuleOut])
def confirm_import(body: ClashImportConfirm, db: Session = Depends(get_db)) -> list[ClashRuleOut]:
    lines = body.rules or []
    if not lines and body.yaml:
        imported, _invalid = _extract_rule_lines(body.yaml)
        lines = imported
    if not lines:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="没有可添加的规则")
    created = add_rules(db, lines, before_match=True)
    return [_to_out(row) for row in created]
