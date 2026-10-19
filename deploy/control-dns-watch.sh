#!/usr/bin/env bash
# 每隔一段时间由 timer 调用：解析管控域名 A 记录，若指向本机则自动 promote，否则 demote。
# 你只需要在域名面板改 DNS IP；主备机器各自监听即可。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/control-common.sh"
ha_load_env

LOCK_DIR="${HA_LOCK_DIR:-/var/lib/trojan-admin}"
mkdir -p "$LOCK_DIR"
LOCK_FILE="${LOCK_DIR}/dns-watch.lock"

if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    ha_blue "已有 dns-watch 在运行，跳过"
    exit 0
  fi
else
  if ! mkdir "${LOCK_FILE}.d" 2>/dev/null; then
    ha_blue "已有 dns-watch 在运行，跳过"
    exit 0
  fi
  trap 'rmdir "${LOCK_FILE}.d" 2>/dev/null || true' EXIT
fi

dns_name="$(ha_control_dns_name)"
[[ -n "$dns_name" ]] || {
  ha_red "未配置 CONTROL_DNS_NAME，且 .env 无 DOMAIN"
  exit 1
}

local_ip="$(ha_detect_public_ip)" || {
  ha_red "无法确定本机公网 IP，请在 ha.env 设置 CONTROL_LOCAL_IP="
  exit 1
}

mapfile -t dns_ips < <(ha_resolve_a "$dns_name" || true)
if [[ "${#dns_ips[@]}" -eq 0 ]]; then
  ha_red "解析 ${dns_name} 失败，本轮不切换（避免误 demote）"
  exit 1
fi

role="$(ha_current_role)"
ha_blue "DNS ${dns_name} → ${dns_ips[*]}；本机 ${local_ip}；当前角色 ${role}"

if ha_ip_in_list "$local_ip" "${dns_ips[@]}"; then
  if [[ "$role" == "primary" ]]; then
    ha_blue "域名已指向本机且已是 primary，无需操作"
    exit 0
  fi
  ha_green "域名指向本机，自动 promote…"
  if [[ -f "${HA_INCOMING}/trojan.db" ]]; then
    bash "${SCRIPT_DIR}/control-promote.sh"
  else
    bash "${SCRIPT_DIR}/control-promote.sh" --keep-db
  fi
else
  if [[ "$role" != "primary" ]]; then
    ha_blue "域名未指向本机且已是 standby，无需操作"
    exit 0
  fi
  ha_green "域名已离开本机，自动 demote…"
  bash "${SCRIPT_DIR}/control-promote.sh" --demote
fi
