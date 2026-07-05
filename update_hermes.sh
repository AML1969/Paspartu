#!/bin/bash
# ============================================================================
# Безопасное обновление Hermes Agent на ХОСТЕ (main + paspartu-rm).
# Живёт в git (hermes-docker/), на сервер раскатывается в /root/hermes_patches/.
#
# Использование:  bash /root/hermes_patches/update_hermes.sh [версия]
#   без аргумента — переустановка ПИНОВАННОЙ версии (см. HERMES_PIN ниже);
#   с аргументом  — установка указанной версии (перед этим прогони pre-flight:
#                   bash preflight_patches.sh <версия> — проверит цепочку
#                   на чистом виле ДО касания живого site-packages).
#
# Делает: TG-уведомления → pip install (пин!) → перенакат патчей по манифесту
# → verify (py_compile + маркеры) → рестарт гейтвеев → проверка.
# Любой шаг с ошибкой останавливает скрипт ДО рестарта гейтвеев.
#
# ВАЖНО (2026-07-05): PyPI уже отдаёт 0.17/0.18. На 0.18 telegram.py переехал в
# plugins/platforms/telegram/adapter.py — цепочка НЕ ляжет. Поэтому пин.
# ============================================================================
set -Eeuo pipefail

HERMES_PIN="0.16.0"
VERSION="${1:-$HERMES_PIN}"
PY=/root/.local/share/pipx/venvs/hermes-agent/bin/python
PATCH_DIR="${PATCH_DIR:-/root/hermes_patches}"
MANIFEST="$PATCH_DIR/patches.txt"

# Манифест обязателен — единый источник порядка (тот же файл читает Dockerfile BIF).
[ -s "$MANIFEST" ] || { echo "FATAL: нет $MANIFEST — скопируй seed/patches/patches.txt из репо"; exit 1; }
mapfile -t PATCHES < <(grep -vE '^[[:space:]]*(#|$)' "$MANIFEST")
[ "${#PATCHES[@]}" -ge 1 ] || { echo "FATAL: манифест пуст"; exit 1; }
for p in "${PATCHES[@]}"; do
  [ -f "$PATCH_DIR/$p" ] || { echo "FATAL: в манифесте $p, а файла в $PATCH_DIR нет"; exit 1; }
done

if [ "$VERSION" != "$HERMES_PIN" ]; then
  echo "⚠️  Версия $VERSION != пин $HERMES_PIN. Убедись, что pre-flight пройден. 5 сек на Ctrl-C..."
  sleep 5
fi

notify() { # $1=env-file $2=text $3=silent(optional)
  # Сбой уведомления НЕ роняет скрипт (|| true), но виден в выводе.
  # shellcheck disable=SC1090  # путь к .env приходит аргументом
  ( set -a; source "$1"; set +a
    local args=(-s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"
                -d chat_id="${TELEGRAM_HOME_CHANNEL}" --data-urlencode text="$2")
    [ -n "${3:-}" ] && args+=(-d disable_notification=true)
    curl "${args[@]}" >/dev/null ) || echo "WARN: не смог отправить уведомление ($1)"
}

echo "=== 1. Уведомления о техработах ==="
notify /root/.hermes/.env "⚠️ Техработы: обновляю Hermes. Бот не будет отвечать ~2 минуты."
notify /root/.hermes/profiles/paspartu-rm/.env "⚠️ Техработы: обновление. ~2 минуты." silent

echo "=== 2. Установка пакета (pin: $VERSION) ==="
# pipefail: падение pip больше НЕ маскируется tail'ом.
$PY -m pip install "hermes-agent==$VERSION" 2>&1 | tail -5
/root/.local/bin/hermes --version | head -1

echo "=== 3. Перенакат патчей по манифесту (${#PATCHES[@]} шт., идемпотентные) ==="
for p in "${PATCHES[@]}"; do
  echo "--- $p"
  "$PY" "$PATCH_DIR/$p" || { echo "ПАТЧ $p УПАЛ — СТОП. Гейтвеи НЕ рестартую (работают на старом коде в памяти)."; exit 1; }
done

echo "=== 4. Verify ДО рестарта (py_compile + маркеры) ==="
SP=$($PY -c 'import gateway,os;print(os.path.dirname(os.path.dirname(os.path.abspath(gateway.__file__))))')
$PY -c "import py_compile
for f in ['$SP/gateway/platforms/telegram.py','$SP/gateway/platforms/base.py','$SP/gateway/stream_consumer.py']:
    py_compile.compile(f, doraise=True)
print('py_compile OK')"
VFAIL=0
grep -q "RU rich-message patch"  "$SP/gateway/platforms/telegram.py" || { echo "FAIL: нет маркера rich_messages"; VFAIL=1; }
grep -q "RU carousel-fix v8"     "$SP/gateway/platforms/telegram.py" || { echo "FAIL: нет маркера rich_v8"; VFAIL=1; }
grep -q "RU carousel-fix"        "$SP/gateway/platforms/base.py"     || { echo "FAIL: нет маркера rich_v7"; VFAIL=1; }
grep -q "RU empty-sentinel guard" "$SP/gateway/stream_consumer.py"   || { echo "FAIL: нет маркера sentinel"; VFAIL=1; }
grep -q "Требуется подтверждение команды" "$SP/gateway/platforms/telegram.py" || { echo "FAIL: нет маркера localize_ru"; VFAIL=1; }
[ "$VFAIL" = 0 ] || { echo "VERIFY FAIL — СТОП, гейтвеи НЕ рестартую."; exit 1; }
echo "verify OK"

echo "=== 5. Рестарт гейтвеев ==="
systemctl restart hermes-gateway
systemctl restart hermes-gateway-paspartu-rm
sleep 12

echo "=== 6. Проверка ==="
systemctl is-active hermes-gateway hermes-gateway-paspartu-rm
grep "✓ telegram" /root/.hermes/logs/gateway.log | tail -1 || echo "WARN: '✓ telegram' не найден в логе main — проверь глазами"
grep "✓ telegram" /root/.hermes/profiles/paspartu-rm/logs/gateway.log | tail -1 || echo "WARN: '✓ telegram' не найден в логе rm — проверь глазами"
PID=$(systemctl show hermes-gateway-paspartu-rm -p MainPID --value); cat "/proc/$PID/attr/current"

echo "=== 7. Готово ==="
notify /root/.hermes/.env "✅ Обновление завершено, всё работает."
notify /root/.hermes/profiles/paspartu-rm/.env "✅ Готово, бот снова на связи." silent

echo "=== 8. Чистка старых .bak (>30 дней) ==="
find "$SP/gateway" -name '*.bak-*' -mtime +30 -delete 2>/dev/null || true
echo "OK"
# Памятка:
# - .env Андрея под chattr +i: для правок сначала chattr -i, потом вернуть +i.
# - Флаги per-profile НЕ трогает апдейт: HERMES_RICH_MESSAGES в .env,
#   gateway.streaming.enabled в config.yaml — проверять не надо.
# - cancel_words_ru/cancel_barge_in/tracker_rich — в archive/, НЕ в цепочке
#   (решение 2026-07-05; cancel-патчи отменены ещё 2026-06-05).
# - Апгрейд версии: сначала bash preflight_patches.sh <версия>, потом сюда аргументом.
