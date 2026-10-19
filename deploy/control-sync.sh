#!/usr/bin/env bash
# 主控：先 backup，再把 latest 快照 rsync 到备控 HA_INCOMING。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/control-common.sh"
ha_load_env

if [[ "$(ha_current_role)" != "primary" ]]; then
  ha_blue "CONTROL_ROLE=$(ha_current_role)，非 primary，跳过 sync"
  exit 0
fi

ha_require_cmd rsync ssh
[[ -n "${STANDBY_HOST:-}" ]] || {
  ha_red "请在 ha.env 中设置 STANDBY_HOST"
  exit 1
}

bash "${SCRIPT_DIR}/control-backup.sh"

latest="${HA_BACKUP_DIR}/latest"
[[ -f "${latest}/trojan.db" ]] || {
  ha_red "备份目录缺少 trojan.db: ${latest}"
  exit 1
}

ssh_opts=(-p "${STANDBY_PORT}" -o BatchMode=yes -o IdentitiesOnly=yes)
# shellcheck disable=SC2206
ssh_opts+=(${RSYNC_RSH_EXTRA:--o StrictHostKeyChecking=accept-new})
key="${STANDBY_SSH_KEY:-}"
if [[ -z "$key" && -f "${SSH_DIR}/id_ed25519" ]]; then
  key="${SSH_DIR}/id_ed25519"
fi
if [[ -n "$key" ]]; then
  ssh_opts+=(-i "$key")
fi

rsh="ssh"
for o in "${ssh_opts[@]}"; do
  rsh+=" $(printf '%q' "$o")"
done

remote="${STANDBY_USER}@${STANDBY_HOST}:${HA_INCOMING}/"
ha_blue "同步 ${latest}/ → ${remote}"
ssh "${ssh_opts[@]}" "${STANDBY_USER}@${STANDBY_HOST}" "mkdir -p $(printf '%q' "${HA_INCOMING}")"
rsync -az --delete -e "$rsh" "${latest}/" "$remote"

ha_green "已同步到备控 ${STANDBY_USER}@${STANDBY_HOST}:${HA_INCOMING}"
