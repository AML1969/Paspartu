#!/usr/bin/env python3
"""Патч v2: сделать потерю аргументов tool_call видимой для модели.

Причина (аудит 16.08.2026, сессии Игоря 10.08 и 16.08):
agent/message_sanitization.py при неремонтируемом JSON аргументов возвращает
"{}" — молча. Дальше terminal получает command=None и отвечает
"Invalid command: expected string, got NoneType". Модель не понимает, что
её собственный вызов был обрезан, продолжает как ни в чём не бывало —
пользователь видит «куда делся текст, он тут вперемешку» и «полный бред».

Корень — длинный многострочный python внутри terminal(command=...):
аргумент обрезается на границе токенов, JSON остаётся незакрытым, и ни один
из четырёх repair-проходов его не чинит (обрезку починить нельзя в принципе).

Что делает патч: в last-resort ветке для tool_name == "terminal" вместо "{}"
подставляется безобидный echo с объяснением на русском. Модель читает его
как вывод инструмента и понимает, что нужно переделать вызов через write_file.
Для остальных инструментов поведение прежнее ("{}" → их собственная ошибка
про отсутствующий обязательный параметр, она и так видима).

Профилактика (не код, а SOUL): правило «код длиннее 3 строк — write_file
во временный .py, потом python3 file.py» — вносится отдельно.

Конвенции v2: путь через env HERMES_SITE_PACKAGES (приоритет) или pipx-glob;
маркер идемпотентности, abort если якорь не найден, timestamped .bak,
py_compile с автооткатом.
Маркер: "toolcall-loss-visible-patch".
"""
import glob
import os
import py_compile
import shutil
import sys
import time

SP = os.environ.get("HERMES_SITE_PACKAGES", "")
if not SP:
    cands = glob.glob("/root/.local/share/pipx/venvs/hermes-agent/lib/python3*/site-packages")
    SP = cands[0] if cands else ""
if not SP or not os.path.isdir(SP):
    print("[toolcall_loss_visible] FATAL: site-packages не найден (HERMES_SITE_PACKAGES/pipx)")
    sys.exit(1)

TARGET = SP + "/agent/message_sanitization.py"
MARKER = "toolcall-loss-visible-patch"

OLD = '''    logger.warning(
        "Unrepairable tool_call arguments for %s — "
        "replaced with empty object (was: %s)",
        tool_name, raw_stripped[:80],
    )
    return "{}"'''

NEW = '''    logger.warning(
        "Unrepairable tool_call arguments for %s — "
        "replaced with empty object (was: %s)",
        tool_name, raw_stripped[:80],
    )
    # toolcall-loss-visible-patch: не терять шаг молча.
    # terminal всегда принимает command → возвращаем echo с объяснением,
    # чтобы модель прочитала его как вывод и переделала вызов.
    if tool_name == "terminal":
        import json as _json
        _msg = (
            "[АРГУМЕНТЫ ВЫЗОВА ПОТЕРЯНЫ] Твой tool_call terminal пришёл с "
            "битым/обрезанным JSON и был отброшен — команда НЕ выполнялась. "
            "Почти всегда причина одна: многострочный код прямо в command. "
            "Переделай так: write_file запиши код в /tmp/step.py, затем "
            "terminal: python3 /tmp/step.py. Не повторяй прежний вызов как есть "
            "и не считай предыдущий шаг выполненным."
        )
        return _json.dumps({"command": "echo " + _json.dumps(_msg, ensure_ascii=False)},
                           ensure_ascii=False)
    return "{}"'''


def main() -> int:
    if not os.path.isfile(TARGET):
        print("[toolcall_loss_visible] FATAL: нет %s" % TARGET)
        return 1
    src = open(TARGET, encoding="utf-8").read()
    if MARKER in src:
        print("[toolcall_loss_visible] already applied")
        return 0
    if src.count(OLD) != 1:
        print("[toolcall_loss_visible] FATAL: якорь найден %d раз" % src.count(OLD))
        return 1

    bak = TARGET + ".bak-tclost-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(TARGET, bak)
    open(TARGET, "w", encoding="utf-8").write(src.replace(OLD, NEW))
    try:
        py_compile.compile(TARGET, doraise=True)
    except Exception as exc:
        shutil.copy2(bak, TARGET)
        print("[toolcall_loss_visible] FATAL: compile failed, rolled back: %s" % exc)
        return 1
    print("[toolcall_loss_visible] applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
