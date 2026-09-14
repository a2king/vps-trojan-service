#!/usr/bin/env bash
set -euo pipefail

blue()  { echo -e "\033[34m\033[01m$*\033[0m"; }
green() { echo -e "\033[32m\033[01m$*\033[0m"; }
red()   { echo -e "\033[31m\033[01m$*\033[0m"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$SCRIPT_DIR}"
CERT_DIR="/etc/trojan/certs"
ACME_WEBROOT="/var/www/acme"
NODE_HTML="/var/www/node-html"
# shellcheck source=/dev/null
source "${INSTALL_DIR}/deploy/select-camouflage.sh"

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    red "请使用 root 运行：sudo bash install-node.sh"
    exit 1
  fi
}

detect_os() {
  # shellcheck source=/dev/null
  source /etc/os-release
  OS_ID="${ID:-}"
  case "$OS_ID" in
    ubuntu|debian)
      PKG="apt-get"
      NGINX_USER="www-data"
      ;;
    centos|rocky|almalinux|rhel)
      if command -v dnf >/dev/null 2>&1; then PKG="dnf"; else PKG="yum"; fi
      NGINX_USER="nginx"
      ;;
    *)
      red "暂不支持当前系统：$OS_ID"
      exit 1
      ;;
  esac
}

render() {
  local src="$1"
  local dst="$2"
  python3 - "$src" "$dst" <<'PY'
import os
import sys
from pathlib import Path
src, dst = sys.argv[1], sys.argv[2]
text = Path(src).read_text(encoding="utf-8")
repl = {
    "__DOMAIN__": os.environ.get("DOMAIN", ""),
    "__INSTALL_DIR__": os.environ.get("INSTALL_DIR", ""),
    "__NGINX_USER__": os.environ.get("NGINX_USER", "www-data"),
    "__TROJAN_FALLBACK_PASSWORD__": os.environ.get("TROJAN_FALLBACK_PASSWORD", ""),
    "__MYSQL_USERNAME__": os.environ.get("MYSQL_USERNAME", "trojan"),
}
for key, value in repl.items():
    text = text.replace(key, value)
Path(dst).parent.mkdir(parents=True, exist_ok=True)
Path(dst).write_text(text, encoding="utf-8")
PY
}

http_get() {
  local url="$1"
  if command -v curl >/dev/null 2>&1 && curl -4 -fsS --max-time 8 "$url"; then
    return 0
  fi
  if command -v wget >/dev/null 2>&1 && wget -4 -qO- --timeout=8 "$url"; then
    return 0
  fi
  if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys, urllib.request; print(urllib.request.urlopen(sys.argv[1], timeout=8).read().decode().strip())' "$url"; then
    return 0
  fi
  return 1
}

public_ip() {
  http_get https://ipv4.icanhazip.com || http_get https://ifconfig.me
}

resolve_domain() {
  python3 - "$1" <<'PY'
import socket, sys
host = sys.argv[1]
try:
    print(socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0])
except Exception:
    sys.exit(1)
PY
}

check_ports() {
  local busy
  busy="$(ss -lnt 2>/dev/null | awk '{print $4}' | grep -E ':80$' || true)"
  if [[ -n "$busy" ]] && ! pgrep -x nginx >/dev/null 2>&1; then
    red "80 端口已被占用，请先释放后再安装"
    ss -lntp | grep ':80' || true
    exit 1
  fi
  busy="$(ss -lnt 2>/dev/null | awk '{print $4}' | grep -E ':443$' || true)"
  if [[ -n "$busy" ]] && ! pgrep -x trojan >/dev/null 2>&1; then
    red "443 端口已被占用，请先释放后再安装"
    ss -lntp | grep ':443' || true
    exit 1
  fi
}

