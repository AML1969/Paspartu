#!/usr/bin/env bash
# ============================================================================
# Точка входа контейнера-копии Hermes «Паспарту» v1.0 — блочная сборка.
# При первом запуске раскладывает в том /data только выбранные блоки (WITH_*),
# генерирует .env из переменных окружения и стартует gateway.
# Источник правды артефактов — сервер 188.166.122.243 (Hermes 0.16.0).
# ============================================================================
set -euo pipefail

export HERMES_HOME="${HERMES_HOME:-/data/hermes}"
WORKSPACE="${HERMES_WORKSPACE:-/data/workspace}"
# ВАЖНО (инцидент 2026-06-11): HERMES_ENV/TASKS_DIR — явно в окружение, чтобы
# subprocess-скиллы (tasks.py tg_send) не схватили чужой /root/.hermes/.env.
export HERMES_ENV="${HERMES_ENV:-$HERMES_HOME/.env}"
export TASKS_DIR="${TASKS_DIR:-$WORKSPACE/tasks}"
SEED=/opt/hermes-seed
mkdir -p "$HERMES_HOME" "$WORKSPACE" "$TASKS_DIR"

# ── Тумблеры блоков (1/0). Авто-выбор по наличию ключа, если флаг не задан. ──
on()  { case "${1:-}" in 1|y|Y|yes|true|on) return 0;; *) return 1;; esac }
auto(){ [ -n "${1:-}" ] && echo 1 || echo 0; }   # 1, если значение непустое

WITH_OPENAI="${WITH_OPENAI:-$(auto "${OPENAI_API_KEY:-}")}"
WITH_OPENROUTER="${WITH_OPENROUTER:-$(auto "${OPENROUTER_API_KEY:-}")}"
WITH_PERPLEXITY="${WITH_PERPLEXITY:-$(auto "${PERPLEXITY_API_KEY:-}")}"
WITH_HMEM="${WITH_HMEM:-1}"
WITH_VOICE="${WITH_VOICE:-1}"
WITH_TRACKER="${WITH_TRACKER:-1}"
WITH_GOOGLE="${WITH_GOOGLE:-0}"
WITH_CODEX="${WITH_CODEX:-0}"
WITH_SITE="${WITH_SITE:-0}"
HERMES_RICH_MESSAGES="${HERMES_RICH_MESSAGES:-1}"

# ── Обязательные переменные ──────────────────────────────────────────────
: "${TELEGRAM_BOT_TOKEN:?нужен TELEGRAM_BOT_TOKEN}"
: "${TELEGRAM_ALLOWED_USERS:?нужен TELEGRAM_ALLOWED_USERS}"
: "${DEEPSEEK_API_KEY:?нужен DEEPSEEK_API_KEY}"

FIRST_RUN=0
[ ! -f "$HERMES_HOME/config.yaml" ] && FIRST_RUN=1

# ── Первый запуск: конфиг + скиллы ───────────────────────────────────────
if [ "$FIRST_RUN" = 1 ]; then
  echo "[entrypoint] первый запуск: раскладываю блоки в $HERMES_HOME"
  cp "$SEED/config.yaml" "$HERMES_HOME/config.yaml"
  sed -i "s#^  cwd: .*#  cwd: ${WORKSPACE}#" "$HERMES_HOME/config.yaml"

  mkdir -p "$HERMES_HOME/skills"
  tar xzf "$SEED/skills.tar.gz" -C "$HERMES_HOME/skills"

  # Блочная прополка скиллов: убрать то, что выключено
  on "$WITH_TRACKER"    || rm -rf "$HERMES_HOME/skills/productivity/task-tracker"
  on "$WITH_PERPLEXITY" || rm -rf "$HERMES_HOME/skills/openclaw-imports/perplexity"

  # Конфиг-правки под блоки (pyyaml идёт с hermes-agent)
  python - "$HERMES_HOME/config.yaml" "$WITH_OPENAI" "$WITH_VOICE" "$HERMES_RICH_MESSAGES" <<'PY'
import sys,yaml
f,wo,wv,wr=sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4]
c=yaml.safe_load(open(f))
def truthy(x): return str(x).lower() in ("1","y","yes","true","on")
# OpenAI выключен → снять плагин картинок и тулсет image_gen, чтобы не падало без ключа
if not truthy(wo):
    pl=c.get("plugins",{}); pl["enabled"]=[p for p in pl.get("enabled",[]) if not str(p).startswith("image_gen")]; c["plugins"]=pl
    pts=c.get("platform_toolsets",{}).get("cli")
    if pts: c["platform_toolsets"]["cli"]=[t for t in pts if t not in ("image_gen","vision")]
# Голос выключен → STT off
if not truthy(wv):
    c.setdefault("stt",{})["enabled"]=False
    pts=c.get("platform_toolsets",{}).get("cli")
    if pts: c["platform_toolsets"]["cli"]=[t for t in pts if t!="tts"]
