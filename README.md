# BIF — образ изолированных копий Hermes-агента, **v1.0**

Версионируется **сам Docker-образ** (а не версия Hermes). Текущая: **BIF 1.0** (`VERSION`). Образ: `bif:1.0`.
Ядро внутри — **Hermes Agent 0.16.0**, ровно как на живом сервере `188.166.122.243` (источник правды).

## Что нового в v1.0 (против прошлого образа)

| | Было (0.15.2-образ) | Стало (v1.0) |
|---|---|---|
| Версия Hermes | 0.15.2 | **0.16.0** (= сервер) |
| Патчи (rich/локализация/sentinel) | **нет ни одного** | **8 запечены на build-time** |
| Архитектура | монолит «всё включено» | **блочная**, тумблеры `WITH_*` |
| Установка | руками править `.env` | **интерактивный `install.sh`** + живая валидация ключей |
| Обновление | — | **пересборка образа**, а не починка якорей патчей |

Патчи берутся verbatim с сервера (`seed/patches/`), на сборке их pipx-путь ретаргетится на
site-packages контейнера и они накатываются в том же порядке, что `update_hermes.sh`
(localize → sentinel → rich_messages → rich_v2…v6). Проверено: все 8 чисто ложатся на чистый
0.16.0 и компилируются.

## Блоки

| Блок | Тумблер | Обяз. | Что даёт |
|---|---|:--:|---|
| core + DeepSeek + Telegram + rich | — | ✅ | мозг `deepseek-v4-pro`, бот, rich-формат |
| OpenAI | `WITH_OPENAI` (авто по ключу) | | картинки `gpt-image-2`, голос OpenAI, запасной LLM |
| OpenRouter / Nemotron | `WITH_OPENROUTER` (авто) | | `/model` через OpenRouter |
| Perplexity (поиск) | `WITH_PERPLEXITY` (авто) | | веб-поиск, уровень 2 маршрутизации |
| hmem (банк памяти) | `WITH_HMEM` | | поиск+индекс по заметкам, онтология |
| Voice (STT/TTS) | `WITH_VOICE` | | локальный faster-whisper |
| Task-tracker | `WITH_TRACKER` | | задачи + напоминания + Google Calendar |
| Google Workspace | `WITH_GOOGLE` | | почта/календарь/диск (нужен пост-шаг OAuth) |
| Codex (сайдкар) | `WITH_CODEX` | | сложные фоновые задачи (профиль `codex`) |
| Автодеплой сайта | `WITH_SITE` | | Caddy (профиль `site`) |

Лёгкие блоки — рантайм-тумблеры в `.env`. Тяжёлые сайдкары (Codex, Postgres-vault, Caddy) — профили compose.

## Требования (на сервере)

- **Linux x86_64** (Ubuntu/Debian рекомендуются), `bash`, `git`.
- **Docker Engine + плагин `docker compose` v2.** Установка: <https://docs.docker.com/engine/install/>.
  Проверка: `docker compose version` → должно вывести `v2.x`.
- **~2 ГБ RAM** (≈4 ГБ при включённых голосе/hmem/документах), **~10 ГБ** свободного диска
  (образ ~3 ГБ из-за офис-стека libreoffice/pandoc + слои сборки + том `/data`).
- **Стабильный исходящий HTTPS.** На сборке нужен доступ к PyPI и `deb.nodesource.com`; в работе — к
  `api.deepseek.com` и `api.telegram.org` (плюс OpenAI / Perplexity / Google под включённые блоки).
  Голос при первой расшифровке один раз качает модель `faster-whisper` (base/ru, ~150 МБ) с HuggingFace.

**Если сервер — Linux в виртуалке на Windows 11 (VM или WSL2):**
- Docker: либо **Docker Desktop** с включённой WSL2-интеграцией, либо `docker` поставить **внутри**
  Linux-VM/WSL2-дистрибутива. Проверка та же: `docker compose version`.
