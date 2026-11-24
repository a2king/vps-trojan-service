from __future__ import annotations

import logging
import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
from shutil import which

from app.config import get_settings
from app.database import SessionLocal
from app.models import Vps
from app.paths import repo_root
from app.services import ssh
from app.services.control_lock import mark_switch_in_progress, primary_switch_lock

logger = logging.getLogger("trojan-admin")
settings = get_settings()


def _script(name: str) -> Path:
    path = repo_root() / "deploy" / name
    if not path.is_file():
        raise RuntimeError(f"缺少部署脚本：deploy/{name}")
    return path


def _run_local(script: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["bash", str(script), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=1800,
    )
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode != 0:
        raise RuntimeError(output or f"{script.name} 执行失败（退出码 {result.returncode}）")
    return output


def _ha_env_for_target(target: Vps, incoming: str) -> tuple[dict[str, str], Path]:
    candidates = []
    configured = os.environ.get("HA_ENV", "").strip()
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            Path("/etc/trojan-admin/ha.env"),
            repo_root() / "deploy" / "ha.env",
            repo_root() / "deploy" / "ha.env.example",
        ]
    )
    source = next((path for path in candidates if path.is_file()), None)
    content = source.read_text(encoding="utf-8") if source else ""
    overrides = [
        "CONTROL_ROLE=primary",
        # The API process runs with ``backend`` as its working directory, so
        # sqlite_path.parent.parent points at ``.../backend``.  The control
        # scripts expect INSTALL_DIR to be the repository root (the directory
        # that contains deploy/ and backend/).
        f"INSTALL_DIR={repo_root()}",
        f"STANDBY_HOST={target.ssh_host}",
        f"STANDBY_PORT={int(target.ssh_port or 22)}",
        f"STANDBY_USER={target.ssh_user or 'root'}",
        f"HA_INCOMING={incoming}",
        f"STANDBY_SSH_KEY={ssh.node_private_key_path(target.id)}",
        f"STANDBY_KNOWN_HOSTS={ssh.known_hosts_path()}",
    ]
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="trojan-ha-switch-",
        suffix=".env",
        delete=False,
    )
    try:
        handle.write(content.rstrip() + "\n" if content else "")
        handle.write("\n".join(overrides) + "\n")
    finally:
        handle.close()
    path = Path(handle.name)
    path.chmod(0o600)
    env = os.environ.copy()
    env["HA_ENV"] = str(path)
    return env, path


def _check_standby(client) -> dict[str, str]:
    command = r"""
set -e
# 页面安装的备控默认落在 /opt/vps-trojan-node，手工安装则通常使用
# /opt/vps-trojan-service。ha.env 是可选配置，不能作为备控就绪条件。
ROOT=''
SERVICE_ROOT="$(systemctl show -p WorkingDirectory --value trojan-admin 2>/dev/null || true)"
SERVICE_ROOT="${SERVICE_ROOT%/backend}"
for candidate in "$SERVICE_ROOT" /opt/vps-trojan-node /opt/vps-trojan-service; do
  [ -n "$candidate" ] || continue
  if [ -f "$candidate/deploy/control-common.sh" ] && [ -f "$candidate/deploy/control-promote.sh" ] && [ -f "$candidate/.env" ]; then
    ROOT="$candidate"
    break
  fi
done
test -n "$ROOT" || { echo '找不到备控安装目录（缺少 .env 或 control-promote.sh）' >&2; exit 20; }
systemctl cat trojan-admin >/dev/null 2>&1 || { echo '缺少 trojan-admin 服务' >&2; exit 23; }
source "$ROOT/deploy/control-common.sh"
ha_load_env
# ha.env 中的 INSTALL_DIR 可能是模板默认值；页面安装目录以实际部署文件为准。
INSTALL_DIR="$ROOT"
ENV_FILE="$ROOT/.env"
DATA_DIR="$ROOT/backend/data"
DB_PATH="$DATA_DIR/trojan.db"
SSH_DIR="$DATA_DIR/ssh"
test -f "$ENV_FILE" || { echo "缺少 .env: $ENV_FILE" >&2; exit 24; }
test "$(ha_current_role)" = standby || { echo "目标当前角色不是 standby: $(ha_current_role)" >&2; exit 25; }
curl -fsS --max-time 5 http://127.0.0.1:8000/api/health >/dev/null || {
  echo 'trojan-admin 健康检查失败' >&2
  exit 26
}
mkdir -p "$HA_INCOMING"
printf 'INSTALL_DIR=%s\nHA_INCOMING=%s\nDOMAIN=%s\n' "$INSTALL_DIR" "$HA_INCOMING" "$(ha_control_dns_name)"
""".strip()
    output = ssh.run(client, command, timeout=60)
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    if not values.get("INSTALL_DIR") or not values.get("HA_INCOMING"):
        raise RuntimeError("目标备机未返回有效 HA 路径")
    return values


