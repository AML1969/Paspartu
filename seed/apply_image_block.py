#!/usr/bin/env python3
"""Ставит/обновляет блок правил про картинки в SOUL.md / AGENTS.md.
Идемпотентно и, в отличие от простого append, УМЕЕТ ОБНОВЛЯТЬ: старый блок снимается
(по маркерам image:begin/end, а у самых первых копий — легаси-блок без маркеров),
новый дописывается. Аргументы: <файл> <файл-блока>."""
import re, sys

f, b = sys.argv[1], sys.argv[2]
block = open(b, encoding="utf-8").read().strip()
t = open(f, encoding="utf-8").read()

# 1) блок в маркерах — вырезаем целиком
t = re.sub(r"\n*<!-- image:begin -->.*?<!-- image:end -->\n*", "\n\n", t, flags=re.S)
# 2) легаси-блок без маркеров (наши первые версии) — от его заголовка до конца файла,
#    но только если после него не идут другие блоки (routing/codex/tracker)
m = re.search(r"\n## (?:Картинки — квитанция|Квитанция перед генерацией)", t)
if m:
    tail = t[m.start():]
    if not re.search(r"\n## Маршрутизация|codex-no-media-guard|<!-- task-tracker:begin -->", tail):
        t = t[:m.start()]
t = t.rstrip() + "\n\n" + block + "\n"
open(f, "w", encoding="utf-8").write(t)
print("image-блок обновлён:", f)
