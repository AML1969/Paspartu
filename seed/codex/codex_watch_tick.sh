#!/usr/bin/env bash
# BIF: один проход вотчера Codex-задач (bif:1.2). Вызывается циклом из entrypoint (~180с).
# Доставляет в TG: финальный результат / ошибку / статус «выполняется» / «прервана».
JOBS=/data/codex-jobs
[ -d "$JOBS" ] || exit 0
TG() {
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d chat_id="${TELEGRAM_HOME_CHANNEL}" --data-urlencode text="$1" >/dev/null || true
}
now=$(date +%s)
for JD in "$JOBS"/[0-9]*; do
  [ -d "$JD" ] || continue
  N=$(basename "$JD")
  [ -f "$JD/delivered" ] && continue
  LLM=$(cat "$JD/llm.txt" 2>/dev/null || echo deepseek)
  if [ "$LLM" = "sol" ]; then PROV="OpenAI (gpt-5.6-sol)"; else PROV="DeepSeek"; fi
  if [ -f "$JD/exit_code" ]; then
    EC=$(cat "$JD/exit_code")
    if [ "$EC" = "0" ] && [ -s "$JD/answer.txt" ]; then
      ANS=$(head -c 3500 "$JD/answer.txt")
      TG "🏁 Задача №${N} готова (провайдер: ${PROV}).

${ANS}"
    else
      TG "⚠️ Задача №${N} завершилась с ошибкой (код ${EC}, провайдер: ${PROV}). Можно повторить — или запустить на Codex от OpenAI."
    fi
    date +%s > "$JD/delivered"
    continue
  fi
  P=$(cat "$JD/pid" 2>/dev/null || echo "")
  ST=$(cat "$JD/started_at" 2>/dev/null || echo "$now")
  if [ -n "$P" ] && kill -0 "$P" 2>/dev/null; then
    LAST=$(cat "$JD/status_ts" 2>/dev/null || echo "$ST")
    if [ $((now - LAST)) -ge 170 ]; then
      MIN=$(( (now - ST) / 60 ))
      TG "⏳ Задача №${N}: выполняется (~${MIN} мин, провайдер: ${PROV})."
      date +%s > "$JD/status_ts"
    fi
  else
    AGE=$((now - ST))
    if [ "$AGE" -ge 300 ]; then
      TG "⚠️ Задача №${N} прервана (перезапуск или сбой). Запустите её, пожалуйста, заново."
      date +%s > "$JD/delivered"
    fi
  fi
done
