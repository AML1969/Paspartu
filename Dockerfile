# syntax=docker/dockerfile:1
# ============================================================================
# BIF — образ изолированных копий Hermes-агента, v1.0
# Источник правды: живой сервер 188.166.122.243 (Hermes Agent v0.16.0).
# Отличие от прошлого образа (0.15.2, без патчей):
#   • та же версия, что на сервере — 0.16.0;
#   • 15 рабочих патчей (rich-формат + карусели + локализация кнопок + sentinel +
#     MCP-реконнект + русское сообщение про лимит 20 МБ)
#     ЗАПЕКАЮТСЯ в образ на build-time, ретаргет pipx-пути на site-packages
#     контейнера; порядок — из манифеста seed/patches/patches.txt (его же читает
#     update_hermes.sh на хосте — единый источник порядка);
#   • блочная архитектура: всё лишнее выключается тумблерами WITH_* в entrypoint.
# ============================================================================
FROM python:3.12-slim

# pipefail для RUN с пайпами (curl | bash ниже) — иначе падение curl маскируется.
SHELL ["/bin/bash","-o","pipefail","-c"]

ARG HERMES_VERSION=0.16.0

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    HERMES_HOME=/data/hermes \
    HERMES_ENV=/data/hermes/.env \
    TASKS_DIR=/data/workspace/tasks \
    HF_HOME=/data/cache/hf

# Системные зависимости + Node 22 (perplexity-поиск/скиллы) + ripgrep (hmem) + ffmpeg (голос)
# + imagemagick (`convert` — ресайз фото перед qwen-image-edit: у fal лимит 15 МБ на вход;
#   без него скилл падал с `convert: command not found`, ловили 14.07 на живом боте).
# + офис-стек для документов: pandoc (HTML→DOCX), libreoffice writer/impress/calc
#   (DOCX/PPTX→PDF, headless), poppler-utils (рендер слайдов), шрифты с кириллицей.
#   pptxgenjs (создание .pptx с нуля) ставится глобально через npm.
# build-essential НЕ ставим: build.log реальной сборки показал 0 компиляций —
# все python-колёса manylinux готовые (при сборке под arm64 сперва проверить
# наличие aarch64-колёс у ctranslate2/av, иначе вернуть toolchain).
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git ripgrep ffmpeg openssl tini procps rsync openssh-client \
        pandoc libreoffice-writer libreoffice-impress libreoffice-calc poppler-utils \
        fonts-liberation fonts-dejavu imagemagick \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g pptxgenjs@4.0.1 && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/*

# Ядро — ровно та версия, что на сервере.
# ВАЖНО: bare `hermes-agent` НЕ тянет python-telegram-bot (он только в extra
# `messaging`) — без него бот-копия стартует, но никогда не подключится к Telegram.
# messaging → Telegram (обязательно); voice → faster-whisper/STT (WITH_VOICE=1 по
# умолчанию в standard); vision → Pillow (картинки); google → Gmail/Calendar/Drive/
# Docs/Sheets (скилл google-workspace; WITH_GOOGLE=1 в пресете standard).
# ddgs — бэкенд встроенного web_search (toolset `web` в config по умолчанию).
# markitdown[pptx] — чтение/извлечение текста из .pptx/.docx для скиллов документов.
# (edge-tts намеренно не ставим — голосовые ОТВЕТЫ/TTS клиентским копиям не нужны;
#  голосовой ВВОД/STT работает локально через faster-whisper без доп. пакетов.)
RUN pip install "hermes-agent[messaging,voice,vision,google,mcp]==${HERMES_VERSION}" \
        "ddgs==9.14.4" "markitdown[pptx]==0.1.6"

# --- Запекание патчей (отдельный слой для кэша) ---------------------------
# Патчи verbatim с сервера хардкодят pipx-путь; ретаргетим на реальный
# site-packages контейнера. Порядок — ТОЛЬКО из seed/patches/patches.txt
# (тот же манифест читает update_hermes.sh на хосте; двойного хардкода нет).
COPY seed/patches/ /opt/hermes-seed/patches/
RUN set -eu; \
    SP="$(python -c 'import gateway,os;print(os.path.dirname(os.path.dirname(os.path.abspath(gateway.__file__))))')"; \
    echo "[build] site-packages = $SP"; \
    PIPX='/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages'; \
    MANIFEST=/opt/hermes-seed/patches/patches.txt; \
    [ -s "$MANIFEST" ] || { echo "[build] FATAL: нет $MANIFEST"; exit 1; }; \
    N=0; \
    for p in $(grep -vE '^[[:space:]]*(#|$)' "$MANIFEST"); do \
        p="${p%.py}"; N=$((N+1)); \
        [ -f "/opt/hermes-seed/patches/$p.py" ] || { echo "[build] FATAL: в манифесте есть $p.py, а файла нет"; exit 1; }; \
        sed "s#${PIPX}#${SP}#g" "/opt/hermes-seed/patches/$p.py" > "/tmp/$p.py"; \
        echo "[build] applying $p"; \
        python "/tmp/$p.py"; \
    done; \
    [ "$N" -ge 1 ] || { echo "[build] FATAL: манифест пуст"; exit 1; }; \
    echo "[build] applied $N patches"; \
    python -c "import py_compile,glob,os; \
sp=os.environ.get('SP','$SP'); \
[py_compile.compile(f,doraise=True) for f in [sp+'/gateway/platforms/telegram.py', sp+'/gateway/platforms/base.py', sp+'/gateway/stream_consumer.py']]; \
print('[build] patched files compile OK')"; \
    find "$SP/gateway" -name '*.bak-*' -delete; \
    rm -f /tmp/*.py

# --- Семя образа (конфиг + скиллы + блоки) --------------------------------
COPY seed/ /opt/hermes-seed/

COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh

# Чтобы скилл powerpoint мог `require("pptxgenjs")` (и глобальные react/sharp при
# доустановке) — node должен искать модули в глобальной папке. Ставим в конце, чтобы
# не инвалидировать дорогие apt/pip-слои.
ENV NODE_PATH=/usr/lib/node_modules

# LABEL в конце: смена версии не инвалидирует дорогие apt/pip-слои выше.
LABEL org.opencontainers.image.title="bif" \
      org.opencontainers.image.version="1.0" \
      org.opencontainers.image.description="BIF: Hermes Agent 0.16.0 + rich/carousel/localize/sentinel патчи (манифест seed/patches/patches.txt), блочная сборка"

# Все данные копии (память, сессии, конфиг, скиллы, токены) — на томе /data.
# HF_HOME тоже на томе: whisper-модель (~74 МБ) не перекачивается при пересоздании.
VOLUME ["/data"]

# Живость gateway-процесса: если он умер, а контейнер жив — пометить unhealthy
# (видно в docker ps; авто-рестарт можно навесить autoheal'ом позже).
HEALTHCHECK --interval=60s --timeout=10s --start-period=180s --retries=3 \
  CMD pgrep -f "hermes.*gateway" >/dev/null || exit 1

# Telegram long-poll наружу — входящие порты не нужны.
ENTRYPOINT ["/usr/bin/tini","--","/usr/local/bin/entrypoint.sh"]
