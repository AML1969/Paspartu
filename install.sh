#!/usr/bin/env bash
# ============================================================================
# BIF v1.0 — установщик копии. Три режима:
#   ./install.sh                 # интерактивный мастер (человек у терминала)
#   ./install.sh --check <name>  # повторная живая валидация ключей копии
#   ./install.sh --headless      # неинтерактивно из env (для запуска по SSH без TTY)
#
# HEADLESS (env-driven, секреты через переменные окружения, не через argv):
#   NAME=petrov PRESET=standard \
#   TELEGRAM_BOT_TOKEN=... TELEGRAM_ALLOWED_USERS=... DEEPSEEK_API_KEY=... \
#   [OPENAI_API_KEY=...] [PERPLEXITY_API_KEY=...] [OPENROUTER_API_KEY=...] \
#   [WITH_CODEX=1 WITH_SITE=1 WITH_GOOGLE=0 ...]  [NO_COMPOSE=1] [BIF_VALIDATE=0] \
#   ./install.sh --headless
#   PRESET ∈ minimal|standard|full (default standard); явные WITH_* перекрывают пресет.
#   NO_COMPOSE=1 — только записать copies/<name>.env, контейнер не поднимать (dry-run).
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p copies

# ── вывод ─────────────────────────────────────────────────────────────────
c_ok(){ printf '  \033[32m✅ %s\033[0m\n' "$*"; }
c_no(){ printf '  \033[31m❌ %s\033[0m\n' "$*"; }
c_warn(){ printf '  \033[33m⚠️  %s\033[0m\n' "$*"; }
hdr(){ printf '\n\033[1m%s\033[0m\n' "$*"; }
ask(){ local p="$1" d="${2:-}"; local v; read -rp "  $p${d:+ [$d]}: " v; echo "${v:-$d}"; }
# askk VAR "prompt" "validator_fn" required(1/0)
#   Видимый ввод ключа (взамен старого слепого read -rsp): чистит случайные пробелы
#   от вставки, и при BIF_VALIDATE=1 сразу проверяет ключ, предлагая повтор при ошибке.
#   Результат кладёт в переменную VAR (без stdout-capture). required=0 → Enter = пропустить.
askk(){
  local __var="$1" prompt="$2" vfn="${3:-}" required="${4:-1}" val choice
  while :; do
    read -rp "  $prompt: " val || { c_no "ввод прерван"; exit 1; }
    val="${val//[$' \t\r\n']/}"
    if [ -z "$val" ]; then
      [ "$required" = 0 ] && { printf -v "$__var" '%s' ''; return 0; }
      c_no "пусто — вставь значение ещё раз"; continue
    fi
    if [ "${BIF_VALIDATE:-1}" = 1 ] && [ -n "$vfn" ]; then
      if "$vfn" "$val"; then printf -v "$__var" '%s' "$val"; return 0; fi
      c_warn "не прошло проверку. Enter — ввести заново; s — оставить как есть"
      read -rp "  [Enter=заново / s=оставить]: " choice || choice=""
      case "$choice" in s|S) printf -v "$__var" '%s' "$val"; return 0;; *) continue;; esac
    fi
    printf -v "$__var" '%s' "$val"; return 0
  done
}
yn(){ local p="$1" d="${2:-Y}"; local v; read -rp "  $p [$([ "$d" = Y ] && echo 'Y/n' || echo 'y/N')]: " v; v="${v:-$d}"; case "$v" in y|Y|yes|да) echo 1;; *) echo 0;; esac; }

# ── имя копии: нормализация + проверка под имя файла и docker-проект bif-<name> ──
# Возвращает нормализованное имя (lowercase); код возврата 1 + пустой вывод, если
# имя пустое или содержит что-то кроме [a-z0-9_-] (пробелы, заглавные, кириллица…).
norm_name(){ local n; n="$(printf '%s' "${1:-}" | tr 'A-Z' 'a-z')"; case "$n" in ''|*[!a-z0-9_-]*) return 1;; esac; printf '%s' "$n"; }

