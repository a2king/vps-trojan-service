#!/usr/bin/env bash
# 生成完整冷备包，并可通过 rsync 推送到一个完整备控。
# 多个备控由调用方（后台广播任务）分别调用本脚本，或直接复用 latest 目录。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/control-common.sh"
ha_load_env

REMOTE_HOST="${COLD_BACKUP_HOST:-${STANDBY_HOST:-}}"
REMOTE_PORT="${COLD_BACKUP_PORT:-${STANDBY_PORT:-22}}"
REMOTE_USER="${COLD_BACKUP_USER:-${STANDBY_USER:-root}}"
REMOTE_KEY="${COLD_BACKUP_SSH_KEY:-${STANDBY_SSH_KEY:-}}"
REMOTE_ROOT="${COLD_BACKUP_REMOTE_ROOT:-/opt/data/trojan-backup}"
LOCAL_ROOT="${HA_COLD_BACKUP_DIR:-${HA_BACKUP_DIR}/cold}"
DO_PUSH=0

usage() {
  cat <<EOF
用法: control-cold-backup.sh [选项]

  生成 ${LOCAL_ROOT}/latest 完整冷备包；指定 --host 时同步到一个备控。
  --host HOST       远端 SSH 主机（默认 COLD_BACKUP_HOST/STANDBY_HOST）
  --port PORT       SSH 端口（默认 COLD_BACKUP_PORT/STANDBY_PORT/22）
  --user USER       SSH 用户（默认 COLD_BACKUP_USER/STANDBY_USER/root）
  --key FILE        SSH 私钥（默认 COLD_BACKUP_SSH_KEY/STANDBY_SSH_KEY/data/ssh/id_ed25519）
  --remote-root DIR 远端根目录（默认 /opt/data/trojan-backup）
  --local-only      只生成本地冷备，不上传
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) [[ $# -ge 2 ]] || { ha_red "--host 缺少参数"; exit 2; }; REMOTE_HOST="$2"; DO_PUSH=1; shift 2 ;;
    --port) [[ $# -ge 2 ]] || { ha_red "--port 缺少参数"; exit 2; }; REMOTE_PORT="$2"; shift 2 ;;
    --user) [[ $# -ge 2 ]] || { ha_red "--user 缺少参数"; exit 2; }; REMOTE_USER="$2"; shift 2 ;;
    --key) [[ $# -ge 2 ]] || { ha_red "--key 缺少参数"; exit 2; }; REMOTE_KEY="$2"; shift 2 ;;
    --remote-root) [[ $# -ge 2 ]] || { ha_red "--remote-root 缺少参数"; exit 2; }; REMOTE_ROOT="$2"; shift 2 ;;
    --local-only) DO_PUSH=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) ha_red "未知参数: $1"; usage >&2; exit 2 ;;
  esac
done

if [[ "$(ha_current_role)" != "primary" ]]; then
  ha_blue "CONTROL_ROLE=$(ha_current_role)，非 primary，跳过冷备广播"
  exit 0
fi

ha_require_cmd python3
[[ -f "$DB_PATH" ]] || { ha_red "找不到数据库: ${DB_PATH}"; exit 1; }
[[ -f "$ENV_FILE" ]] || { ha_red "找不到安装根 .env: ${ENV_FILE}"; exit 1; }
stamp="$(date +%Y%m%d-%H%M%S)"
outdir="${LOCAL_ROOT}/${stamp}"
latest="${LOCAL_ROOT}/latest"
mkdir -p "$LOCAL_ROOT" "$outdir"
chmod 700 "$LOCAL_ROOT" "$outdir"
ha_blue "生成完整冷备包 ${outdir}"
ha_sqlite_backup "$DB_PATH" "${outdir}/trojan.db"

if [[ -d "$SSH_DIR" ]]; then
  mkdir -p "${outdir}/backend/data/ssh"
  chmod 700 "${outdir}/backend" "${outdir}/backend/data" "${outdir}/backend/data/ssh"
  for key in id_ed25519 id_ed25519.pub; do
    if [[ -f "${SSH_DIR}/${key}" ]]; then
      cp -a "${SSH_DIR}/${key}" "${outdir}/backend/data/ssh/"
    fi
  done
  [[ -f "${outdir}/backend/data/ssh/id_ed25519" ]] && chmod 600 "${outdir}/backend/data/ssh/id_ed25519"
  [[ -f "${outdir}/backend/data/ssh/id_ed25519.pub" ]] && chmod 644 "${outdir}/backend/data/ssh/id_ed25519.pub"
fi
if [[ -f "$ENV_FILE" ]]; then
  cp -a "$ENV_FILE" "${outdir}/.env"
  chmod 600 "${outdir}/.env"
fi
ha_env_file="$(ha_env_file_path 2>/dev/null || true)"
if [[ -n "$ha_env_file" && -f "$ha_env_file" ]]; then
  cp -a "$ha_env_file" "${outdir}/ha.env"
  chmod 600 "${outdir}/ha.env"
fi
printf '%s\n' "$stamp" >"${outdir}/TIMESTAMP"
chmod 600 "${outdir}/trojan.db" "${outdir}/TIMESTAMP"
cat >"${outdir}/RESTORE.md" <<'EOF'
# Trojan 管控完整冷备恢复

本目录包含 SQLite 数据库、管控 SSH 密钥、安装根 `.env` 和可选 `ha.env`。
账号、规则、隧道及节点关系都在 `trojan.db` 中。

从零恢复：完成同版本安装后停止 `trojan-admin`，将 `.env` 放回安装根；将
`trojan.db` 放到 `backend/data/trojan.db`（权限 600），将 `backend/data/ssh/`
放回对应目录（目录 700、私钥 600）；若有 `ha.env`，复制到
`/etc/trojan-admin/ha.env`（权限 600），检查 `INSTALL_DIR` 和 `CONTROL_ROLE`，
然后执行 `systemctl daemon-reload && systemctl enable --now trojan-admin`。
不要让两台机器同时以 `primary` 写库。
EOF
chmod 600 "${outdir}/RESTORE.md"
rm -rf "$latest"
cp -a "$outdir" "$latest"
chmod 700 "$latest"
i=0
while IFS= read -r d; do
  [[ -n "$d" && -d "$d" ]] || continue
  i=$((i + 1))
  [[ "$i" -le 14 ]] || rm -rf "$d"
done < <(ls -1dt "${LOCAL_ROOT}"/20* 2>/dev/null || true)
[[ -f "${latest}/trojan.db" && -f "${latest}/TIMESTAMP" ]] || {
  ha_red "本地冷备不完整: ${latest}"
  exit 1
}
stamp="$(cat "${latest}/TIMESTAMP")"
printf 'COLD_BACKUP_DIR=%s\n' "${latest}"

[[ "$DO_PUSH" -eq 1 || -n "$REMOTE_HOST" ]] || exit 0
REMOTE_HOST="${REMOTE_HOST:-}"
[[ -n "$REMOTE_HOST" ]] || { ha_red "请提供 --host 或 COLD_BACKUP_HOST"; exit 2; }
ha_require_cmd rsync ssh

ssh_opts=(-p "$REMOTE_PORT" -o BatchMode=yes -o IdentitiesOnly=yes)
# shellcheck disable=SC2206
ssh_opts+=( ${RSYNC_RSH_EXTRA:--o StrictHostKeyChecking=accept-new} )
if [[ -z "$REMOTE_KEY" && -f "${SSH_DIR}/id_ed25519" ]]; then
  REMOTE_KEY="${SSH_DIR}/id_ed25519"
fi
[[ -z "$REMOTE_KEY" || -f "$REMOTE_KEY" ]] || { ha_red "找不到 SSH 私钥: ${REMOTE_KEY}"; exit 1; }
if [[ -n "$REMOTE_KEY" ]]; then
  ssh_opts+=(-i "$REMOTE_KEY")
fi

rsh="ssh"
for o in "${ssh_opts[@]}"; do
  rsh+=" $(printf '%q' "$o")"
done

remote_base="${REMOTE_ROOT%/}"
remote_stamp="${remote_base}/${stamp}"
remote_base_q="$(printf '%q' "$remote_base")"
remote_stamp_q="$(printf '%q' "$remote_stamp")"
remote_target="${REMOTE_USER}@${REMOTE_HOST}:${remote_stamp}/"

ha_blue "上传完整冷备 ${latest}/ → ${remote_target}"
ssh "${ssh_opts[@]}" "${REMOTE_USER}@${REMOTE_HOST}" \
  "install -d -m 700 ${remote_base_q} ${remote_stamp_q}"
rsync -az --delete --chmod=F600,D700 -e "$rsh" "${latest}/" "$remote_target"
ssh "${ssh_opts[@]}" "${REMOTE_USER}@${REMOTE_HOST}" \
  "find ${remote_base_q} -mindepth 1 -maxdepth 1 -type d -name '20*' -printf '%f\\n' | sort -r | tail -n +15 | while IFS= read -r d; do rm -rf -- ${remote_base_q}/\"\$d\"; done"

ha_green "冷备已上传: ${REMOTE_USER}@${REMOTE_HOST}:${remote_stamp}（远端保留最近 14 份）"
