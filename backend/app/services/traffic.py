from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Account, TrafficDaily, TrafficSnapshot, TrojanUser
from app.services.ips import ingest_trojan_ips
from app.timeutil import now_local

logger = logging.getLogger("trojan-admin")
settings = get_settings()


def _add_daily(db: Session, account: Account, today: date, now: datetime, d_delta: int, u_delta: int) -> None:
    d_delta = max(0, int(d_delta))
    u_delta = max(0, int(u_delta))
    if d_delta == 0 and u_delta == 0:
        return
    account.last_seen_at = now
    daily = db.execute(
        select(TrafficDaily).where(
            TrafficDaily.account_id == account.id,
            TrafficDaily.stat_date == today,
        )
    ).scalar_one_or_none()
    if daily is None:
        daily = TrafficDaily(
            account_id=account.id,
            stat_date=today,
            download_bytes=0,
            upload_bytes=0,
        )
        db.add(daily)
        db.flush()
    daily.download_bytes = int(daily.download_bytes) + d_delta
    daily.upload_bytes = int(daily.upload_bytes) + u_delta


def _recorded_totals(db: Session) -> dict[int, tuple[int, int]]:
    rows = db.execute(
        select(
            TrafficDaily.account_id,
            func.coalesce(func.sum(TrafficDaily.download_bytes), 0),
            func.coalesce(func.sum(TrafficDaily.upload_bytes), 0),
        ).group_by(TrafficDaily.account_id)
    ).all()
    return {int(account_id): (int(down), int(up)) for account_id, down, up in rows}


def collect_traffic(db: Session) -> None:
    now = now_local()
    today = now.date()
    accounts = db.execute(select(Account)).scalars().all()
    users = {u.password: u for u in db.execute(select(TrojanUser)).scalars().all()}
    snapshots = {
        s.account_id: s for s in db.execute(select(TrafficSnapshot)).scalars().all()
    }
    recorded = _recorded_totals(db)

    for account in accounts:
        trojan = users.get(account.password_sha224)
        if trojan is None:
            continue
        download = int(trojan.download or 0)
        upload = int(trojan.upload or 0)
        snap = snapshots.get(account.id)
        if snap is None:
            snap = TrafficSnapshot(
                account_id=account.id,
                download_bytes=0,
                upload_bytes=0,
                collected_at=now,
            )
            db.add(snap)
            db.flush()
            snapshots[account.id] = snap

        d_delta = download - int(snap.download_bytes)
        u_delta = upload - int(snap.upload_bytes)
        if d_delta < 0 or u_delta < 0:
            snap.download_bytes = download
            snap.upload_bytes = upload
            snap.collected_at = now
            d_delta = 0
            u_delta = 0

        if d_delta == 0 and u_delta == 0:
            down_rec, up_rec = recorded.get(account.id, (0, 0))
            if down_rec == 0 and up_rec == 0 and (download > 0 or upload > 0):
                d_delta = download
                u_delta = upload

        snap.download_bytes = download
        snap.upload_bytes = upload
        snap.collected_at = now
        _add_daily(db, account, today, now, d_delta, u_delta)
        if account.last_seen_at is None and (download > 0 or upload > 0):
            account.last_seen_at = now

    cutoff = today - timedelta(days=max(1, settings.log_retention_days))
    db.query(TrafficDaily).filter(TrafficDaily.stat_date < cutoff).delete()
    db.commit()


def collect_once(db: Session) -> None:
    collect_traffic(db)
    try:
        ingest_trojan_ips(db)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("读取登录日志失败")


def month_start(day: date) -> date:
    return day.replace(day=1)


def daily_map(db: Session, day: date) -> dict[int, TrafficDaily]:
    rows = db.execute(select(TrafficDaily).where(TrafficDaily.stat_date == day)).scalars().all()
    return {row.account_id: row for row in rows}


def month_totals(db: Session, start: date) -> dict[int, tuple[int, int]]:
    rows = db.execute(
        select(
            TrafficDaily.account_id,
            func.coalesce(func.sum(TrafficDaily.download_bytes), 0),
            func.coalesce(func.sum(TrafficDaily.upload_bytes), 0),
        )
        .where(TrafficDaily.stat_date >= start)
        .group_by(TrafficDaily.account_id)
    ).all()
    return {int(account_id): (int(down), int(up)) for account_id, down, up in rows}


def series_daily(db: Session, account_id: int, days: int = 30) -> list[dict]:
    end = now_local().date()
    start = end - timedelta(days=days - 1)
    rows = {
        row.stat_date: row
        for row in db.execute(
            select(TrafficDaily).where(
                TrafficDaily.account_id == account_id,
                TrafficDaily.stat_date >= start,
                TrafficDaily.stat_date <= end,
            )
        ).scalars().all()
    }
    points = []
    cursor = start
    while cursor <= end:
        row = rows.get(cursor)
        points.append(
            {
                "period": cursor.isoformat(),
                "download": int(row.download_bytes) if row else 0,
                "upload": int(row.upload_bytes) if row else 0,
            }
        )
        cursor += timedelta(days=1)
    return points


def series_monthly(db: Session, account_id: int, months: int = 2) -> list[dict]:
    today = now_local().date()
    points = []
    year = today.year
    month = today.month
    buckets: list[tuple[int, int]] = []
    for _ in range(months):
        buckets.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    buckets.reverse()

    rows = db.execute(
        select(TrafficDaily).where(TrafficDaily.account_id == account_id)
    ).scalars().all()
    grouped: dict[str, tuple[int, int]] = {}
    for row in rows:
        key = f"{row.stat_date.year:04d}-{row.stat_date.month:02d}"
        down, up = grouped.get(key, (0, 0))
        grouped[key] = (down + int(row.download_bytes), up + int(row.upload_bytes))

    for year, month in buckets:
        key = f"{year:04d}-{month:02d}"
        down, up = grouped.get(key, (0, 0))
        points.append({"period": key, "download": down, "upload": up})
    return points


def overview_stats(db: Session) -> dict:
    today = now_local().date()
    start = month_start(today)
    accounts = db.execute(select(Account)).scalars().all()
    today_rows = daily_map(db, today)
    month_rows = month_totals(db, start)
    today_down = sum(int(r.download_bytes) for r in today_rows.values())
    today_up = sum(int(r.upload_bytes) for r in today_rows.values())
    month_down = sum(v[0] for v in month_rows.values())
    month_up = sum(v[1] for v in month_rows.values())
    active_today = 0
    tz = ZoneInfo(settings.timezone)
    for account in accounts:
        if account.last_seen_at is None:
            continue
        seen = account.last_seen_at
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=tz)
        if seen.astimezone(tz).date() == today:
            active_today += 1
    return {
        "user_count": len(accounts),
        "enabled_count": sum(1 for a in accounts if a.enabled),
        "active_today": active_today,
        "today_download": today_down,
        "today_upload": today_up,
        "month_download": month_down,
        "month_upload": month_up,
    }
