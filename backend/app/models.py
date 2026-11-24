from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TrojanUser(Base):
    """trojan-gfw 原生用户表，密码字段为 SHA224 hex。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password: Mapped[str] = mapped_column(String(56), nullable=False, index=True)
    quota: Mapped[int] = mapped_column(BigInteger, nullable=False, default=-1)
    download: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    upload: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password: Mapped[str] = mapped_column(String(128), nullable=False)
    password_sha224: Mapped[str] = mapped_column(String(56), nullable=False, unique=True, index=True)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    remark: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    telemetry_key: Mapped[str] = mapped_column(String(32), nullable=False, default=lambda: uuid4().hex)


class NodeTrafficDaily(Base):
    __tablename__ = "node_traffic_daily"

    node_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    account_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    stat_date: Mapped[date] = mapped_column(Date, primary_key=True)
    download_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    upload_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class NodeTrafficMonthly(Base):
    __tablename__ = "node_traffic_monthly"

    node_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    account_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    download_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    upload_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)


class NodeObservation(Base):
    __tablename__ = "node_observations"

    node_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    traffic_at: Mapped[datetime | None] = mapped_column(DateTime)
    traffic_error: Mapped[str | None] = mapped_column(Text)
    ips_at: Mapped[datetime | None] = mapped_column(DateTime)
    ips_error: Mapped[str | None] = mapped_column(Text)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime)
    applied_version: Mapped[str | None] = mapped_column(String(64))
    sync_error: Mapped[str | None] = mapped_column(Text)


class AccountNodeIp(Base):
    __tablename__ = "account_node_ips"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    ip: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AccountAuthPrefix(Base):
    __tablename__ = "account_auth_prefixes"

    prefix: Mapped[str] = mapped_column(String(7), primary_key=True)
    account_key: Mapped[str] = mapped_column(String(32), primary_key=True)


class AdminAuditEvent(Base):
    __tablename__ = "admin_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    actor: Mapped[str] = mapped_column(String(64))
    event: Mapped[str] = mapped_column(Text)


class TrafficDaily(Base):
    __tablename__ = "traffic_daily"
    __table_args__ = (UniqueConstraint("account_id", "stat_date", name="uk_account_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    download_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    upload_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class TrafficSnapshot(Base):
    __tablename__ = "traffic_snapshot"

    account_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    download_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    upload_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    collected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class ClashSetting(Base):
    __tablename__ = "clash_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data_json: Mapped[str] = mapped_column(Text, nullable=False)


class ClashRule(Base):
    __tablename__ = "clash_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    line: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)


class AccountIp(Base):
    __tablename__ = "account_ips"
    __table_args__ = (UniqueConstraint("account_id", "ip", name="uk_account_ip"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    ip: Mapped[str] = mapped_column(String(64), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class LogCursor(Base):
    __tablename__ = "log_cursors"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    inode: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    offset: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    anchor: Mapped[str] = mapped_column(String(16), nullable=False, default="")


class Vps(Base):
    """节点。主键为域名。"""

    __tablename__ = "vps"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    ssh_host: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    ssh_user: Mapped[str] = mapped_column(String(64), nullable=False, default="root")
    is_local: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 1=页面安装的完整备机（本机后台+代理），认证不走主节点反向隧道
    replica: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hidden: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class AccountGroupVps(Base):
    __tablename__ = "account_group_vps"
    __table_args__ = (UniqueConstraint("account_id", "group_name", "vps_id", name="uk_account_group_vps"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    group_name: Mapped[str] = mapped_column(String(128), nullable=False)
    vps_id: Mapped[str] = mapped_column(String(255), ForeignKey("vps.id", ondelete="CASCADE"), nullable=False, index=True)


class TunnelProxy(Base):
    """经 VPS 跳转的家用 HTTP/HTTPS/SOCKS5 代理。"""

    __tablename__ = "tunnel_proxies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    protocol: Mapped[str] = mapped_column(String(16), nullable=False, default="http")
    server: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    password: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    vps_id: Mapped[str] = mapped_column(String(255), ForeignKey("vps.id", ondelete="RESTRICT"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class AccountGroupTunnel(Base):
    __tablename__ = "account_group_tunnels"
    __table_args__ = (UniqueConstraint("account_id", "group_name", "tunnel_id", name="uk_account_group_tunnel"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    group_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tunnel_id: Mapped[int] = mapped_column(Integer, ForeignKey("tunnel_proxies.id", ondelete="CASCADE"), nullable=False, index=True)
