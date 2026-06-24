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
cp secrets.env.example copies/petrov.env   # заполнить, chmod 600
COPY=petrov docker compose -p bif-petrov up -d --build
# с сайдкарами:
COPY=petrov docker compose -p bif-petrov --profile codex --profile site up -d --build
```

Изоляция копий — через **имя проекта** `-p bif-<имя>`: тома (`data`/`vault`/`site`) автоматически
префиксуются и физически не пересекаются. Все данные копии — на томе `/data` (память, сессии, конфиг,
скиллы, токены). Секреты **никогда** не в образе и не в git — только в `copies/*.env` на хосте.

## Google — отдельный пост-шаг (headless OAuth невозможен)

1. В своём Google Cloud создать OAuth-клиент типа **Desktop**, включить Gmail/Calendar/Drive/Sheets/Docs/People API.
2. **Обязательно Publish app → In production** — иначе у Testing-приложения refresh-токен умирает через **7 дней** (инцидент 2026-06-10).
3. Положить `client_secret.json` в том копии, пройти мастер внутри контейнера
   (`google-workspace` `setup.py --client-secret … → --auth-url → --auth-code …`). Ссылку открывать в **Chrome**.
4. Токен ляжет в `/data` и переживёт рестарты.

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

- `docker build` не прогонялся в песочнице (там нет docker), но **каждый нетривиальный шаг
  симулирован**: накат 8 патчей на реальный 0.16.0 + py_compile, конфиг-правки под все тумблеры,
  вырезка hmem-блока. OS-слой идентичен прошлому рабочему 0.15.2-образу.
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
