#!/usr/bin/env bash
# 对主控 SQLite 做 online backup。
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


printf '%s\n' "$stamp" >"${outdir}/TIMESTAMP"
rm -rf "$latest"
mkdir -p "$latest"
cp -a "${outdir}/trojan.db" "${latest}/"
cp -a "${outdir}/TIMESTAMP" "${latest}/"

# 保留最近 8 份带时间戳的备份
i=0
while IFS= read -r d; do
  i=$((i + 1))
  [[ "$i" -gt 8 && -d "$d" ]] && rm -rf "$d"
done < <(ls -1dt "${HA_BACKUP_DIR}"/20* 2>/dev/null || true)

ha_green "备份完成: ${latest}（快照 ${stamp}）"
