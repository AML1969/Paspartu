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

# ── Картинки: IMAGE_PROVIDER ∈ QWEN|GPT|NONE ──────────────────────────────
# Пусто/не задано (старые copies/*.env без переменной) = АВТО: FAL_KEY есть → QWEN;
# иначе OPENAI_API_KEY есть и WITH_OPENAI включён → GPT (прежнее поведение — старые
# копии НЕ теряют картинки); иначе NONE. WITH_OPENAI картинками БОЛЬШЕ НЕ управляет —
# он остаётся только за vision (анализ присланных фото).
IMAGE_PROVIDER="$(printf '%s' "${IMAGE_PROVIDER:-}" | tr 'a-z' 'A-Z')"
case "$IMAGE_PROVIDER" in
  QWEN|GPT|NONE|'') ;;
  *) echo "[entrypoint] ⚠️ IMAGE_PROVIDER='$IMAGE_PROVIDER' не распознан (жду QWEN|GPT|NONE) → АВТО"; IMAGE_PROVIDER="" ;;
esac
if [ -z "$IMAGE_PROVIDER" ]; then
  if [ -n "${FAL_KEY:-}" ]; then IMAGE_PROVIDER=QWEN
  elif [ -n "${OPENAI_API_KEY:-}" ] && on "$WITH_OPENAI"; then IMAGE_PROVIDER=GPT
  else IMAGE_PROVIDER=NONE; fi
fi
# провайдер без своего ключа → NONE (иначе image_generate упадёт на первом же вызове)
if [ "$IMAGE_PROVIDER" = QWEN ] && [ -z "${FAL_KEY:-}" ]; then
  echo "[entrypoint] ⚠️ IMAGE_PROVIDER=QWEN без FAL_KEY → картинки выключены (NONE)"; IMAGE_PROVIDER=NONE
