#!/usr/bin/env bash
set -euo pipefail

blue()  { echo -e "\033[34m\033[01m$*\033[0m"; }
green() { echo -e "\033[32m\033[01m$*\033[0m"; }
red()   { echo -e "\033[31m\033[01m$*\033[0m"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$SCRIPT_DIR}"
CERT_DIR="/etc/trojan/certs"
ACME_WEBROOT="/var/www/acme"
CAMOUFLAGE_HTML="/var/www/camouflage"
# shellcheck source=/dev/null
source "${INSTALL_DIR}/deploy/select-camouflage.sh"

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    red "请使用 root 运行：sudo bash install.sh"
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

rand_hex() {
  local n="${1:-24}"
  head -c "$n" /dev/urandom | od -An -tx1 | tr -d ' \n' | cut -c1-48
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

ask_inputs() {
  echo
  green "请输入已解析到本机的域名"
  read -r -p "域名: " DOMAIN
  [[ -n "${DOMAIN}" ]] || { red "域名不能为空"; exit 1; }

  read -r -p "证书邮箱 [acme@${DOMAIN}]: " ACME_EMAIL
  ACME_EMAIL="${ACME_EMAIL:-acme@${DOMAIN}}"

  read -r -p "管理员用户名 [admin]: " ADMIN_USERNAME
  ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"

  read -r -s -p "管理员密码: " ADMIN_PASSWORD
  echo
  [[ -n "${ADMIN_PASSWORD}" ]] || { red "管理员密码不能为空"; exit 1; }

  export DOMAIN INSTALL_DIR NGINX_USER
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
  if [[ -n "$busy" ]]; then
    red "443 端口已被占用，请先释放后再安装"
    ss -lntp | grep ':443' || true
    exit 1
  fi
}

disable_ipv6() {
  green "关闭 IPv6…"
  mkdir -p /etc/sysctl.d
  install -m 644 "${INSTALL_DIR}/deploy/disable-ipv6.conf" /etc/sysctl.d/99-disable-ipv6.conf
  sysctl -p /etc/sysctl.d/99-disable-ipv6.conf >/dev/null 2>&1 || true
  if [[ "$(sysctl -n net.ipv6.conf.all.disable_ipv6 2>/dev/null || echo 0)" == "1" ]]; then
    green "IPv6 已关闭"
  else
    red "IPv6 未能关闭，请检查内核是否允许 sysctl"
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
  local algo
  algo="$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null || true)"
  if [[ "$algo" == "bbr" ]]; then
    green "BBR 已启用（qdisc=$(sysctl -n net.core.default_qdisc)）"
  else
    red "BBR 未能生效，当前算法：${algo:-未知}。可用算法：$(sysctl -n net.ipv4.tcp_available_congestion_control 2>/dev/null || true)"
  fi
}

setup_public_dns() {
  green "切换系统 DNS 为 8.8.8.8 / 1.1.1.1 / 223.5.5.5…"
  local resolv_body
  resolv_body=$'nameserver 8.8.8.8\nnameserver 1.1.1.1\nnameserver 223.5.5.5\noptions timeout:2 attempts:3\n'

  mkdir -p /etc/systemd/resolved.conf.d
  cat > /etc/systemd/resolved.conf.d/99-vps-dns.conf <<'EOF'
[Resolve]
DNS=8.8.8.8 1.1.1.1 223.5.5.5
FallbackDNS=8.8.4.4 1.0.0.1 223.6.6.6
Domains=~.
EOF

  mkdir -p /etc/cloud/cloud.cfg.d
  cat > /etc/cloud/cloud.cfg.d/99-vps-dns.cfg <<'EOF'
# 禁止 cloud-init 把云厂商 DHCP DNS 写回 resolv.conf
manage_resolv_conf: false
EOF

  if [[ -d /etc/netplan ]]; then
    install -m 644 "${INSTALL_DIR}/deploy/netplan-dns.yaml" /etc/netplan/99-vps-dns.yaml
    netplan generate >/dev/null 2>&1 || true
    netplan apply >/dev/null 2>&1 || true
  fi

  if [[ -d /etc/NetworkManager/conf.d ]]; then
    cat > /etc/NetworkManager/conf.d/99-vps-dns.conf <<'EOF'
[main]
dns=none
EOF
  fi

  if [[ -f /etc/dhcp/dhclient.conf ]]; then
    if ! grep -q "supersede domain-name-servers 8.8.8.8" /etc/dhcp/dhclient.conf; then
      echo "supersede domain-name-servers 8.8.8.8, 1.1.1.1, 223.5.5.5;" >> /etc/dhcp/dhclient.conf
    fi
  fi

  if command -v nmcli >/dev/null 2>&1; then
    local con
    while IFS= read -r con; do
      [[ -n "$con" ]] || continue
      nmcli connection modify "$con" ipv4.ignore-auto-dns yes ipv4.dns "8.8.8.8 1.1.1.1 223.5.5.5" >/dev/null 2>&1 || true
      nmcli connection modify "$con" ipv6.ignore-auto-dns yes >/dev/null 2>&1 || true
    done < <(nmcli -t -f NAME connection show 2>/dev/null || true)
  fi

  if systemctl list-unit-files systemd-resolved.service >/dev/null 2>&1; then
    systemctl restart systemd-resolved 2>/dev/null || true
    if command -v resolvectl >/dev/null 2>&1; then
      local iface
      for iface in /sys/class/net/*; do
        iface="$(basename "$iface")"
        [[ "$iface" == "lo" ]] && continue
        resolvectl dns "$iface" 8.8.8.8 1.1.1.1 223.5.5.5 >/dev/null 2>&1 || true
        resolvectl domain "$iface" '~.' >/dev/null 2>&1 || true
      done
    fi
  fi

  if systemctl is-active --quiet NetworkManager 2>/dev/null; then
    systemctl reload NetworkManager 2>/dev/null || systemctl restart NetworkManager 2>/dev/null || true
  fi

  if [[ -L /etc/resolv.conf ]]; then
    true
  else
    printf '%s' "$resolv_body" > /etc/resolv.conf
  fi

  green "当前 DNS："
  grep -E '^nameserver' /etc/resolv.conf 2>/dev/null || true
  if command -v resolvectl >/dev/null 2>&1; then
    resolvectl dns 2>/dev/null | head -n 20 || true
  fi
}

setup_timezone() {
  green "设置系统时区为 Asia/Shanghai…"
  if command -v timedatectl >/dev/null 2>&1; then
    timedatectl set-timezone Asia/Shanghai
  elif [[ -e /usr/share/zoneinfo/Asia/Shanghai ]]; then
    ln -sfn /usr/share/zoneinfo/Asia/Shanghai /etc/localtime
    echo Asia/Shanghai > /etc/timezone 2>/dev/null || true
  else
    red "未找到时区数据，跳过 timedatectl"
    return 0
  fi
  if [[ -f /etc/sysconfig/clock ]]; then
    if grep -q '^ZONE=' /etc/sysconfig/clock; then
      sed -i 's|^ZONE=.*|ZONE="Asia/Shanghai"|' /etc/sysconfig/clock
    else
      echo 'ZONE="Asia/Shanghai"' >> /etc/sysconfig/clock
    fi
  fi
  green "当前时间：$(date '+%Y-%m-%d %H:%M:%S %z') ($(timedatectl show -p Timezone --value 2>/dev/null || echo Asia/Shanghai))"
}

optimize_network() {
  require_root
  detect_os
  enable_bbr
  disable_ipv6
  setup_public_dns
  setup_timezone
  green "网络优化完成"
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
    red "域名解析与本机 IP 不一致。"
    read -r -p "确认解析无误并强制继续？[y/N]: " yn
    [[ "${yn}" == "y" || "${yn}" == "Y" ]] || exit 1
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
  red "等待 apt 锁超时。请稍后再装，或先结束占用锁的进程。"
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

install_node() {
  if command -v node >/dev/null 2>&1; then
    return
  fi
  green "安装 Node.js…"
  if [[ "$PKG" == "apt-get" ]]; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt_cmd install -y nodejs
  else
    curl -fsSL https://rpm.nodesource.com/setup_22.x | bash -
    $PKG install -y nodejs
  fi
}

install_uv() {
  if ! command -v uv >/dev/null 2>&1; then
    green "安装 uv…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
  export PATH="/root/.local/bin:${HOME}/.local/bin:${PATH}"
}

write_env() {
  mkdir -p "${INSTALL_DIR}/backend/data"
  cat > "${INSTALL_DIR}/.env" <<EOF
ADMIN_USERNAME=${ADMIN_USERNAME}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
JWT_SECRET=${JWT_SECRET}
JWT_EXPIRE_HOURS=168
DOMAIN=${DOMAIN}
TROJAN_PORT=443
DATABASE_URL=sqlite:///./data/trojan.db
CONTROL_ROLE=primary
TROJAN_MYSQL_HOST=127.0.0.1
TROJAN_MYSQL_PORT=13306
TRAFFIC_INTERVAL_SECONDS=300
IP_INGEST_INTERVAL_SECONDS=3600
LOG_RETENTION_DAYS=31
TIMEZONE=Asia/Shanghai
TROJAN_LOG_PATH=/var/log/trojan.log
EOF
  chmod 600 "${INSTALL_DIR}/.env"
}

setup_backend() {
  green "安装 Python 管理后端…"
  cd "${INSTALL_DIR}/backend"
  uv python install 3.12
  uv sync --python 3.12
}

build_frontend() {
  green "构建前端…"
  cd "${INSTALL_DIR}/frontend"
  npm install
  npm run build
}

link_frontend() {
  local dest="${INSTALL_DIR}/frontend/dist"
  local html="/usr/share/nginx/html"
  mkdir -p "$(dirname "$html")" "$dest"
  if [[ -L "$html" ]]; then
    ln -sfn "$dest" "$html"
  elif [[ -e "$html" ]]; then
    mv "$html" "${html}.bak.$(date +%s)"
    ln -sfn "$dest" "$html"
  else
    ln -sfn "$dest" "$html"
  fi
  green "已将 ${html} 软连接到 ${dest}"
}

setup_log_retention() {
  green "配置日志最多保留 30 天…"
  touch /var/log/trojan.log /var/log/trojan-admin.log
  chmod 644 /var/log/trojan.log /var/log/trojan-admin.log
  install -m 644 "${INSTALL_DIR}/deploy/logrotate.conf" /etc/logrotate.d/vps-trojan
  mkdir -p /etc/systemd/journald.conf.d
  install -m 644 "${INSTALL_DIR}/deploy/journald-retention.conf" /etc/systemd/journald.conf.d/vps-trojan.conf
  systemctl restart systemd-journald 2>/dev/null || true
  journalctl --vacuum-time=30d >/dev/null 2>&1 || true
}

write_nginx() {
  local tpl="$1"
  render "$tpl" /etc/nginx/nginx.conf
  mkdir -p "${ACME_WEBROOT}/.well-known/acme-challenge"
  rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf 2>/dev/null || true
  nginx -t
  systemctl restart nginx
}

issue_cert() {
  green "申请 ACME 证书…"
  mkdir -p "${CERT_DIR}"
  if [[ ! -x /root/.acme.sh/acme.sh ]]; then
    curl https://get.acme.sh | sh -s email="${ACME_EMAIL}"
  fi
  /root/.acme.sh/acme.sh --set-default-ca --server letsencrypt

  # 证书签发时还没装 trojan.service。reloadcmd 必须在 unit 不存在时成功返回，
  # 否则 acme.sh 非 0 退出会让 set -e 直接中断，后续永远装不上 Trojan。
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
  /root/.acme.sh/acme.sh --issue -d "${DOMAIN}" -w "${ACME_WEBROOT}" ${1:+--force}
  issue_rc=$?
  set -e
  # 0=成功，2=证书已存在/未到续期时间，均可继续安装
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
  export TROJAN_FALLBACK_PASSWORD
  render "${INSTALL_DIR}/deploy/trojan.json.tpl" /etc/trojan/config.json
  install -m 644 "${INSTALL_DIR}/deploy/trojan.service" /etc/systemd/system/trojan.service
  systemctl daemon-reload
}

wait_for_admin() {
  local i
  for i in $(seq 1 20); do
    if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  red "管理后端未能在 20 秒内启动"
  systemctl status trojan-admin --no-pager || true
  exit 1
}

setup_admin_service() {
  render "${INSTALL_DIR}/deploy/trojan-admin.service.tpl" /etc/systemd/system/trojan-admin.service
  systemctl daemon-reload
  systemctl enable nginx trojan-admin trojan
  green "启动管理后端…"
  systemctl restart trojan-admin
  wait_for_admin
  green "启动 Trojan…"
  systemctl restart trojan
  systemctl restart nginx
}

load_installed_env() {
  local env_file="${INSTALL_DIR}/.env"
  if [[ ! -f "$env_file" ]]; then
    red "未找到 ${env_file}，请先完成安装"
    exit 1
  fi
  set -a
  # shellcheck source=/dev/null
  source "$env_file"
  set +a
  [[ -n "${DOMAIN:-}" ]] || { red ".env 里没有 DOMAIN"; exit 1; }
  export DOMAIN INSTALL_DIR NGINX_USER
}

redeploy() {
  require_root
  detect_os
  load_installed_env
  export PATH="/root/.local/bin:${HOME}/.local/bin:${PATH}"

  green "更新并重启（前端打包 + nginx 配置 + 后端 + nginx）"
  disable_ipv6
  setup_timezone
  install_node
  install_uv
  setup_backend
  build_frontend
  link_frontend
  deploy_camouflage "${CAMOUFLAGE_HTML}"

  green "更新 nginx 配置…"
  write_nginx "${INSTALL_DIR}/deploy/nginx.conf.tpl"

  if [[ -f /etc/systemd/system/trojan.service ]]; then
    green "更新 Trojan 服务（固定日志时区 Asia/Shanghai）…"
    install -m 644 "${INSTALL_DIR}/deploy/trojan.service" /etc/systemd/system/trojan.service
  fi
  if [[ -f /etc/systemd/system/trojan-admin.service ]]; then
    render "${INSTALL_DIR}/deploy/trojan-admin.service.tpl" /etc/systemd/system/trojan-admin.service
  fi
  systemctl daemon-reload

  green "重启管理后端…"
  systemctl restart trojan-admin
  wait_for_admin

  if [[ -f /etc/systemd/system/trojan.service ]]; then
    green "重启 Trojan…"
    systemctl restart trojan
  fi

  green "重启 nginx…"
  systemctl restart nginx

  echo
  green "更新完成"
  green "伪装首页: https://${DOMAIN}/"
  green "管理后台: https://${DOMAIN}/admin"
  green "Clash 订阅: https://${DOMAIN}/config/<用户密码>"
}

install_all() {
  require_root
  detect_os
  enable_bbr
  disable_ipv6
  setup_public_dns
  setup_timezone
  ask_inputs
  check_ports
  ensure_bootstrap_tools
  check_dns

  JWT_SECRET="$(rand_hex 24)"
  TROJAN_FALLBACK_PASSWORD="$(rand_hex 16)"
  MYSQL_USERNAME="${MYSQL_USERNAME:-trojan}"
  export TROJAN_FALLBACK_PASSWORD JWT_SECRET DOMAIN INSTALL_DIR NGINX_USER MYSQL_USERNAME

  install_packages
  install_node
  install_uv
  write_env
  setup_backend
  build_frontend
  link_frontend
  deploy_camouflage "${CAMOUFLAGE_HTML}"
  setup_log_retention
  write_nginx "${INSTALL_DIR}/deploy/nginx-bootstrap.conf.tpl"
  issue_cert
  install_trojan_bin
  write_nginx "${INSTALL_DIR}/deploy/nginx.conf.tpl"
  setup_admin_service

  echo
  green "安装完成"
  green "伪装首页: https://${DOMAIN}/"
  green "管理后台: https://${DOMAIN}/admin"
  green "管理员:   ${ADMIN_USERNAME}"
  echo
  blue "客户端使用管理后台里生成的密码，分享链接格式为："
  echo "  trojan://密码@${DOMAIN}:443#用户名"
}

repair_cert() {
  require_root
  detect_os
  read -r -p "域名: " DOMAIN
  read -r -p "证书邮箱 [acme@${DOMAIN}]: " ACME_EMAIL
  ACME_EMAIL="${ACME_EMAIL:-acme@${DOMAIN}}"
  export DOMAIN INSTALL_DIR NGINX_USER
  NGINX_USER="${NGINX_USER}"
  systemctl stop trojan || true
  write_nginx "${INSTALL_DIR}/deploy/nginx-bootstrap.conf.tpl"
  issue_cert force
  write_nginx "${INSTALL_DIR}/deploy/nginx.conf.tpl"
  systemctl restart trojan
  green "证书修复完成"
}

remove_all() {
  require_root
  detect_os
  red "将停止 Trojan / 管理后端。SQLite 数据文件默认保留。"
  read -r -p "确认卸载？[y/N]: " yn
  [[ "${yn}" == "y" || "${yn}" == "Y" ]] || exit 0
  systemctl disable --now trojan trojan-admin 2>/dev/null || true
  rm -f /etc/systemd/system/trojan.service /etc/systemd/system/trojan-admin.service
  rm -f /etc/logrotate.d/vps-trojan /etc/systemd/journald.conf.d/vps-trojan.conf
  systemctl daemon-reload
  systemctl disable --now nginx 2>/dev/null || true
  if [[ -L /usr/share/nginx/html ]]; then
    rm -f /usr/share/nginx/html
  fi
  rm -rf /etc/trojan
  rm -f /usr/local/bin/trojan
  green "服务已卸载。项目目录与 SQLite 数据未删除。"
}

menu() {
  clear
  green "=============================================="
  green " Northline / Trojan 个性化安装"
  green " 架构: nginx + acme.sh + trojan + 管理后台"
  green "=============================================="
  echo
  echo " 1. 安装"
  echo " 2. 卸载"
  echo " 3. 修复证书"
  echo " 4. 更新并重启（前端 + nginx 配置 + 后端）"
  echo " 5. 开启 BBR、公共 DNS 与上海时区"
  echo " 0. 退出"
  echo
  read -r -p "请输入数字: " num
  case "$num" in
    1) install_all ;;
    2) remove_all ;;
    3) repair_cert ;;
    4) redeploy ;;
    5) optimize_network ;;
    0) exit 0 ;;
    *) red "请输入正确数字"; sleep 1; menu ;;
  esac
}

menu