# ── Telegram ID: только цифры (несколько ID — через запятую) ──────────────
# Единственный обязательный ввод без живой валидации через API — опечатка иначе
# молча уезжает в .env и всплывает только как «бот не отвечает».
val_tgid(){ case "${1:-}" in ''|*[!0-9,]*|,*|*,|*,,*) return 1;; *) return 0;; esac; }

# ── живая валидация ключей ────────────────────────────────────────────────
val_deepseek(){ curl -fsS -m 12 https://api.deepseek.com/v1/models -H "Authorization: Bearer $1" -o /dev/null && c_ok "DeepSeek" || { c_no "DeepSeek — ключ отклонён"; return 1; }; }
val_openai(){   curl -fsS -m 12 https://api.openai.com/v1/models   -H "Authorization: Bearer $1" -o /dev/null && { c_ok "OpenAI"; return 0; } || { c_no "OpenAI — ключ отклонён"; return 1; }; }
val_pplx(){     curl -fsS -m 12 https://api.perplexity.ai/chat/completions -H "Authorization: Bearer $1" -H 'Content-Type: application/json' -d '{"model":"sonar","messages":[{"role":"user","content":"ping"}],"max_tokens":1}' -o /dev/null && { c_ok "Perplexity"; return 0; } || { c_no "Perplexity — ключ отклонён (проверь баланс)"; return 1; }; }
val_tg(){ local r; r=$(curl -fsS -m 12 "https://api.telegram.org/bot$1/getMe" 2>/dev/null||true); case "$r" in *'"ok":true'*) c_ok "Telegram $(echo "$r"|grep -o '"username":"[^"]*"'|cut -d'"' -f4|sed 's/^/@/')"; return 0;; *) c_no "Telegram getMe — токен отклонён"; return 1;; esac; }

# ── пресет → тумблеры (явные WITH_* потом перекрывают) ────────────────────
apply_preset(){ # $1=preset → выставляет P_* по умолчанию
  P_OPENAI=0; P_PPLX=0; P_HMEM=0; P_VOICE=0; P_GOOGLE=0; P_TRACKER=0; P_CODEX=0; P_SITE=0
  case "$1" in
    minimal) P_PPLX=1 ;;
    full)    P_OPENAI=1; P_PPLX=1; P_HMEM=1; P_VOICE=1; P_GOOGLE=1; P_TRACKER=1; P_CODEX=1; P_SITE=1 ;;
    *)       P_OPENAI=1; P_PPLX=1; P_HMEM=1; P_VOICE=1; P_GOOGLE=1; P_TRACKER=1 ;;  # standard
  esac
}

# ── запись copies/<name>.env ──────────────────────────────────────────────
write_env(){ # uses NAME + DS_KEY TG_TOKEN TG_ID + OA_KEY PX_KEY OR_KEY + W_*
  local ENVF="copies/$NAME.env"
  if [ -f "$ENVF" ]; then
    cp -p "$ENVF" "$ENVF.bak-$(date +%s)" && c_warn "существующий $ENVF сохранён в бэкап"
    # интерактив: спросить подтверждение; в --headless (нет TTY) перезаписываем молча (бэкап уже есть)
    if [ -t 0 ] && [ "$(yn "Копия «$NAME» уже существует — перезаписать .env?" N)" != 1 ]; then
      c_no "отмена — оставляю прежний $ENVF"; exit 1
    fi
  fi
  umask 177
  cat > "$ENVF" <<EOF
# BIF copy: $NAME  (сгенерировано install.sh $(date -u +%FT%TZ))
DEEPSEEK_API_KEY=$DS_KEY
TELEGRAM_BOT_TOKEN=$TG_TOKEN
TELEGRAM_ALLOWED_USERS=$TG_ID
TELEGRAM_HOME_CHANNEL=${TG_HOME:-${TG_ID%%,*}}
${OA_KEY:+OPENAI_API_KEY=$OA_KEY}
${PX_KEY:+PERPLEXITY_API_KEY=$PX_KEY}
${OR_KEY:+OPENROUTER_API_KEY=$OR_KEY}
WITH_OPENAI=$W_OPENAI
WITH_OPENROUTER=$([ -n "${OR_KEY:-}" ] && echo 1 || echo 0)
WITH_PERPLEXITY=$W_PPLX
WITH_HMEM=$W_HMEM
WITH_VOICE=$W_VOICE
WITH_GOOGLE=$W_GOOGLE
WITH_TRACKER=$W_TRACKER
WITH_CODEX=$W_CODEX
WITH_SITE=$W_SITE
HERMES_RICH_MESSAGES=1
EOF
  umask 022
  c_ok "записан $ENVF (chmod 600)"
}