def _remote_env_file(install_dir: str, incoming: str) -> str:
    return "\n".join(
        [
            "set -eu",
            "umask 077",
            "env_file=/var/lib/trojan-admin/primary-switch.env",
            "mkdir -p /var/lib/trojan-admin",
            "cat >\"$env_file\" <<'EOF'",
            f"INSTALL_DIR={shlex.quote(install_dir)}",
            f"HA_INCOMING={shlex.quote(incoming)}",
            f"ENV_FILE={shlex.quote(f'{install_dir}/.env')}",
            f"DATA_DIR={shlex.quote(f'{install_dir}/backend/data')}",
            f"DB_PATH={shlex.quote(f'{install_dir}/backend/data/trojan.db')}",
            f"SSH_DIR={shlex.quote(f'{install_dir}/backend/data/ssh')}",
            "EOF",
            "printf '%s\\n' \"$env_file\"",
        ]
    )


def _remote_apply_only(client, install_dir: str, incoming: str) -> None:
    command = "\n".join(
        [
            _remote_env_file(install_dir, incoming),
            f"test -f {shlex.quote(incoming)}/trojan.db",
            f"HA_ENV=/var/lib/trojan-admin/primary-switch.env bash {shlex.quote(install_dir)}/deploy/control-promote.sh --apply-only",
        ]
    )
    ssh.run(client, command, timeout=180)


def prepare_management(client, target: Vps, install_dir: str) -> None:
    """Authorize the chosen controller's own public keys on its future peers."""
    directory = f"{install_dir}/backend/data/ssh"
    known_hosts = f"{directory}/known_hosts"
    with SessionLocal() as db:
        peers = db.query(Vps).filter(Vps.id != target.id, Vps.status == "online").all()
    for peer in peers:
        key_path = f"{directory}/nodes/{hashlib.sha256(peer.id.encode()).hexdigest()}"
        command = (
            f"umask 077; mkdir -p {shlex.quote(directory + '/nodes')}; "
            f"test -s {shlex.quote(key_path)} || ssh-keygen -q -t ed25519 -N '' -f {shlex.quote(key_path)}; "
            f"ssh-keygen -y -f {shlex.quote(key_path)}"
        )
        public_key = ssh.run(client, command).strip()
        if peer.is_local:
            host = peer.id
            host_keys = list(Path("/etc/ssh").glob("ssh_host_*_key.pub"))
            if not host_keys:
                raise RuntimeError("找不到旧主 SSH 主机公钥")
            host_records = [f"{host} {' '.join(path.read_text().split()[:2])}" for path in host_keys]
            ssh.authorize_local(public_key)
        else:
            peer_connection = ssh.connect_node(peer)
            try:
                server_key = peer_connection.get_transport().get_remote_server_key()
                port = int(peer.ssh_port or 22)
                host = peer.ssh_host if port == 22 else f"[{peer.ssh_host}]:{port}"
                host_records = [f"{host} {server_key.get_name()} {server_key.get_base64()}"]
                ssh.install_authorized_key(peer_connection, public_key)
            finally:
                peer_connection.close()
        for record in host_records:
            quoted = shlex.quote(record)
            known = shlex.quote(known_hosts)
            ssh.run(client, f"umask 077; touch {known}; grep -Fqx {quoted} {known} || printf '%s\\n' {quoted} >> {known}")


def prepare_standby(target: Vps) -> None:
    if not settings.is_control_primary or target.is_local or not target.replica or target.status != "online":
        raise RuntimeError("请选择在线的完整备控")
    with primary_switch_lock():
        client = ssh.connect_node(target)
        try:
            remote = _check_standby(client)
            prepare_management(client, target, remote["INSTALL_DIR"])
        finally:
            client.close()


