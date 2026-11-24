from __future__ import annotations

import io
import hashlib
import logging
import select
import socket
import threading
import time
from pathlib import Path
from typing import Callable

import paramiko
from paramiko.rsakey import RSAKey
from paramiko.ed25519key import Ed25519Key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from app.config import get_settings

logger = logging.getLogger("trojan-admin")
settings = get_settings()
_key_lock = threading.RLock()

OnLine = Callable[[str], None]


def ssh_dir() -> Path:
    path = settings.sqlite_path.parent / "ssh"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def private_key_path() -> Path:
    return ssh_dir() / "id_ed25519"


def public_key_path() -> Path:
    return ssh_dir() / "id_ed25519.pub"


def ensure_host_key() -> tuple[Path, str]:
    priv = private_key_path()
    pub = public_key_path()
    if not priv.is_file():
        try:
            key: paramiko.PKey = Ed25519Key.generate()
        except Exception:
            key = RSAKey.generate(2048)
        key.write_private_key_file(str(priv))
        priv.chmod(0o600)
        pub.write_text(f"{key.get_name()} {key.get_base64()} trojan-admin\n", encoding="utf-8")
        pub.chmod(0o644)
    return priv, public_key_path().read_text(encoding="utf-8").strip()


def _load_private_key() -> paramiko.PKey:
    ensure_host_key()
    path = str(private_key_path())
    try:
        return Ed25519Key.from_private_key_file(path)
    except Exception:
        return RSAKey.from_private_key_file(path)


def _node_paths(node_id: str) -> tuple[Path, Path]:
    directory = ssh_dir() / "nodes"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / hashlib.sha256(node_id.encode()).hexdigest()
    return path, path.with_suffix(".enrolled")


def node_private_key_path(node_id: str) -> Path:
    return _node_paths(node_id)[0]


def known_hosts_path() -> Path:
    return ssh_dir() / "known_hosts"


class RememberHostKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key) -> None:
        with _key_lock:
            known = paramiko.HostKeys()
            path = known_hosts_path()
            if path.exists():
                known.load(str(path))
            stored = known.lookup(hostname)
            if stored and stored.get(key.get_name()) != key:
                raise paramiko.SSHException(f"SSH 主机密钥已变更：{hostname}")
            known.add(hostname, key.get_name(), key)
            known.save(str(path))
            path.chmod(0o600)


def connect_node(vps, *, password: str | None = None, timeout: int = 20) -> paramiko.SSHClient:
    path, enrolled = _node_paths(vps.id)
    host, port, user = vps.ssh_host, int(vps.ssh_port or 22), vps.ssh_user or "root"
    with _key_lock:
        if not path.exists():
            generated = Ed25519PrivateKey.generate()
            data = generated.private_bytes(Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption())
            path.write_bytes(data)
            path.chmod(0o600)
    key = Ed25519Key.from_private_key_file(str(path))
    if enrolled.exists() and not password:
        return connect(host, port, user, key=key, timeout=timeout)
    try:
        client = connect(host, port, user, key=key, timeout=timeout)
    except paramiko.AuthenticationException:
        bootstrap = connect(host, port, user, password=password, timeout=timeout)
        try:
            install_authorized_key(bootstrap, f"{key.get_name()} {key.get_base64()} trojan-admin-{vps.id}")
        finally:
            bootstrap.close()
        client = connect(host, port, user, key=key, timeout=timeout)
    try:
        if public_key_path().is_file():
            revoke_authorized_key(client, public_key_path().read_text(encoding="utf-8"))
        enrolled.touch(mode=0o600)
    except Exception:
        client.close()
        raise
    return client


def revoke_authorized_key(client: paramiko.SSHClient, public_key: str) -> None:
    key_parts = public_key.split()[:2]
    if len(key_parts) != 2:
        return
    run(client, (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        f"key = {key_parts!r}\n"
        "path = Path.home() / '.ssh' / 'authorized_keys'\n"
        "if path.exists():\n"
        "    lines = path.read_text(encoding='utf-8').splitlines()\n"
        "    remaining = [line for line in lines if line.split()[:2] != key]\n"
        "    if remaining != lines:\n"
        "        path.write_text('\\n'.join(remaining) + '\\n', encoding='utf-8')\n"
        "PY"
    ))


def connect(
    host: str,
    port: int,
    username: str,
    *,
    password: str | None = None,
    key: paramiko.PKey | None = None,
    timeout: int = 20,
) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    known_hosts = known_hosts_path()
    if known_hosts.exists():
        client.load_host_keys(str(known_hosts))
    client.set_missing_host_key_policy(RememberHostKey())
    kwargs: dict = {
        "hostname": host,
        "port": int(port or 22),
        "username": username or "root",
        "timeout": timeout,
        "allow_agent": False,
        "look_for_keys": False,
    }
    if password:
        kwargs["password"] = password
    else:
        kwargs["pkey"] = key or _load_private_key()
    try:
        client.connect(**kwargs)
    except Exception:
        client.close()
        raise
    return client


