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

public_ip() {
  curl -4 -fsS --max-time 8 https://ipv4.icanhazip.com || curl -4 -fsS --max-time 8 https://ifconfig.me
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
  localip="$(public_ip | tr -d '[:space:]')"
  real="$(resolve_domain "$DOMAIN" || true)"
  green "本机公网 IP: ${localip}"
  green "域名解析 IP: ${real:-未知}"
  if [[ "$real" != "$localip" ]]; then
    red "域名解析与本机 IP 不一致，已中止。请把 ${DOMAIN} 解析到 ${localip} 后再装。"
    exit 1
  fi
}

install_packages() {
  green "安装系统依赖…"
  if [[ "$PKG" == "apt-get" ]]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y nginx curl wget unzip zip tar socat cron python3 xz-utils ca-certificates gnupg logrotate libmariadb3
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
  mkdir -p "${NODE_HTML}"
  cat > "${NODE_HTML}/index.html" <<EOF
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>${DOMAIN}</title></head>
<body style="font-family:sans-serif;padding:48px;color:#333">
  <h1>It works</h1>
  <p>${DOMAIN}</p>
</body>
</html>
EOF
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
    red "当前仅支持 linux-amd64 的 trojan"
    exit 1
  fi
  green "安装 Trojan…"
  local tmp latest url proxy
  tmp="$(mktemp -d)"
  proxy="${GITHUB_PROXY:-}"
  latest="$(curl -fsSL https://api.github.com/repos/trojan-gfw/trojan/releases/latest | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"].lstrip("v"))')"
  url="${proxy}https://github.com/trojan-gfw/trojan/releases/download/v${latest}/trojan-${latest}-linux-amd64.tar.xz"
  wget -O "${tmp}/trojan.tar.xz" "$url"
  tar -xf "${tmp}/trojan.tar.xz" -C "$tmp"
  install -m 755 "${tmp}/trojan/trojan" /usr/local/bin/trojan
  rm -rf "$tmp"
  render "${INSTALL_DIR}/deploy/trojan.json.tpl" /etc/trojan/config.json
  install -m 644 "${INSTALL_DIR}/deploy/trojan-node.service" /etc/systemd/system/trojan.service
  systemctl daemon-reload
}

setup_log() {
  touch /var/log/trojan.log
  chmod 640 /var/log/trojan.log
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
