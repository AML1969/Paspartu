#!/usr/bin/env python3
# rich_v6b_draft_strip.py — RU v6b: strip local-path images from rich draft text
# (идемпотентный патч Hermes Gateway, скриптовая версия ручной правки от 30.06.2026)
#
# ПРОБЛЕМА: sendRichMessageDraft (живой rich-превью на стриминге) отбивает
# ![alt](local/path) с RICH_MESSAGE_PHOTO_URL_INVALID (rich требует HTTP-URL для
# инлайн-фото). Из-за этого превью с локальными картинками падало целиком.
#
# ФИКС: в send_draft() (создан rich_v3.py) —
#   1) вычислить _draft_text (текст без разметки локальных картинок) и флаг
#      _has_local_images ПОСЛЕ строки thread_id = self._metadata_thread_id(metadata);
#   2) rich-draft путь пропускать, если есть локальные картинки
#      (if self._rich_enabled() and not _has_local_images:) — тогда финальный
#      send() доставит и текст, и медиа-альбом штатно;
#   3) сток-путь (MarkdownV2 draft) слать уже почищенный _draft_text.
#
# Слот: ПОСЛЕ rich_v6.py и ДО rich_v7_carousel.py. Отдельный скрипт (а не в v8),
# потому что правка касается send_draft (семейство v3–v6), не зависит от хелпера
# v8 и от base.py-каруселя, а inline-форма re.sub здесь ИНАЯ ([^)]+\)\s*), чем в
# хелпере v8 — воспроизводим рабочее LIVE-состояние 1:1.
#
# Идемпотентно (маркер "RU v6: strip local-path image markdown from draft text"),
# атомарно (все якоря проверяются, затем одна запись), py_compile + авто-откат.
# Слетает при `hermes update` — включён в update_hermes.sh после rich_v6.py.

import sys, py_compile, shutil, time

TG = "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages/gateway/platforms/telegram.py"
MARKER = "RU v6: strip local-path image markdown from draft text"

PAIRS = [
    # 1) insert _draft_text/_has_local_images block right after send_draft's
    #    thread_id line, before the RU v3 rich-draft comment. This anchor
    #    (thread_id + blank line + RU v3 comment) is unique to send_draft.
    (
        "        thread_id = self._metadata_thread_id(metadata)\n"
        "\n"
        "        # RU v3: rich draft streaming — render markdown live in the preview.\n",
        "        thread_id = self._metadata_thread_id(metadata)\n"
        "\n"
        "        # RU v6: strip local-path image markdown from draft text.\n"
        "        # sendRichMessageDraft rejects ![...](local/path) with\n"
        "        # RICH_MESSAGE_PHOTO_URL_INVALID. Strip them so the draft\n"
        "        # renders cleanly; the final send() will deliver media properly.\n"
        "        _draft_text = text\n"
        "        _has_local_images = False\n"
        "        if re.search(r'!\\[[^\\]]*\\]\\((?:/|~/|file://)', text):\n"
        "            _has_local_images = True\n"
        "            _draft_text = re.sub(r'!\\[[^\\]]*\\]\\((?:/|~/|file://)[^)]+\\)\\s*', '', text)\n"
        "\n"
        "        # RU v3: rich draft streaming — render markdown live in the preview.\n",
    ),
    # 2) gate the rich-draft path so local-image drafts fall to the stock path.
    (
        "        # RU v3: rich draft streaming — render markdown live in the preview.\n"
        "        if self._rich_enabled():\n",
        "        # RU v3: rich draft streaming — render markdown live in the preview.\n"
        "        if self._rich_enabled() and not _has_local_images:\n",
    ),
    # 3) stock MarkdownV2 draft path sends the cleaned _draft_text.
    (
        '                "text": self.format_message(text) if use_markdown else text,\n',
        '                "text": self.format_message(_draft_text) if use_markdown else _draft_text,\n',
    ),
]


def main() -> int:
    src = open(TG, encoding="utf-8").read()
    if MARKER in src:
        print("telegram.py: уже пропатчен (RU v6b draft-strip), пропуск")
        return 0
    # verify all anchors present exactly once BEFORE touching anything
    for i, (anchor, _new) in enumerate(PAIRS, 1):
        n = src.count(anchor)
        if n != 1:
            print(f"telegram.py: якорь #{i} найден {n} раз (ожидалось 1) — АБОРТ, изменений нет")
            return 1
    bak = TG + ".bak-" + time.strftime("%Y%m%d-%H%M%S") + "-" + str(time.time_ns() % 10**9)
    shutil.copy2(TG, bak)
    patched = src
    for anchor, new in PAIRS:
        patched = patched.replace(anchor, new, 1)
    open(TG, "w", encoding="utf-8").write(patched)
    try:
        py_compile.compile(TG, doraise=True)
    except Exception as e:  # noqa: BLE001
        shutil.copy2(bak, TG)
        print(f"telegram.py: py_compile ОШИБКА, откат из {bak}: {e}")
        return 1
    print(f"telegram.py: RU v6b draft-strip применён (3 правки). Бэкап: {bak}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
