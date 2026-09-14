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
from app.services.nodes import normalize_domain

logger = logging.getLogger("trojan-admin")
settings = get_settings()

REMOTE_ROOT = "/opt/vps-trojan-node"
_jobs: dict[str, threading.Thread] = {}


def _pack_payload() -> bytes:
    root = repo_root()
    deploy = root / "deploy"
    script = root / "install-node.sh"
    vendor = root / "vendor" / "trojan"
    if not deploy.is_dir() or not script.is_file():
        raise RuntimeError("缺少 deploy/ 或 install-node.sh，无法远程安装")
    if not vendor.is_dir() or not any(vendor.glob("trojan-*-linux-amd64.tar.xz")):
        raise RuntimeError("缺少 vendor/trojan 预置包，无法远程安装")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(deploy, arcname="deploy")
        tar.add(script, arcname="install-node.sh")
        tar.add(vendor, arcname="vendor/trojan")
    return buffer.getvalue()


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
        subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True)
        result = subprocess.run(
            ["systemctl", "enable", "--now", name],
            check=False,
            capture_output=True,
            text=True,
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
        subprocess.run(["systemctl", "disable", "--now", name], check=False, capture_output=True)
    except Exception:
        return


def ensure_tunnel(vps: Vps) -> None:
    if vps.is_local:
        return
    if try_start_autossh(vps):
        ssh.stop_tunnel(vps.id)
        return
    ssh.start_tunnel(vps.id, vps.ssh_host, int(vps.ssh_port or 22), vps.ssh_user or "root")


def stop_tunnel(vps: Vps | str) -> None:
    vps_id = vps.id if isinstance(vps, Vps) else vps
    try_stop_autossh(vps_id)
    ssh.stop_tunnel(vps_id)


def start_online_tunnels() -> None:
    db = SessionLocal()
    try:
        rows = db.query(Vps).filter(Vps.is_local == 0, Vps.status == "online").all()
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


def install_remote(vps_id: str, password: str | None = None) -> None:
    db = SessionLocal()
    try:
        row = db.get(Vps, normalize_domain(vps_id))
        if row is None or row.is_local:
            return
        vps_id = row.id
        password = (password or "").strip() or None
        install_logs.clear_log(vps_id)
        _set_status(db, vps_id, "installing")
        log = lambda line: install_logs.append_log(vps_id, line)

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
            log("上传安装包")
            payload = _pack_payload()
            ssh.run(client, f"mkdir -p {REMOTE_ROOT} /tmp")
            ssh.sftp_put_bytes(client, payload, "/tmp/vps-trojan-node.tgz")
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
            log("开始远程安装 Trojan（证书 + nginx + 节点服务）")
            command = f"{env} bash {REMOTE_ROOT}/install-node.sh"
            code = ssh.run_logged(client, command, log, timeout=1800)
            if code != 0:
                raise RuntimeError("远程安装失败")
        finally:
            client.close()

        log("建立到主库的认证隧道")
        row = db.get(Vps, vps_id)
        if row is None:
            return
        ensure_tunnel(row)
        row.status = "online"
        row.last_error = ""
        db.commit()
        log("安装完成")
    except Exception as exc:
        logger.exception("远程安装失败：%s", vps_id)
        install_logs.append_log(vps_id, f"失败：{exc}")
        try:
            _set_status(db, vps_id, "error", str(exc))
        except Exception:
            db.rollback()
    finally:
        db.close()


def start_install_job(vps_id: str, password: str | None = None) -> None:
    existing = _jobs.get(vps_id)
    if existing is not None and existing.is_alive():
        raise RuntimeError("该节点正在安装")
    thread = threading.Thread(target=install_remote, args=(vps_id, password), daemon=True, name=f"install-{vps_id}")
    _jobs[vps_id] = thread
    thread.start()