def _remote_promote_watcher(client, install_dir: str, old_domain: str) -> str:
    promote = f"{install_dir}/deploy/control-promote.sh"
    env_file = "/var/lib/trojan-admin/primary-switch.env"
    payload = "\n".join(
        [
            "set -eu",
            f"trap 'rm -f {shlex.quote(env_file)}' EXIT",
            "old_ok=0",
            "for _ in $(seq 1 120); do",
            # Prefer the public HTTPS endpoint, but keep a local HTTP fallback
            # for installations without nginx/TLS.  Either response must
            # explicitly report standby before the new node is promoted.
            f"  health=$(curl -ksS --max-time 3 https://{shlex.quote(old_domain)}/api/health 2>/dev/null || true)",
            f"  [ -n \"$health\" ] || health=$(curl -fsS --max-time 3 http://{shlex.quote(old_domain)}/api/health 2>/dev/null || true)",
            "  case \"$health\" in",
            "    *'\"control_role\":\"standby\"'*) old_ok=1; break ;;",
            "  esac",
            "  sleep 1",
            "done",
            "if [ \"$old_ok\" -ne 1 ]; then echo '旧主未确认 standby，停止 promote' >&2; exit 30; fi",
            f"HA_ENV={shlex.quote(env_file)} bash {shlex.quote(promote)}",
            "echo 'promote 已完成；请将管控 DNS 指向新主'",
            "for _ in $(seq 1 30); do",
            "  health=$(curl -fsS --max-time 3 http://127.0.0.1:8000/api/health 2>/dev/null || true)",
            "  case \"$health\" in *'\"control_role\":\"primary\"'*) break ;; esac",
            "  sleep 1",
            "done",
            "case \"$health\" in *'\"control_role\":\"primary\"'*) ;; *) echo '新主健康检查失败' >&2; exit 31 ;; esac",
            "echo 'primary switch complete; 请将管控 DNS 指向新主'",
        ]
    )
    log_path = "/var/lib/trojan-admin/primary-switch.log"
    command = f"mkdir -p /var/lib/trojan-admin; nohup bash -c {shlex.quote(payload)} >{shlex.quote(log_path)} 2>&1 </dev/null & echo $!"
    return ssh.run(client, command, timeout=30).strip().splitlines()[-1]


def _schedule_demote() -> None:
    script = _script("control-promote.sh")
    systemd_run = which("systemd-run")
    if systemd_run:
        result = subprocess.run(
            [systemd_run, "--unit=trojan-control-switch-demote", "--collect", "--no-block", "bash", str(script), "--demote"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return
        logger.warning("systemd-run demote 失败，改用 detached 子进程：%s", result.stderr.strip())
    subprocess.Popen(
        ["bash", str(script), "--demote"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def promote_primary(target: Vps) -> dict[str, str]:
    if not settings.is_control_primary:
        raise RuntimeError("当前节点不是 primary，不能发起切主")
    if target.is_local:
        raise RuntimeError("目标必须是备控节点，不能选择本机")
    if not target.replica or target.status != "online":
        raise RuntimeError("目标机必须是已完成安装且在线的完整备控")

    with primary_switch_lock():
        mark_switch_in_progress()
        client = None
        env_path: Path | None = None
        try:
            client = ssh.connect_node(target)
            remote = _check_standby(client)
            prepare_management(client, target, remote["INSTALL_DIR"])
            env, env_path = _ha_env_for_target(target, remote["HA_INCOMING"])
            _run_local(_script("control-sync.sh"), env=env)
            _remote_apply_only(client, remote["INSTALL_DIR"], remote["HA_INCOMING"])
            _schedule_demote()
            # The watcher promotes only after the old primary answers
            # /api/health as standby.  Schedule demotion first so the
            # orchestration follows backup/sync -> apply -> demote -> promote.
            pid = _remote_promote_watcher(client, remote["INSTALL_DIR"], settings.domain)
            logger.warning("已安排切主：目标=%s，远端 watcher=%s；请在新主 promote 后修改管控 DNS", target.id, pid)
            return {
                "target": target.id,
                "status": "scheduled",
                "message": "旧主已安排降级，备机将在确认旧主 standby 后 promote；完成后请将管控 DNS 指向新主。",
            }
        except Exception:
            logger.exception("主节点切换预检或编排失败")
            # 预检、备份、同步或套用失败时旧主仍是 primary，应恢复写入。
            try:
                from app.services.control_lock import clear_switch_marker

                clear_switch_marker()
            except Exception:
                logger.exception("清理主切换标记失败")
            raise
        finally:
            if client is not None:
                client.close()
            if env_path is not None:
                try:
                    env_path.unlink()
                except FileNotFoundError:
                    pass
