from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Account, TrojanUser
from app.schemas import UserOut
from app.services.clash import clash_subscribe_url
from app.services.nodes import assigned_vps_ids, group_nodes_map
from app.timeutil import TZ, now_local, to_iso

settings = get_settings()

UNLIMITED_QUOTA = -1
DISABLED_QUOTA = 0


def share_uri(password: str, name: str, host: str | None = None) -> str:
    domain = (host or settings.domain).strip() or settings.domain
    return f"trojan://{password}@{domain}:{settings.trojan_port}#{name}"


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
    db: Session | None = None,
) -> UserOut:
    total_down = int(trojan.download) if trojan else 0
    total_up = int(trojan.upload) if trojan else 0
    created = account.created_at or now_local()
    if created.tzinfo is None:
        created = created.replace(tzinfo=TZ)
    nodes: dict[str, list[str]] = {}
    host = settings.domain
    if db is not None:
        nodes = group_nodes_map(db, account.id)
        ids = assigned_vps_ids(db, account.id)
        if ids:
            host = ids[0]
    return UserOut(
        id=account.id,
        name=account.name,
        password=account.password,
        share_uri=share_uri(account.password, account.name, host),
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
        group_nodes=nodes,
    )
