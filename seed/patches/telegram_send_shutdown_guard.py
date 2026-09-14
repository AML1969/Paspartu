#!/usr/bin/env python3
"""Fix2-B (14.09.2026): третий рубеж доставки в Telegram на голом stdlib.

Проблема: при остановке процесса (SIGTERM от apt-daily/needrestart, atexit
гасит пулы) доигрывающая cron-джоба идёт слать результат. RICH-путь просит
поток у anyio через httpx -> RuntimeError('cannot schedule new futures after
interpreter shutdown'), а «фолбэк на legacy» (python-telegram-bot) падает тем
же самым, потому что тоже поверх httpx. Оба уровня зависят от одной машинерии.

Решение: urllib.request — блокирующий сокет в текущем потоке, ему не нужен ни
пул, ни event loop. Вызывается, когда текст исключения говорит про shutdown.
Формат при этом деградирует до plain text — осознанный размен «простое
сообщение вместо потерянного».

Конвенции: путь через env HERMES_SITE_PACKAGES (приоритет) или pipx-glob;
маркер идемпотентности; abort если якорь не найден; timestamped .bak.
"""
import glob, io, os, shutil, sys, time

MARKER = "fix2b-shutdown-stdlib-guard"

def site_packages():
    sp = os.environ.get("HERMES_SITE_PACKAGES")
    if sp and os.path.isdir(sp):
        return sp
    for pat in ("/usr/local/lib/python3.*/site-packages",
                "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.*/site-packages"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    print("ABORT: site-packages не найден"); sys.exit(1)

SP = site_packages()
F = os.path.join(SP, "tools", "send_message_tool.py")
if not os.path.isfile(F):
    print("ABORT: нет", F); sys.exit(1)

src = io.open(F, encoding="utf-8").read()
if MARKER in src:
    print("patch already applied:", MARKER); sys.exit(0)

HELPER = '''

def _is_interpreter_shutdown_error(err):  # ''' + MARKER + '''
    """Опознать гонку выключения: пул/цикл событий уже недоступны."""
    t = str(err).lower()
    return ("cannot schedule new futures" in t
            or "interpreter shutdown" in t
            or "can't create new thread" in t
            or "cannot schedule new thread" in t)


def _send_telegram_stdlib(token, chat_id, text, thread_id=None):  # ''' + MARKER + '''
    """Последний рубеж доставки: работает после начала interpreter shutdown.

    httpx/PTB просят поток у anyio -> RuntimeError. urllib.request — блокирующий
    сокет в текущем потоке, ему пул не нужен. Формат деградирует до plain text.
    """
    import json as _json
    import urllib.request as _u
    payload = {"chat_id": int(chat_id), "text": (text or "")[:4096]}
    if thread_id is not None and str(thread_id) not in ("1", "None", ""):
        payload["message_thread_id"] = int(thread_id)
    req = _u.Request(
        "https://api.telegram.org/bot%s/sendMessage" % token,
        data=_json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with _u.urlopen(req, timeout=15) as r:
        return _json.loads(r.read().decode("utf-8"))
'''

# 1) helper после импортов
anchor_imp = "\nlogger = logging.getLogger(__name__)"
if anchor_imp not in src:
    print("ABORT: якорь logger не найден"); sys.exit(1)
src = src.replace(anchor_imp, anchor_imp + HELPER, 1)

# 2) врезка в except RICH v9.
# Текст комментария/сообщения отличается между 0.19 и 0.16 — пробуем оба варианта.
_CANDIDATES = [
    '''        except Exception as _rich_err:  # noqa: BLE001 — отправка не должна падать
            logger.warning(
                "[Telegram] rich send failed (%s) — fallback to legacy path",
                _sanitize_error_text(_rich_err),
            )''',
    '''        except Exception as _rich_err:  # noqa: BLE001 - never break the send
            logger.warning(
                "[Telegram] rich send failed (%s) — falling back to legacy path",
                _sanitize_error_text(_rich_err),
            )''',
]
anchor_rich = None
for _c in _CANDIDATES:
    if _c in src:
        anchor_rich = _c
        break
if anchor_rich is None:
    print("ABORT: якорь except RICH v9 не найден ни в одном из известных вариантов")
    sys.exit(1)
GUARD = anchor_rich + '''
            if _is_interpreter_shutdown_error(_rich_err):  # ''' + MARKER + '''
                # Legacy-путь тоже поверх httpx и упадёт тем же самым — идём
                # сразу на stdlib, иначе доставка теряется совсем.
                try:
                    _sd = _send_telegram_stdlib(token, chat_id, message, thread_id)
                    if isinstance(_sd, dict) and _sd.get("ok"):
                        logger.warning(
                            "[Telegram] delivered via stdlib fallback during shutdown "
                            "(plain text, msg_id=%s)", _sd["result"]["message_id"])
                        return {
                            "success": True, "platform": "telegram",
                            "chat_id": chat_id,
                            "message_id": str(_sd["result"]["message_id"]),
                            "rich": False, "degraded": "shutdown-stdlib",
                        }
                except Exception as _sd_err:  # noqa: BLE001
                    logger.error("[Telegram] stdlib fallback failed too: %s",
                                 _sanitize_error_text(_sd_err))'''
src = src.replace(anchor_rich, GUARD, 1)

shutil.copy2(F, F + ".bak-fix2b-" + time.strftime("%Y%m%d-%H%M%S"))
io.open(F, "w", encoding="utf-8").write(src)
print("patched:", F)
print("marker:", MARKER)
