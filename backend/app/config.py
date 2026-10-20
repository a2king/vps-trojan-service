from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    admin_username: str = "admin"
    admin_password: str = "change-me"
    jwt_secret: str = "please-change-this-to-a-long-random-string"
    jwt_expire_hours: int = 24

    domain: str = "example.com"
    trojan_port: int = 443

    database_url: str = "sqlite:///./data/trojan.db"

    # Trojan 通过 MySQL 协议读写 SQLite 中的 users 表（进程内模拟，不是独立数据库服务）
    trojan_mysql_host: str = "127.0.0.1"
    trojan_mysql_port: int = 13306

    # 主控角色：primary 负责写库并向备机同步；standby 是页面装上的完整备机
    control_role: str = "primary"
    # 向各备机推送 SQLite 快照的间隔（秒）
    replica_sync_interval_seconds: int = 21600

    traffic_interval_seconds: int = 300
    ip_ingest_interval_seconds: int = 3600
    log_retention_days: int = 31
    timezone: str = "Asia/Shanghai"
    trojan_log_path: str = "/var/log/trojan.log"

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def sqlalchemy_url(self) -> str:
        return self.database_url or "sqlite:///./data/trojan.db"

    @property
    def sqlite_path(self) -> Path:
        url = self.sqlalchemy_url
        prefix = "sqlite:///"
        if not url.startswith(prefix):
            raise ValueError("目前只支持 SQLite，请设置 DATABASE_URL=sqlite:///...")
        raw = url[len(prefix) :]
        path = Path(raw)
        if not path.is_absolute():
            path = Path.cwd() / path
        return path

    @property
    def is_control_primary(self) -> bool:
        return (self.control_role or "primary").strip().lower() == "primary"


@lru_cache
def get_settings() -> Settings:
    return Settings()
