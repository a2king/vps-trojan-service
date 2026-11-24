from datetime import date, timedelta

from sqlalchemy import func, select

from app.models import Account, AccountNodeIp, NodeTrafficDaily, NodeTrafficMonthly, TrafficDaily, TrafficSnapshot, TrojanUser
from app.timeutil import now_local


def finalize_legacy_traffic(db) -> None:
    today = now_local().date()
    for account, counter in db.execute(
        select(Account, TrojanUser).join(TrojanUser, TrojanUser.password == Account.password_sha224)
    ):
        snapshot = db.get(TrafficSnapshot, account.id)
        if snapshot is None:
            continue
        download = max(0, counter.download - snapshot.download_bytes)
        upload = max(0, counter.upload - snapshot.upload_bytes)
        if not (download or upload):
            continue
        daily = db.scalar(select(TrafficDaily).where(TrafficDaily.account_id == account.id, TrafficDaily.stat_date == today))
        if daily is None:
            db.add(TrafficDaily(account_id=account.id, stat_date=today, download_bytes=download, upload_bytes=upload))
        else:
            daily.download_bytes += download
            daily.upload_bytes += upload
        snapshot.download_bytes = counter.download
        snapshot.upload_bytes = counter.upload
    db.commit()


def traffic_rows(db, *, since=None, until=None, account_id=None, node_id=None) -> list:
    legacy = select(TrafficDaily.account_id, TrafficDaily.stat_date, TrafficDaily.download_bytes, TrafficDaily.upload_bytes)
    collected = select(Account.id, NodeTrafficDaily.stat_date, NodeTrafficDaily.download_bytes, NodeTrafficDaily.upload_bytes).join(
        NodeTrafficDaily, NodeTrafficDaily.account_key == Account.telemetry_key
    )
    if since is not None:
        legacy = legacy.where(TrafficDaily.stat_date >= since)
        collected = collected.where(NodeTrafficDaily.stat_date >= since)
    if until is not None:
        legacy = legacy.where(TrafficDaily.stat_date <= until)
        collected = collected.where(NodeTrafficDaily.stat_date <= until)
    if account_id is not None:
        legacy = legacy.where(TrafficDaily.account_id == account_id)
        collected = collected.where(Account.id == account_id)
    if node_id is not None:
        combined = collected.where(NodeTrafficDaily.node_id == node_id).subquery()
    else:
        combined = legacy.union_all(collected).subquery()
    return db.execute(select(
        combined.c.account_id, combined.c.stat_date,
        func.sum(combined.c.download_bytes), func.sum(combined.c.upload_bytes),
    ).group_by(combined.c.account_id, combined.c.stat_date)).all()


def recent_activity(db) -> tuple[dict, dict, dict]:
    auth = dict(db.execute(select(AccountNodeIp.account_id, func.max(AccountNodeIp.last_seen_at)).group_by(AccountNodeIp.account_id)).tuples().all())
    totals = {
        key: (download, upload, last)
        for key, download, upload, last in db.execute(select(
            NodeTrafficDaily.account_key, func.sum(NodeTrafficDaily.download_bytes),
            func.sum(NodeTrafficDaily.upload_bytes), func.max(NodeTrafficDaily.last_used_at),
        ).group_by(NodeTrafficDaily.account_key))
    }
    legacy = dict(db.execute(select(Account.id, Account.last_seen_at)).tuples().all())
    return auth, totals, legacy


def month_start(day: date) -> date:
    return day.replace(day=1)


def usage_totals(db, since: date, until: date | None = None) -> dict[int, tuple[int, int]]:
    totals: dict[int, tuple[int, int]] = {}
    for account_id, _day, download, upload in traffic_rows(db, since=since, until=until):
        previous = totals.get(account_id, (0, 0))
        totals[account_id] = (previous[0] + download, previous[1] + upload)
    return totals


def _points(periods, values):
    return [
        {"period": period, "download": values.get(period, (0, 0))[0], "upload": values.get(period, (0, 0))[1]}
        for period in periods
    ]


def series_daily(db, account_id: int, days: int = 30, *, node_id: str | None = None) -> list[dict]:
    today = now_local().date()
    start = today - timedelta(days=days - 1)
    rows = traffic_rows(db, since=start, until=today, account_id=account_id, node_id=node_id)
    values = {day.isoformat(): (down, up) for _, day, down, up in rows}
    return _points([(start + timedelta(days=offset)).isoformat() for offset in range(days)], values)


def series_monthly(db, account_id: int, months: int = 12, *, node_id: str | None = None) -> list[dict]:
    cursor = month_start(now_local().date())
    periods = []
    for _ in range(months):
        periods.append(cursor)
        cursor = month_start(cursor - timedelta(days=1))
    values: dict[str, tuple[int, int]] = {}
    def add_value(day, down, up):
        key = str(day)[:7]
        old = values.get(key, (0, 0))
        values[key] = (old[0] + down, old[1] + up)
    if node_id is None:
        for day, down, up in db.execute(select(
            TrafficDaily.stat_date, TrafficDaily.download_bytes, TrafficDaily.upload_bytes,
        ).where(TrafficDaily.account_id == account_id, TrafficDaily.stat_date >= periods[-1])):
            add_value(day, down, up)
    query = select(
        NodeTrafficMonthly.month, func.sum(NodeTrafficMonthly.download_bytes), func.sum(NodeTrafficMonthly.upload_bytes),
    ).join(Account, Account.telemetry_key == NodeTrafficMonthly.account_key).where(
        Account.id == account_id, NodeTrafficMonthly.month >= periods[-1],
    ).group_by(NodeTrafficMonthly.month)
    if node_id is not None:
        query = query.where(NodeTrafficMonthly.node_id == node_id)
    for month, down, up in db.execute(query):
        add_value(month, down, up)
    return _points([month.strftime("%Y-%m") for month in reversed(periods)], values)


def overview_stats(db) -> dict:
    today = now_local().date()
    today_values = usage_totals(db, today, today)
    month_values = usage_totals(db, month_start(today), today)
    accounts = db.scalars(select(Account)).all()
    auth, totals, legacy = recent_activity(db)
    active_today = 0
    for account in accounts:
        candidates = (auth.get(account.id), totals.get(account.telemetry_key, (0, 0, None))[2], legacy.get(account.id))
        if any(value is not None and value.date() == today for value in candidates):
            active_today += 1
    return {
        "user_count": len(accounts),
        "enabled_count": sum(1 for account in accounts if account.enabled),
        "active_today": active_today,
        "today_download": sum(value[0] for value in today_values.values()),
        "today_upload": sum(value[1] for value in today_values.values()),
        "month_download": sum(value[0] for value in month_values.values()),
        "month_upload": sum(value[1] for value in month_values.values()),
    }
