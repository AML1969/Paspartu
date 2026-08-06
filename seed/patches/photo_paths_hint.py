#!/usr/bin/env python3
"""Патч: класть в промпт РЕАЛЬНЫЕ пути картинок/видео с диска.

Без него gateway/run.py не сообщал модели пути вложений, и та отвечала
«не вижу фото» либо выдумывала имя файла -> Invalid image source.
Видео дополнительно отбрасывалось (media_types video/*), поэтому блок
video_analyze добавляется отдельно.

Оформлено из /root/photofix.py (31.07.2026) в формат цепочки:
путь хардкодом на pipx (Dockerfile ретаргетит sed'ом на site-packages образа),
маркер идемпотентности, abort при отсутствии якоря, .bak, py_compile с автооткатом.
Маркер: "PHOTO_PATHS_HINT".
"""
import io
import pathlib
import py_compile
import shutil
import sys
import time

RUN = "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages/gateway/run.py"
MARKER = "PHOTO_PATHS_HINT"
NL = chr(10)


def main():
    P = pathlib.Path(RUN)
    if not P.is_file():
        print("FAIL: run.py не найден: %s" % RUN)
        sys.exit(2)
    src = io.open(str(P), encoding="utf-8").read()
    if MARKER in src:
        print("already applied — no-op")
        return

    lines = src.split(NL)

    ai = [i for i, l in enumerate(lines)
          if l.strip() == "pending_native[session_key] = list(image_paths)"]
    if len(ai) != 1:
        print("ANCHOR PROBLEM (image): совпадений %d — версия hermes изменилась? abort" % len(ai))
        sys.exit(1)
    i = ai[0]
    ind = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
    blk = [
        ind + '# PHOTO_PATHS_HINT: real on-disk paths so the model never invents a filename',
        ind + 'try:',
        ind + '    message_text = (message_text or "") + NL_H*2 + "[attached image files on disk - use these exact paths, never guess a filename]" + NL_H + NL_H.join("- " + str(_p) for _p in image_paths)',
        ind + 'except Exception:',
        ind + '    pass',
    ]
    lines[i + 1:i + 1] = blk

    j = None
    for k in range(i, len(lines)):
        if lines[k].strip() == "if audio_paths:":
            j = k
            break
    if j is None:
        print("ANCHOR PROBLEM (video): нет 'if audio_paths:' — abort")
        sys.exit(1)
    ind2 = lines[j][:len(lines[j]) - len(lines[j].lstrip())]
    vblk = [
        ind2 + '# PHOTO_PATHS_HINT video: expose cached video paths to the model',
        ind2 + 'try:',
        ind2 + '    _mt = list(getattr(event, "media_types", None) or [])',
        ind2 + '    _vp = [p for n, p in enumerate(event.media_urls or []) if (_mt[n] if n < len(_mt) else "").startswith("video/")]',
        ind2 + '    if _vp:',
        ind2 + '        message_text = (message_text or "") + NL_H*2 + "[attached video files on disk - analyse them with video_analyze, never claim you see nothing]" + NL_H + NL_H.join("- " + str(_p) for _p in _vp)',
        ind2 + 'except Exception:',
        ind2 + '    pass',
    ]
    lines[j:j] = vblk

    out = NL.join(lines).replace("NL_H", "chr(10)")

    bak = str(P) + ".bak-" + time.strftime("%Y%m%d-%H%M%S") + "-photohint"
    shutil.copy2(str(P), bak)
    print("backup:", bak)
    io.open(str(P), "w", encoding="utf-8").write(out)
    try:
        py_compile.compile(str(P), doraise=True)
    except Exception as e:
        shutil.copy2(bak, str(P))
        print("COMPILE FAILED, reverted:", e)
        sys.exit(1)
    print("run.py py_compile OK — PHOTO_PATHS_HINT applied")


if __name__ == "__main__":
    main()
