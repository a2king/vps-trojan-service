from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import get_settings

settings = get_settings()
TZ = ZoneInfo(settings.timezone)


def now_local() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def as_local(dt: datetime) -> datetime:
    """把任意时间转成配置时区的墙钟时间（无 tzinfo，写入 SQLite）。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ).replace(tzinfo=None)


def to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return as_local(dt).replace(tzinfo=TZ).isoformat(timespec="seconds")
