"""Read-only, independently authenticated observation of a replica."""

import hashlib
import json
import secrets
import shlex
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.config import get_settings
from app.models import Vps
from app.services import ssh

settings = get_settings()
_token_lock = threading.Lock()


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def token_path() -> Path:
    return settings.sqlite_path.with_name("node-read.token")


def read_token() -> str:
    try:
        return token_path().read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return ""


def _request(vps: Vps, token: str, action: str, query: dict) -> dict:
    url = f"https://{vps.id}/api/node-read/{action}?{urlencode(query)}"
    request = Request(url, headers={"X-Node-Read-Token": token})
    with build_opener(NoRedirects()).open(request, timeout=12) as response:
        return json.load(response)


def _get_remote_token(vps: Vps, path: Path, rejected_token: str = "") -> str:
    with _token_lock:
        try:
            token = path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            token = ""
        if token and token != rejected_token:
            return token
        client = ssh.connect_node(vps)
        try:
            initialize = f'test -s "$1" || printf %s {shlex.quote(secrets.token_hex(32))} > "$1"; cat "$1"'
            command = (
                'set -eu; umask 077; '
                'root="$(systemctl show -p WorkingDirectory --value trojan-admin)"; '
                'case "$root" in /*/backend) ;; *) exit 1 ;; esac; '
                'token="$root/data/node-read.token"; '
                f'flock "$token.lock" sh -ec {shlex.quote(initialize)} sh "$token"'
            )
            token = ssh.run(client, command).strip()
        finally:
            client.close()
        if len(token) != 64 or any(character not in "0123456789abcdef" for character in token):
            raise RuntimeError("节点未返回有效的观测凭据")
        path.write_text(token, encoding="ascii")
        path.chmod(0o600)
        return token


def read_remote(vps: Vps, action: str, **query) -> dict:
    directory = ssh.ssh_dir() / "read-tokens"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / hashlib.sha256(vps.id.encode()).hexdigest()
    token = _get_remote_token(vps, path)
    try:
        return _request(vps, token, action, query)
    except HTTPError as exc:
        if exc.code != 401:
            raise
    return _request(vps, _get_remote_token(vps, path, rejected_token=token), action, query)


def read_legacy_log(vps: Vps, *, cursor: dict | None = None, limit: int = 500) -> dict:
    source = Path(__file__).with_name("runtime_logs.py").read_text(encoding="utf-8")
    script = (
        source + "\nimport json, sys\n"
        "data = json.loads(sys.argv[1])\n"
        "path = Path('/var/log/trojan.log')\n"
        "result = read_chunk(path, **data['cursor']) if data['cursor'] is not None else {'lines': tail_log(path, data['limit'])}\n"
        "print(json.dumps(result))"
    )
    arguments = json.dumps({"cursor": cursor, "limit": limit})
    client = ssh.connect_node(vps, timeout=12)
    try:
        return json.loads(ssh.run(client, f"python3 -c {shlex.quote(script)} {shlex.quote(arguments)}", timeout=30))
    finally:
        client.close()
