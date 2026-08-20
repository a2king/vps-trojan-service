from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_admin
from app.models import Account, AccountGroupVps, AccountIp, Admin, TrafficDaily, TrafficSnapshot, TrojanUser
from app.schemas import Overview, PasswordReset, TrafficPoint, UserCreate, UserIpOut, UserNodesUpdate, UserOut, UserUpdate
from app.security import generate_password, sha224_hex
from app.services import get_trojan_user, set_trojan_enabled, to_user_out
from app.services.ips import list_account_ips
from app.services.nodes import assign_default_nodes, replace_group_nodes
from app.services.traffic import daily_map, month_start, month_totals, overview_stats, series_daily, series_monthly
from app.timeutil import now_local, to_iso

router = APIRouter(prefix="/api", tags=["admin"], dependencies=[Depends(get_current_admin)])


def _stats_for_accounts(db: Session, accounts: list[Account]) -> list[UserOut]:
    today = now_local().date()
    today_rows = daily_map(db, today)
    month_rows = month_totals(db, month_start(today))
    users = {u.password: u for u in db.execute(select(TrojanUser)).scalars().all()}
    result = []
    for account in accounts:
        today_row = today_rows.get(account.id)
        month = month_rows.get(account.id, (0, 0))
        result.append(
            to_user_out(
                account,
                users.get(account.password_sha224),
                today_download=int(today_row.download_bytes) if today_row else 0,
                today_upload=int(today_row.upload_bytes) if today_row else 0,
                month_download=month[0],
                month_upload=month[1],
                db=db,
            )
        )
    return result


def _get_account(db: Session, user_id: int) -> Account:
    account = db.get(Account, user_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return account


@router.get("/overview", response_model=Overview)
def overview(_: Admin = Depends(get_current_admin), db: Session = Depends(get_db)) -> Overview:
    return Overview(**overview_stats(db))


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)) -> list[UserOut]:
    accounts = db.execute(select(Account).order_by(Account.id.desc())).scalars().all()
    return _stats_for_accounts(db, accounts)


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, db: Session = Depends(get_db)) -> UserOut:
    name = body.name.strip()
    if db.execute(select(Account).where(Account.name == name)).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
    password = body.password or generate_password()
    digest = sha224_hex(password)
    if db.execute(select(Account).where(Account.password_sha224 == digest)).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="密码已被占用，请换一个")
    account = Account(
        name=name,
        password=password,
        password_sha224=digest,
        enabled=1,
        remark=body.remark.strip() if body.remark else "",
        created_at=now_local(),
    )
    db.add(account)
    db.flush()
    db.add(
        TrojanUser(
            username=name,
            password=digest,
            quota=-1,
            download=0,
            upload=0,
        )
    )
    db.add(
        TrafficSnapshot(
            account_id=account.id,
            download_bytes=0,
            upload_bytes=0,
            collected_at=now_local(),
        )
    )
    assign_default_nodes(db, account.id)
    db.commit()
    db.refresh(account)
    return _stats_for_accounts(db, [account])[0]


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: int, db: Session = Depends(get_db)) -> UserOut:
    account = _get_account(db, user_id)
    return _stats_for_accounts(db, [account])[0]


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, db: Session = Depends(get_db)) -> UserOut:
    account = _get_account(db, user_id)
    trojan = get_trojan_user(db, account.password_sha224)
    if body.name is not None:
        name = body.name.strip()
        exists = db.execute(
            select(Account).where(Account.name == name, Account.id != account.id)
        ).scalar_one_or_none()
        if exists:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
        account.name = name
        if trojan is not None:
            trojan.username = name
    if body.remark is not None:
        account.remark = body.remark.strip()
    if body.enabled is not None:
        account.enabled = 1 if body.enabled else 0
        if trojan is not None:
            set_trojan_enabled(trojan, body.enabled)
    db.commit()
    db.refresh(account)
    return _stats_for_accounts(db, [account])[0]


@router.post("/users/{user_id}/password", response_model=UserOut)
def reset_password(user_id: int, body: PasswordReset, db: Session = Depends(get_db)) -> UserOut:
    account = _get_account(db, user_id)
    password = body.password or generate_password()
    digest = sha224_hex(password)
    if db.execute(
        select(Account).where(Account.password_sha224 == digest, Account.id != account.id)
    ).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="密码已被占用，请换一个")
    trojan = get_trojan_user(db, account.password_sha224)
    account.password = password
    account.password_sha224 = digest
    if trojan is not None:
        trojan.password = digest
        trojan.username = account.name
    db.commit()
    db.refresh(account)
    return _stats_for_accounts(db, [account])[0]


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: Session = Depends(get_db)) -> None:
    account = _get_account(db, user_id)
    trojan = get_trojan_user(db, account.password_sha224)
    db.query(TrafficDaily).filter(TrafficDaily.account_id == account.id).delete()
    db.query(TrafficSnapshot).filter(TrafficSnapshot.account_id == account.id).delete()
    db.query(AccountIp).filter(AccountIp.account_id == account.id).delete()
    db.query(AccountGroupVps).filter(AccountGroupVps.account_id == account.id).delete()
    if trojan is not None:
        db.delete(trojan)
    db.delete(account)
    db.commit()


@router.get("/users/{user_id}/traffic", response_model=list[TrafficPoint])
def user_traffic(
    user_id: int,
    granularity: Literal["day", "month"] = "day",
    db: Session = Depends(get_db),
) -> list[TrafficPoint]:
    _get_account(db, user_id)
    raw = series_daily(db, user_id) if granularity == "day" else series_monthly(db, user_id)
    return [TrafficPoint(**item) for item in raw]


@router.get("/users/{user_id}/ips", response_model=list[UserIpOut])
def user_ips(user_id: int, db: Session = Depends(get_db)) -> list[UserIpOut]:
    _get_account(db, user_id)
    rows = list_account_ips(db, user_id)
    return [
        UserIpOut(ip=row.ip, last_seen_at=to_iso(row.last_seen_at) or "")
        for row in rows
    ]


@router.put("/users/{user_id}/nodes", response_model=UserOut)
def update_user_nodes(user_id: int, body: UserNodesUpdate, db: Session = Depends(get_db)) -> UserOut:
    account = _get_account(db, user_id)
    replace_group_nodes(db, account.id, [(item.group_name, item.vps_ids) for item in body.groups])
    return _stats_for_accounts(db, [account])[0]
