#!/usr/bin/env python3
"""Патч: файл >20 МБ в Telegram -> прямой русский ответ пользователю, мимо модели.
Идемпотентный, self-backup, py_compile с автооткатом. Слетает при `hermes update` — перезапустить.
Маркер: "doc-limit-ru-patch".
"""
import py_compile
import shutil
import sys

TG = "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages/gateway/platforms/telegram.py"
MARKER = "doc-limit-ru-patch"

OLD = """                if not doc.file_size or doc.file_size > self._max_doc_bytes:
                    limit_mb = self._max_doc_bytes // (1024 * 1024)
                    event.text = (
                        "The document is too large or its size could not be verified. "
                        f"Maximum: {limit_mb} MB."
                    )
                    logger.info("[Telegram] Document too large: %s bytes", doc.file_size)
                    await self.handle_message(event)
                    return
"""

NEW = """                if not doc.file_size or doc.file_size > self._max_doc_bytes:
                    limit_mb = self._max_doc_bytes // (1024 * 1024)
                    # doc-limit-ru-patch: прямой ответ пользователю, без вызова модели
                    if doc.file_size:
                        _sz = doc.file_size / (1024 * 1024)
                        _msg = (
                            f"\\u26a0\\ufe0f Файл ~{_sz:.0f} МБ, а Telegram разрешает ботам "
                            f"скачивать максимум {limit_mb} МБ."
                        )
                    else:
                        _msg = (
                            f"\\u26a0\\ufe0f Telegram разрешает ботам скачивать файлы максимум {limit_mb} МБ "
                            "(размер этого файла проверить не удалось)."
                        )
                    _msg += " Пришли это как фото (Telegram сожмёт сам) или дай ссылку на файл — скачаю по ней."
                    try:
                        await update.message.reply_text(_msg)
                    except Exception:
                        logger.exception("[Telegram] doc-limit notice failed")
                    logger.info("[Telegram] Document too large: %s bytes", doc.file_size)
                    return
"""


def main():
    src = open(TG, encoding="utf-8").read()
    if MARKER in src:
        print("already applied — no-op")
        return
    if OLD not in src:
        print("ERROR: original block not found — hermes version changed? aborting")
        sys.exit(1)
    bak = TG + ".bak-doclimit"
    shutil.copy2(TG, bak)
    open(TG, "w", encoding="utf-8").write(src.replace(OLD, NEW, 1))
    try:
        py_compile.compile(TG, doraise=True)
    except Exception as e:
        shutil.copy2(bak, TG)
        print("COMPILE FAILED, reverted:", e)
        sys.exit(1)
    print("patched OK, backup:", bak)


if __name__ == "__main__":
    main()
