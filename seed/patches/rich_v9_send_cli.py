#!/usr/bin/env python3
# rich_v9_send_cli.py — rich для standalone-отправок (идемпотентный патч Hermes)
#
# ПРОБЛЕМА (12.07.2026): rich-цепочка (rich_messages…rich_v8) патчит ТОЛЬКО
# адаптер gateway (gateway/platforms/telegram.py::_send_rich). Но всё, что
# уходит МИМО gateway — `hermes send`, cron-джобы, скрипты, task-tracker и
# даже инструмент модели send_message в другой чат — идёт через
# tools/send_message_tool.py::_send_telegram, а он шлёт sendMessage с
# parse_mode=MarkdownV2. Итог: **жирный** и [ссылки](url) приезжают СЫРЫМИ,
# таблицы/заголовки ломаются, длинный текст режется на куски по 4096.
#
# ФИКС: в начале _send_telegram пробуем Bot API 10.1 sendRichMessage
# (rich_message.markdown — GFM-суперсет: заголовки, таблицы, цитаты, до 32768
# символов). При любой осечке (медиа, не-числовой chat_id, HTTP-ошибка,
# отключён HERMES_RICH_MESSAGES=0) — тихо проваливаемся в старый путь ниже,
# поведение не регрессирует.
#
# Идемпотентно: маркер "RICH v9", проверка якоря, бэкап .bak-<ts>,
# py_compile-проверка, авто-откат при ошибке.
# Слетает при `hermes update` — включён в манифест seed/patches/patches.txt.

import sys, py_compile, shutil, time

BASE = "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages/tools/send_message_tool.py"
MARKER = "RICH v9"

ANCHOR = """    try:
        from telegram import Bot
        from telegram.constants import ParseMode
"""

NEW = '''    # --- RICH v9 (2026-07-12) -------------------------------------------
    # Bot API 10.1 rich messages для standalone-пути: `hermes send`, cron,
    # скрипты и инструмент send_message модели. Gateway это уже умеет,
    # а здесь markdown уезжал сырым. Пробуем rich; при осечке — старый путь.
    if (
        message
        and message.strip()
        and not media_files
        and "MEDIA:" not in message
        and len(message) <= 32000
        and os.environ.get("HERMES_RICH_MESSAGES", "1").strip().lower()
        not in ("0", "false", "no")
    ):
        try:
            import httpx as _httpx

            _payload = {
                "chat_id": int(chat_id),
                "rich_message": {"markdown": message},
            }
            if thread_id is not None and str(thread_id) not in ("1", "None", ""):
                _payload["message_thread_id"] = int(thread_id)
            async with _httpx.AsyncClient(timeout=30) as _client:
                _resp = await _client.post(
                    "https://api.telegram.org/bot%s/sendRichMessage" % token,
                    json=_payload,
                )
            _data = _resp.json() if _resp.status_code == 200 else {}
            if _data.get("ok"):
                logger.info(
                    "[Telegram] rich send OK (chars=%d, msg_id=%s)",
                    len(message),
                    _data["result"]["message_id"],
                )
                return {
                    "success": True,
                    "platform": "telegram",
                    "chat_id": chat_id,
                    "message_id": str(_data["result"]["message_id"]),
                    "rich": True,
                }
            logger.warning(
                "[Telegram] rich send rejected (HTTP %s) — fallback to legacy path",
                _resp.status_code,
            )
        except Exception as _rich_err:  # noqa: BLE001 — отправка не должна падать
            logger.warning(
                "[Telegram] rich send failed (%s) — fallback to legacy path",
                _sanitize_error_text(_rich_err),
            )
    # --- /RICH v9 --------------------------------------------------------

'''


def main():
    src = open(BASE, encoding="utf-8").read()

    if MARKER in src:
        print("rich_v9: already applied — skip")
        return 0

    if src.count(ANCHOR) != 1:
        print("rich_v9: FATAL — якорь найден %d раз (ожидался 1)" % src.count(ANCHOR))
        return 1

    if "async def _send_telegram(" not in src.split(ANCHOR)[0]:
        print("rich_v9: FATAL — якорь вне _send_telegram")
        return 1

    bak = "%s.bak-%s" % (BASE, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(BASE, bak)

    open(BASE, "w", encoding="utf-8").write(src.replace(ANCHOR, NEW + ANCHOR, 1))

    try:
        py_compile.compile(BASE, doraise=True)
    except Exception as exc:
        shutil.copy2(bak, BASE)
        print("rich_v9: FATAL — не компилируется, откатил: %s" % exc)
        return 1

    print("rich_v9: applied OK (backup: %s)" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
