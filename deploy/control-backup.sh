#!/usr/bin/env bash
# 对主控 SQLite 做 online backup，并打包 SSH 管控密钥到 HA_BACKUP_DIR。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/control-common.sh"
ha_load_env

ha_require_cmd python3
stamp="$(date +%Y%m%d-%H%M%S)"
outdir="${HA_BACKUP_DIR}/${stamp}"
latest="${HA_BACKUP_DIR}/latest"

mkdir -p "$outdir"
ha_blue "备份数据库 ${DB_PATH} → ${outdir}/trojan.db"
ha_sqlite_backup "$DB_PATH" "${outdir}/trojan.db"

if [[ -d "$SSH_DIR" ]]; then
  mkdir -p "${outdir}/ssh"
  if [[ -f "${SSH_DIR}/id_ed25519" ]]; then
    cp -a "${SSH_DIR}/id_ed25519" "${outdir}/ssh/"
    chmod 600 "${outdir}/ssh/id_ed25519"
  fi
  if [[ -f "${SSH_DIR}/id_ed25519.pub" ]]; then
    cp -a "${SSH_DIR}/id_ed25519.pub" "${outdir}/ssh/"
  fi
else
  ha_red "警告: 未找到 SSH 目录 ${SSH_DIR}（备机 promote 后将无法拉隧道）"
fi

printf '%s\n' "$stamp" >"${outdir}/TIMESTAMP"
rm -rf "$latest"
mkdir -p "$latest"
cp -a "${outdir}/trojan.db" "${latest}/"
[[ -d "${outdir}/ssh" ]] && cp -a "${outdir}/ssh" "${latest}/"
cp -a "${outdir}/TIMESTAMP" "${latest}/"

# 保留最近 8 份带时间戳的备份
i=0
while IFS= read -r d; do
  i=$((i + 1))
  [[ "$i" -gt 8 && -d "$d" ]] && rm -rf "$d"
done < <(ls -1dt "${HA_BACKUP_DIR}"/20* 2>/dev/null || true)

ha_green "备份完成: ${latest}（快照 ${stamp}）"
