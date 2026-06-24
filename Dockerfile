# syntax=docker/dockerfile:1
# ============================================================================
# BIF — образ изолированных копий Hermes-агента, v1.0
# Источник правды: живой сервер 188.166.122.243 (Hermes Agent v0.16.0).
# Отличие от прошлого образа (0.15.2, без патчей):
#   • та же версия, что на сервере — 0.16.0;
#   • 8 рабочих патчей (rich-формат + локализация кнопок + sentinel) ЗАПЕКАЮТСЯ
#     в образ на build-time, ретаргет pipx-пути на site-packages контейнера;
#   • блочная архитектура: всё лишнее выключается тумблерами WITH_* в entrypoint.
# ============================================================================
FROM python:3.12-slim

ARG HERMES_VERSION=0.16.0
LABEL org.opencontainers.image.title="bif" \
      org.opencontainers.image.version="1.0" \
      org.opencontainers.image.description="BIF: Hermes Agent 0.16.0 + rich/localize/sentinel патчи, блочная сборка"

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    HERMES_HOME=/data/hermes \
    HERMES_ENV=/data/hermes/.env \
    TASKS_DIR=/data/workspace/tasks

# Системные зависимости + Node 22 (perplexity-поиск/скиллы) + ripgrep (hmem) + ffmpeg (голос)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git ripgrep ffmpeg openssl tini build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get purge -y build-essential && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Ядро — ровно та версия, что на сервере
RUN pip install "hermes-agent==${HERMES_VERSION}"

# --- Запекание патчей (отдельный слой для кэша) ---------------------------
# Патчи verbatim с сервера хардкодят pipx-путь; ретаргетим на реальный
# site-packages контейнера и накатываем В ТОМ ЖЕ ПОРЯДКЕ, что update_hermes.sh.
COPY seed/patches/ /opt/hermes-seed/patches/
RUN set -eu; \
    SP="$(python -c 'import gateway,os;print(os.path.dirname(os.path.dirname(os.path.abspath(gateway.__file__))))')"; \
    echo "[build] site-packages = $SP"; \
    PIPX='/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages'; \
    for p in localize_approval_ru stream_empty_sentinel rich_messages rich_v2_delta rich_v3 rich_v4 rich_v5 rich_v6; do \
        sed "s#${PIPX}#${SP}#g" "/opt/hermes-seed/patches/$p.py" > "/tmp/$p.py"; \
        echo "[build] applying $p"; \
        python "/tmp/$p.py"; \
    done; \
    python -c "import py_compile,glob,os; \
sp=os.environ.get('SP','$SP'); \
[py_compile.compile(f,doraise=True) for f in [sp+'/gateway/platforms/telegram.py', sp+'/gateway/stream_consumer.py']]; \
print('[build] patched files compile OK')"

# --- Семя образа (конфиг + скиллы + блоки) --------------------------------
COPY seed/ /opt/hermes-seed/

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Все данные копии (память, сессии, конфиг, скиллы, токены) — на томе /data
VOLUME ["/data"]

# Telegram long-poll наружу — входящие порты не нужны.
ENTRYPOINT ["/usr/bin/tini","--","/usr/local/bin/entrypoint.sh"]
