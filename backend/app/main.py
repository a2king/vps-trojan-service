from contextlib import asynccontextmanager
import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import Account, AccountGroupVps, AccountIp, Admin, ClashRule, ClashSetting, LogCursor, TrafficDaily, TrafficSnapshot, TrojanUser, Vps  # noqa: F401
from app.mysql_bridge import create_mysql_server
from app.routers import auth, clash, subscribe, system, users, vps
from app.security import hash_admin_password
from app.services.clash import seed_clash
from app.services.node_install import start_online_tunnels
from app.services.nodes import seed_local_vps
from app.services.ssh import stop_all_tunnels
from app.services.traffic import collect_once
from app.timeutil import now_local

settings = get_settings()
logger = logging.getLogger("trojan-admin")


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        admin = db.execute(select(Admin).where(Admin.username == settings.admin_username)).scalar_one_or_none()
        if admin is None:
            db.add(
                Admin(
                    username=settings.admin_username,
                    password_hash=hash_admin_password(settings.admin_password),
                    created_at=now_local(),
                )
            )
        else:
            admin.password_hash = hash_admin_password(settings.admin_password)
        db.commit()
        seed_clash(db)
        seed_local_vps(db)
    finally:
        db.close()


def job_collect() -> None:
    db = SessionLocal()
    try:
        collect_once(db)
    except Exception:
        logger.exception("流量或登录日志采集失败")
        db.rollback()
    finally:
        db.close()


def _after_start() -> None:
    try:
        start_online_tunnels()
    except Exception:
        logger.exception("启动节点隧道失败")
    try:
        job_collect()
    except Exception:
        logger.exception("启动采集失败")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    mysql_server = create_mysql_server()
    await mysql_server.start_server(host=settings.trojan_mysql_host, port=settings.trojan_mysql_port)
    logger.info("SQLite MySQL 兼容端口已监听 %s:%s", settings.trojan_mysql_host, settings.trojan_mysql_port)
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    scheduler.add_job(
        job_collect,
        "interval",
        seconds=max(60, settings.traffic_interval_seconds),
        id="traffic-collect",
        replace_existing=True,
    )
    scheduler.start()
    threading.Thread(target=_after_start, daemon=True, name="startup-tunnels").start()
    yield
    scheduler.shutdown(wait=False)
    stop_all_tunnels()
    mysql_server.close()
    await mysql_server.wait_closed()


app = FastAPI(title="Trojan Admin", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(vps.router)
app.include_router(system.router)
app.include_router(clash.router)
app.include_router(subscribe.router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}