def install_authorized_key(client: paramiko.SSHClient, public_key: str) -> None:
    key = public_key.strip()
    remote = (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        f"key = {key!r}\n"
        "ssh = Path.home() / '.ssh'\n"
        "ssh.mkdir(mode=0o700, exist_ok=True)\n"
        "auth = ssh / 'authorized_keys'\n"
        "text = auth.read_text(encoding='utf-8') if auth.exists() else ''\n"
        "lines = [line.strip() for line in text.splitlines() if line.strip()]\n"
        "if key not in lines:\n"
        "    lines.append(key)\n"
        "auth.write_text('\\n'.join(lines) + '\\n', encoding='utf-8')\n"
        "auth.chmod(0o600)\n"
        "ssh.chmod(0o700)\n"
        "print('ok')\n"
        "PY\n"
    )
    try:
        run(client, remote)
    except RuntimeError:
        quoted = key.replace("'", "'\"'\"'")
        run(
            client,
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && "
            f"grep -Fqx '{quoted}' ~/.ssh/authorized_keys || printf '%s\\n' '{quoted}' >> ~/.ssh/authorized_keys",
        )


def authorize_local(public_key: str) -> None:
    directory = Path.home() / ".ssh"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / "authorized_keys"
    with _key_lock:
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        key = public_key.strip()
        if key not in lines:
            with path.open("a", encoding="utf-8") as handle:
                if lines:
                    handle.write("\n")
                handle.write(key + "\n")
        path.chmod(0o600)


def run(client: paramiko.SSHClient, command: str, timeout: int = 60) -> str:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    if code != 0:
        raise RuntimeError((err or out or f"exit {code}").strip())
    return out


def run_ok(client: paramiko.SSHClient, command: str, timeout: int = 60) -> str:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    stdout.channel.recv_exit_status()
    return out


def enable_tcp_forwarding(client: paramiko.SSHClient) -> None:
    run(
        client,
        r"""
python3 - <<'PY'
import re, subprocess
from pathlib import Path

paths = [Path("/etc/ssh/sshd_config")]
confd = Path("/etc/ssh/sshd_config.d")
if confd.is_dir():
    paths.extend(sorted(confd.glob("*.conf")))

def sub_line(text, key, value):
    pattern = re.compile(rf"(?im)^\s*#?\s*{re.escape(key)}\s+\S+")
    if pattern.search(text):
        return pattern.sub(f"{key} {value}", text)
    return text.rstrip() + f"\n{key} {value}\n"

changed = False
for path in paths:
    text = path.read_text(encoding="utf-8", errors="replace")
    original = text
    text = sub_line(text, "AllowTcpForwarding", "yes")
    text = sub_line(text, "GatewayPorts", "no")
    if text != original:
        path.write_text(text, encoding="utf-8")
        changed = True

if confd.is_dir():
    (confd / "99-trojan-forward.conf").write_text(
        "AllowTcpForwarding yes\nGatewayPorts no\nPermitListen 127.0.0.1:13306 localhost:13306\n",
        encoding="utf-8",
    )

check = subprocess.run(["sshd", "-t"], capture_output=True, text=True)
if check.returncode != 0:
    raise SystemExit((check.stderr or check.stdout or "sshd -t 失败").strip())
if changed or confd.is_dir():
    for args in (
        ["systemctl", "reload", "ssh"],
        ["systemctl", "reload", "sshd"],
        ["service", "ssh", "reload"],
        ["service", "sshd", "reload"],
    ):
        if subprocess.run(args, capture_output=True, text=True).returncode == 0:
            break
print("ok")
PY
""".strip(),
        timeout=40,
    )


def prepare_reverse_forward(client: paramiko.SSHClient, port: int) -> None:
    mode = run_ok(client, "sshd -T 2>/dev/null | awk '/^allowtcpforwarding/{print $2; exit}'").strip().lower()
    if mode not in {"yes", "all", "remote"}:
        logger.info("远端 sshd AllowTcpForwarding=%s，正在打开反向隧道权限", mode or "未知")
        enable_tcp_forwarding(client)
    run_ok(
        client,
        f"fuser -k {int(port)}/tcp >/dev/null 2>&1 || "
        f"ss -K sport = :{int(port)} >/dev/null 2>&1 || true",
    )


