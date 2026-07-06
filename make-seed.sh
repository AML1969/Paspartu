#!/usr/bin/env bash
# ============================================================================
# make-seed.sh — регенерация seed/skills.tar.gz БЕЗ мусора и стейта куратора.
# Источник правды скиллов = живой сервер (/root/.hermes/skills). Скрипт стягивает
# их во временную папку с excludes и пакует детерминированный tarball.
#
# Запуск (из папки репо, ключ digocean_open рядом или путь в SEED_KEY):
#   bash make-seed.sh                 # с сервера 188.166.122.243
#   SRC_LOCAL=/path/to/skills bash make-seed.sh   # из локальной папки скиллов
#
# Зачем: раньше tarball коммитили руками с живого сервера — внутрь уезжали
# .curator_state/.curator_backups/.usage.json (стейт куратора с путями /root),
# __pycache__ и .bak. Каждый онбординг тащил этот мусор. Теперь — чистая сборка.
# ============================================================================
set -Eeuo pipefail
cd "$(dirname "$0")"

OUT="seed/skills.tar.gz"
HOST="${SEED_HOST:-root@188.166.122.243}"
KEY="${SEED_KEY:-digocean_open}"
REMOTE_SKILLS="${REMOTE_SKILLS:-/root/.hermes/skills}"

# Что НИКОГДА не должно попасть в seed (стейт куратора, кэши, бэкапы, секреты).
EXCLUDES=(
  --exclude='.curator_state'      --exclude='.curator_backups'
  --exclude='.hub'                --exclude='.bundled_manifest'
  --exclude='.usage.json'         --exclude='.usage.json.lock'
  --exclude='__pycache__'         --exclude='*.pyc'
  --exclude='*.bak-*'             --exclude='*.log'
  --exclude='.git'                --exclude='.DS_Store'
  --exclude='*.env'               --exclude='.env'
)

WORK="$(mktemp -d /tmp/bif-seed-XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

if [ -n "${SRC_LOCAL:-}" ]; then
  echo "[make-seed] источник: локальная папка $SRC_LOCAL"
  rsync -a "${EXCLUDES[@]}" "$SRC_LOCAL/" "$WORK/skills/"
else
  echo "[make-seed] источник: $HOST:$REMOTE_SKILLS"
  install -m 600 "$KEY" /tmp/_seedkey
  rsync -a -e "ssh -i /tmp/_seedkey -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
    "${EXCLUDES[@]}" "$HOST:$REMOTE_SKILLS/" "$WORK/skills/"
  rm -f /tmp/_seedkey
fi

# Пост-проверка: мусора и секретов быть не должно.
BAD="$(find "$WORK/skills" \( -name '.curator_state' -o -name '__pycache__' -o -name '*.bak-*' -o -name '.usage.json' \) | head)"
[ -z "$BAD" ] || { echo "[make-seed] FATAL: мусор просочился:"; echo "$BAD"; exit 1; }
# Секрет-скан по СОДЕРЖИМОМУ (не по именам), с отсевом очевидных плейсхолдеров
# в документации (xxxx / XXXX / YOUR_ / <...> / example / placeholder).
HITS="$(grep -rhoE 'sk-[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{30,}|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}|pplx-[A-Za-z0-9]{20,}' \
          "$WORK/skills" 2>/dev/null \
        | grep -viE 'x{4,}|X{4,}|your_|placeholder|example|<[a-z]' || true)"
if [ -n "$HITS" ]; then
  echo "[make-seed] FATAL: похоже на настоящий секрет в скиллах — проверь вручную:"
  echo "$HITS" | sed -E 's/(.{6}).*/\1…/' | sort -u | head
  exit 1
fi

# Детерминированный tarball (сортировка + фикс mtime/uid → стабильный хеш при неизменных данных).
tar --sort=name --mtime='UTC 2020-01-01' --owner=0 --group=0 --numeric-owner \
    -czf "$OUT" -C "$WORK" skills
N=$(tar -tzf "$OUT" | wc -l)
echo "[make-seed] готово: $OUT ($(du -h "$OUT" | cut -f1), $N записей)"
echo "[make-seed] дальше: залить $OUT в git (обе ветки) + git pull на сервере."