enable_bbr() {
  green "开启 TCP BBR 加速…"
  local major minor
  major="$(uname -r | cut -d. -f1)"
  minor="$(uname -r | cut -d. -f2)"
  if [[ "${major:-0}" -lt 4 ]] || { [[ "$major" -eq 4 ]] && [[ "${minor:-0}" -lt 9 ]]; }; then
    red "当前内核 $(uname -r) 低于 4.9，无法使用官方 BBR，已跳过"
    return 0
  fi
  modprobe tcp_bbr 2>/dev/null || true
  modprobe sch_fq 2>/dev/null || true
  mkdir -p /etc/modules-load.d /etc/sysctl.d
  install -m 644 "${INSTALL_DIR}/deploy/bbr-modules.conf" /etc/modules-load.d/vps-bbr.conf
  install -m 644 "${INSTALL_DIR}/deploy/bbr-sysctl.conf" /etc/sysctl.d/99-vps-bbr.conf
  sysctl --system >/dev/null 2>&1 || sysctl -p /etc/sysctl.d/99-vps-bbr.conf >/dev/null 2>&1 || true
}

setup_public_dns() {
  green "切换系统 DNS 为 8.8.8.8 / 1.1.1.1 / 223.5.5.5…"
  mkdir -p /etc/systemd/resolved.conf.d
  cat > /etc/systemd/resolved.conf.d/99-vps-dns.conf <<'EOF'
[Resolve]
DNS=8.8.8.8 1.1.1.1 223.5.5.5
FallbackDNS=8.8.4.4 1.0.0.1 223.6.6.6
Domains=~.
EOF
  if systemctl list-unit-files systemd-resolved.service >/dev/null 2>&1; then
    systemctl restart systemd-resolved 2>/dev/null || true
  fi
  if [[ ! -L /etc/resolv.conf ]]; then
    printf 'nameserver 8.8.8.8\nnameserver 1.1.1.1\nnameserver 223.5.5.5\n' > /etc/resolv.conf
  fi
}

setup_timezone() {
  green "设置系统时区为 Asia/Shanghai…"
  if command -v timedatectl >/dev/null 2>&1; then
    timedatectl set-timezone Asia/Shanghai
  elif [[ -e /usr/share/zoneinfo/Asia/Shanghai ]]; then
    ln -sfn /usr/share/zoneinfo/Asia/Shanghai /etc/localtime
    echo Asia/Shanghai > /etc/timezone 2>/dev/null || true
  fi
}

check_dns() {
  local real localip
  if ! localip="$(public_ip | tr -d '[:space:]')" || [[ -z "$localip" ]]; then
    red "无法获取本机公网 IP，请检查出网后再装"
    exit 1
  fi
  real="$(resolve_domain "$DOMAIN" || true)"
  green "本机公网 IP: ${localip}"
  green "域名解析 IP: ${real:-未知}"
  if [[ "$real" != "$localip" ]]; then
    red "域名解析与本机 IP 不一致，已中止。请把 ${DOMAIN} 解析到 ${localip} 后再装。"
    exit 1
  fi
}

# apt 是否真的持有锁（不能靠进程名：pgrep -x apt / unattended-upgr 会误判）
apt_lock_busy() {
  python3 - <<'PY' 2>/dev/null
import fcntl, os, sys
busy = False
for p in (
    "/var/lib/dpkg/lock-frontend",
    "/var/lib/dpkg/lock",
    "/var/lib/apt/lists/lock",
    "/var/cache/apt/archives/lock",
):
    try:
        fd = os.open(p, os.O_RDWR)
    except FileNotFoundError:
        continue
    except OSError:
        busy = True
        continue
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.lockf(fd, fcntl.LOCK_UN)
    except OSError:
        busy = True
    os.close(fd)
sys.exit(1 if busy else 0)
PY
  local rc=$?
  [[ $rc -eq 1 ]] && return 0
  [[ $rc -eq 0 ]] && return 1
  local f ino
  for f in /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock /var/cache/apt/archives/lock; do
    [[ -e "$f" ]] || continue
    ino=$(stat -c %i "$f" 2>/dev/null) || continue
    grep -E ":${ino}[[:space:]]" /proc/locks >/dev/null 2>&1 && return 0
  done
  return 1
}

