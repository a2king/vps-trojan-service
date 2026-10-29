#!/usr/bin/env bash
# 在备控上执行：套用 HA_INCOMING 快照、切 CONTROL_ROLE=primary、重启 trojan-admin 以重建隧道。
# 切换前请先：1) 确认旧主已停或不可用  2) 把管控域名 DNS 指到本机
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/control-common.sh"
ha_load_env

APPLY_ONLY=0
DEMOTE=0
KEEP_DB=0
FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply-only) APPLY_ONLY=1 ;;
    --demote) DEMOTE=1 ;;
    --keep-db) KEEP_DB=1 ;;
    --force) FORCE=1 ;;
    -h | --help)
      cat <<EOF
用法: control-promote.sh [--apply-only] [--demote] [--keep-db] [--force]

  默认（promote）: 套用 ${HA_INCOMING} → 数据目录，CONTROL_ROLE=primary，重启服务
  --apply-only     只把 incoming 拷到数据目录，不改角色、不重启
  --keep-db        无 incoming 时沿用本机已有 trojan.db（DNS 自动切换用）
  --force          已是 primary 时仍套用快照并重启
  --demote         本机降为 standby：停隧道、CONTROL_ROLE=standby、重启（旧主修复回切时用）
EOF
      exit 0
      ;;
    *)
      ha_red "未知参数: $1"
      exit 1
      ;;
  esac
  shift
done

if [[ "$DEMOTE" -eq 1 ]]; then
  if [[ "$(ha_current_role)" == "standby" ]]; then
    ha_blue "已是 standby，跳过 demote"
    exit 0
  fi
  ha_blue "降级为本机 standby…"
  ha_stop_tunnels
  ha_set_control_role standby "$ENV_FILE"
  if systemctl is-active --quiet trojan-admin 2>/dev/null; then
    systemctl restart trojan-admin
  fi
  ha_green "已降级为 standby（CONTROL_ROLE=standby）。请确认不会再有流量写本库，并让新主向本机 sync。"
  exit 0
fi

if [[ "$(ha_current_role)" == "primary" && "$APPLY_ONLY" -eq 0 && "$FORCE" -eq 0 ]]; then
  ha_blue "已是 primary，跳过 promote（需要刷新库时加 --force）"
  exit 0
fi

USE_INCOMING=0
if [[ -f "${HA_INCOMING}/trojan.db" ]]; then
  USE_INCOMING=1
elif [[ "$KEEP_DB" -eq 1 && -f "$DB_PATH" ]]; then
  ha_blue "无 incoming，沿用本机库 ${DB_PATH}"
elif [[ "$KEEP_DB" -eq 1 ]]; then
  ha_red "无 incoming 且本机也没有 ${DB_PATH}"
  exit 1
else
  ha_red "缺少同步库: ${HA_INCOMING}/trojan.db（请先在主控跑 control-sync.sh，或加 --keep-db）"
  exit 1
fi

ha_blue "停止隧道与 trojan-admin…"
ha_stop_tunnels
systemctl stop trojan-admin 2>/dev/null || true

mkdir -p "$DATA_DIR" "$SSH_DIR"
if [[ "$USE_INCOMING" -eq 1 ]]; then
  ha_blue "套用快照 → ${DATA_DIR}"
  cp -a "${HA_INCOMING}/trojan.db" "${DB_PATH}"
  chmod 600 "${DB_PATH}"
  # 去掉可能残留的 WAL，避免旧会话文件干扰
  rm -f "${DB_PATH}-wal" "${DB_PATH}-shm"

  if [[ -d "${HA_INCOMING}/ssh" ]]; then
    mkdir -p "$SSH_DIR"
    if [[ -f "${HA_INCOMING}/ssh/id_ed25519" ]]; then
      cp -a "${HA_INCOMING}/ssh/id_ed25519" "${SSH_DIR}/"
      chmod 600 "${SSH_DIR}/id_ed25519"
    fi
    if [[ -f "${HA_INCOMING}/ssh/id_ed25519.pub" ]]; then
      cp -a "${HA_INCOMING}/ssh/id_ed25519.pub" "${SSH_DIR}/"
    fi
  fi
fi

# 主备快照来自旧主，promote 后由部署脚本把当前机器标回本机，避免沿用旧主 is_local。
ha_retarget_local_db "$(ha_env_get DOMAIN)"

if [[ "$APPLY_ONLY" -eq 1 ]]; then
  ha_green "已套用快照（未改角色、未启动服务）"
  exit 0
fi

ha_set_control_role primary "$ENV_FILE"
ha_blue "启动 trojan-admin（primary，将重建反向隧道）…"
systemctl start trojan-admin
sleep 2
if systemctl is-active --quiet trojan-admin; then
  ha_green "promote 完成。请确认管控域名已指向本机，并抽查 /api/health 与子节点鉴权。"
else
  ha_red "trojan-admin 未能保持运行，请检查 journalctl -u trojan-admin"
  exit 1
fi
