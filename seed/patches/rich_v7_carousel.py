#!/usr/bin/env python3
# rich_v7_carousel.py — RU carousel-fix (идемпотентный патч Hermes Gateway)
#
# ПРОБЛЕМА (30.06.2026): модель (DeepSeek) иногда вставляет путь картинки с
# ДВОЙНЫМ слэшем — ![alt](//root/.hermes/cache/images/x.png). Файл существует
# (в POSIX // схлопывается), но extract_local_files() в base.py использует
# lookbehind (?<![/:\w.]) и НЕ матчит //root -> список локальных файлов пустой
# -> альбом (send_multiple_images -> send_media_group) не собирается ->
# картинки/карусель не доходят в Telegram вообще. Одиночный слэш /root матчится
# и работает (карусель 08.06 работала именно так).
#
# ФИКС: в начале extract_local_files() нормализуем ](//  ->  ](/ (2+ слэша сразу
# после markdown-открытия ссылки). Единая точка — покрывает и стриминг-путь
# (_deliver_media_from_response в run.py), и обычный путь (base.py). Механизм
# альбома уже существует и рабочий — ему просто начинают доезжать пути.
#
# Идемпотентно: маркер "RU carousel-fix", проверка якоря (ровно 1 вхождение),
# бэкап .bak-<ts>, py_compile-проверка, авто-откат при ошибке.
# Слетает при `hermes update` — включён в update_hermes.sh после rich_v6.py.

import sys, py_compile, shutil, time

BASE = "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages/gateway/platforms/base.py"
MARKER = "RU carousel-fix"

ANCHOR = "        _LOCAL_MEDIA_EXTS = MEDIA_DELIVERY_EXTS"
NEW = (
    "        content = re.sub(r'\\]\\(/{2,}', '](/', content)  # RU carousel-fix: model emits //root double-slash\n"
    "        _LOCAL_MEDIA_EXTS = MEDIA_DELIVERY_EXTS"
)


def main() -> int:
    src = open(BASE, encoding="utf-8").read()
    if MARKER in src:
        print("base.py: уже пропатчен (RU carousel-fix), пропуск")
        return 0
    n = src.count(ANCHOR)
    if n != 1:
        print(f"base.py: якорь найден {n} раз (ожидалось 1) — АБОРТ, изменений нет")
        return 1
    bak = BASE + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(BASE, bak)
    patched = src.replace(ANCHOR, NEW, 1)
    open(BASE, "w", encoding="utf-8").write(patched)
    try:
        py_compile.compile(BASE, doraise=True)
    except Exception as e:  # noqa: BLE001
        shutil.copy2(bak, BASE)
        print(f"base.py: py_compile ОШИБКА, откат из {bak}: {e}")
        return 1
    print(f"base.py: RU carousel-fix применён. Бэкап: {bak}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
