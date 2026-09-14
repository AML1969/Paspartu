---
name: mcp-first-check
description: "Перед использованием любого внешнего сервиса — всегда проверять наличие MCP-сервера."
version: 1.0.0
metadata:
  hermes:
    tags: [MCP, tools, integration, workflow]
---

# MCP-First Check

Перед тем как использовать любой внешний сервис (поиск билетов, бронирование, API, парсинг сайтов через Playwright/браузер), **всегда сначала проверяй, есть ли у сервиса официальный MCP-сервер**.

## Порядок действий

1. Перед запуском браузерной автоматизации или веб-скрапинга — спроси у Perplexity: `"<название сервиса> MCP server Model Context Protocol"`
2. Если MCP есть — настрой его в `~/.hermes/config.yaml` через `hermes config set mcp_servers.<имя>.<ключ> <значение>`
3. Для HTTP-based MCP используй `url`, для stdio-based — `command` + `args`
4. MCP требует перезапуска Hermes для активации — предупреди об этом

## Известные MCP-серверы

| Сервис | URL/команда | Транспорт |
|--------|-------------|-----------|
| **Kiwi.com** | `https://mcp.kiwi.com` | HTTP |
| **GitHub** | `npx -y @modelcontextprotocol/server-github` | stdio |
| **Filesystem** | `npx -y @modelcontextprotocol/server-filesystem` | stdio |

## Пример: Kiwi.com

```bash
hermes config set mcp_servers.kiwi.url "https://mcp.kiwi.com"
hermes config set mcp_servers.kiwi.timeout 180
hermes config set mcp_servers.kiwi.connect_timeout 60
```

После перезапуска Hermes инструмент `mcp__kiwi__search_flight` станет доступен.

## Pitfalls

### Codex на Kiwi — те же проблемы, что у веб-версии
Codex скрапит Kiwi через Playwright и получает те же данные, что видит пользователь в браузере. Kiwi часто выдаёт безумные маршруты с 20–50ч стыковками (KIV→EDI→FNC и т.п.). MCP-инструмент `search-flight` даёт структурированные, отфильтрованные результаты — **всегда лучше скрапинга** для поиска билетов.

### MCP требует перезапуска
После добавления сервера в конфиг, инструменты появятся только после рестарта Hermes. Предупреди об этом и предложи альтернативу на текущую сессию (Perplexity для общей картины).

## Поиск авиабилетов — полный workflow

Когда MCP недоступен (требуется перезапуск), используй этот порядок:

1. **Perplexity для общей картины** — какие авиакомпании, есть ли прямые рейсы, примерные цены
2. **Перекрёстный поиск хабов** — если нет прямых:
   - Найди все города с прямыми рейсами ИЗ пункта отправления в нужную дату
   - Найди все города с прямыми рейсами В пункт назначения на следующее утро
   - Пересечение = оптимальный хаб для ночёвки
3. **Оформляй таблицами:** время вылета/прилёта (местное), авиакомпания, номер рейса, длительность, цена, ссылка на бронь

Детали настройки Kiwi MCP: `references/kiwi-mcp-setup.md`.

Проверенные маршруты и хабы: `references/flight-search-patterns.md`.