def run_logged(client: paramiko.SSHClient, command: str, on_line: OnLine, timeout: int = 1800) -> int:
    transport = client.get_transport()
    if transport is None:
        raise RuntimeError("SSH 连接已断开")
    channel = transport.open_session()
    channel.settimeout(timeout)
    channel.exec_command(command)
    buffer = ""
    deadline = time.time() + timeout
    while True:
        if time.time() > deadline:
            channel.close()
            raise TimeoutError("远程命令超时")
        if channel.recv_ready():
            buffer += channel.recv(4096).decode("utf-8", errors="replace")
            buffer = _flush_lines(buffer, on_line)
        if channel.recv_stderr_ready():
            buffer += channel.recv_stderr(4096).decode("utf-8", errors="replace")
            buffer = _flush_lines(buffer, on_line)
        if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
            break
        time.sleep(0.05)
    if buffer.strip():
        on_line(buffer.rstrip("\n"))
    return int(channel.recv_exit_status())


def _flush_lines(buffer: str, on_line: OnLine) -> str:
    while "\n" in buffer:
        line, buffer = buffer.split("\n", 1)
        text = line.rstrip("\r")
        if text:
            on_line(text)
    return buffer


def sftp_put_bytes(client: paramiko.SSHClient, data: bytes, remote_path: str) -> None:
    sftp = client.open_sftp()
    try:
        sftp.putfo(io.BytesIO(data), remote_path)
    finally:
        sftp.close()


class ReverseTunnel(threading.Thread):
    def __init__(self, node_id: str, host: str, port: int, username: str, remote_port: int, local_host: str, local_port: int) -> None:
        super().__init__(daemon=True, name=f"tunnel-{host}:{remote_port}")
        self.node_id = node_id
        self.host = host
        self.port = port
        self.username = username
        self.remote_port = remote_port
        self.local_host = local_host
        self.local_port = local_port
        self._stop = threading.Event()
        self.client: paramiko.SSHClient | None = None

    def stop(self) -> None:
        self._stop.set()
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass

    def run(self) -> None:
        delay = 5
        while not self._stop.is_set():
            try:
                self._loop()
                delay = 5
            except Exception as exc:
                logger.warning("反向隧道断开 %s:%s：%s", self.host, self.port, exc)
            if self._stop.is_set():
                break
            time.sleep(delay)
            delay = min(delay * 2, 60)

    def _loop(self) -> None:
        key = Ed25519Key.from_private_key_file(str(node_private_key_path(self.node_id)))
        client = connect(self.host, self.port, self.username, key=key, timeout=20)
        self.client = client
        try:
            prepare_reverse_forward(client, self.remote_port)
        except Exception:
            logger.exception("准备远端反向隧道失败 %s", self.host)
        transport = client.get_transport()
        if transport is None:
            raise RuntimeError("SSH transport 为空")
        transport.set_keepalive(30)
        last_error: Exception | None = None
        for bind in ("127.0.0.1", "localhost", ""):
            try:
                transport.request_port_forward(bind, self.remote_port)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise RuntimeError(
                f"TCP forwarding request denied（请确认远端 sshd AllowTcpForwarding yes，且 {self.remote_port} 未被占用）"
            ) from last_error
        logger.info("已建立反向隧道 %s -> 127.0.0.1:%s", self.host, self.local_port)
        while not self._stop.is_set() and transport.is_active():
            channel = transport.accept(1)
            if channel is None:
                continue
            threading.Thread(target=self._forward, args=(channel,), daemon=True).start()
        try:
            transport.cancel_port_forward("127.0.0.1", self.remote_port)
        except Exception:
            pass
        client.close()

    def _forward(self, channel: paramiko.Channel) -> None:
        try:
            sock = socket.create_connection((self.local_host, self.local_port), timeout=10)
        except OSError:
            channel.close()
            return
        try:
            while not self._stop.is_set():
                readable, _, _ = select.select([sock, channel], [], [], 1)
                if sock in readable:
                    data = sock.recv(32768)
                    if not data:
                        break
                    channel.sendall(data)
                if channel in readable:
                    data = channel.recv(32768)
                    if not data:
                        break
                    sock.sendall(data)
        except Exception:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass
            try:
                channel.close()
            except Exception:
                pass


_tunnels: dict[str, ReverseTunnel] = {}
_tunnel_lock = threading.Lock()


def start_tunnel(vps_id: str, host: str, port: int, username: str) -> None:
    stop_tunnel(vps_id)
    tunnel = ReverseTunnel(
        node_id=vps_id,
        host=host,
        port=port,
        username=username,
        remote_port=settings.trojan_mysql_port,
        local_host=settings.trojan_mysql_host,
        local_port=settings.trojan_mysql_port,
    )
    with _tunnel_lock:
        _tunnels[vps_id] = tunnel
    tunnel.start()


def stop_tunnel(vps_id: str) -> None:
    with _tunnel_lock:
        tunnel = _tunnels.pop(vps_id, None)
    if tunnel is not None:
        tunnel.stop()


def stop_all_tunnels() -> None:
    with _tunnel_lock:
        items = list(_tunnels.items())
        _tunnels.clear()
    for _vps_id, tunnel in items:
        tunnel.stop()
