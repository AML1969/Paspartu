#!/usr/bin/env python3
# rich_v8_carousel_rich.py — RU carousel-fix v8 (идемпотентный патч Hermes Gateway)
#
# ПРОБЛЕМА (тест 01.07.2026): карусель картинок починена (Fix B), но rich-текст
# ломается на середине. Разметка локальных картинок ![alt](//root/...png) остаётся
# ВНУТРИ текста; как только rich-рендер (sendRichMessage / editMessageText) до неё
# доходит, Telegram отбивает всё сообщение (RICH_MESSAGE_PHOTO_URL_INVALID) и остаток
# доживает ПЛОСКИМ: таблица -> буллеты, <details> и вложенные цитаты сырьём,
# подписи "!Горное озеро..." протекают текстом.
#
# ФИКС v8: вырезать разметку локальных картинок из ТЕКСТА во всех rich-точках
# рендера. Тогда текст рендерится rich до конца, а картинки по-прежнему уходят
# ОТДЕЛЬНЫМ альбомом (send_media_group, механизм не трогаем). 5 правок:
#   1) новый хелпер TelegramAdapter._rich_strip_local_images()
#   2) _rich_suitable: не сваливать в плоское из-за картинок (только если текст
#      состоит ТОЛЬКО из картинок)
#   3) _send_rich: стрип перед normalize (+ пропуск пустого)
#   4a) edit_message finalize: доп. условие «после стрипа непусто»
#   4b) edit_message finalize: стрип внутри rich_message.markdown
#
# ВАЖНО (chain-safe): правка #2 привязана к БЕЗ-ГАРДОВОМУ хвосту _rich_suitable,
# который производят rich_messages.py + rich_v5.py на ПРИСТИННОМ upstream
# (комментарий "RU v5: always rich ..." + "return True"), а НЕ к рукотворной
# гардовой форме от 30.06 (её на чистом апдейте не существует). v8 сам вставляет
# гард-со-стрипом перед финальным "return True". re уже импортирован в начале
# _rich_suitable, поэтому повторный import не добавляем.
#
# Идемпотентно (маркер "RU carousel-fix v8"), атомарно (все якоря проверяются,
# затем одна запись), py_compile + авто-откат. Слетает при `hermes update` —
# включить в update_hermes.sh после rich_v7_carousel.py.

import sys, py_compile, shutil, time

TG = "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages/gateway/platforms/telegram.py"
MARKER = "RU carousel-fix v8"

PAIRS = [
    # 1) helper
    (
        "    def _rich_enabled(self):\n        import os\n",
        "    def _rich_strip_local_images(self, md):\n"
        "        import re\n"
        "        # RU carousel-fix v8: strip ![alt](local/path) so rich text renders\n"
        "        # end-to-end; local paths can't be inline in rich (need HTTP URL) and\n"
        "        # ship separately as a Telegram album (send_media_group).\n"
        "        return re.sub(r'!\\[[^\\]]*\\]\\((?:/|~/|file://)[^)]*\\)[ \\t]*\\n?', '', md)\n"
        "\n"
        "    def _rich_enabled(self):\n        import os\n",
    ),
    # 2) _rich_suitable guard — CHAIN-SAFE: anchor on the UN-guarded return True
    #    produced by rich_messages.py + rich_v5.py on pristine upstream. v8
    #    inserts the guard-with-strip before that final return True. re is
    #    already imported at the top of _rich_suitable, so no re-import here.
    (
        "        # RU v5: always rich — drafts render rich unconditionally, so the\n"
        "        # final must too, otherwise short answers degrade at finalization.\n"
        "        return True\n",
        "        # RU v5: always rich — drafts render rich unconditionally, so the\n"
        "        # final must too, otherwise short answers degrade at finalization.\n"
        "        # RU carousel-fix v8: keep rich even when the message embeds local-path\n"
        "        # images — the rich render strips the image markdown, images ship as an\n"
        "        # album. Only fall back to plain when the message is NOTHING BUT image(s).\n"
        "        if re.search(r'!\\[[^\\]]*\\]\\((?:/|~/|file://)', content):\n"
        "            if not re.sub(r'!\\[[^\\]]*\\]\\((?:/|~/|file://)[^)]*\\)', '', content).strip():\n"
        "                return False\n"
        "        return True\n",
    ),
    # 3) _send_rich strip-before-normalize
    (
        "            content = self._rich_normalize(content)  # RU v4\n",
        "            content = self._rich_strip_local_images(content)  # RU carousel-fix v8\n"
        "            if not content.strip():\n"
        "                return None\n"
        "            content = self._rich_normalize(content)  # RU v4\n",
    ),
    # 4a) edit_message finalize condition
    (
        "        if finalize and self._rich_enabled() and len(content) <= 32000 \\\n"
        "                and \"MEDIA:\" not in content and \"[[audio_as_voice]]\" not in content:\n",
        "        if finalize and self._rich_enabled() and len(content) <= 32000 \\\n"
        "                and \"MEDIA:\" not in content and \"[[audio_as_voice]]\" not in content \\\n"
        "                and self._rich_strip_local_images(content).strip():  # RU carousel-fix v8\n",
    ),
    # 4b) edit_message finalize markdown strip
    (
        '{"markdown": self._rich_normalize(content)}',
        '{"markdown": self._rich_normalize(self._rich_strip_local_images(content))}',
    ),
]


def main() -> int:
    src = open(TG, encoding="utf-8").read()
    if MARKER in src:
        print("telegram.py: уже пропатчен (RU carousel-fix v8), пропуск")
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
    print(f"telegram.py: RU carousel-fix v8 применён (5 правок). Бэкап: {bak}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
