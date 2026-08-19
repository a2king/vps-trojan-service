from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    token: str
    username: str


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    password: str | None = Field(default=None, min_length=8, max_length=64)
    remark: str = Field(default="", max_length=255)


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    remark: str | None = Field(default=None, max_length=255)
    enabled: bool | None = None


class PasswordReset(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=64)


class TrafficPoint(BaseModel):
    period: str
    download: int
    upload: int


class UserOut(BaseModel):
    id: int
    name: str
    password: str
    share_uri: str
    enabled: bool
    remark: str
    created_at: str
    last_seen_at: str | None
    total_download: int
    total_upload: int
    today_download: int
    today_upload: int
    month_download: int
    month_upload: int


class Overview(BaseModel):
    user_count: int
    enabled_count: int
    active_today: int
    today_download: int
    today_upload: int
    month_download: int
    month_upload: int