# ── подъём контейнера ─────────────────────────────────────────────────────
bring_up(){
  local PROFILES=()
  [ "$W_CODEX" = 1 ] && PROFILES+=(--profile codex)
  [ "$W_SITE" = 1 ]  && PROFILES+=(--profile site)
  if [ "${NO_COMPOSE:-0}" = 1 ]; then
    c_warn "NO_COMPOSE=1 — контейнер не поднимаю. Команда вручную:"
    echo "    COPY=$NAME docker compose -p bif-$NAME ${PROFILES[*]:-} up -d --build"
    return 0
  fi
  hdr "Подъём контейнера"
  echo "  COPY=$NAME docker compose -p bif-$NAME ${PROFILES[*]:-} up -d --build"
  if command -v docker >/dev/null 2>&1; then
    COPY="$NAME" docker compose -p "bif-$NAME" ${PROFILES[@]+"${PROFILES[@]}"} up -d --build
    c_ok "копия «$NAME» поднята"
  else
    c_no "docker не найден — запусти вручную команду выше"
  fi
}

# ── итог + Google пост-шаг ────────────────────────────────────────────────
summary(){
  hdr "Готово. Включено:"
  printf '  core+DeepSeek+Telegram+rich  ✅\n'
  [ "$W_PPLX" = 1 ]    && echo "  Perplexity ✅"
  [ "$W_HMEM" = 1 ]    && echo "  hmem ✅"
  [ "$W_OPENAI" = 1 ]  && echo "  OpenAI картинки/голос ✅"
  [ "$W_VOICE" = 1 ]   && echo "  Voice (STT/TTS) ✅"
  [ "$W_TRACKER" = 1 ] && echo "  Task-tracker ✅"
  [ "$W_CODEX" = 1 ]   && echo "  Codex (сайдкар) ✅"
  [ "$W_SITE" = 1 ]    && echo "  Автодеплой сайта ✅"
  if [ "$W_GOOGLE" = 1 ]; then
    hdr "⚠️ Осталось донастроить: Google OAuth (headless невозможен)"
    echo "  1) Google Cloud → OAuth-клиент Desktop, включить Gmail/Calendar/Drive/Sheets/Docs/People."
    echo "  2) ОБЯЗАТЕЛЬНО Publish app → In production (иначе refresh-токен умрёт за 7 дней)."
    echo "  3) client_secret.json в том копии, пройти мастер внутри контейнера (README → Google). Ссылку — в Chrome."
  fi
  echo
  echo "  Логи:   COPY=$NAME docker compose -p bif-$NAME logs -f hermes"
  echo "  Проверка ключей позже:  ./install.sh --check $NAME"
}

# ── derive W_* from preset + explicit overrides + наличие ключей ──────────
resolve_flags(){ # uses PRESET + env WITH_* overrides + OA_KEY/PX_KEY/OR_KEY
  apply_preset "${PRESET:-standard}"
  W_OPENAI="${WITH_OPENAI:-$P_OPENAI}"; W_PPLX="${WITH_PERPLEXITY:-$P_PPLX}"
  W_HMEM="${WITH_HMEM:-$P_HMEM}"; W_VOICE="${WITH_VOICE:-$P_VOICE}"
  W_GOOGLE="${WITH_GOOGLE:-$P_GOOGLE}"; W_TRACKER="${WITH_TRACKER:-$P_TRACKER}"
  W_CODEX="${WITH_CODEX:-$P_CODEX}"; W_SITE="${WITH_SITE:-$P_SITE}"
  # блок без ключа → выключить + предупредить
  [ "$W_OPENAI" = 1 ] && [ -z "${OA_KEY:-}" ] && { c_warn "OpenAI без ключа → выключаю блок"; W_OPENAI=0; }
  [ "$W_PPLX" = 1 ]   && [ -z "${PX_KEY:-}" ] && { c_warn "Perplexity без ключа → выключаю блок"; W_PPLX=0; }
  return 0
}