- Дай виртуалке **≥4 ГБ RAM и ≥15 ГБ диска** (образ ~3 ГБ + слои сборки + модели голоса).
- Клонируй и собирай **внутри** Linux-ФС (домашняя папка WSL2/VM), а не в `/mnt/c/...` — так быстрее
  и без проблем с переводами строк (репозиторий форсит LF через `.gitattributes`).
- Telegram работает через исходящий long-poll — **проброс портов в VM не нужен.**

Скачать репозиторий и войти в папку:

```bash
git clone https://github.com/AML1969/Paspartu.git && cd Paspartu
./install.sh
```

## Установка (рекомендуемый путь)

```bash
./install.sh
```

Мастер спросит имя копии, обязательные ключи (Telegram-бот, Telegram ID, DeepSeek), затем профиль:

- **minimal** — мозг + Telegram + Perplexity + файловая память
- **standard** — minimal + hmem + картинки + голос + Google + трекер *(рекоменд.)*
- **full** — standard + Codex + автодеплой сайта
- **custom** — да/нет по каждому блоку

Под выбранные блоки спросит только нужные ключи, проведёт **живую валидацию** (DeepSeek `/models`,
Telegram `getMe`, OpenAI, Perplexity), запишет `copies/<имя>.env` (chmod 600) и поднимет контейнер
с нужными профилями. Повторно проверить ключи: `./install.sh --check <имя>`.

## Ручной путь (для технарей)

```bash
cp secrets.env.example copies/petrov.env
$EDITOR copies/petrov.env                  # заполнить ключи
chmod 600 copies/petrov.env                # права только владельцу
COPY=petrov docker compose -p bif-petrov up -d --build
# с сайдкарами:
COPY=petrov docker compose -p bif-petrov --profile codex --profile site up -d --build
```

Изоляция копий — через **имя проекта** `-p bif-<имя>`: тома (`data`/`vault`/`site`) автоматически
префиксуются и физически не пересекаются. Все данные копии — на томе `/data` (память, сессии, конфиг,
скиллы, токены). Секреты **никогда** не в образе и не в git — только в `copies/*.env` на хосте.

## Проверка после установки (smoke test)

Первая сборка образа делается именно на этом сервере — обязательно проверь, что копия реально поднялась:

1. `COPY=<имя> docker compose -p bif-<имя> ps` — колонка STATUS должна быть `Up`/`running`, не `Restarting`/`Exited`.
2. `COPY=<имя> docker compose -p bif-<имя> logs -f hermes` — дождись старта gateway без traceback (Ctrl-C для выхода).
3. Напиши боту в Telegram `/start` — он должен ответить.

Если контейнер в `Restarting` или бот молчит — смотри логи (шаг 2): чаще всего это неверный ключ
(`401`/`Unauthorized`) или заблокированный исходящий интернет на сервере.

## Google — отдельный пост-шаг (headless OAuth невозможен)

1. В своём Google Cloud создать OAuth-клиент типа **Desktop**, включить Gmail/Calendar/Drive/Sheets/Docs/People API.
2. **Обязательно Publish app → In production** — иначе у Testing-приложения refresh-токен умирает через **7 дней** (инцидент 2026-06-10).
3. Скопировать `client_secret.json` в том копии и пройти мастер OAuth:
   ```bash
   COPY=<имя> docker compose -p bif-<имя> cp ./client_secret.json hermes:/data/hermes/client_secret.json
   ```
   Затем написать боту в Telegram «настрой Google Workspace» — агент сам запустит мастер и пришлёт ссылку.
   Ссылку открыть в браузере **на своём компьютере** (сервер headless), а полученный код вставить обратно в чат.
4. Токен ляжет в `/data` и переживёт рестарты.

## Документы, PDF и презентации

В образ запечён офис-стек (`pandoc` + `libreoffice` + `poppler` + `markitdown` + `pptxgenjs`), поэтому копия умеет:

- **DOCX / PDF** — скилл `document-generation`: markdown/анализ → оформленный DOCX → PDF (pandoc → libreoffice) → загрузка на Google Диск.
- **Презентации `.pptx`** — скилл `powerpoint`: создание/чтение/правка колод (pptxgenjs + markitdown), рендер в PDF через libreoffice.
- **Правка PDF** — `nano-pdf` (ставится по требованию).

