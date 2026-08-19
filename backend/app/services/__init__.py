from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Account, TrojanUser
from app.schemas import UserOut
from app.services.clash import clash_subscribe_url
from app.timeutil import TZ, now_local, to_iso

settings = get_settings()

UNLIMITED_QUOTA = -1
DISABLED_QUOTA = 0


def share_uri(password: str, name: str) -> str:
    return f"trojan://{password}@{settings.domain}:{settings.trojan_port}#{name}"


def get_trojan_user(db: Session, sha224: str) -> TrojanUser | None:
    return db.query(TrojanUser).filter(TrojanUser.password == sha224).one_or_none()


def set_trojan_enabled(user: TrojanUser, enabled: bool) -> None:
    user.quota = UNLIMITED_QUOTA if enabled else DISABLED_QUOTA


def to_user_out(
    account: Account,
    trojan: TrojanUser | None,
    today_download: int = 0,
    today_upload: int = 0,
    month_download: int = 0,
    month_upload: int = 0,
) -> UserOut:
    total_down = int(trojan.download) if trojan else 0
    total_up = int(trojan.upload) if trojan else 0
    created = account.created_at or now_local()
    if created.tzinfo is None:
        created = created.replace(tzinfo=TZ)
    return UserOut(
        id=account.id,
        name=account.name,
        password=account.password,
        share_uri=share_uri(account.password, account.name),
        clash_subscribe_url=clash_subscribe_url(account.password),
        enabled=bool(account.enabled),
        remark=account.remark or "",
        created_at=to_iso(account.created_at) or created.isoformat(),
        last_seen_at=to_iso(account.last_seen_at),
        total_download=total_down,
        total_upload=total_up,
        today_download=today_download,
        today_upload=today_upload,
        month_download=month_download,
        month_upload=month_upload,
    )
