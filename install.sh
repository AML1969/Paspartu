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
#   [FAL_KEY=key_id:key_secret] [IMAGE_PROVIDER=QWEN|GPT|NONE] \
#   [WITH_CODEX=1 WITH_SITE=1 WITH_GOOGLE=0 ...]  [NO_COMPOSE=1] [BIF_VALIDATE=0] \
#   ./install.sh --headless
#   PRESET ∈ minimal|standard|full (default standard); явные WITH_* перекрывают пресет.
#   IMAGE_PROVIDER пуст/не задан = АВТО: FAL_KEY→QWEN, иначе OPENAI_API_KEY(+WITH_OPENAI)→GPT, иначе NONE.
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
val_pplx(){ local c; c=$(curl -s -m 12 -o /dev/null -w '%{http_code}' https://api.perplexity.ai/chat/completions -H "Authorization: Bearer $1" -H 'Content-Type: application/json' -d '{"model":"sonar","messages":[{"role":"user","content":"ping"}],"max_tokens":16}'); case "$c" in 401|403) c_no "Perplexity — ключ отклонён (401/403)"; return 1;; 000) c_no "Perplexity — сеть недоступна, ключ не проверен"; return 0;; *) c_ok "Perplexity (HTTP $c)"; return 0;; esac; }
val_tg(){ local r; r=$(curl -fsS -m 12 "https://api.telegram.org/bot$1/getMe" 2>/dev/null||true); case "$r" in *'"ok":true'*) c_ok "Telegram $(echo "$r"|grep -o '"username":"[^"]*"'|cut -d'"' -f4|sed 's/^/@/')"; return 0;; *) c_no "Telegram getMe — токен отклонён"; return 1;; esac; }
# fal.ai (картинки Qwen): лёгкий GET статуса несуществующего queue-запроса — генерацию
# не запускает и денег не тратит. 401/403 = плохой ключ; 404/422 и прочее = ключ принят.
val_fal(){ local c; c=$(curl -s -m 12 -o /dev/null -w '%{http_code}' -H "Authorization: Key $1" "https://queue.fal.run/fal-ai/qwen-image/requests/00000000-0000-0000-0000-000000000000/status"); case "$c" in 401|403) c_no "FAL — ключ отклонён (401/403)"; return 1;; 000) c_no "FAL — сеть недоступна, ключ не проверен"; return 0;; *) c_ok "FAL (HTTP $c)"; return 0;; esac; }

# ── пресет → тумблеры (явные WITH_* потом перекрывают) ────────────────────
apply_preset(){ # $1=preset → выставляет P_* по умолчанию
  P_OPENAI=0; P_PPLX=0; P_HMEM=0; P_VOICE=0; P_GOOGLE=0; P_TRACKER=0; P_CODEX=0; P_SITE=0
  P_IMAGE=""   # картинки: во всех пресетах АВТО (FAL_KEY→QWEN, иначе OpenAI→GPT, иначе NONE)
  case "$1" in
    minimal) P_PPLX=1 ;;
    full)    P_OPENAI=1; P_PPLX=1; P_HMEM=1; P_VOICE=1; P_GOOGLE=1; P_TRACKER=1; P_CODEX=1; P_SITE=1 ;;
    *)       P_OPENAI=1; P_PPLX=1; P_HMEM=1; P_VOICE=1; P_GOOGLE=1; P_TRACKER=1 ;;  # standard
  esac
}

