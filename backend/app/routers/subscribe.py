from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account
from app.services.clash import render_clash_yaml

router = APIRouter(tags=["subscribe"])


@router.get("/config/{password:path}")
def clash_subscribe(password: str, db: Session = Depends(get_db)) -> PlainTextResponse:
    secret = unquote(password or "").strip()
    if not secret:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="配置不存在")
    account = db.query(Account).filter(Account.password == secret).one_or_none()
    if account is None or not account.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="配置不存在")
    body = render_clash_yaml(account, db)
    filename = f"{account.name}.yaml"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Profile-Update-Interval": "24",
        "Subscription-Userinfo": (
            f"upload=0; download=0; total=0; expire=0"
        ),
    }
    return PlainTextResponse(content=body, media_type="text/yaml; charset=utf-8", headers=headers)