show_apt_locks() {
  echo "当前 apt/dpkg 锁占用："
  fuser -v /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock /var/cache/apt/archives/lock 2>&1 | sed 's/^/  /' || true
  ps -eo pid,comm,args | grep -E '[a]pt|[d]pkg|[u]nattended' | sed 's/^/  /' || true
}

pause_apt_timers() {
  systemctl stop unattended-upgrades.service apt-daily.service apt-daily-upgrade.service 2>/dev/null || true
  systemctl stop apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
}

wait_for_apt() {
  [[ "${PKG:-}" == "apt-get" ]] || return 0
  pause_apt_timers
  local i
  for i in $(seq 1 90); do
    if ! apt_lock_busy; then
      return 0
    fi
    echo "系统 apt 正忙（锁被占用），等待空闲… ${i}/90"
    if [[ "$i" -eq 1 || $((i % 15)) -eq 0 ]]; then
      show_apt_locks
    fi
    sleep 2
  done
  show_apt_locks
  red "等待 apt 锁超时。可稍后再添加该节点，或到机器上结束占用锁的进程后再装。"
  exit 1
}

apt_cmd() {
  local n=0
  wait_for_apt
  until apt-get "$@"; do
    n=$((n + 1))
    if [[ "$n" -ge 8 ]]; then
      return 1
    fi
    echo "apt 失败，${n}/8 次重试…"
    wait_for_apt
    sleep 3
  done
}

ensure_bootstrap_tools() {
  if command -v curl >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
    return 0
  fi
  green "安装基础工具（curl / python3）…"
  if [[ "$PKG" == "apt-get" ]]; then
    export DEBIAN_FRONTEND=noninteractive
    wait_for_apt
    apt_cmd update -y
    apt_cmd install -y ca-certificates curl wget python3
  else
    $PKG install -y ca-certificates curl wget python3
  fi
}

install_packages() {
  green "安装系统依赖…"
  if [[ "$PKG" == "apt-get" ]]; then
    export DEBIAN_FRONTEND=noninteractive
    wait_for_apt
    apt_cmd update -y
    apt_cmd install -y nginx curl wget unzip zip tar socat cron python3 xz-utils ca-certificates gnupg logrotate libmariadb3
  else
    $PKG install -y nginx curl wget unzip zip tar socat cron python3 xz ca-certificates logrotate mariadb-libs
    if [[ -f /etc/selinux/config ]]; then
      setenforce 0 2>/dev/null || true
      sed -i 's/^SELINUX=enforcing/SELINUX=disabled/' /etc/selinux/config || true
    fi
    if systemctl is-active --quiet firewalld; then
      firewall-cmd --permanent --add-port=80/tcp
      firewall-cmd --permanent --add-port=443/tcp
      firewall-cmd --reload
    fi
  fi
  if systemctl is-active --quiet ufw 2>/dev/null; then
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw reload
  fi
  systemctl enable --now nginx
}

write_nginx() {
  local tpl="$1"
  render "$tpl" /etc/nginx/nginx.conf
  mkdir -p "${ACME_WEBROOT}/.well-known/acme-challenge"
  rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf 2>/dev/null || true
  nginx -t
  systemctl restart nginx
}

write_camouflage() {
  deploy_camouflage "${NODE_HTML}"
}