# ── запись copies/<name>.env ──────────────────────────────────────────────
write_env(){ # uses NAME + DS_KEY TG_TOKEN TG_ID + OA_KEY PX_KEY OR_KEY FAL_KEY + W_* + IMAGE_PROVIDER
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
TELEGRAM_HOME_CHANNEL=$TG_HOME
${OA_KEY:+OPENAI_API_KEY=$OA_KEY}
${PX_KEY:+PERPLEXITY_API_KEY=$PX_KEY}
${OR_KEY:+OPENROUTER_API_KEY=$OR_KEY}
${FAL_KEY:+FAL_KEY=$FAL_KEY}
IMAGE_PROVIDER=${IMAGE_PROVIDER:-}
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
  [ "$W_OPENAI" = 1 ]  && echo "  OpenAI vision (анализ присланных фото) ✅"
  case "${IMAGE_PROVIDER:-NONE}" in
    QWEN) echo "  Картинки: Qwen-Image (fal.ai) ✅";;
    GPT)  echo "  Картинки: OpenAI gpt-image-2 ✅";;
    *)    echo "  Картинки: выключены";;
  esac
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
resolve_flags(){ # uses PRESET + env WITH_*/IMAGE_PROVIDER overrides + OA_KEY/PX_KEY/OR_KEY/FAL_KEY
  apply_preset "${PRESET:-standard}"
  W_OPENAI="${WITH_OPENAI:-$P_OPENAI}"; W_PPLX="${WITH_PERPLEXITY:-$P_PPLX}"
  W_HMEM="${WITH_HMEM:-$P_HMEM}"; W_VOICE="${WITH_VOICE:-$P_VOICE}"
  W_GOOGLE="${WITH_GOOGLE:-$P_GOOGLE}"; W_TRACKER="${WITH_TRACKER:-$P_TRACKER}"
  W_CODEX="${WITH_CODEX:-$P_CODEX}"; W_SITE="${WITH_SITE:-$P_SITE}"
  # блок без ключа → выключить + предупредить
  [ "$W_OPENAI" = 1 ] && [ -z "${OA_KEY:-}" ] && { c_warn "OpenAI без ключа → выключаю блок"; W_OPENAI=0; }
  [ "$W_PPLX" = 1 ]   && [ -z "${PX_KEY:-}" ] && { c_warn "Perplexity без ключа → выключаю блок"; W_PPLX=0; }
  # картинки: IMAGE_PROVIDER ∈ QWEN|GPT|NONE, пусто = АВТО по ключам.
  # WITH_OPENAI картинками БОЛЬШЕ НЕ управляет (он теперь только vision).
  IMAGE_PROVIDER="$(printf '%s' "${IMAGE_PROVIDER:-$P_IMAGE}" | tr 'a-z' 'A-Z')"
  case "$IMAGE_PROVIDER" in
    QWEN|GPT|NONE|'') ;;
    *) c_warn "IMAGE_PROVIDER='$IMAGE_PROVIDER' не распознан (жду QWEN|GPT|NONE) → АВТО"; IMAGE_PROVIDER="" ;;
  esac
  if [ -z "$IMAGE_PROVIDER" ]; then
    if [ -n "${FAL_KEY:-}" ]; then IMAGE_PROVIDER=QWEN
    elif [ -n "${OA_KEY:-}" ] && [ "$W_OPENAI" = 1 ]; then IMAGE_PROVIDER=GPT
    else IMAGE_PROVIDER=NONE; fi
  fi
  # авто-даунгрейд: выбранный провайдер без своего ключа → NONE + предупреждение
  if [ "$IMAGE_PROVIDER" = QWEN ] && [ -z "${FAL_KEY:-}" ]; then
    c_warn "IMAGE_PROVIDER=QWEN без FAL_KEY → картинки выключаю (NONE)"; IMAGE_PROVIDER=NONE
  fi
  if [ "$IMAGE_PROVIDER" = GPT ] && [ -z "${OA_KEY:-}" ]; then
    c_warn "IMAGE_PROVIDER=GPT без OPENAI_API_KEY → картинки выключаю (NONE)"; IMAGE_PROVIDER=NONE
  fi
  return 0
}

# ── живая валидация набора ключей (общая для --check и --headless) ─────────
# validate_keys DS TG OA PX FAL STRICT
#   Пингует каждый непустой ключ. STRICT=1 → вернёт 1, если DeepSeek/Telegram
#   (обязательные) не прошли; OpenAI/Perplexity/FAL всегда некритичны. STRICT=0 →
#   только печать, всегда 0 (режим --check).
validate_keys(){
  local ds="$1" tg="$2" oa="${3:-}" px="${4:-}" fal="${5:-}" strict="${6:-0}" rc=0
  [ -n "$ds" ] && { val_deepseek "$ds" || rc=1; }
  [ -n "$tg" ] && { val_tg "$tg" || rc=1; }
  [ -n "$oa" ] && { val_openai "$oa" || true; }
  [ -n "$px" ] && { val_pplx "$px" || true; }
  [ -n "$fal" ] && { val_fal "$fal" || true; }
  [ "$strict" = 1 ] && return "$rc"
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
  validate_keys "${DEEPSEEK_API_KEY:-}" "${TELEGRAM_BOT_TOKEN:-}" \
                "${OPENAI_API_KEY:-}" "${PERPLEXITY_API_KEY:-}" "${FAL_KEY:-}" 0
  # Согласованность картинок: провайдер из .env должен иметь свой ключ
  case "$(printf '%s' "${IMAGE_PROVIDER:-}" | tr 'a-z' 'A-Z')" in
    QWEN) if [ -z "${FAL_KEY:-}" ]; then c_warn "IMAGE_PROVIDER=QWEN, а FAL_KEY пуст — entrypoint уронит картинки в NONE"; fi ;;
    GPT)  if [ -z "${OPENAI_API_KEY:-}" ]; then c_warn "IMAGE_PROVIDER=GPT, а OPENAI_API_KEY пуст — entrypoint уронит картинки в NONE"; fi ;;
    NONE) echo "  Картинки: выключены (IMAGE_PROVIDER=NONE)" ;;
    '')   if [ -n "${FAL_KEY:-}" ]; then echo "  Картинки: АВТО → QWEN (fal.ai)";
          elif [ -n "${OPENAI_API_KEY:-}" ] && [ "${WITH_OPENAI:-1}" != 0 ]; then echo "  Картинки: АВТО → GPT (OpenAI)";
          else c_warn "картинки: АВТО без FAL_KEY/OPENAI_API_KEY → выключены"; fi ;;
  esac
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
  TG_HOME="${TELEGRAM_HOME_CHANNEL:-${TG_ID%%,*}}"
  OA_KEY="${OPENAI_API_KEY:-}"; PX_KEY="${PERPLEXITY_API_KEY:-}"; OR_KEY="${OPENROUTER_API_KEY:-}"
  FAL_KEY="${FAL_KEY:-}"   # картинки Qwen (fal.ai); IMAGE_PROVIDER тоже берётся из env (пусто = АВТО)
  hdr "BIF headless — копия «$NAME» (preset=${PRESET:-standard})"
  resolve_flags
  if [ "${BIF_VALIDATE:-1}" = 1 ]; then
    hdr "Живая валидация ключей"
    rc=0
    validate_keys "$DS_KEY" "$TG_TOKEN" "$OA_KEY" "$PX_KEY" "$FAL_KEY" 1 || rc=1
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
# home-channel = ПЕРВЫЙ id из whitelist (куда бот шлёт карточки/уведомления);
# whitelist может быть списком «1,2», но канал — один чат.
TG_HOME="${TG_ID%%,*}"

