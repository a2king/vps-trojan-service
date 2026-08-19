from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
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
