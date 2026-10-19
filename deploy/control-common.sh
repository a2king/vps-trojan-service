#!/usr/bin/env bash
# 主备 HA 脚本共用函数。由 control-*.sh source，勿直接执行。

set -euo pipefail

_HA_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ha_load_env() {
  local repo_ha
  repo_ha="$(cd "${_HA_COMMON_DIR}/.." && pwd)/deploy/ha.env"
  local candidates=(
    "${HA_ENV:-}"
    /etc/trojan-admin/ha.env
    "${_HA_COMMON_DIR}/ha.env"
    "$repo_ha"
  )
  local f
  for f in "${candidates[@]}"; do
    [[ -n "$f" && -f "$f" ]] || continue
    # shellcheck disable=SC1090
    source "$f"
    break
  done

  INSTALL_DIR="${INSTALL_DIR:-/opt/vps-trojan-service}"
  HA_BACKUP_DIR="${HA_BACKUP_DIR:-/var/lib/trojan-admin/ha-backup}"
  HA_INCOMING="${HA_INCOMING:-/var/lib/trojan-admin/ha-incoming}"
  STANDBY_PORT="${STANDBY_PORT:-22}"
  STANDBY_USER="${STANDBY_USER:-root}"
  DATA_DIR="${DATA_DIR:-${INSTALL_DIR}/backend/data}"
  ENV_FILE="${ENV_FILE:-${INSTALL_DIR}/.env}"
  DB_PATH="${DB_PATH:-${DATA_DIR}/trojan.db}"
  SSH_DIR="${SSH_DIR:-${DATA_DIR}/ssh}"
  DNS_RESOLVERS="${DNS_RESOLVERS:-1.1.1.1 8.8.8.8}"
}

ha_env_get() {
  local key="$1"
  local file="${2:-$ENV_FILE}"
  [[ -f "$file" ]] || return 0
  local line
  line="$(grep -E "^${key}=" "$file" | tail -n1 || true)"
  [[ -n "$line" ]] || return 0
  printf '%s\n' "${line#*=}"
}

ha_current_role() {
  local role
  role="$(ha_env_get CONTROL_ROLE | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
  printf '%s\n' "${role:-primary}"
}

ha_control_dns_name() {
  local name="${CONTROL_DNS_NAME:-}"
  if [[ -z "$name" ]]; then
    name="$(ha_env_get DOMAIN)"
  fi
  name="${name%%/*}"
  name="${name#http://}"
  name="${name#https://}"
  name="${name%%:*}"
  printf '%s\n' "$name"
}

ha_resolve_a() {
  local host="$1"
  local resolver out
  for resolver in $DNS_RESOLVERS; do
    if command -v dig >/dev/null 2>&1; then
      out="$(dig +short +time=3 +tries=1 A "$host" "@${resolver}" 2>/dev/null | grep -E '^[0-9.]+$' || true)"
    elif command -v getent >/dev/null 2>&1; then
      out="$(getent ahostsv4 "$host" 2>/dev/null | awk '{print $1}' | grep -E '^[0-9.]+$' || true)"
    else
      out="$(python3 - "$host" <<'PY'
import socket, sys
host = sys.argv[1]
try:
    infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
except OSError:
    sys.exit(0)
seen = set()
for info in infos:
    ip = info[4][0]
    if ip not in seen:
        seen.add(ip)
        print(ip)
PY
)"
    fi
    if [[ -n "${out:-}" ]]; then
      printf '%s\n' "$out" | sort -u
      return 0
    fi
  done
  return 1
}

ha_detect_public_ip() {
  if [[ -n "${CONTROL_LOCAL_IP:-}" ]]; then
    printf '%s\n' "$CONTROL_LOCAL_IP"
    return 0
  fi
  local url ip
  for url in https://api.ipify.org https://ifconfig.me/ip https://ipv4.icanhazip.com; do
    ip="$(curl -4 -fsS --max-time 5 "$url" 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      printf '%s\n' "$ip"
      return 0
    fi
  done
  return 1
}

ha_ip_in_list() {
  local needle="$1"
  local item
  shift
  for item in "$@"; do
    [[ "$item" == "$needle" ]] && return 0
  done
  return 1
}

ha_red() { printf '\033[31m%s\033[0m\n' "$*" >&2; }
ha_green() { printf '\033[32m%s\033[0m\n' "$*"; }
ha_blue() { printf '\033[34m%s\033[0m\n' "$*"; }

ha_require_cmd() {
  local c
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || {
      ha_red "缺少命令: $c"
      exit 1
    }
  done
}

ha_sqlite_backup() {
  local src="$1"
  local dest="$2"
  [[ -f "$src" ]] || {
    ha_red "找不到数据库: $src"
    exit 1
  }
  mkdir -p "$(dirname "$dest")"
  rm -f "$dest"
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$src" ".backup '$dest'"
  else
    python3 - "$src" "$dest" <<'PY'
import sqlite3, sys
src, dest = sys.argv[1], sys.argv[2]
src_conn = sqlite3.connect(src)
dest_conn = sqlite3.connect(dest)
try:
    src_conn.backup(dest_conn)
finally:
    dest_conn.close()
    src_conn.close()
PY
  fi
  chmod 600 "$dest"
}

ha_set_control_role() {
  local role="$1"
  local file="$2"
  [[ -f "$file" ]] || {
    ha_red "找不到 .env: $file"
    exit 1
  }
  if grep -qE '^CONTROL_ROLE=' "$file"; then
    sed -i.bak -E "s/^CONTROL_ROLE=.*/CONTROL_ROLE=${role}/" "$file"
    rm -f "${file}.bak"
  else
    printf '\nCONTROL_ROLE=%s\n' "$role" >>"$file"
  fi
}

ha_stop_tunnels() {
  if command -v systemctl >/dev/null 2>&1; then
    local unit
    while read -r unit; do
      [[ -n "$unit" ]] || continue
      systemctl disable --now "$unit" 2>/dev/null || true
    done < <(systemctl list-units --type=service --all --no-legend 'trojan-tunnel@*' 2>/dev/null | awk '{print $1}')
  fi
}