⚠️ **Нативные Google Slides не поддерживаются.** Презентации делаются как `.pptx` и заливаются на Google
Диск (Google открывает/конвертирует их в Slides). Google **Docs/Sheets/Gmail/Calendar/Drive** —
поддерживаются напрямую через `google-workspace` (нужен пост-шаг OAuth выше).

## Обновление = пересборка (а не починка патчей)

```bash
git pull               # подтянуть новый VERSION / patches / seed
./run-copy.sh petrov   # пересобрать образ и перезапустить копию
```

Патчи — часть build-шага, поэтому апдейт не зависит от ручного перенаката якорей. Когда выйдет новый
Hermes — поднять `HERMES_VERSION` в `docker-compose.yml`, проверить что патчи ещё ложатся (локально),
выпустить v1.1.

## Структура

```
hermes-docker/
  VERSION                 # 1.0
  Dockerfile              # 0.16.0 + build-time накат патчей
  entrypoint.sh           # блочная раскладка по WITH_*
  install.sh              # интерактивный мастер + живая валидация
  run-copy.sh             # ручной перезапуск копии
  docker-compose.yml      # сервис hermes + профили codex/vault/site
  secrets.env.example     # шаблон секретов (фолбэк)
  copies/                 # <имя>.env каждой копии (в .gitignore)
  seed/
    config.yaml           # живой конфиг сервера (auto_prune→on для копий)
    AGENTS.md             # с маркерами hmem:begin/end (режется, если hmem off)
    SOUL.template.md      # generic-душа; routing/codex дописываются по тумблерам
    ROUTING_BLOCK.md      # 3 уровня DeepSeek→Perplexity→Codex (шаблон)
    CODEX_BLOCK.md        # codex-блок (шаблон, пути подставляются)
    MEMORY.md             # стартовая файловая память
    patches/              # 8 активных патчей verbatim с сервера
    skills.tar.gz         # курированный набор (без *-imported дублей)
    hmem-bin.tar.gz       # hmem CLI (без серверного tirith)
    task-tracker-src.tar.gz
```

## Известные оговорки v1.0

- **`docker build` проверен** (arm64): образ собирается, `hermes-agent[messaging,voice,vision]==0.16.0`
  встаёт из PyPI как wheels, все 8 патчей накатываются и py_compile проходит, модуль `telegram` и CLI
  `hermes` на месте. На сервере друга (amd64) wheels для тех же зависимостей тоже опубликованы.
  Перед боевым запуском всё равно прогони smoke-test (см. раздел выше).
- STT-модель в сиде = `base`/ru (как на сервере). В памяти есть установка «whisper small» —
  расхождение оставлено как у первоисточника, решение за Андреем (см. ревизию §3.11).
- Codex-сайдкар в compose — пока заглушка-плейсхолдер: раннер монтируется в `/data/codex-jobs`,
  полную формализацию (юнит + общий tg-helper) делаем в следующей версии.
  - **TODO для будущей формализации Codex — шаг публикации в веб-корень.** Codex работает в
    песочнице и пишет результат в свою рабочую папку, а не в веб-корень сайта (если включён
    `WITH_SITE`/Caddy). Поэтому после Codex-задачи нужен шаг **от root**, который аддитивно
    (без удаления) копирует готовое из рабочей папки Codex в веб-корень контейнера, затем
    выставляет владельца и права (755 каталоги / 644 файлы). На боевом сервере это сделано в
    `codex_task_runner.sh` (ветка профиля: `~codexrm/.codex/work/krm/` → `/var/www/<домен>/krm/`).
    В докере путь веб-корня другой — Caddy-том `site:/srv` (или `/data/site`), поэтому при
    формализации Codex-сайдкара параметризовать SRC=рабочая папка Codex, DST=веб-корень тома `site`.
    Без этого шага любой Codex-запрос «сделай страницу на сайте» отдаёт 404 (страница есть в
    песочнице, но не в веб-корне).
- `tirith` (12 МБ, арх-зависимый) в образ не кладётся — он `fail_open`, идёт с пакетом/не критичен.
