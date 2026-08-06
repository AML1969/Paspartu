#!/usr/bin/env python3
# rich_v10_divider.py — видимая линия вместо невидимого divider-блока
#
# ПРОБЛЕМА (12.07.2026): markdown `---` уходит в Bot API 10.1 как блок
# {"type":"divider"} (сервер его принимает — проверено по ответу API), но
# клиент Telegram у пользователя этот блок НЕ рисует: между разделами пусто, всё
# слипается. Это не регресс сервера — patch rich_v4 работает, divider-блоки
# просто не отображаются в его сборке клиента.
#
# ФИКС: в _rich_normalize (RU v4) вместо `---` подставляем символьную линию
# `──────────────` (14× U+2500). Это обычный текст — рендерится в ЛЮБОМ
# клиенте, ровно как линия в утренней сводке task-tracker'а, которая всегда
# работала. Пустые строки вокруг сохраняем (отдельный абзац).
#
# Вторая точка: standalone-путь (RICH v9 в tools/send_message_tool.py) шлёт
# markdown БЕЗ нормализации — там `---` тоже становился невидимым. Прогоняем
# его через тот же TelegramAdapter._rich_normalize (staticmethod).
#
# Идемпотентно: маркер "RU divider v10", проверка якорей, бэкапы .bak-<ts>,
# py_compile, авто-откат. Слетает при `hermes update` — в манифесте patches.txt.

import sys, py_compile, shutil, time

SP = "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages"
TG = SP + "/gateway/platforms/telegram.py"
SM = SP + "/tools/send_message_tool.py"

MARKER = "RU divider v10"
LINE = "──────────────"

# --- точка 1: gateway/platforms/telegram.py::_rich_normalize -----------------
TG_ANCHOR = '''            if stripped in ("---", "***", "___"):
                if out and out[-1].strip():
                    out.append("")
                out.append("---")
                out.append("")
                continue
'''

TG_NEW = '''            if stripped in ("---", "***", "___"):
                # RU divider v10: клиент не рисует блок {"type":"divider"} —
                # отдаём видимую символьную линию (рендерится везде).
                if out and out[-1].strip():
                    out.append("")
                out.append("%s")
                out.append("")
                continue
''' % LINE

# --- точка 2: tools/send_message_tool.py, блок RICH v9 ----------------------
SM_ANCHOR = '''            _payload = {
                "chat_id": int(chat_id),
                "rich_message": {"markdown": message},
            }
'''

SM_NEW = '''            _md = message
            try:  # RU divider v10: та же нормализация, что в gateway
                from gateway.platforms.telegram import TelegramAdapter as _TA

                _md = _TA._rich_normalize(_md)
            except Exception:
                pass
            _payload = {
                "chat_id": int(chat_id),
                "rich_message": {"markdown": _md},
            }
'''


def patch(path, anchor, new, label):
    src = open(path, encoding="utf-8").read()
    if MARKER in src:
        print("rich_v10: %s — already applied, skip" % label)
        return True
    if src.count(anchor) != 1:
        print("rich_v10: FATAL — якорь в %s найден %d раз (ожидался 1)" % (label, src.count(anchor)))
        return False
    bak = "%s.bak-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, bak)
    open(path, "w", encoding="utf-8").write(src.replace(anchor, new, 1))
    try:
        py_compile.compile(path, doraise=True)
    except Exception as exc:
        shutil.copy2(bak, path)
        print("rich_v10: FATAL — %s не компилируется, откатил: %s" % (label, exc))
        return False
    print("rich_v10: %s patched OK (backup: %s)" % (label, bak))
    return True


def main():
    ok = patch(TG, TG_ANCHOR, TG_NEW, "telegram.py::_rich_normalize")
    if not ok:
        return 1
    # send_message_tool патчим только если там уже есть RICH v9 (иначе нечего)
    sm_src = open(SM, encoding="utf-8").read()
    if "RICH v9" not in sm_src:
        print("rich_v10: send_message_tool без RICH v9 — пропускаю вторую точку")
        return 0
    return 0 if patch(SM, SM_ANCHOR, SM_NEW, "send_message_tool.py::RICH v9") else 1


if __name__ == "__main__":
    sys.exit(main())
