from contextlib import asynccontextmanager
from hashlib import sha256
import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select, text

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import Account, AccountGroupTunnel, AccountGroupVps, AccountIp, Admin, AdminAuditEvent, ClashRule, ClashSetting, LogCursor, TrafficDaily, TrafficSnapshot, TrojanUser, TunnelProxy, Vps  # noqa: F401
from app.mysql_bridge import create_mysql_server
from app.routers import auth, clash, observations, subscribe, system, tunnels, users, vps
from app.security import decode_access_token, hash_admin_password
from app.services.clash import seed_clash
from app.services.node_install import start_online_tunnels
from app.services.cold_backup import job_cold_backup
from app.services.control_lock import clear_switch_marker, is_switch_in_progress
from app.services.nodes import seed_local_vps
from app.services.replica_sync import sync_replicas
from app.services.ips import ingest_trojan_ips, remember_auth_prefix
from app.services.ssh import stop_all_tunnels
from app.services.observation import collect_node_traffic
from app.services.node_traffic import init_node_traffic
from app.timeutil import now_local

settings = get_settings()
logger = logging.getLogger("trojan-admin")


def _migrate(db) -> None:
    rows = db.execute(text("PRAGMA table_info(vps)")).all()
    names = {row[1] for row in rows}
    if names and "replica" not in names:
        db.execute(text("ALTER TABLE vps ADD COLUMN replica INTEGER NOT NULL DEFAULT 0"))
        db.commit()
    names = {row[1] for row in db.execute(text("PRAGMA table_info(accounts)"))}
    if "telemetry_key" not in names:
        db.execute(text("ALTER TABLE accounts ADD COLUMN telemetry_key VARCHAR(32) NOT NULL DEFAULT ''"))
        accounts = db.execute(text("SELECT id, created_at FROM accounts")).all()
        for account_id, created_at in accounts:
            key = sha256(f"legacy:{account_id}:{created_at}".encode()).hexdigest()[:32]
            db.execute(text("UPDATE accounts SET telemetry_key=:key WHERE id=:id"), {"key": key, "id": account_id})
        if settings.is_control_primary:
            from app.services.traffic import finalize_legacy_traffic

            finalize_legacy_traffic(db)
        else:
            db.commit()
    names = {row[1] for row in db.execute(text("PRAGMA table_info(log_cursors)"))}
    if "anchor" not in names:
        db.execute(text("ALTER TABLE log_cursors ADD COLUMN anchor VARCHAR(16) NOT NULL DEFAULT ''"))
        db.commit()


def init_db() -> None:
    init_node_traffic()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        _migrate(db)
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
        for account in db.scalars(select(Account)):
            remember_auth_prefix(db, account)
        db.commit()
        seed_clash(db)
        seed_local_vps(db)
        if settings.is_control_primary:
            clear_switch_marker()
    finally:
        db.close()


def job_traffic() -> None:
    if is_switch_in_progress():
        logger.info("主节点切换进行中，跳过流量采集")
        return
    try:
        collect_node_traffic()
    except Exception:
        logger.exception("流量采集失败")


def job_sync_replicas() -> None:
    if is_switch_in_progress():
        logger.info("主节点切换进行中，跳过备机同步")
        return
    try:
        sync_replicas()
    except Exception:
        logger.exception("备机数据同步失败")


def job_cold_backup_daily() -> None:
    if is_switch_in_progress():
        logger.info("主节点切换进行中，跳过每日冷备份")
        return
    try:
        job_cold_backup()
    except Exception:
        logger.exception("每日冷备份广播失败")


def job_ingest_ips() -> None:
    if is_switch_in_progress():
        logger.info("主节点切换进行中，跳过登录 IP 采集")
        return
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
    if not settings.is_control_primary:
        logger.info("CONTROL_ROLE=%s，跳过隧道与后台采集", settings.control_role)
        return
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
    logger.info(
        "SQLite MySQL 兼容端口已监听 %s:%s（CONTROL_ROLE=%s）",
        settings.trojan_mysql_host,
        settings.trojan_mysql_port,
        settings.control_role,
    )
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    if settings.is_control_primary:
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
        scheduler.add_job(
            job_sync_replicas,
            "interval",
            seconds=max(300, settings.replica_sync_interval_seconds),
            id="replica-sync",
            replace_existing=True,
        )
        scheduler.add_job(
            job_cold_backup_daily,
            "cron",
            hour=3,
            minute=0,
            id="cold-backup-daily",
            replace_existing=True,
        )
        scheduler.start()
    else:
        logger.info("CONTROL_ROLE=%s，未启动流量/IP 定时任务", settings.control_role)
    threading.Thread(target=_after_start, daemon=True, name="startup-tunnels").start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)
    stop_all_tunnels()
    mysql_server.close()
    await mysql_server.wait_closed()


app = FastAPI(title="Trojan Admin", lifespan=lifespan)


@app.middleware("http")
async def standby_write_guard(request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path != "/api/auth/login":
        if not settings.is_control_primary:
            return JSONResponse(status_code=409, content={"detail": "当前为 standby，只读；请通过管控域名访问当前 primary"})
        if is_switch_in_progress():
            return JSONResponse(status_code=409, content={"detail": "主节点切换进行中，暂时禁止写入"})
    return await call_next(request)


@app.middleware("http")
async def audit_admin_changes(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"} or response.status_code >= 300:
        return response
    if not path.startswith(("/api/users", "/api/vps", "/api/tunnels", "/api/clash")) or path.endswith("/preview"):
        return response
    authorization = request.headers.get("Authorization", "")
    actor = decode_access_token(authorization[7:]) if authorization.lower().startswith("bearer ") else None
    if actor:
        try:
            action = getattr(request.state, "audit_action", request.method)
            await run_in_threadpool(_save_audit, actor, f"{action} {path}")
        except Exception:
            logger.exception("写入管理审计失败")
    return response


def _save_audit(actor: str, event: str) -> None:
    with SessionLocal.begin() as db:
        db.add(AdminAuditEvent(occurred_at=now_local(), actor=actor, event=event))


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
app.include_router(observations.router)
app.include_router(tunnels.router)
app.include_router(system.router)
app.include_router(clash.router)
app.include_router(subscribe.router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "control_role": "primary" if settings.is_control_primary else "standby"}