# ════════════════════════ MODE: --check ════════════════════════
if [ "${1:-}" = "--check" ]; then
  NAME="${2:?укажи имя копии: ./install.sh --check имя}"; ENVF="copies/$NAME.env"
  [ -f "$ENVF" ] || { c_no "нет $ENVF"; exit 1; }
  # парсим .env буквально (без `. "$ENVF"`, чтобы значения ключей не исполнялись как shell)
  while IFS='=' read -r k v; do
    case "$k" in ''|\#*) continue;; esac
    [[ "$k" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    export "$k=$v"
  done < "$ENVF"
  hdr "Проверка ключей копии «$NAME»"
  val_deepseek "${DEEPSEEK_API_KEY:-}" || true
  [ -n "${OPENAI_API_KEY:-}" ] && { val_openai "$OPENAI_API_KEY" || true; }
  [ -n "${PERPLEXITY_API_KEY:-}" ] && { val_pplx "$PERPLEXITY_API_KEY" || true; }
  val_tg "${TELEGRAM_BOT_TOKEN:-}" || true
  exit 0
fi

# ════════════════════════ MODE: --headless ════════════════════════
if [ "${1:-}" = "--headless" ]; then
  NAME="${NAME:?headless: нужен NAME}"
  NAME="$(norm_name "$NAME")" || { c_no "headless: NAME только латиница a-z, цифры, _ и - (lowercase, без пробелов/кириллицы)"; exit 1; }
  TG_TOKEN="${TELEGRAM_BOT_TOKEN:?headless: нужен TELEGRAM_BOT_TOKEN}"
  TG_ID="${TELEGRAM_ALLOWED_USERS:?headless: нужен TELEGRAM_ALLOWED_USERS}"
  val_tgid "$TG_ID" || c_warn "TELEGRAM_ALLOWED_USERS выглядит подозрительно (ожидаю цифры/запятые): '$TG_ID'"
  DS_KEY="${DEEPSEEK_API_KEY:?headless: нужен DEEPSEEK_API_KEY}"
  TG_HOME="${TELEGRAM_HOME_CHANNEL:-$TG_ID}"
  OA_KEY="${OPENAI_API_KEY:-}"; PX_KEY="${PERPLEXITY_API_KEY:-}"; OR_KEY="${OPENROUTER_API_KEY:-}"
  hdr "BIF headless — копия «$NAME» (preset=${PRESET:-standard})"
  resolve_flags
  if [ "${BIF_VALIDATE:-1}" = 1 ]; then
    hdr "Живая валидация ключей"
    rc=0
    val_deepseek "$DS_KEY" || rc=1
    val_tg "$TG_TOKEN" || rc=1
    [ -n "$OA_KEY" ] && { val_openai "$OA_KEY" || true; }
    [ -n "$PX_KEY" ] && { val_pplx "$PX_KEY" || true; }
    [ "$rc" = 1 ] && c_warn "обязательный ключ (DeepSeek/Telegram) не прошёл — копия не поднимется корректно"
  fi
  write_env
  bring_up
  summary
  exit 0
fi

# ════════════════════════ MODE: интерактивный мастер ════════════════════════
[ -t 0 ] || { c_no 'нет интерактивного ввода (нет TTY) — используй ./install.sh --headless (см. шапку скрипта)'; exit 1; }
hdr "════ BIF v1.0 — установка копии ════"
echo "  Ключи вводятся видимо и проверяются сразу; при ошибке — повтор."
echo "  ❌ иногда значит заблокированный исходящий HTTPS, а не плохой ключ —"
echo "  тогда можно оставить ключ (s) или пропустить проверки: BIF_VALIDATE=0 ./install.sh"
NAME="$(ask 'Имя копии (латиницей, напр. petrov)')"
NAME="$(norm_name "$NAME")" || { c_no "имя копии: только латиница a-z, цифры, _ и - (без пробелов/заглавных/кириллицы)"; exit 1; }

hdr "Шаг 1. Telegram-бот (обязательно)"
echo "  Открой @BotFather → /newbot → пришли токен."
askk TG_TOKEN 'Telegram bot token' val_tg 1
echo "  Свой Telegram ID узнать: @userinfobot"
TG_ID="$(ask 'Твой Telegram ID (whitelist)')"
until val_tgid "$TG_ID"; do
  c_no "ID — это число (несколько ID — через запятую без пробелов), напр. 123456789"
  TG_ID="$(ask 'Твой Telegram ID (whitelist)')"
done
TG_HOME="$TG_ID"

hdr "Шаг 2. Мозг (обязательно)"
echo "  Ключ: platform.deepseek.com"
askk DS_KEY 'DeepSeek API key' val_deepseek 1

hdr "Шаг 3. Профиль установки"
echo "  1) minimal  — мозг + Telegram + Perplexity + файловая память"
echo "  2) standard — minimal + hmem + картинки + голос + Google + трекер   [рекоменд.]"
echo "  3) full     — standard + Codex + автодеплой сайта"
echo "  4) custom   — выбрать блоки вручную"
PROF="$(ask 'Профиль' 2)"

OA_KEY=""; PX_KEY=""; OR_KEY=""
case "$PROF" in
  1|minimal)  PRESET=minimal ;;
  3|full)     PRESET=full ;;
  4|custom)
    PRESET=custom
    hdr "Custom — да/нет по каждому блоку"
    WITH_OPENAI=$(yn "Картинки/голос-OpenAI (image_gen gpt-image-2)?" Y)
    WITH_PERPLEXITY=$(yn "Веб-поиск (Perplexity)?" Y)
    WITH_HMEM=$(yn "Банк памяти + индексация (hmem)?" Y)
    WITH_VOICE=$(yn "Голос локально (STT/TTS)?" Y)
    WITH_GOOGLE=$(yn "Google (почта/календарь/диск)?" N)
    WITH_TRACKER=$(yn "Трекер задач + напоминания?" Y)
    WITH_CODEX=$(yn "Codex (тяжёлый сайдкар)?" N)
    WITH_SITE=$(yn "Автодеплой сайта (Caddy)?" N) ;;
  *)          PRESET=standard ;;
esac
# для custom пресет не важен — флаги уже заданы в WITH_*; для остальных пресет задаёт дефолты
# ключи под блоки (спросим до resolve, чтобы resolve мог выключить блок без ключа)
apply_preset "${PRESET}"
NEED_OPENAI="${WITH_OPENAI:-$P_OPENAI}"; NEED_PPLX="${WITH_PERPLEXITY:-$P_PPLX}"
[ "$NEED_OPENAI" = 1 ] && { hdr "Ключ OpenAI (platform.openai.com)"; askk OA_KEY 'OPENAI_API_KEY' val_openai 0; }
[ "$NEED_PPLX" = 1 ]   && { hdr "Ключ Perplexity (perplexity.ai/settings/api)"; askk PX_KEY 'PERPLEXITY_API_KEY' val_pplx 0; }
if [ "$(yn 'Добавить OpenRouter (Nemotron/запасной LLM)?' N)" = 1 ]; then
  hdr "Ключ OpenRouter (openrouter.ai/keys)"; askk OR_KEY 'OPENROUTER_API_KEY' '' 0
fi

resolve_flags
# Ключи уже проверены вживую на шагах ввода (askk с валидатором). Отдельный шаг проверки не нужен.

write_env
bring_up
summary