issue_cert() {
  green "申请 ACME 证书…"
  mkdir -p "${CERT_DIR}"
  if [[ ! -x /root/.acme.sh/acme.sh ]]; then
    curl https://get.acme.sh | sh -s email="${ACME_EMAIL}"
  fi
  /root/.acme.sh/acme.sh --set-default-ca --server letsencrypt
  local reloadcmd='if [ -f /etc/systemd/system/trojan.service ]; then systemctl restart trojan; fi'
  local acme_home="/root/.acme.sh"
  if [[ -d "${acme_home}/${DOMAIN}" || -d "${acme_home}/${DOMAIN}_ecc" ]]; then
    /root/.acme.sh/acme.sh --install-cert -d "${DOMAIN}" \
      --key-file "${CERT_DIR}/private.key" \
      --fullchain-file "${CERT_DIR}/fullchain.cer" \
      --reloadcmd "$reloadcmd" || true
  fi
  local issue_rc=0
  set +e
  /root/.acme.sh/acme.sh --issue -d "${DOMAIN}" -w "${ACME_WEBROOT}"
  issue_rc=$?
  set -e
  if [[ "$issue_rc" -ne 0 && "$issue_rc" -ne 2 ]]; then
    red "证书申请失败（acme.sh 退出码 ${issue_rc}）"
    exit 1
  fi
  /root/.acme.sh/acme.sh --install-cert -d "${DOMAIN}" \
    --key-file "${CERT_DIR}/private.key" \
    --fullchain-file "${CERT_DIR}/fullchain.cer" \
    --reloadcmd "$reloadcmd"
  if [[ ! -s "${CERT_DIR}/fullchain.cer" ]]; then
    red "证书申请失败"
    exit 1
  fi
}

install_trojan_bin() {
  if [[ "$(uname -m)" != "x86_64" ]]; then
    red "当前仅打包了 linux-amd64 的 trojan"
    exit 1
  fi
  local vendor="${INSTALL_DIR}/vendor/trojan"
  local archive
  archive="$(ls -1 "${vendor}"/trojan-*-linux-amd64.tar.xz 2>/dev/null | tail -n1 || true)"
  if [[ -z "${archive}" || ! -f "${archive}" ]]; then
    red "未找到预置包：${vendor}/trojan-*-linux-amd64.tar.xz"
    exit 1
  fi
  green "安装 Trojan（预置包 $(basename "$archive")）…"
  local tmp bin
  tmp="$(mktemp -d)"
  tar -xf "${archive}" -C "$tmp"
  bin="$(find "$tmp" -type f -name trojan | head -n1)"
  if [[ -z "$bin" || ! -f "$bin" ]]; then
    red "预置包里没有 trojan 可执行文件"
    rm -rf "$tmp"
    exit 1
  fi
  install -m 755 "$bin" /usr/local/bin/trojan
  rm -rf "$tmp"
  render "${INSTALL_DIR}/deploy/trojan.json.tpl" /etc/trojan/config.json
  install -m 644 "${INSTALL_DIR}/deploy/trojan-node.service" /etc/systemd/system/trojan.service
  systemctl daemon-reload
}

setup_log() {
  touch /var/log/trojan.log
  chmod 644 /var/log/trojan.log
}

require_root
detect_os

DOMAIN="${DOMAIN:-}"
[[ -n "$DOMAIN" ]] || { red "请设置 DOMAIN"; exit 1; }
ACME_EMAIL="${ACME_EMAIL:-acme@${DOMAIN}}"
MYSQL_USERNAME="${MYSQL_USERNAME:-$DOMAIN}"
TROJAN_FALLBACK_PASSWORD="${TROJAN_FALLBACK_PASSWORD:-node-fallback}"
export DOMAIN INSTALL_DIR NGINX_USER MYSQL_USERNAME TROJAN_FALLBACK_PASSWORD ACME_EMAIL

enable_bbr
setup_public_dns
setup_timezone
check_ports
ensure_bootstrap_tools
check_dns
install_packages
setup_log
write_camouflage
write_nginx "${INSTALL_DIR}/deploy/nginx-bootstrap.conf.tpl"
issue_cert
install_trojan_bin
write_nginx "${INSTALL_DIR}/deploy/nginx-node.conf.tpl"
systemctl enable nginx trojan
systemctl restart nginx
systemctl restart trojan

echo
green "节点安装完成"
green "域名: ${DOMAIN}"
blue "Trojan 认证走本机 127.0.0.1:13306（由主机器反向隧道提供）"
