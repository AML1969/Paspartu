#!/usr/bin/env python3
"""Патч: SPILL + GUARD — порт идей deepseek-harness (dsh) в Hermes. bif:1.2.

1) Создаёт agent/spill_guard.py:
   SPILL — длинные строковые tool-результаты уходят целиком в файл
   (~/.hermes/spill/ либо $HERMES_SPILL_DIR), в контекст модели кладётся
   начало+конец и путь к файлу (лечит раздувание контекста);
   GUARD — 3+ одинаковых результата инструмента подряд → advisory-напоминание
   модели сменить подход (анти-зацикливание).
2) Врезает вызов в agent/tool_dispatch_helpers.py::make_tool_result_message
   (перед _maybe_wrap_untrusted) — общая точка sequential+concurrent путей.

Идемпотентный, self-backup, py_compile с автооткатом.
Маркер: "dsh-spill-guard-patch".
"""
import os
import py_compile
import shutil
import sys

SP = "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages"
HELPERS = SP + "/agent/tool_dispatch_helpers.py"
TARGET = SP + "/agent/spill_guard.py"
MARKER = "dsh-spill-guard-patch"

SPILL_GUARD_SRC = r'''# -*- coding: utf-8 -*-
"""Spill + Guard — порт идей из deepseek-harness (dsh) для Hermes.

SPILL: длинные строковые результаты инструментов уходят целиком в файл
(~/.hermes/spill/), в контекст модели кладётся начало+конец и путь к файлу.
GUARD: несколько одинаковых результатов инструмента подряд → advisory-напоминание.
Выключатели через env: HERMES_SPILL=0, HERMES_GUARD=0.
Настройки: HERMES_SPILL_LIMIT (default 12000), HERMES_SPILL_HEAD (default 6000),
HERMES_SPILL_DIR (default ~/.hermes/spill).
"""
import hashlib
import os
import time

SPILL_DIR = os.path.expanduser(os.environ.get("HERMES_SPILL_DIR", "~/.hermes/spill"))
SPILL_LIMIT = int(os.environ.get("HERMES_SPILL_LIMIT", "12000"))
SPILL_HEAD = int(os.environ.get("HERMES_SPILL_HEAD", "6000"))
SPILL_TAIL = 800
_MAX_SPILL_FILES = 200

_recent = {}
_MAX_RECENT = 400
_GUARD_TTL = 1800


def _spill(name, content):
    if os.environ.get("HERMES_SPILL", "1") == "0":
        return content
    if not isinstance(content, str) or len(content) <= SPILL_LIMIT:
        return content
    try:
        os.makedirs(SPILL_DIR, exist_ok=True)
        try:
            files = sorted(f for f in os.listdir(SPILL_DIR) if f.endswith(".txt"))
            if len(files) > _MAX_SPILL_FILES:
                for old in files[: len(files) - _MAX_SPILL_FILES]:
                    try:
                        os.remove(os.path.join(SPILL_DIR, old))
                    except OSError:
                        pass
        except OSError:
            pass
        digest = hashlib.sha1(content.encode("utf-8", "ignore")).hexdigest()[:8]
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in (name or "tool"))[:40]
        path = os.path.join(SPILL_DIR, time.strftime("%Y%m%d-%H%M%S") + "-" + digest + "-" + safe + ".txt")
        with open(path, "w", encoding="utf-8", errors="ignore") as f:
            f.write(content)
        cut = len(content) - SPILL_HEAD - SPILL_TAIL
        return (
            content[:SPILL_HEAD]
            + "\n…[SPILL: вырезано " + str(cut) + " символов]…\n"
            + content[-SPILL_TAIL:]
            + "\n\n[SPILL] Полный вывод инструмента (" + str(len(content))
            + " символов) сохранён в файл: " + path
            + " — если нужны вырезанные детали, читай его через terminal (cat/grep/sed)."
        )
    except Exception:
        return content


def _guard(name, content):
    if os.environ.get("HERMES_GUARD", "1") == "0":
        return content
    if not isinstance(content, str) or not content:
        return content
    try:
        now = time.time()
        key = (name or "") + ":" + hashlib.sha1(content[:4000].encode("utf-8", "ignore")).hexdigest()
        cnt, ts = _recent.get(key, (0, now))
        if now - ts > _GUARD_TTL:
            cnt = 0
        cnt += 1
        if len(_recent) > _MAX_RECENT:
            _recent.clear()
        _recent[key] = (cnt, now)
        if cnt >= 3:
            return content + (
                "\n\n[GUARD] Инструмент «" + (name or "?") + "» уже " + str(cnt)
                + " раза подряд вернул одинаковый результат. Не повторяй тот же вызов "
                + "с теми же параметрами — смени подход или честно сообщи пользователю, что не выходит."
            )
        return content
    except Exception:
        return content


def process(name, content):
    """Единая точка: сначала spill (укоротить), затем guard (пометить повтор)."""
    return _guard(name, _spill(name, content))
'''

OLD = "    wrapped = _maybe_wrap_untrusted(name, content)"
NEW = """    try:  # dsh-spill-guard-patch: spill + guard (см. agent/spill_guard.py)
        from agent.spill_guard import process as _sg_process
        content = _sg_process(name, content)
    except Exception:
        pass
    wrapped = _maybe_wrap_untrusted(name, content)"""


def main() -> int:
    src = open(HELPERS, encoding="utf-8").read()
    if MARKER in src and os.path.exists(TARGET):
        print("[spill_guard_v1] уже применён — пропускаю")
        return 0
    if OLD not in src:
        print("[spill_guard_v1] FATAL: якорь _maybe_wrap_untrusted не найден")
        return 1
    open(TARGET, "w", encoding="utf-8").write(SPILL_GUARD_SRC)
    py_compile.compile(TARGET, doraise=True)
    bak = HELPERS + ".bak-spillguard"
    shutil.copy2(HELPERS, bak)
    open(HELPERS, "w", encoding="utf-8").write(src.replace(OLD, NEW, 1))
    try:
        py_compile.compile(HELPERS, doraise=True)
    except Exception as e:
        shutil.copy2(bak, HELPERS)
        print("[spill_guard_v1] FATAL: компиляция не прошла, откат: %s" % e)
        return 1
    print("[spill_guard_v1] OK: spill_guard.py создан, врезка внесена")
    return 0


if __name__ == "__main__":
    sys.exit(main())
