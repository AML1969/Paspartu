#!/usr/bin/env bash
# Поднять/пересобрать изолированную копию из готового copies/<имя>.env.
# Обычный путь установки новой копии — ./install.sh (мастер). Этот скрипт —
# для ручного перезапуска, когда .env уже есть.
#   ./run-copy.sh <имя>
set -euo pipefail
NAME="${1:?Укажи имя копии: ./run-copy.sh petrov}"
NAME="$(printf '%s' "$NAME" | tr 'A-Z' 'a-z')"
case "$NAME" in ''|*[!a-z0-9_-]*) echo "имя копии: только латиница a-z, цифры, _ и -"; exit 1 ;; esac
DIR="$(cd "$(dirname "$0")" && pwd)"; cd "$DIR"
ENVF="copies/$NAME.env"
[ -f "$ENVF" ] || { echo "нет $ENVF — запусти ./install.sh"; exit 1; }

# профили сайдкаров из .env (codex/site)
PROFILES=()
grep -q '^WITH_CODEX=1' "$ENVF" && PROFILES+=(--profile codex)
grep -q '^WITH_SITE=1'  "$ENVF" && PROFILES+=(--profile site)

COPY="$NAME" docker compose -p "bif-$NAME" ${PROFILES[@]+"${PROFILES[@]}"} up -d --build
echo "Копия bif-$NAME запущена."
echo "Логи:  COPY=$NAME docker compose -p bif-$NAME logs -f hermes"
echo "Внутрь: COPY=$NAME docker compose -p bif-$NAME exec hermes bash"
