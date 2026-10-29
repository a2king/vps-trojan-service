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


class UserIpOut(BaseModel):
    ip: str
    last_seen_at: str


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
    group_nodes: dict[str, list[str]] = Field(default_factory=dict)


class Overview(BaseModel):
    user_count: int
    enabled_count: int
    active_today: int
    today_download: int
    today_upload: int
    month_download: int
    month_upload: int


class CpuInfo(BaseModel):
    percent: float
    per_core: list[float]
    model: str
    cores_physical: int | None
    cores_logical: int
    load_1: float | None
    load_5: float | None
    load_15: float | None


class MemoryInfo(BaseModel):
    total: int
    used: int
    available: int
    free: int
    cached: int
    buffers: int
    percent: float
    swap_total: int
    swap_used: int
    swap_percent: float


class DiskInfo(BaseModel):
    device: str
    mountpoint: str
    fstype: str
    total: int
    used: int
    free: int
    percent: float


class SystemOut(BaseModel):
    hostname: str
    os_name: str
    kernel: str
    arch: str
    timezone: str
    boot_time: str | None
    uptime_seconds: int
    collected_at: str | None
    process_count: int
    ips: list[str]
    cpu: CpuInfo
    memory: MemoryInfo
    disks: list[DiskInfo]


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


class GroupNodesIn(BaseModel):
    group_name: str = Field(min_length=1, max_length=128)
    vps_ids: list[str] = Field(default_factory=list)


class UserNodesUpdate(BaseModel):
    groups: list[GroupNodesIn]


class VpsOut(BaseModel):
    id: str
    name: str
    ssh_host: str
    ssh_port: int
    ssh_user: str
    is_local: bool
    replica: bool
    hidden: bool
    status: str
    last_error: str
    created_at: str


class VpsCreate(BaseModel):
    id: str = Field(min_length=1, max_length=255, description="节点域名")
    ssh_host: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(default="root", min_length=1, max_length=64)
    ssh_password: str = Field(min_length=1, max_length=256)
    name: str = Field(default="", max_length=128)
    hidden: bool = False


class VpsUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    hidden: bool | None = None


class VpsLogOut(BaseModel):
    id: str
    status: str
    log: str


class VpsRedeploy(BaseModel):
    ssh_password: str = Field(default="", max_length=256)


class VpsPromotePrimaryOut(BaseModel):
    target: str
    status: str
    message: str


class TunnelProxyOut(BaseModel):
    id: int
    assign_id: str
    name: str
    protocol: str
    server: str
    port: int
    username: str
    vps_id: str
    vps_name: str
    created_at: str


class TunnelProxyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    protocol: str = Field(min_length=4, max_length=16)
    server: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
    vps_id: str = Field(min_length=1, max_length=255)


class TunnelProxyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    protocol: str | None = Field(default=None, min_length=4, max_length=16)
    server: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, min_length=1, max_length=128)
    password: str | None = Field(default=None, min_length=1, max_length=256)
    vps_id: str | None = Field(default=None, min_length=1, max_length=255)
