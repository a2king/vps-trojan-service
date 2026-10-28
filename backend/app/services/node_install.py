from __future__ import annotations

import io
import logging
import secrets
import tarfile
import threading
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import Vps
from app.paths import repo_root
from app.services import install_logs, ssh
from app.services.nodes import bind_vps_to_proxy_groups, normalize_domain
from app.services.replica_sync import backup_sqlite, push_online_replicas

logger = logging.getLogger("trojan-admin")
settings = get_settings()

REMOTE_ROOT = "/opt/vps-trojan-node"
_jobs: dict[str, threading.Thread] = {}


def _skip_payload(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    parts = set(Path(info.name).parts)
    if parts & {".venv", "__pycache__", "node_modules", ".git"}:
        return None
    if info.name.startswith("backend/data") or info.name == "backend/.env":
        return None
    if info.name.endswith(".pyc"):
        return None
    return info


def _pack_payload(snapshot: Path) -> bytes:
    root = repo_root()
    deploy = root / "deploy"
    script = root / "install-node.sh"
    vendor = root / "vendor" / "trojan"
    backend = root / "backend"
    dist = root / "frontend" / "dist"
    if not deploy.is_dir() or not script.is_file():
        raise RuntimeError("缺少 deploy/ 或 install-node.sh，无法远程安装")
    if not vendor.is_dir() or not any(vendor.glob("trojan-*-linux-amd64.tar.xz")):
        raise RuntimeError("缺少 vendor/trojan 预置包，无法远程安装")
    if not backend.is_dir() or not (backend / "pyproject.toml").is_file():
        raise RuntimeError("缺少 backend/，无法把节点装成备机")
    if not (dist / "index.html").is_file():
        raise RuntimeError("缺少 frontend/dist，请先在主节点完成前端打包")
    if not snapshot.is_file():
        raise RuntimeError("缺少数据库快照，无法初始化备机")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(deploy, arcname="deploy", filter=_skip_payload)
        tar.add(script, arcname="install-node.sh")
        tar.add(vendor, arcname="vendor/trojan")
        tar.add(backend, arcname="backend", filter=_skip_payload)
        tar.add(dist, arcname="frontend/dist")
        tar.add(snapshot, arcname="seed/trojan.db")
    return buffer.getvalue()


def _standby_env(domain: str) -> str:
    return "\n".join(
        [
            f"ADMIN_USERNAME={settings.admin_username}",
            f"ADMIN_PASSWORD={settings.admin_password}",
            f"JWT_SECRET={settings.jwt_secret}",
            f"JWT_EXPIRE_HOURS={settings.jwt_expire_hours}",
            f"DOMAIN={domain}",
            "TROJAN_PORT=443",
            "DATABASE_URL=sqlite:///./data/trojan.db",
            "CONTROL_ROLE=standby",
            "TROJAN_MYSQL_HOST=127.0.0.1",
            "TROJAN_MYSQL_PORT=13306",
            f"TRAFFIC_INTERVAL_SECONDS={settings.traffic_interval_seconds}",
            f"IP_INGEST_INTERVAL_SECONDS={settings.ip_ingest_interval_seconds}",
            f"LOG_RETENTION_DAYS={settings.log_retention_days}",
            f"TIMEZONE={settings.timezone}",
            "TROJAN_LOG_PATH=/var/log/trojan.log",
            "",
        ]
    )


def _unit_instance(vps_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ".-" else "-" for ch in vps_id)


def try_start_autossh(vps: Vps) -> bool:
    try:
        import shutil
        import subprocess

        if shutil.which("autossh") is None or shutil.which("systemctl") is None:
            return False
        unit_dir = Path("/etc/systemd/system")
        if not unit_dir.is_dir():
            return False
        template = repo_root() / "deploy" / "trojan-tunnel@.service"
        target = unit_dir / "trojan-tunnel@.service"
        if template.is_file():
            target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        env_dir = Path("/var/lib/trojan-admin/tunnels")
        env_dir.mkdir(parents=True, exist_ok=True)
        instance = _unit_instance(vps.id)
        env_path = env_dir / f"{instance}.env"
        env_path.write_text(
            "\n".join(
                [
                    f"SSH_HOST={vps.ssh_host}",
                    f"SSH_PORT={int(vps.ssh_port or 22)}",
                    f"SSH_USER={vps.ssh_user or 'root'}",
                    f"SSH_KEY={ssh.private_key_path()}",
                    f"LOCAL_HOST={settings.trojan_mysql_host}",
                    f"LOCAL_PORT={settings.trojan_mysql_port}",
                    f"REMOTE_PORT={settings.trojan_mysql_port}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        env_path.chmod(0o600)
        name = f"trojan-tunnel@{instance}"
        subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True, timeout=15)
        result = subprocess.run(
            ["systemctl", "enable", "--now", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result.returncode == 0
    except Exception:
        logger.exception("autossh 隧道启动失败，将改用进程内隧道")
        return False


def try_stop_autossh(vps_id: str) -> None:
    try:
        import shutil
        import subprocess

        if shutil.which("systemctl") is None:
            return
        name = f"trojan-tunnel@{_unit_instance(vps_id)}"
        subprocess.run(["systemctl", "disable", "--now", name], check=False, capture_output=True, timeout=20)
    except Exception:
        return


def ensure_tunnel(vps: Vps) -> None:
    if vps.is_local or vps.replica:
        return
    if not settings.is_control_primary:
        raise RuntimeError(f"CONTROL_ROLE={settings.control_role}，备控不能建立反向隧道")
    if try_start_autossh(vps):
        ssh.stop_tunnel(vps.id)
        return
    ssh.start_tunnel(vps.id, vps.ssh_host, int(vps.ssh_port or 22), vps.ssh_user or "root")


def stop_tunnel(vps: Vps | str) -> None:
    vps_id = vps.id if isinstance(vps, Vps) else vps
    try_stop_autossh(vps_id)
    ssh.stop_tunnel(vps_id)


def start_online_tunnels() -> None:
    if not settings.is_control_primary:
        logger.info("CONTROL_ROLE=%s，跳过 start_online_tunnels", settings.control_role)
        return
    db = SessionLocal()
    try:
        rows = db.query(Vps).filter(Vps.is_local == 0, Vps.replica == 0, Vps.status == "online").all()
        for row in rows:
            try:
                ensure_tunnel(row)
            except Exception:
                logger.exception("启动节点隧道失败：%s", row.id)
    finally:
        db.close()


def _set_status(db: Session, vps_id: str, status: str, error: str = "") -> None:
    row = db.get(Vps, vps_id)
    if row is None:
        return
    row.status = status
    row.last_error = error
    db.commit()


def install_remote(vps_id: str, password: str | None = None, apply_groups: list[str] | None = None) -> None:
    db = SessionLocal()
    try:
        row = db.get(Vps, normalize_domain(vps_id))
        if row is None or row.is_local:
            return
        vps_id = row.id
        row.replica = 1
        db.commit()
        try:
            bind_vps_to_proxy_groups(db, vps_id, apply_groups)
        except Exception:
            logger.exception("装机前自动绑组失败：%s", vps_id)
        password = (password or "").strip() or None
        install_logs.clear_log(vps_id)
        _set_status(db, vps_id, "installing")
        log = lambda line: install_logs.append_log(vps_id, line)
        stop_tunnel(vps_id)

        log(f"连接 {row.ssh_user}@{row.ssh_host}:{row.ssh_port}")
        if password:
            client = ssh.connect(row.ssh_host, int(row.ssh_port or 22), row.ssh_user or "root", password=password)
            try:
                _, pub = ssh.ensure_host_key()
                log("写入免密公钥")
                ssh.install_authorized_key(client, pub)
            finally:
                client.close()
            log("改用密钥登录")
        else:
            log("使用已有密钥登录")
        client = ssh.connect(row.ssh_host, int(row.ssh_port or 22), row.ssh_user or "root")
        try:
            log("准备本机认证库快照")
            snapshot = settings.sqlite_path.parent / "replica-snapshot.db"
            backup_sqlite(snapshot)
            log("上传安装包（代理 + 管理后台）")
            payload = _pack_payload(snapshot)
            ssh.run(client, f"mkdir -p {REMOTE_ROOT} /tmp")
            ssh.sftp_put_bytes(client, payload, "/tmp/vps-trojan-node.tgz")
            ssh.sftp_put_bytes(client, _standby_env(row.id).encode("utf-8"), f"{REMOTE_ROOT}/.env")
            code = ssh.run_logged(
                client,
                f"tar -xzf /tmp/vps-trojan-node.tgz -C {REMOTE_ROOT} && chmod +x {REMOTE_ROOT}/install-node.sh",
                log,
                timeout=120,
            )
            if code != 0:
                raise RuntimeError("解压安装包失败")

            fallback = secrets.token_hex(16)
            email = f"acme@{row.id}"
            env = (
                f"DOMAIN={row.id} "
                f"ACME_EMAIL={email} "
                f"MYSQL_USERNAME={row.id} "
                f"TROJAN_FALLBACK_PASSWORD={fallback} "
                f"INSTALL_DIR={REMOTE_ROOT}"
            )
            log("开始远程安装（证书 + 代理 + 备机后台）")
            command = f"{env} STANDBY_ADMIN=1 bash {REMOTE_ROOT}/install-node.sh"
            code = ssh.run_logged(client, command, log, timeout=1800)
            if code != 0:
                raise RuntimeError("远程安装失败")
        finally:
            client.close()

        row = db.get(Vps, vps_id)
        if row is None:
            return
        row.replica = 1
        row.status = "online"
        row.last_error = ""
        db.commit()
        log("绑定新节点到已有 PROXY 分组")
        try:
            bound = bind_vps_to_proxy_groups(db, vps_id, apply_groups)
            log(f"已写入分组：{', '.join(bound) if bound else '无'}")
        except Exception as exc:
            log(f"自动绑组失败：{exc}")
        log("同步账号与配置到备机")
        try:
            errors = push_online_replicas(db)
            for failed_id, message in errors:
                log(f"同步失败 {failed_id}：{message}")
            if not errors:
                log("备机同步完成")
        except Exception as exc:
            log(f"首次同步失败（安装包里的快照仍可用，稍后会自动重试）：{exc}")
        log(f"安装完成。管理后台：https://{row.id}/admin （本机认证，不依赖主节点隧道）")
    except Exception as exc:
        logger.exception("远程安装失败：%s", vps_id)
        install_logs.append_log(vps_id, f"失败：{exc}")
        try:
            _set_status(db, vps_id, "error", str(exc))
        except Exception:
            db.rollback()
    finally:
        db.close()


def start_install_job(vps_id: str, password: str | None = None, apply_groups: list[str] | None = None) -> None:
    existing = _jobs.get(vps_id)
    if existing is not None and existing.is_alive():
        raise RuntimeError("该节点正在安装")
    thread = threading.Thread(
        target=install_remote,
        args=(vps_id, password, apply_groups),
        daemon=True,
        name=f"install-{vps_id}",
    )
    _jobs[vps_id] = thread
    thread.start()
