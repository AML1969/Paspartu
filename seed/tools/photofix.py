#!/usr/bin/env python3
"""
photofix.py — идемпотентный патч gateway/run.py: отдать модели РЕАЛЬНЫЕ пути
присланных фото и видео прямо в тексте сообщения.

Зачем:
  1) В режиме image_input_mode="native" пути картинок складываются только в
     _pending_native_image_paths_by_session и в промпт НЕ попадают. Когда модель
     решает вызвать vision_analyze, у неё нет валидного имени файла — она его
     выдумывает и получает «Invalid image source».
  2) Видео вообще терялось: run.py собирает из event.media_urls только image/*
     и audio/*, а video/* молча отбрасывает — бот отвечал «не вижу фото».

Что делает: вставляет два блока с маркером PHOTO_PATHS_HINT.
Повторный запуск ничего не меняет. Бэкап рядом: <file>.bak-photofix

Использование:
  python3 photofix.py /path/to/site-packages/gateway/run.py
  docker cp photofix.py <container>:/tmp/photofix.py && \
    docker exec <container> python3 /tmp/photofix.py \
      /usr/local/lib/python3.12/site-packages/gateway/run.py
"""
import pathlib, shutil, sys
NL = chr(10)
P = pathlib.Path(sys.argv[1])
src = P.read_text()
if 'PHOTO_PATHS_HINT' in src:
    print('ALREADY PATCHED'); sys.exit(0)
lines = src.split(NL)
ai = [i for i, l in enumerate(lines) if l.strip() == 'pending_native[session_key] = list(image_paths)']
if len(ai) != 1:
    print('ANCHOR PROBLEM', ai); sys.exit(1)
i = ai[0]
ind = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
blk = [
 ind + '# PHOTO_PATHS_HINT: real on-disk paths so the model never invents a filename',
 ind + 'try:',
 ind + '    message_text = (message_text or "") + NL_H*2 + "[attached image files on disk - use these exact paths, never guess a filename]" + NL_H + NL_H.join("- " + str(_p) for _p in image_paths)',
 ind + 'except Exception:',
 ind + '    pass',
]
lines[i+1:i+1] = blk
j = None
for k in range(i, len(lines)):
    if lines[k].strip() == 'if audio_paths:':
        j = k; break
if j is None:
    print('NO audio_paths ANCHOR'); sys.exit(1)
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
out = NL.join(lines)
out = out.replace('NL_H', 'chr(10)')
shutil.copy(str(P), str(P) + '.bak-photofix')
P.write_text(out)
print('PATCHED ok')
