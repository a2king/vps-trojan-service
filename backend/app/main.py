from contextlib import asynccontextmanager
import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import Account, AccountGroupTunnel, AccountGroupVps, AccountIp, Admin, ClashRule, ClashSetting, LogCursor, TrafficDaily, TrafficSnapshot, TrojanUser, TunnelProxy, Vps  # noqa: F401
from app.mysql_bridge import create_mysql_server
from app.routers import auth, clash, subscribe, system, tunnels, users, vps
from app.security import hash_admin_password
from app.services.clash import seed_clash
from app.services.node_install import start_online_tunnels
from app.services.nodes import seed_local_vps
from app.services.ips import ingest_trojan_ips
from app.services.ssh import stop_all_tunnels
from app.services.traffic import collect_traffic
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


def job_traffic() -> None:
    db = SessionLocal()
    try:
        collect_traffic(db)
    except Exception:
        logger.exception("流量采集失败")
        db.rollback()
    finally:
        db.close()


def job_ingest_ips() -> None:
    db = SessionLocal()
    try:
        ingest_trojan_ips(db)
        db.commit()
    except Exception:
        logger.exception("登录 IP 采集失败")
        db.rollback()
    finally:
        db.close()


def _after_start() -> None:
    try:
        start_online_tunnels()
    except Exception:
        logger.exception("启动节点隧道失败")
    try:
        job_traffic()
    except Exception:
        logger.exception("启动流量采集失败")
    try:
        job_ingest_ips()
    except Exception:
        logger.exception("启动登录 IP 采集失败")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    mysql_server = create_mysql_server()
    await mysql_server.start_server(host=settings.trojan_mysql_host, port=settings.trojan_mysql_port)
    logger.info("SQLite MySQL 兼容端口已监听 %s:%s", settings.trojan_mysql_host, settings.trojan_mysql_port)
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    scheduler.add_job(
        job_traffic,
        "interval",
        seconds=max(60, settings.traffic_interval_seconds),
        id="traffic-collect",
        replace_existing=True,
    )
    scheduler.add_job(
        job_ingest_ips,
        "interval",
        seconds=max(60, settings.ip_ingest_interval_seconds),
        id="ip-ingest",
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
app.include_router(tunnels.router)
app.include_router(system.router)
app.include_router(clash.router)
app.include_router(subscribe.router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}