# Streaming/rich — рантайм-тумблер дублируем в конфиг для наглядности
c.setdefault("gateway",{}).setdefault("streaming",{})["enabled"]=truthy(wr)
yaml.safe_dump(c,open(f,"w"),sort_keys=False,allow_unicode=True)
print("[entrypoint] config: openai=%s voice=%s rich=%s"%(wo,wv,wr))
PY

  # Файловая память сразу (встроенная переполняется ~2200 символов)
  [ -f "$HERMES_HOME/MEMORY.md" ] || cp "$SEED/MEMORY.md" "$HERMES_HOME/MEMORY.md"

  # ── AGENTS.md рабочей папки + вырезка hmem-блока, если hmem выключен ──
  sed -e "s#__HERMES_HOME__#${HERMES_HOME}#g" -e "s#__WORKSPACE__#${WORKSPACE}#g" "$SEED/AGENTS.md" > "$WORKSPACE/AGENTS.md"
  if ! on "$WITH_HMEM"; then
    sed -i '/<!-- hmem:begin -->/,/<!-- hmem:end -->/d' "$WORKSPACE/AGENTS.md"
  fi

  # ── hmem (банк памяти) — опциональный блок ──
  if on "$WITH_HMEM"; then
    mkdir -p "$HERMES_HOME/bin"
    tar xzf "$SEED/hmem-bin.tar.gz" -C "$HERMES_HOME/bin"
    chmod +x "$HERMES_HOME/bin/hmem" 2>/dev/null || true
    ( cd "$WORKSPACE" && "$HERMES_HOME/bin/hmem" index >/dev/null 2>&1 || echo "[entrypoint] hmem index отложен (ничего индексировать)" )
  fi

  # ── Трекер задач — исходник для cron/переустановки ──
  if on "$WITH_TRACKER"; then
    mkdir -p /opt/hermes-task-tracker-src
    tar xzf "$SEED/task-tracker-src.tar.gz" -C /opt/hermes-task-tracker-src 2>/dev/null || true
  fi

  # ── SOUL: шаблон + routing/codex-блоки по тумблерам ──
  if [ ! -f "$HERMES_HOME/SOUL.md" ]; then
    if [ -f /data/SOUL.md ]; then
      cp /data/SOUL.md "$HERMES_HOME/SOUL.md"   # пользователь принёс свой
    else
      cp "$SEED/SOUL.template.md" "$HERMES_HOME/SOUL.md"
      PXPATH="$HERMES_HOME/skills/openclaw-imports/perplexity/scripts/search.mjs"
      if on "$WITH_CODEX"; then
        sed -e "s#{CODEX_TASK_SH}#/data/codex-jobs/codex_task.sh#g" \
            -e "s#{CODEX_ENABLED_FILE}#/data/codex/enabled#g" \
            "$SEED/CODEX_BLOCK.md" >> "$HERMES_HOME/SOUL.md"
      fi
      if on "$WITH_PERPLEXITY" || on "$WITH_CODEX"; then
        sed -e "s#{ПУТЬ_PERPLEXITY}#${PXPATH}#g" \
            -e "s#{ИМЯ_ПОЛЬЗОВАТЕЛЯ}#пользователь#g" \
            "$SEED/ROUTING_BLOCK.md" >> "$HERMES_HOME/SOUL.md"
      fi
    fi
  fi
fi

# ── .env копии — только релевантные ключи под выбранные блоки ──
GW_TOKEN="${HERMES_GATEWAY_TOKEN:-$(openssl rand -hex 24)}"
{
  echo "DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}"
  on "$WITH_OPENAI"     && echo "OPENAI_API_KEY=${OPENAI_API_KEY:-}"
  on "$WITH_OPENROUTER" && echo "OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}"
  on "$WITH_PERPLEXITY" && echo "PERPLEXITY_API_KEY=${PERPLEXITY_API_KEY:-}"
  echo "TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}"
  echo "TELEGRAM_ALLOWED_USERS=${TELEGRAM_ALLOWED_USERS}"
  echo "TELEGRAM_HOME_CHANNEL=${TELEGRAM_HOME_CHANNEL:-${TELEGRAM_ALLOWED_USERS}}"
  echo "HERMES_GATEWAY_TOKEN=${GW_TOKEN}"
  echo "HERMES_RICH_MESSAGES=${HERMES_RICH_MESSAGES}"
  echo "MESSAGING_CWD=${WORKSPACE}"
  echo "HERMES_MEDIA_ALLOW_DIRS=${HERMES_HOME}/cache/images:${WORKSPACE}"
} > "$HERMES_HOME/.env"
chmod 600 "$HERMES_HOME/.env"

# ── Напоминание про Google (инцидент 2026-06-10: Testing-токен умирает за 7 дней) ──
if on "$WITH_GOOGLE" && [ ! -f "$HERMES_HOME/google_token.json" ]; then
  echo "[entrypoint] ℹ️ Google включён, но не авторизован. ОБЯЗАТЕЛЬНО Publish app → In production"
  echo "[entrypoint]    (иначе refresh-токен умрёт через 7 дней). Шаги: README.md → Google."
fi

echo "[entrypoint] блоки: openai=$WITH_OPENAI openrouter=$WITH_OPENROUTER perplexity=$WITH_PERPLEXITY hmem=$WITH_HMEM voice=$WITH_VOICE tracker=$WITH_TRACKER google=$WITH_GOOGLE codex=$WITH_CODEX site=$WITH_SITE rich=$HERMES_RICH_MESSAGES"
echo "[entrypoint] старт gateway (HERMES_HOME=$HERMES_HOME)"
exec hermes gateway run
