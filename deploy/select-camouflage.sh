deploy_camouflage() {
  local dest="$1"
  local catalog="${INSTALL_DIR}/deploy/camouflage"
  local list=()
  local d
  local had_nullglob=0
  shopt -q nullglob && had_nullglob=1
  shopt -s nullglob
  for d in "${catalog}"/*/index.html; do
    list+=("$(dirname "$d")")
  done
  [[ "$had_nullglob" -eq 1 ]] || shopt -u nullglob
  if [[ ${#list[@]} -eq 0 ]]; then
    red "缺少伪装站模板：${catalog}/<主题>/index.html"
    exit 1
  fi
  local chosen
  chosen="$(
    DOMAIN="${DOMAIN:-}" python3 -c '
import hashlib, os, random, sys
themes = sys.argv[1:]
seed = os.environ.get("DOMAIN") or os.urandom(16).hex()
rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest(), 16))
print(rng.choice(themes))
' "${list[@]}"
  )"
  mkdir -p "$dest"
  find "$dest" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  cp -a "${chosen}/." "$dest/"
  green "伪装站主题: $(basename "$chosen")"
}
