from contextlib import asynccontextmanager
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import Account, Admin, ClashRule, ClashSetting, TrafficDaily, TrafficSnapshot, TrojanUser  # noqa: F401
from app.mysql_bridge import create_mysql_server
from app.routers import auth, clash, subscribe, users
from app.security import hash_admin_password
from app.services.clash import seed_clash
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
    finally:
        db.close()


def job_collect() -> None:
    db = SessionLocal()
    try:
        collect_once(db)
    except Exception:
        logger.exception("流量采集失败")
        db.rollback()
    finally:
        db.close()


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
    job_collect()
    yield
    scheduler.shutdown(wait=False)
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
app.include_router(clash.router)
app.include_router(subscribe.router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}
