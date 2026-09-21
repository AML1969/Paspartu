#!/usr/bin/env python3
"""Патч: поднять лимит контекстных файлов (SOUL.md, AGENTS.md, .hermes.md) с 20 000 до 60 000 символов.

Почему: prompt_builder._truncate_content режет файл длиннее лимита — оставляет первые 70%
и последние 20% лимита, СЕРЕДИНУ ВЫБРАСЫВАЕТ. SOUL у всех трёх ботов 29–31 тыс. символов →
модель месяцами не видела ~13 тыс. символов правил (ссылки, люди и факты, законы записи и др.).
Найдено 21.09.2026 по маркеру «[...truncated SOUL.md: kept 14000+4000 of 31455 chars]».

⚠️ max_chars — дефолт аргумента функции, вычисляется при импорте: после патча нужен рестарт гейтвея.
Идемпотентный, self-backup, py_compile с автооткатом. Слетает при `hermes update` — перезапустить.
Маркер: "soul-limit-60k-patch". Необязательный аргумент — путь к prompt_builder.py (для docker).
"""
import py_compile
import shutil
import sys

PB = "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages/agent/prompt_builder.py"
MARKER = "soul-limit-60k-patch"
OLD = "CONTEXT_FILE_MAX_CHARS = 20_000\n"
NEW = "CONTEXT_FILE_MAX_CHARS = 60_000  # soul-limit-60k-patch: при 20k SOUL резался посередине\n"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else PB
    src = open(path, encoding="utf-8").read()
    if MARKER in src:
        print("already applied — no-op")
        return
    if src.count(OLD) != 1:
        print("ERROR: строка лимита не найдена (или не одна) — версия hermes поменялась? отмена")
        sys.exit(1)
    bak = path + ".bak-soullimit"
    shutil.copy2(path, bak)
    open(path, "w", encoding="utf-8").write(src.replace(OLD, NEW, 1))
    try:
        py_compile.compile(path, doraise=True)
    except py_compile.PyCompileError as e:
        shutil.copy2(bak, path)
        print("ERROR: py_compile упал, откатил:", e)
        sys.exit(1)
    print("applied: CONTEXT_FILE_MAX_CHARS 20_000 -> 60_000 (%s)" % path)


if __name__ == "__main__":
    main()
