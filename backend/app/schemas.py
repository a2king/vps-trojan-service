from typing import Any

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
    clash_subscribe_url: str
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


class ClashRuleOut(BaseModel):
    id: int
    line: str
    sort_order: int


class ClashRuleCreate(BaseModel):
    line: str = Field(min_length=3, max_length=1024)


class ClashReorder(BaseModel):
    ids: list[int]


class ClashImportedRule(BaseModel):
    line: str
    selected: bool = True


class ClashImportPreview(BaseModel):
    total: int
    duplicated: int
    invalid: list[str]
    new_rules: list[ClashImportedRule]


class ClashImportConfirm(BaseModel):
    yaml: str | None = None
    rules: list[str] | None = None


class ClashSettingsOut(BaseModel):
    settings: dict[str, Any]
