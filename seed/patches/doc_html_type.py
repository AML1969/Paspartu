#!/usr/bin/env python3
"""Патч: разрешить приём .html/.htm как входящих документов (Telegram и др.).

Причина: SUPPORTED_DOCUMENT_TYPES в gateway/platforms/base.py не содержал
html — gateway отбивал файл сообщением "Unsupported document type '.html'",
а модель поверх этого сочиняла "Telegram не принимает .html" (неправда:
Telegram доставляет файл нормально).

Идемпотентный, self-backup, py_compile с автооткатом.
Маркер: "html-doc-type-patch".
"""
import py_compile
import shutil
import sys

BASE = "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages/gateway/platforms/base.py"
MARKER = "html-doc-type-patch"

OLD = '    ".cfg": "text/plain",\n    ".zip": "application/zip",'
NEW = (
    '    ".cfg": "text/plain",\n'
    '    # html-doc-type-patch: html/htm — текстовые документы, читаются read_file\n'
    '    ".html": "text/html",\n'
    '    ".htm": "text/html",\n'
    '    ".zip": "application/zip",'
)


def main() -> int:
    src = open(BASE, encoding="utf-8").read()
    if MARKER in src:
        print("[html-doc-type] already applied")
        return 0
    if src.count(OLD) != 1:
        print("[html-doc-type] FATAL: anchor count = %d" % src.count(OLD))
        return 1
    bak = BASE + ".bak-htmldoc"
    shutil.copy2(BASE, bak)
    open(BASE, "w", encoding="utf-8").write(src.replace(OLD, NEW))
    try:
        py_compile.compile(BASE, doraise=True)
    except Exception as exc:
        shutil.copy2(bak, BASE)
        print("[html-doc-type] FATAL: compile failed, rolled back: %s" % exc)
        return 1
    print("[html-doc-type] applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