hdr "Шаг 2. Мозг (обязательно)"
echo "  Ключ: platform.deepseek.com"
askk DS_KEY 'DeepSeek API key' val_deepseek 1

hdr "Шаг 3. Профиль установки"
echo "  1) minimal  — мозг + Telegram + Perplexity + файловая память"
echo "  2) standard — minimal + hmem + картинки + голос + Google + трекер   [рекоменд.]"
echo "  3) full     — standard + Codex + автодеплой сайта"
echo "  4) custom   — выбрать блоки вручную"
PROF="$(ask 'Профиль' 2)"

OA_KEY=""; PX_KEY=""; OR_KEY=""; FAL_KEY=""; IMAGE_PROVIDER=""
case "$PROF" in
  1|minimal)  PRESET=minimal ;;
  3|full)     PRESET=full ;;
  4|custom)
    PRESET=custom
    hdr "Custom — да/нет по каждому блоку"
    WITH_OPENAI=$(yn "OpenAI vision (анализ присланных фото)?" Y)
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

hdr "Картинки (генерация изображений) — провайдер"
echo "  1) auto — по ключам: есть FAL_KEY → Qwen; иначе OpenAI → GPT; иначе выкл   [рекоменд.]"
echo "  2) qwen — Qwen-Image на fal.ai (нужен FAL_KEY; дёшево, ~\$0.02/картинка)"
echo "  3) gpt  — OpenAI gpt-image-2 (нужен OPENAI_API_KEY)"
echo "  4) none — без генерации картинок"
IMG_CHOICE="$(ask 'Провайдер картинок' 1)"
case "$IMG_CHOICE" in
  2|qwen|QWEN) IMAGE_PROVIDER=QWEN ;;
  3|gpt|GPT)   IMAGE_PROVIDER=GPT ;;
  4|none|NONE) IMAGE_PROVIDER=NONE ;;
  *)           IMAGE_PROVIDER="" ;;   # auto
esac

NEED_OPENAI="${WITH_OPENAI:-$P_OPENAI}"; NEED_PPLX="${WITH_PERPLEXITY:-$P_PPLX}"
[ "$IMAGE_PROVIDER" = GPT ] && NEED_OPENAI=1   # GPT-картинкам нужен ключ OpenAI
[ "$NEED_OPENAI" = 1 ] && { hdr "Ключ OpenAI (platform.openai.com)"; askk OA_KEY 'OPENAI_API_KEY' val_openai 0; }
[ "$NEED_PPLX" = 1 ]   && { hdr "Ключ Perplexity (perplexity.ai/settings/api)"; askk PX_KEY 'PERPLEXITY_API_KEY' val_pplx 0; }
# FAL-ключ: обязателен при явном QWEN; при AUTO — опционален (Enter — пропустить)
if [ "$IMAGE_PROVIDER" = QWEN ]; then
  hdr "Ключ fal.ai (fal.ai → Keys; формат key_id:key_secret)"
  askk FAL_KEY 'FAL_KEY' val_fal 1
elif [ -z "$IMAGE_PROVIDER" ]; then
  hdr "Ключ fal.ai для Qwen-картинок — опционально (fal.ai → Keys)"
  askk FAL_KEY 'FAL_KEY (key_id:key_secret; Enter — пропустить)' val_fal 0
fi
if [ "$(yn 'Добавить OpenRouter (Nemotron/запасной LLM)?' N)" = 1 ]; then
  hdr "Ключ OpenRouter (openrouter.ai/keys)"; askk OR_KEY 'OPENROUTER_API_KEY' '' 0
fi

resolve_flags
# Ключи уже проверены вживую на шагах ввода (askk с валидатором). Отдельный шаг проверки не нужен.

write_env
bring_up
summary
