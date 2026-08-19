from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import get_settings

settings = get_settings()
TZ = ZoneInfo(settings.timezone)


def now_local() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.isoformat()
