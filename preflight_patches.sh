#!/bin/bash
# ============================================================================
# Pre-flight: проверить, ляжет ли цепочка патчей на версию hermes-agent X,
# НЕ касаясь живого site-packages. Гонять ПЕРЕД любым апгрейдом.
#
# Использование: bash preflight_patches.sh <версия> [patch_dir]
#   пример:      bash preflight_patches.sh 0.17.0
#   patch_dir по умолчанию: seed/patches рядом со скриптом (репо)
#                           или /root/hermes_patches, если запущен на сервере.
#
# Делает: pip download sdist/wheel → распаковка во временную папку →
# sed-ретаргет pipx-пути патчей на распакованный пакет → прогон цепочки по
# манифесту → повторный прогон (проверка идемпотентности) → py_compile →
# сводка OK/SKIP/FAIL. Живой Hermes не затрагивается вообще.
# ============================================================================
set -Eeuo pipefail

VERSION="${1:?использование: preflight_patches.sh <версия> [patch_dir]}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -n "${2:-}" ]; then PATCH_DIR="$2"
elif [ -d "$SCRIPT_DIR/seed/patches" ]; then PATCH_DIR="$SCRIPT_DIR/seed/patches"
elif [ -d /root/hermes_patches ]; then PATCH_DIR=/root/hermes_patches
else echo "FATAL: не нашёл папку патчей — укажи вторым аргументом"; exit 1; fi
MANIFEST="$PATCH_DIR/patches.txt"
[ -s "$MANIFEST" ] || { echo "FATAL: нет $MANIFEST"; exit 1; }

PYBIN="${PYBIN:-python3}"
command -v "$PYBIN" >/dev/null || { echo "FATAL: нет python3"; exit 1; }

WORK="$(mktemp -d /tmp/bif-preflight-XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
echo "[pre-flight] hermes-agent==$VERSION → $WORK"

echo "[pre-flight] скачиваю пакет с PyPI..."
# fallback: если локальный python старее требований пакета — качаем wheel
# принудительно под 3.12 (нам нужен только исходник, не установка).
"$PYBIN" -m pip download "hermes-agent==$VERSION" --no-deps -d "$WORK/dl" -q \
  || "$PYBIN" -m pip download "hermes-agent==$VERSION" --no-deps -d "$WORK/dl" -q \
       --only-binary=:all: --python-version 3.12
WHEEL="$(ls "$WORK"/dl/hermes_agent-*.whl 2>/dev/null | head -1 || true)"
if [ -n "$WHEEL" ]; then
  "$PYBIN" -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$WHEEL" "$WORK/pkg"
else
  SDIST="$(ls "$WORK"/dl/hermes*agent-*.tar.gz | head -1)"
  tar -xzf "$SDIST" -C "$WORK" && mv "$WORK"/hermes*agent-*/ "$WORK/pkg" 2>/dev/null || true
fi
[ -d "$WORK/pkg/gateway" ] || { echo "FAIL: в пакете $VERSION нет gateway/ по старому пути — структура изменилась (0.18+?), цепочке нужен перенос якорей"; exit 2; }

PIPX='/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages'
mapfile -t PATCHES < <(grep -vE '^[[:space:]]*(#|$)' "$MANIFEST")

run_chain(){ # $1 = метка прогона; глобальные счётчики OK/SKIP/FAIL
  local label="$1" rc out p
  N_OK=0; N_SKIP=0; N_FAIL=0; FAILED=""
  for p in "${PATCHES[@]}"; do
    sed "s#${PIPX}#${WORK}/pkg#g" "$PATCH_DIR/$p" > "$WORK/run_$p"
    set +e; out="$(HERMES_SITE_PACKAGES="$WORK/pkg" "$PYBIN" "$WORK/run_$p" 2>&1)"; rc=$?; set -e
    if [ $rc -eq 0 ]; then
      case "$out" in *SKIP*|*skip*|*уже*|*already*) N_SKIP=$((N_SKIP+1)); echo "  [$label] SKIP $p";;
                     *) N_OK=$((N_OK+1)); echo "  [$label] OK   $p";; esac
    else
      N_FAIL=$((N_FAIL+1)); FAILED="$FAILED $p"
      echo "  [$label] FAIL $p (rc=$rc)"; echo "$out" | tail -3 | sed 's/^/         /'
    fi
  done
}

echo "[pre-flight] прогон 1 (чистый вил):"
run_chain apply
R1_FAIL=$N_FAIL; R1_FAILED="$FAILED"

echo "[pre-flight] прогон 2 (идемпотентность — ожидаю все SKIP):"
run_chain rerun
R2_NONSKIP=$((N_OK + N_FAIL))

COMPILE_FAIL=0
if [ "$R1_FAIL" -eq 0 ]; then
  echo "[pre-flight] py_compile патченных файлов:"
  "$PYBIN" -c "import py_compile,sys
for f in ['$WORK/pkg/gateway/platforms/telegram.py','$WORK/pkg/gateway/platforms/base.py','$WORK/pkg/gateway/stream_consumer.py']:
    py_compile.compile(f, doraise=True)
print('  compile OK')" || COMPILE_FAIL=1
else
  echo "[pre-flight] py_compile пропущен (есть FAIL в прогоне 1)"
fi

echo "============================================================"
if [ "$R1_FAIL" -eq 0 ] && [ "$R2_NONSKIP" -eq 0 ] && [ "$COMPILE_FAIL" -eq 0 ]; then
  echo "PRE-FLIGHT OK: цепочка ложится на $VERSION чисто и идемпотентно."
  echo "Можно обновлять: bash update_hermes.sh $VERSION"
else
  echo "PRE-FLIGHT FAIL для $VERSION:"
  [ "$R1_FAIL" -gt 0 ] && echo "  не легли:$R1_FAILED"
  [ "$R2_NONSKIP" -gt 0 ] && echo "  идемпотентность нарушена: $R2_NONSKIP патч(ей) не дали SKIP на повторе"
  echo "НЕ обновляйся на $VERSION без переноса якорей."
  exit 2
fi
