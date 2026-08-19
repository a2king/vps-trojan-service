import hashlib
import secrets
import string
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import get_settings

settings = get_settings()
ALGORITHM = "HS256"
PASSWORD_ALPHABET = string.ascii_letters + string.digits


def sha224_hex(password: str) -> str:
    return hashlib.sha224(password.encode("utf-8")).hexdigest()


def generate_password(length: int = 16) -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def hash_admin_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_admin_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    payload = {"sub": username, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    return str(sub) if sub else None
