#!/usr/bin/env bash
# BIF: фоновый запуск Codex-задачи ИЗ КОНТЕЙНЕРА (bif:1.2).
# Usage: codex_task.sh "task text" [workdir] [est_minutes] [profile: совместимость, игнорируется] [llm: deepseek|sol]
# Квитанция в TG сразу; статусы и результат доставляет codex_watch_tick (цикл entrypoint).
set -euo pipefail
TASK="${1:?usage: codex_task.sh \"task\" [workdir] [est_minutes] [profile] [llm]}"
WORKDIR="${2:-/data/codex-work}"
EST="${3:-10}"
LLM="${5:-deepseek}"
ENABLED=/data/codex/enabled
if [ ! -f "$ENABLED" ] || [ "$(cat "$ENABLED" 2>/dev/null)" != "on" ]; then
  echo "Codex выключен. Скажите «кодекс вкл», чтобы включить."
  exit 0
fi
JOBS=/data/codex-jobs
mkdir -p "$JOBS" "$WORKDIR"
N=$(( $(cat "$JOBS/.counter" 2>/dev/null || echo 0) + 1 )); echo "$N" > "$JOBS/.counter"
JD="$JOBS/$N"; mkdir -p "$JD"
printf '%s' "$TASK" > "$JD/task.txt"
printf '%s' "$WORKDIR" > "$JD/workdir.txt"
printf '%s' "$EST" > "$JD/est.txt"
printf '%s' "$LLM" > "$JD/llm.txt"
date +%s > "$JD/started_at"
nohup "$JOBS/codex_task_runner.sh" "$JD" >/dev/null 2>&1 &
echo $! > "$JD/pid"
if [ "$LLM" = "sol" ]; then CXN="Codex-OpenAI"; else CXN="Codex"; fi
RTEXT="✅ Принял задачу №${N} (${CXN}, ~${EST} мин). Пока она выполняется, можете спокойно давать другие задачи — статусы хода и результат пришлю сюда."
curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -d chat_id="${TELEGRAM_HOME_CHANNEL}" \
  --data-urlencode text="$RTEXT" >/dev/null || true
echo "JOB ${N} STARTED (background). Receipt sent. Do NOT wait for completion — end your turn now; watcher доставит статусы и результат."