fi
if [ "$IMAGE_PROVIDER" = GPT ] && [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "[entrypoint] ⚠️ IMAGE_PROVIDER=GPT без OPENAI_API_KEY → картинки выключены (NONE)"; IMAGE_PROVIDER=NONE
fi

# ── Обязательные переменные ──────────────────────────────────────────────
: "${TELEGRAM_BOT_TOKEN:?нужен TELEGRAM_BOT_TOKEN}"
: "${TELEGRAM_ALLOWED_USERS:?нужен TELEGRAM_ALLOWED_USERS}"
: "${DEEPSEEK_API_KEY:?нужен DEEPSEEK_API_KEY}"

# Гейт первого запуска — отдельный сентинел, который ставится ПОСЛЕДНИМ (а не
# config.yaml, который пишется первым): если setup упадёт на середине, рестарт
# повторит раскладку целиком, а не застрянет в полубитом состоянии.
FIRST_RUN=0
[ ! -f "$HERMES_HOME/.setup_done" ] && FIRST_RUN=1

# ── Первый запуск: конфиг + скиллы ───────────────────────────────────────
if [ "$FIRST_RUN" = 1 ]; then
  echo "[entrypoint] первый запуск: раскладываю блоки в $HERMES_HOME"
  cp "$SEED/config.yaml" "$HERMES_HOME/config.yaml"
  sed -i "s#^  cwd: .*#  cwd: ${WORKSPACE}#" "$HERMES_HOME/config.yaml"

  mkdir -p "$HERMES_HOME/skills"
  # ГРАБЛИ: make-seed.sh кладёт в тарбол верхний каталог skills/ → без strip
  # получалось $HERMES_HOME/skills/skills/... и Hermes не видел НИ ОДНОГО скилла.
  # ВНИМАНИЕ: тут НЕЛЬЗЯ писать `tar ... | head -1` — при set -o pipefail tar ловит SIGPIPE
  # (код 141), условие уходит в else, strip не применяется и снова получается skills/skills.
  SKILLS_TOP="$(tar tzf "$SEED/skills.tar.gz" 2>/dev/null | sed -n 1p || true)"
  case "$SKILLS_TOP" in
    skills/*|skills)
      tar xzf "$SEED/skills.tar.gz" -C "$HERMES_HOME/skills" --strip-components=1 ;;
    *)
      tar xzf "$SEED/skills.tar.gz" -C "$HERMES_HOME/skills" ;;
  esac

  # Блочная прополка скиллов: убрать то, что выключено
  on "$WITH_TRACKER"    || rm -rf "$HERMES_HOME/skills/productivity/task-tracker"
  on "$WITH_PERPLEXITY" || rm -rf "$HERMES_HOME/skills/openclaw-imports/perplexity"
  # qwen-image-edit (редактирование фото) работает по FAL_KEY независимо от провайдера генерации
  [ -n "${FAL_KEY:-}" ]  || rm -rf "$HERMES_HOME/skills/media/qwen-image-edit"

  # Конфиг-правки под блоки (pyyaml идёт с hermes-agent).
  # Картинки (image_gen) тут НЕ трогаем — ими управляет IMAGE_PROVIDER отдельным
  # идемпотентным шагом ниже, который работает на КАЖДОМ старте (не только первом).
  python - "$HERMES_HOME/config.yaml" "$WITH_OPENAI" "$WITH_VOICE" "$HERMES_RICH_MESSAGES" <<'PY'
import sys,yaml
f,wo,wv,wr=sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4]
c=yaml.safe_load(open(f))
def truthy(x): return str(x).lower() in ("1","y","yes","true","on")
# WITH_OPENAI выключен → снять ТОЛЬКО тулсет vision (анализ присланных фото).
# image_gen развязан с WITH_OPENAI и управляется IMAGE_PROVIDER (шаг ниже).
if not truthy(wo):
    pts=c.get("platform_toolsets",{}).get("cli")
    if pts: c["platform_toolsets"]["cli"]=[t for t in pts if t!="vision"]
# Голос выключен → STT off
if not truthy(wv):
    c.setdefault("stt",{})["enabled"]=False
    pts=c.get("platform_toolsets",{}).get("cli")
    if pts: c["platform_toolsets"]["cli"]=[t for t in pts if t!="tts"]
# Streaming/rich — рантайм-тумблер дублируем в конфиг для наглядности
c.setdefault("gateway",{}).setdefault("streaming",{})["enabled"]=truthy(wr)
yaml.safe_dump(c,open(f,"w"),sort_keys=False,allow_unicode=True)
print("[entrypoint] config: vision(openai)=%s voice=%s rich=%s"%(wo,wv,wr))
PY

  # Файловая память сразу (встроенная переполняется ~2200 символов)
  [ -f "$HERMES_HOME/MEMORY.md" ] || cp "$SEED/MEMORY.md" "$HERMES_HOME/MEMORY.md"
  # web_direct: бескключевой extract-провайдер (иначе web_extract падает в ddgs)
  if [ -d "$SEED/web_direct" ]; then mkdir -p "$HERMES_HOME/plugins"; cp -a "$SEED/web_direct" "$HERMES_HOME/plugins/web_direct"; fi

  # ── AGENTS.md рабочей папки + вырезка hmem-блока, если hmem выключен ──
  sed -e "s#__HERMES_HOME__#${HERMES_HOME}#g" -e "s#__WORKSPACE__#${WORKSPACE}#g" "$SEED/AGENTS.md" > "$WORKSPACE/AGENTS.md"
  if ! on "$WITH_HMEM"; then
    sed -i '/<!-- hmem:begin -->/,/<!-- hmem:end -->/d' "$WORKSPACE/AGENTS.md"
  fi
  # Картинок нет вообще (ни генерации, ни ключа fal) → вырезать блок правил про них
  if [ "$IMAGE_PROVIDER" = NONE ] && [ -z "${FAL_KEY:-}" ]; then
    sed -i '/<!-- image:begin -->/,/<!-- image:end -->/d' "$WORKSPACE/AGENTS.md"
  fi

  # ── hmem (банк памяти) — опциональный блок ──
  if on "$WITH_HMEM"; then
    mkdir -p "$HERMES_HOME/bin"
    tar xzf "$SEED/hmem-bin.tar.gz" -C "$HERMES_HOME/bin"
    # ГРАБЛИ: обёртка hmem в сиде снята verbatim с pipx-сервера и хардкодит
    # /root/.hermes/bin/hmem.py + /root/hermes-workspace → в контейнере это НЕ существует,
    # банк памяти молча не работал. Перегенерируем обёртку под docker-раскладку.
    cat > "$HERMES_HOME/bin/hmem" <<HMEM
#!/bin/sh
# Ядро: $HERMES_HOME/bin/hmem.py (форк GitMark, MIT).
exec python3 "$HERMES_HOME/bin/hmem.py" --root "$WORKSPACE" "\$@"
HMEM
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
      # путь к perplexity-скрипту подставляем в SOUL только если блок включён;
      # иначе скилл уже удалён (см. выше) — не зашиваем в душу мёртвый путь.
      if on "$WITH_PERPLEXITY"; then
        PXPATH="$HERMES_HOME/skills/openclaw-imports/perplexity/scripts/search.mjs"
      else
        PXPATH="(Perplexity отключён — используй встроенный web_search)"
      fi
      # Память: правило «только файлы» — иначе модель зовёт отключённый memory-инструмент
      # и молча теряет «запомни, что…» (ловили у Игоря, у Андрея и у Руслана 2026-07-11).
      if on "$WITH_HMEM"; then
        cat "$SEED/MEMORY_BLOCK.md" >> "$HERMES_HOME/SOUL.md"
      fi
      # Картинки: квитанции/доставка/qwen-image-edit. Идемпотентно по маркеру
      # «Квитанция перед генерацией» — повторно не дописывается.
      if [ "$IMAGE_PROVIDER" != NONE ] && [ -f "$SEED/IMAGE_BLOCK.md" ] \
         && ! grep -q 'Квитанция перед генерацией' "$HERMES_HOME/SOUL.md"; then
        cat "$SEED/IMAGE_BLOCK.md" >> "$HERMES_HOME/SOUL.md"
      fi
      if on "$WITH_CODEX"; then
        sed -e "s#{CODEX_TASK_SH}#/data/codex-jobs/codex_task.sh#g" \
            -e "s#{CODEX_ENABLED_FILE}#/data/codex/enabled#g" \
            "$SEED/CODEX_BLOCK.md" >> "$HERMES_HOME/SOUL.md"
        cat "$SEED/codex/CODEX_PROVIDER_BLOCK.md" >> "$HERMES_HOME/SOUL.md" 2>/dev/null || true
      fi
      if on "$WITH_PERPLEXITY" || on "$WITH_CODEX"; then
        sed -e "s#{ПУТЬ_PERPLEXITY}#${PXPATH}#g" \
            -e "s#{ИМЯ_ПОЛЬЗОВАТЕЛЯ}#пользователь#g" \
            "$SEED/ROUTING_BLOCK.md" >> "$HERMES_HOME/SOUL.md"
      fi
    fi
  fi
  touch "$HERMES_HOME/.setup_done"   # сентинел завершённого первого запуска (ставится последним)
fi

# ── Картинки: приведение config.yaml к IMAGE_PROVIDER — на КАЖДОМ старте ──
# У живых копий (том существует, FIRST_RUN=0) первичный патчер не срабатывает —
# без этого шага смена провайдера не доехала бы до конфига. Правим ТОЛЬКО
# image_gen-часть: plugins.enabled (image_gen/fal | image_gen/openai), блок
# image_gen и тулсет image_gen в platform_toolsets.cli; остальное не трогаем.
# Идемпотентно: конфиг уже соответствует → no-op (файл не переписывается).
# При первом реальном изменении живого тома — бэкап config.yaml.bak-imageprov.
python - "$HERMES_HOME/config.yaml" "$IMAGE_PROVIDER" "$FIRST_RUN" <<'PY'
import os,shutil,sys,yaml
f,ip,first=sys.argv[1],sys.argv[2],sys.argv[3]
c=yaml.safe_load(open(f)) or {}
before=yaml.safe_dump(c,sort_keys=False,allow_unicode=True)
pl=c.setdefault("plugins",{})
en=[p for p in (pl.get("enabled") or []) if not str(p).startswith("image_gen")]
pts=c.get("platform_toolsets",{}).get("cli")
want={"QWEN":("image_gen/fal",{"provider":"fal","model":"fal-ai/qwen-image-2/text-to-image"}),
      "GPT":("image_gen/openai",{"provider":"openai","model":"gpt-image-2-medium"})}.get(ip)
if want:
    en.append(want[0]); c["image_gen"]=want[1]
    if isinstance(pts,list) and "image_gen" not in pts: pts.append("image_gen")
else:  # NONE — снять плагин и тулсет; сам блок image_gen оставляем (инертен без плагина,
       # ровно как делал старый WITH_OPENAI=0-патчер — схему конфига не трогаем)
    if isinstance(pts,list) and "image_gen" in pts: pts[:]=[t for t in pts if t!="image_gen"]
pl["enabled"]=en
after=yaml.safe_dump(c,sort_keys=False,allow_unicode=True)
if after!=before:
    bak=f+".bak-imageprov"
    if first!="1" and not os.path.exists(bak): shutil.copy2(f,bak)
    open(f,"w").write(after)
    print("[entrypoint] image_gen мигрирован под IMAGE_PROVIDER=%s"%ip)
else:
    print("[entrypoint] image_gen: %s (конфиг уже соответствует)"%ip)
PY

# ── Картинки: миграция ЖИВЫХ томов (скилл + SOUL + AGENTS) — на КАЖДОМ старте ──
# Скиллы/SOUL/AGENTS раскладываются только при FIRST_RUN. У копий, созданных ДО
# появления Qwen (том уже есть, .setup_done стоит), без этого шага не было бы ни
# скилла редактирования, ни правил про квитанцию и доставку. Всё идемпотентно:
# уже на месте → no-op.
# Правила про картинки завязаны на СКИЛЛ (он живёт по FAL_KEY), а не на провайдера:
# у GPT-копии без FAL_KEY скилла нет — и правила «работай скиллом qwen-image-edit»
# были бы враньём (нашло ревью 14.07). Нет ключа → блок снимаем с живого тома.
if [ -n "${FAL_KEY:-}" ]; then
  # Скилл qwen-image-edit — наш артефакт (не пользовательский контент): держим
  # синхронным с сидом, чтобы фиксы доезжали до живых копий при пересборке.
  _tmp="$(mktemp -d)"
  if tar xzf "$SEED/skills.tar.gz" -C "$_tmp" 2>/dev/null; then
    _src="$(find "$_tmp" -type d -name qwen-image-edit -print -quit 2>/dev/null || true)"
    if [ -n "$_src" ]; then
      mkdir -p "$HERMES_HOME/skills/media"
      if ! diff -r -q "$_src" "$HERMES_HOME/skills/media/qwen-image-edit" >/dev/null 2>&1; then
        rm -rf "$HERMES_HOME/skills/media/qwen-image-edit"
        cp -r "$_src" "$HERMES_HOME/skills/media/qwen-image-edit"
        echo "[entrypoint] скилл qwen-image-edit развёрнут/обновлён из сида"
      fi
    fi
  fi
  rm -rf "$_tmp"
else
  # Ключа нет — скилл бесполезен (и будет сыпать ошибками): убрать
  rm -rf "$HERMES_HOME/skills/media/qwen-image-edit"
fi

if [ -n "${FAL_KEY:-}" ] && [ -f "$SEED/IMAGE_BLOCK.md" ]; then
  # SOUL и AGENTS: блок правил про картинки ОБНОВЛЯЕТСЯ (а не дописывается один раз).
  # ГРАБЛЯ 14.07: первая версия просто делала append под guard «блок уже есть» — и
  # следующая, исправленная редакция правил до живых копий уже не доезжала. Теперь
  # блок обёрнут маркерами image:begin/end и заменяется целиком (мигратор снимает и
  # легаси-блок без маркеров у копий первой волны).
  sed -e "s#__HERMES_HOME__#${HERMES_HOME}#g" -e "s#__WORKSPACE__#${WORKSPACE}#g" \
      "$SEED/IMAGE_BLOCK.md" > /tmp/image_block.md
  for F in "$HERMES_HOME/SOUL.md" "$WORKSPACE/AGENTS.md"; do
    [ -f "$F" ] || continue
    if ! cmp -s <(sed -n '/<!-- image:begin -->/,/<!-- image:end -->/p' "$F") /tmp/image_block.md; then
      python "$SEED/apply_image_block.py" "$F" /tmp/image_block.md
    fi
  done
  # Старый codex-guard в живом SOUL говорил «без GPU не делается → Photoshop» —
  # теперь редактирование умеет скилл. Правим формулировку на месте.
  if [ -f "$HERMES_HOME/SOUL.md" ] && grep -q 'Photoshop Generative Fill' "$HERMES_HOME/SOUL.md"; then
    sed -i 's#Для точечного редактирования реального фото (заменить объект на фото) сразу скажи, что на этом сервере без GPU это не делается, и предложи внешний инструмент (Photoshop Generative Fill / Stability AI API)\.#Для точечного редактирования реального фото (заменить объект на фото) используй скилл qwen-image-edit (Qwen-Image 2.0 по API, GPU не нужен).#' "$HERMES_HOME/SOUL.md"
    echo "[entrypoint] SOUL.md: codex-guard обновлён (редактирование фото → qwen-image-edit)"
  fi
else
  # Ключа fal нет → скилла нет → снять блок правил про него с живого тома, иначе
  # модель будет звать несуществующий скрипт (ревью 14.07).
  for F in "$HERMES_HOME/SOUL.md" "$WORKSPACE/AGENTS.md"; do
    [ -f "$F" ] && grep -q '<!-- image:begin -->' "$F" \
      && sed -i '/<!-- image:begin -->/,/<!-- image:end -->/d' "$F" \
      && echo "[entrypoint] $(basename "$F"): блок правил про картинки снят (нет FAL_KEY)"
  done
fi

# ── .env копии — только релевантные ключи под выбранные блоки ──
GW_TOKEN="${HERMES_GATEWAY_TOKEN:-$(openssl rand -hex 24)}"
{
  echo "DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}"
  # OPENAI_API_KEY нужен и vision (WITH_OPENAI), и картинкам GPT (IMAGE_PROVIDER)
  { on "$WITH_OPENAI" || [ "$IMAGE_PROVIDER" = GPT ]; } && echo "OPENAI_API_KEY=${OPENAI_API_KEY:-}"
  on "$WITH_OPENROUTER" && echo "OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}"
  on "$WITH_PERPLEXITY" && echo "PERPLEXITY_API_KEY=${PERPLEXITY_API_KEY:-}"
  # FAL_KEY нужен и плагину image_gen/fal, и скиллу qwen-image-edit — пишем при наличии
  [ -n "${FAL_KEY:-}" ]  && echo "FAL_KEY=${FAL_KEY}"
  echo "TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}"
  echo "TELEGRAM_ALLOWED_USERS=${TELEGRAM_ALLOWED_USERS}"
  echo "TELEGRAM_HOME_CHANNEL=${TELEGRAM_HOME_CHANNEL:-${TELEGRAM_ALLOWED_USERS%%,*}}"
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

# ── Codex-эскалация (bif:1.2): раннер внутри контейнера ────────────────────
if on "$WITH_CODEX"; then
  mkdir -p /data/hermes/logs
  if bash /opt/hermes-seed/codex/codex_setup.sh; then
    ( while :; do bash /data/codex-jobs/codex_watch_tick.sh >> /data/hermes/logs/codex_watch.log 2>&1 || true; sleep 180; done ) &
    echo "[entrypoint] codex: раннер готов, watch-цикл запущен (180s)"
  else
    echo "[entrypoint] ⚠️ codex_setup не прошёл — Codex-блок пропущен"
  fi
fi

echo "[entrypoint] блоки: openai(vision)=$WITH_OPENAI image=$IMAGE_PROVIDER openrouter=$WITH_OPENROUTER perplexity=$WITH_PERPLEXITY hmem=$WITH_HMEM voice=$WITH_VOICE tracker=$WITH_TRACKER google=$WITH_GOOGLE codex=$WITH_CODEX site=$WITH_SITE rich=$HERMES_RICH_MESSAGES"
echo "[entrypoint] старт gateway (HERMES_HOME=$HERMES_HOME)"
# hmem должен быть в PATH при КАЖДОМ старте (/usr/local/bin — слой образа, не том)
if [ -x "$HERMES_HOME/bin/hmem" ]; then ln -sf "$HERMES_HOME/bin/hmem" /usr/local/bin/hmem; fi

exec hermes gateway run
