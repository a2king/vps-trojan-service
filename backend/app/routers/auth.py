from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_admin
from app.models import Admin
from app.schemas import LoginRequest, TokenResponse
from app.security import create_access_token, verify_admin_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    admin = db.query(Admin).filter(Admin.username == body.username).one_or_none()
    if admin is None or not verify_admin_password(body.password, admin.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    return TokenResponse(token=create_access_token(admin.username), username=admin.username)


@router.get("/me")
def me(admin: Admin = Depends(get_current_admin)) -> dict:
    return {"username": admin.username, "domain": settings.domain}
