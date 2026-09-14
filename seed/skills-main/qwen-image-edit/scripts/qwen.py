#!/usr/bin/env python3
"""qwen.py — картинки на fal.ai: генерация + редактирование, два движка.

Движки (11.09.2026):
    openai   OpenAI GPT-Image-2.5 Sunburst  (openai/gpt-image-2.5/sunburst/…) — люди, лица, любой текст
    qwen     Qwen-Image 2.0                 (fal-ai/qwen-image-2/…)            — всё остальное, дёшево и быстро
Какой движок — решает настройка бота в файле $HERMES_HOME/image_engine:
    openai → всегда Sunburst;  auto (или файла нет) → люди/текст → Sunburst, иначе Qwen;  qwen → всегда Qwen.
Флаг --engine перебивает настройку. Упал один движок — скрипт сам пробует второй.
Проба 11.09: Qwen на фото с людьми подменяет лица и путает кириллицу, Sunburst — нет.

Автопромптинг (14.09.2026): промпт собирает DeepSeek по гайду OpenAI GPT-Image-2.5 — для Sunburst
помеченными секциями (Task/Subject/Composition/Style/Text/Preserve/Avoid), для Qwen короткой строкой.
Он же подбирает quality (текст → xhigh), прозрачный фон + png и форму кадра. Флаги всё перебивают.

Ниже — исходное описание Qwen-части (эндпоинты и правила не изменились).

Модель одна — Qwen-Image 2.0 (и её pro-вариант), четыре эндпоинта:
    generate            fal-ai/qwen-image-2/text-to-image      (текст → картинка)
    generate --pro      fal-ai/qwen-image-2/pro/text-to-image  (то же, максимум качества)
    edit                fal-ai/qwen-image-2/edit               (правка РЕАЛЬНЫХ фото, 1–3 шт)
    edit --pro          fal-ai/qwen-image-2/pro/edit

Что умеет edit (всё это — один и тот же вызов, разница только в prompt):
    • заменить/убрать объект, поменять фон, перекрасить, добавить надпись;
    • СЛИТЬ НЕСКОЛЬКО ФОТО: перенести человека/предмет с одного кадра в другой,
      собрать общий кадр из отдельных снимков (--image A --image B [--image C]);
    • сменить свет/сцену/ракурс, сохранив людей и композицию.

Правила жизни:
    • фото пользователя — ИСХОДНИК: если фото есть, правим его, а не рисуем новое;
    • локальные файлы жмутся сами (Pillow, ≤2048px) — внешний `convert` не нужен;
    • ключ из FAL_KEY, а если песочница его вырезала — из .env профиля;
    • результат скачивается локально, наружу идёт поле telegram=MEDIA:<путь>
      (ссылки fal.media Telegram не грузит — они не возвращаются никогда).
"""
import argparse, base64, io, json, mimetypes, os, re, sys, time, urllib.error, urllib.request

BASE = "https://fal.run/"
EP = {
    ("generate", False): "fal-ai/qwen-image-2/text-to-image",
    ("generate", True):  "fal-ai/qwen-image-2/pro/text-to-image",
    ("edit", False):     "fal-ai/qwen-image-2/edit",
    ("edit", True):      "fal-ai/qwen-image-2/pro/edit",
}
# Схема Qwen-Image 2.0 (fal openapi, 14.07.2026) знает РОВНО эти поля. Лишнее → 422.
FIELDS = {"prompt", "image_urls", "image_size", "num_images", "output_format"}
SIZES = ("square_hd", "square", "portrait_4_3", "portrait_16_9",
         "landscape_4_3", "landscape_16_9")
MAX_BYTES, MAX_SIDE = 12 * 1024 * 1024, 2048

# OpenAI GPT-Image-2.5 на fal (schema 11.09.2026). Работает на том же FAL_KEY.
OA_EP = {"generate": "openai/gpt-image-2.5/sunburst/text-to-image",
         "edit":     "openai/gpt-image-2.5/sunburst/edit"}
OA_FIELDS = {"prompt", "image_urls", "image_size", "num_images", "output_format", "quality", "background"}
OA_MAX_IMAGES, QWEN_MAX_IMAGES = 16, 3
QUALITIES = ("low", "medium", "high", "xhigh", "max", "auto")
DEADLINE_S = 165          # у терминала бота лимит 180 с — всё должно успеть внутри
ENGINES = ("auto", "openai", "qwen")

TEXT_RE = re.compile(r"[«»“”„]|надпис|текст|вывеск|постер|плакат|афиш|открытк|логотип|подпис|баннер|обложк|"
                     r"табличк|слоган|меню\b|\bsign\b|\btext\b|poster|lettering|caption|logo|banner|headline", re.I)
PEOPLE_RE = re.compile(r"люд|человек|мужчин|женщин|девушк|девочк|парн|парен|мальчик|ребён|ребен|дет[иьея]|малыш|"
                       r"\bлиц[оау]?\b|портрет|семь[яиюе]|бабушк|дедушк|\bмам[аыу]\b|\bпап[аыу]\b|\bжен[аыу]\b|\bмуж\b|"
                       r"\bсын|\bдоч|друз|селфи|силуэт|\bperson|people|\bman\b|\bmen\b|woman|women|girl|\bboys?\b|"
                       r"child|\bkids?\b|\bfaces?\b|portrait|couple|family|selfie|crowd|silhouette", re.I)
NO_PEOPLE_RE = re.compile(r"(людей|человека|лиц)\s+нет|нет\s+(людей|человека|лиц)|без\s+людей|no\s+(people|person|humans?)", re.I)


def engine_setting():
    """Настройка бота: файл image_engine в HERMES_HOME. Нет файла → auto."""
    home = os.environ.get("HERMES_HOME", "") or os.path.expanduser("~/.hermes")
    for f in (os.path.join(home, "image_engine"), "/data/hermes/image_engine"):
        try:
            v = open(f, encoding="utf-8").read().strip().lower()
            return (v if v in ENGINES else "auto"), f
        except OSError:
            continue
    return "auto", "(файла нет)"


def detect(raw, prompt, descr, llm):
    """Люди/текст. Есть оценка DeepSeek → берём её, а людей дополнительно ловим по vision-описаниям
    исходников (надёжно). Оценки нет (ручной --prompt, сбой) → по словам запроса.
    Сам собранный промпт по словам НЕ проверяем: там бывает «No text», «keep every person…»."""
    seen = NO_PEOPLE_RE.sub(" ", " ".join(descr or []))
    in_photo = bool(PEOPLE_RE.search(seen))
    if llm is not None:
        return {"people": bool(llm.get("people")) or in_photo, "text": bool(llm.get("text"))}
    req = " ".join(x for x in (raw or "", prompt or "") if x)
    req = re.sub(r"(?i)keep every person.*$", "", req)   # хвост KEEP-фразы — не сигнал
    return {"people": bool(PEOPLE_RE.search(req)) or in_photo, "text": bool(TEXT_RE.search(req))}


def choose_engine(forced, setting, n_images, flags):
    if forced in ("openai", "qwen"):
        return forced, "флаг --engine " + forced
    if setting in ("openai", "qwen"):
        return setting, "настройка бота: " + setting
    if n_images > QWEN_MAX_IMAGES:
        return "openai", "больше %d фото" % QWEN_MAX_IMAGES
    if flags.get("people"):
        return "openai", "на картинке люди"
    if flags.get("text"):
        return "openai", "на картинке текст"
    return "qwen", "без людей и текста"


def fail(msg, **extra):
    out = {"error": msg}
    out.update(extra)
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(1)


def get_fal_key() -> str:
    """FAL_KEY из окружения; песочница терминала его вырезает → читаем .env профиля сами."""
    k = os.environ.get("FAL_KEY", "").strip()
    if k:
        return k
    home = os.environ.get("HERMES_HOME", "") or os.path.expanduser("~/.hermes")
    for envf in (os.path.join(home, ".env"), "/data/hermes/.env", "/root/.hermes/.env"):
        try:
            with open(envf, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("FAL_KEY="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


def data_uri(path: str) -> str:
    raw = open(path, "rb").read()
    mime = mimetypes.guess_type(path)[0] or "image/png"
    small = len(raw) <= MAX_BYTES
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        if small and max(im.size) <= MAX_SIDE:
            return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode())
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=92, optimize=True)
        return "data:image/jpeg;base64,%s" % base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        if not small:
            fail("фото %.1f МБ, а Pillow не установлен — уменьши файл" % (len(raw) / 1048576))
        return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode())
    except Exception as e:  # noqa: BLE001
        fail("не смог прочитать/ужать %s: %s" % (path, e))


def to_url(img: str) -> str:
    img = img.strip()
    if img.startswith(("http://", "https://")):
        return img
    if not os.path.isfile(img):
        fail("файл не найден: %s" % img)
    return data_uri(img)


# ── Сборка промпта: vision-описание фото → (опц. факты из веба) → промпт по правилам Qwen ──
KEEP = ("Keep every person's face, identity, expression, hairstyle, body and clothing EXACTLY "
        "unchanged — same people. Do not add, remove or invent anything that was not requested.")

RULEBOOK = """Ты — промпт-инженер для моделей картинок Qwen-Image 2.0 и OpenAI GPT-Image-2.5 Sunburst.
Тебе дают: (1) дословный запрос пользователя (обычно по-русски), (2) описания его исходных фото,
(3) иногда факты из интернета. Верни РОВНО ОДИН JSON-объект — и больше ничего (без markdown, без пояснений):
{"prompt": "<короткий промпт, 1-4 предложения>",
 "sections": {"task": "", "subject": "", "composition": "", "style": "", "text": "", "preserve": "", "avoid": ""},
 "people": true|false, "text": true|false, "transparent": true|false,
 "text_items": ["дословные надписи, которые должны быть НА картинке"],
 "aspect": "square|portrait|story|poster|landscape|wide|auto"}

Оценки:
- people = true, если на исходных фото есть люди ИЛИ на результате должны быть люди (человек, лицо, портрет,
  пара, семья, толпа, силуэт человека).
- text = true, если на результате должен быть читаемый текст: надпись, вывеска, подпись, постер/афиша/
  открытка/меню с текстом, логотип с буквами, даты.
- transparent = true, только если просят объект БЕЗ ФОНА (логотип, стикер, иконка, PNG с прозрачностью).
- text_items — дословно, на языке пользователя, только надписи, которые должны появиться НА картинке; иначе [].
- aspect — форма кадра: story (сторис/reels/вертикальное видео), poster (афиша/плакат/обложка),
  portrait, landscape, wide (баннер/шапка), square (аватар/иконка); не ясно → "auto".

"prompt" — короткая версия (движок Qwen): только английский, начинай с глагола действия
(Replace / Remove / Add / Change / Take ... and place ...), ровно одно смысловое изменение,
1-4 предложения, плотно, без воды.

"sections" — та же задача, разложенная по помеченным секциям: так требует гайд OpenAI GPT-Image-2.5
(scene, subject, details, constraints). Пиши по-английски, каждая секция 1-2 фразы, пустая строка —
если сказать нечего:
- task — что именно сделать. Для правки фото формулируй как "Change only <X>" — ровно одно изменение.
- subject — кто/что в кадре. Если исходных фото несколько, назови РОЛЬ каждого:
  "image 1 — the woman (subject)", "image 2 — the street (background)", "image 3 — the jacket (clothing)";
  поза, кадрирование, куда смотрит.
- composition — расположение в кадре, план, что где стоит, сколько воздуха.
- style — материалы, свет, цвет, носитель; фото-термины (photorealistic photo, genuine camera shot,
  natural lighting, sharp focus, 50mm). НИКОГДА illustration / artistic / painterly, если не просили рисунок.
  Не полагайся на слова о настроении — описывай кадр и фактуру.
- text — надписи ДОСЛОВНО в двойных кавычках на языке пользователя (не переводи и не транслитерируй),
  где стоят и каким шрифтом. Если текста быть не должно — "No text anywhere."
- preserve — что обязано остаться неизменным. Для правки реальных фото обязательно: лица, личность,
  выражение, причёска, одежда, поза, геометрия кадра, свет.
- avoid — чего быть не должно (лишние надписи, водяные знаки, логотипы, подписи, рамки).

Общее: сохраняй суть запроса пользователя целиком, ничего не теряй; не выдумывай деталей, которых не просили."""

# ── Помеченные секции → промпт для GPT-Image-2.5 (гайд: scene, subject, details, constraints) ──
SEC_ORDER = (("task", "Task"), ("subject", "Subject"), ("composition", "Composition"),
             ("style", "Style and rendering"), ("text", "Text"), ("preserve", "Preserve exactly"),
             ("avoid", "Avoid"))
NO_TEXT = "No text, captions, watermarks, signatures or logos anywhere."

# Форма кадра: (enum Qwen, пиксели OpenAI)
ASPECTS = {"square": ("square_hd", (1024, 1024)), "portrait": ("portrait_4_3", (1024, 1280)),
           "story": ("portrait_16_9", (1088, 1920)), "poster": ("portrait_4_3", (1024, 1408)),
           "landscape": ("landscape_4_3", (1280, 1024)), "wide": ("landscape_16_9", (1920, 1088))}
ENUM_PX = {"square_hd": (1024, 1024), "square": (1024, 1024), "portrait_4_3": (1024, 1280),
           "portrait_16_9": (1088, 1920), "landscape_4_3": (1280, 1024), "landscape_16_9": (1920, 1088)}
ASPECT_RE = ((re.compile(r"сторис|стори|stories|reels|рилс|тикток|tiktok|shorts|шортс|вертикальн", re.I), "story"),
             (re.compile(r"постер|плакат|афиш|обложк", re.I), "poster"),
             (re.compile(r"баннер|banner|шапк|широкоформат", re.I), "wide"),
             (re.compile(r"портрет|вертикальн", re.I), "portrait"),
             (re.compile(r"пейзаж|панорам|горизонтальн", re.I), "landscape"),
             (re.compile(r"аватар|квадратн|square|иконк|стикер", re.I), "square"))
TRANSPARENT_RE = re.compile(r"прозрачн|без\s+фона|на\s+прозрачном|transparent|стикер|\bsticker", re.I)
BACKGROUNDS = ("auto", "transparent", "opaque")


def spell_out(items):
    """Гайд OpenAI 2.5: редкие слова и не-латиницу диктовать по буквам — иначе «Бабцукин» вместо «Бабушкин»."""
    out = []
    for it in list(items or [])[:4]:
        for w in re.findall(r"[^\W\d_]{2,}", str(it), re.U)[:6]:
            if any(ord(c) > 127 for c in w):
                out.append("%s = %s" % (w, "-".join(w.upper())))
    return out[:8]


def oa_prompt(short, sections, text_items, editing, want_text):
    """Секции → промпт с метками. Нет секций (ручной --prompt, сбой JSON) → отдаём короткий как есть."""
    if not sections:
        return short + (" " + KEEP if editing and "unchanged" not in short.lower() else "")
    lines = []
    for key, label in SEC_ORDER:
        v = str(sections.get(key) or "").strip()
        if key == "text":
            if want_text:
                if not v and text_items:
                    v = "Render exactly: " + ", ".join('"%s"' % t for t in text_items) + "."
                spelled = spell_out(text_items)
                if spelled:
                    v += " Spell each word letter by letter: " + "; ".join(spelled) + "."
                if v:
                    v += " Exact wording and spelling, legible; do not add any other text."
            else:
                v = NO_TEXT
        elif key == "preserve" and editing and "unchanged" not in v.lower():
            v = (v + " " if v else "") + KEEP
        if v:
            lines.append("%s: %s" % (label, v))
    if not lines:
        return short + (" " + KEEP if editing else "")
    if not lines[0].startswith("Task:"):
        lines.insert(0, "Task: " + short)
    return "\n".join(lines)


def pick_aspect(llm_aspect, raw):
    """Форма кадра: оценка модели → слова запроса → ничего (движок решает сам)."""
    a = str(llm_aspect or "").strip().lower()
    if a in ASPECTS:
        return a, "оценка модели: " + a
    for rx, name in ASPECT_RE:
        if rx.search(raw or ""):
            return name, "по запросу: " + name
    return "", ""


def _http_json(url, payload, headers, timeout=120):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _env(name):
    v = os.environ.get(name, "").strip()
    if v:
        return v
    home = os.environ.get("HERMES_HOME", "") or os.path.expanduser("~/.hermes")
    for envf in (os.path.join(home, ".env"), "/data/hermes/.env", "/root/.hermes/.env"):
        try:
            with open(envf, encoding="utf-8") as f:
                for line in f:
                    if line.startswith(name + "="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


def describe_images(urls):
    """Vision-описание исходников (OpenAI gpt-5.4 — та же aux-роль, что у бота).
    Фото описываются ПАРАЛЛЕЛЬНО: 3 снимка не должны складываться в 60 секунд ожидания."""
    key = _env("OPENAI_API_KEY")
    if not key:
        return []

    def one(item):
        n, u = item
        try:
            r = _http_json(
                "https://api.openai.com/v1/chat/completions",
                {"model": os.environ.get("QWEN_VISION_MODEL", "gpt-5.4"),
                 "messages": [{"role": "user", "content": [
                     {"type": "text", "text": "Опиши это фото для промпт-инженера: кто/что на нём "
                      "(люди, их число, лица, одежда, поза), фон, освещение, ракурс. 2-3 предложения, по-русски."},
                     {"type": "image_url", "image_url": {"url": u}}]}]},
                {"Authorization": "Bearer " + key}, timeout=90)
            return "image %d: %s" % (n, r["choices"][0]["message"]["content"].strip())
        except Exception as e:  # noqa: BLE001 — vision необязателен, промпт соберём и без него
            return "image %d: (описать не удалось: %s)" % (n, str(e)[:80])

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(one, enumerate(urls, 1)))


def research(query):
    """Факты из интернета для промпта (Perplexity sonar) — если агент попросил."""
    key = _env("PERPLEXITY_API_KEY")
    if not key or not query:
        return ""
    try:
        r = _http_json("https://api.perplexity.ai/chat/completions",
                       {"model": "sonar", "messages": [{"role": "user", "content":
                        "Кратко и фактически (3-5 предложений), как это выглядит: " + query}]},
                       {"Authorization": "Bearer " + key}, timeout=60)
        return r["choices"][0]["message"]["content"].strip()
    except Exception:  # noqa: BLE001
        return ""


def _parse_builder(txt):
    """JSON от DeepSeek → (короткий промпт, план). Не JSON — весь текст считаем промптом, плана нет."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip())
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            sec = d.get("sections") if isinstance(d.get("sections"), dict) else {}
            p = str(d.get("prompt", "")).strip()
            if not p and sec:   # модель дала только секции — короткий собираем из них
                p = " ".join(str(sec.get(k) or "") for k in ("task", "subject", "style")).strip()
            if p:
                return p, {"sections": {k: str(v or "") for k, v in sec.items()},
                           "people": bool(d.get("people")), "text": bool(d.get("text")),
                           "transparent": bool(d.get("transparent")),
                           "text_items": [str(x) for x in (d.get("text_items") or []) if str(x).strip()],
                           "aspect": str(d.get("aspect") or "")}
        except Exception:  # noqa: BLE001
            pass
    if len(t) > 1 and t[0] == t[-1] == '"' and t.count('"') == 2:
        t = t[1:-1]   # срезаем только обёртку целиком, не кавычку у текста в конце промпта
    return t, None


def build_prompt(raw, img_descr, facts, editing):
    """Промпт собирает текстовая модель (DeepSeek), она же даёт секции и оценки.
    → (короткий промпт, источник, план|None). План: sections/people/text/transparent/text_items/aspect."""
    key = _env("DEEPSEEK_API_KEY")
    parts = ["Запрос пользователя (дословно): " + raw]
    if img_descr:
        parts.append("Исходные фото:\n" + "\n".join(img_descr))
    if facts:
        parts.append("Факты из интернета:\n" + facts)
    parts.append("Режим: " + ("редактирование существующих фото" if editing else "генерация с нуля"))
    if not key:
        return (raw + (" " + KEEP if editing else ""), "raw-fallback", None)
    for model in (os.environ.get("QWEN_PROMPT_MODEL", "deepseek-v4-pro"), "deepseek-chat"):
        try:
            r = _http_json("https://api.deepseek.com/v1/chat/completions",
                           {"model": model, "temperature": 0.3,
                            "messages": [{"role": "system", "content": RULEBOOK},
                                         {"role": "user", "content": "\n\n".join(parts)}]},
                           {"Authorization": "Bearer " + key}, timeout=120)
            p, plan = _parse_builder(r["choices"][0]["message"]["content"])
            if not p:
                continue
            if editing and "unchanged" not in p.lower():
                p += " " + KEEP
            return (p, "auto:" + model, plan)
        except Exception:  # noqa: BLE001 — пробуем следующую модель
            continue
    return (raw + (" " + KEEP if editing else ""), "raw-fallback", None)



def _snap(w, h):
    """Лимиты GPT-Image-2.5: сторона кратна 16 и ≤3840, соотношение ≤3:1, 0.66–8.29 Мпикс.
    Кривой размер не роняем в 422, а подгоняем к ближайшему допустимому."""
    w, h = max(256, min(3840, int(w))), max(256, min(3840, int(h)))
    if w > h * 3:
        w = h * 3
    if h > w * 3:
        h = w * 3
    if w * h > 8294400:
        k = (8294400.0 / (w * h)) ** 0.5
        w, h = int(w * k), int(h * k)
    w, h = max(256, min(3840, w // 16 * 16)), max(256, min(3840, h // 16 * 16))
    for _ in range(200):
        if w * h >= 655360 or (w >= 3840 and h >= 3840):
            break
        if w <= h:
            w = min(3840, w + 16)
        else:
            h = min(3840, h + 16)
    return w, h


def _size_value(size, engine):
    """Qwen понимает enum, OpenAI — пиксели. Enum для OpenAI разворачиваем в проверенные ширину/высоту."""
    if not size:
        return None
    if size in SIZES:
        if engine != "openai":
            return size
        w, h = _snap(*ENUM_PX.get(size, (1024, 1024)))
        return {"width": w, "height": h}
    if "x" in size.lower():
        try:
            w, h = (int(v) for v in size.lower().split("x", 1))
            if engine == "openai":
                w, h = _snap(w, h)
            return {"width": w, "height": h}
        except ValueError:
            pass
    fail("--size: %s или ШИРИНАxВЫСОТА (числа), напр. 1024x1536" % "|".join(SIZES))


def _request(engine, a, prompt, img_urls, pro, quality, background, size):
    """→ (endpoint, body) для выбранного движка. Каждой схеме — только её поля (лишнее = 422)."""
    body = {"prompt": prompt}
    if img_urls:
        body["image_urls"] = img_urls
    if a.num != 1:
        body["num_images"] = max(1, min(4, a.num))
    fmt = a.fmt or ("png" if background == "transparent" else "")
    if fmt:
        body["output_format"] = fmt   # прозрачность живёт только в png
    sz = _size_value(size, engine)
    if sz:
        body["image_size"] = sz
    if engine == "openai":
        body["quality"] = quality
        if background and background != "auto":
            body["background"] = background
        return OA_EP[a.task], {k: v for k, v in body.items() if k in OA_FIELDS}
    return EP[(a.task, pro)], {k: v for k, v in body.items() if k in FIELDS}



def _call(ep, body, key, timeout):
    req = urllib.request.Request(
        BASE + ep, data=json.dumps(body).encode(),
        headers={"Authorization": "Key " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return None, "fal HTTP %s: %s" % (e.code, re.sub(r"https?://\S+", "<url>", e.read().decode()[:300]))
    except Exception as e:  # noqa: BLE001
        return None, "запрос не прошёл: %s" % e
    items = out.get("images") or []
    if not items or not items[0].get("url"):
        # ВАЖНО: CDN-ссылки fal наружу не отдаём — Telegram их не грузит. Вырезаем любые URL.
        return None, "в ответе fal нет картинок: " + re.sub(r"https?://\S+", "<url скрыт>", str(out))[:300]
    return items, ""


def main():
    t_start = time.time()
    ap = argparse.ArgumentParser(
        description="Картинки на fal: OpenAI GPT-Image-2.5 Sunburst (люди, текст) + Qwen-Image 2.0 (остальное)",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("task", choices=["generate", "edit"])
    ap.add_argument("--image", action="append", default=[],
                    help="исходное фото: абсолютный путь или URL. Повторяется (Qwen до 3, Sunburst до 16) — "
                         "слияние кадров / перенос объекта. Обязателен для edit")
    ap.add_argument("--prompt", help="готовая инструкция НА АНГЛИЙСКОМ (если промпт уже собран)")
    ap.add_argument("--raw", help="ПРЕДПОЧТИТЕЛЬНО: дословный запрос пользователя (можно по-русски). "
                                  "Скрипт сам посмотрит фото (vision), соберёт промпт, выберет движок "
                                  "и отправит всё вместе с фотографиями")
    ap.add_argument("--context", default="", help="доп. факты для промпта (что ты уже нашёл/знаешь)")
    ap.add_argument("--research", dest="research_q", default="",
                    help="что уточнить в интернете перед сборкой промпта (Perplexity)")
    ap.add_argument("--dry-run", action="store_true",
                    help="только собрать промпт и выбрать движок — показать, не рисовать")
    ap.add_argument("--engine", default="", choices=("",) + ENGINES,
                    help="перебить настройку бота: openai | qwen | auto. Только если пользователь прямо назвал модель")
    ap.add_argument("--pro", action="store_true", help="для движка qwen: pro-вариант Qwen 2.0")
    ap.add_argument("--quality", default="", choices=("",) + QUALITIES,
                    help="для движка openai. По умолчанию high, а если на картинке должен быть текст — xhigh")
    ap.add_argument("--background", default="", choices=("",) + BACKGROUNDS,
                    help="для движка openai: transparent (логотип/стикер, сам включит png) | opaque | auto. "
                         "По умолчанию скрипт решает сам по запросу")
    ap.add_argument("--size", default="",
                    help="|".join(SIZES) + " или ШИРИНАxВЫСОТА. По умолчанию скрипт подбирает форму кадра "
                         "по запросу (сторис/афиша/баннер/аватар); для openai размер подгоняется под лимиты")
    ap.add_argument("--num", type=int, default=1, help="число вариантов (1..4)")
    ap.add_argument("--format", dest="fmt", default="", choices=["", "png", "jpeg"])

    a = ap.parse_args()

    key = get_fal_key()
    if not key:
        fail("FAL_KEY не найден: ни в окружении, ни в .env профиля")
    if not a.prompt and not a.raw:
        fail("нужен --raw «дословный запрос пользователя» (скрипт сам соберёт промпт) "
             "или готовый --prompt на английском")
    if a.task == "edit" and not a.image:
        fail("edit требует хотя бы одно --image. Фото пользователя — исходник, "
             "а не вдохновение: генерировать заново вместо правки нельзя")
    if a.task == "generate" and a.image:
        fail("у generate не бывает входных фото. Раз фото есть — это задача edit")
    if len(a.image) > OA_MAX_IMAGES:
        fail("максимум %d фото на вход" % OA_MAX_IMAGES)
    if a.engine == "qwen" and len(a.image) > QWEN_MAX_IMAGES:
        fail("Qwen-Image 2.0 принимает максимум 3 фото — убери --engine qwen, Sunburst берёт до 16")

    img_urls = [to_url(i) for i in a.image]

    # ── конвейер промпта: фото → vision → (веб-факты) → промпт + оценки ──
    t_build = time.time()
    plan = None
    if a.prompt:
        short = a.prompt
        if img_urls and "unchanged" not in short.lower():
            short += " " + KEEP   # иначе модель подменяет людей (жалоба 14.07)
        src, descr = "manual", []
    else:
        descr = describe_images(img_urls) if img_urls else []
        facts = "\n".join(x for x in (a.context, research(a.research_q)) if x)
        short, src, plan = build_prompt(a.raw, descr, facts, bool(img_urls))
    build_s = round(time.time() - t_build, 1)

    route_flags = detect(a.raw, short, descr, plan)
    setting, setting_src = engine_setting()
    engine, why = choose_engine(a.engine, setting, len(img_urls), route_flags)

    # ── авто-параметры по гайду GPT-Image-2.5; любой флаг пользователя перебивает ──
    if a.quality:
        quality, q_why = a.quality, "флаг --quality"
    elif route_flags.get("text"):
        quality, q_why = "xhigh", "текст на картинке → xhigh"
    else:
        quality, q_why = "high", "по умолчанию"
    if a.background:
        background, bg_why = a.background, "флаг --background"
    elif (plan or {}).get("transparent") or TRANSPARENT_RE.search(a.raw or ""):
        background, bg_why = "transparent", "объект без фона → прозрачный png"
    else:
        background, bg_why = "auto", ""
    if a.size:
        size, size_why = a.size, "флаг --size"
    elif a.task == "edit":
        size, size_why = "", "правка фото — форму кадра не навязываем"
    else:
        asp, size_why = pick_aspect((plan or {}).get("aspect"), a.raw or "")
        size = ASPECTS[asp][0] if asp else ""

    # Один промпт на движок: Qwen — короткий, OpenAI — помеченные секции
    prompts = {"qwen": short,
               "openai": oa_prompt(short, (plan or {}).get("sections"), (plan or {}).get("text_items"),
                                   bool(img_urls), bool(route_flags.get("text")))}

    if a.dry_run:
        print(json.dumps({"engine": engine, "route": why, "setting": setting, "setting_file": setting_src,
                          "flags": route_flags, "flags_llm": plan, "prompt": prompts[engine],
                          "prompt_qwen": short, "prompt_source": src,
                          "params": {"quality": quality, "quality_why": q_why,
                                     "background": background, "background_why": bg_why,
                                     "size": size or "auto", "size_why": size_why},
                          "vision": descr, "build_s": build_s}, ensure_ascii=False))
        return

    # ── вызов: основной движок, при ошибке — второй (если успеваем в лимит терминала) ──
    order = [engine] + [e for e in ("openai", "qwen") if e != engine]
    attempts, items, ep, used, t0 = [], None, "", "", time.time()
    for i, eng in enumerate(order):
        left = DEADLINE_S - (time.time() - t_start)
        if i > 0 and (left < 35 or (eng == "qwen" and len(img_urls) > QWEN_MAX_IMAGES)):
            break
        for lean in (False, True):   # 422 на доп. параметрах → повтор без них, а не смена движка
            if lean and (quality == "high" and background == "auto" and not size):
                break
            ep, body = _request(eng, a, prompts[eng], img_urls, pro=(a.pro or i > 0),
                                quality=("high" if lean else quality),
                                background=("auto" if lean else background),
                                size=("" if lean else size))
            left = DEADLINE_S - (time.time() - t_start)
            if left < 20:
                break
            t0 = time.time()
            items, err = _call(ep, body, key, timeout=max(20, min(140 if eng == "openai" else 90, left - 5)))
            if items:
                used = eng
                break
            attempts.append({"engine": eng, "endpoint": ep, "error": err,
                             "s": round(time.time() - t0, 1), "lean": lean})
            if "HTTP 422" not in err:
                break
        if items:
            break

    if not items:
        fail("картинка не получилась ни одним движком", attempts=attempts)

    home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    dstdir = os.path.join(home, "cache", "images")
    os.makedirs(dstdir, exist_ok=True)
    tag = "sunburst" if used == "openai" else "qwen"
    paths = []
    for n, it in enumerate(items):
        url = it["url"]
        ext = ".png" if ".png" in url.split("?")[0].lower() else ".jpg"
        dst = os.path.join(dstdir, "%s_%s_%d%s%s" % (
            tag, a.task, int(time.time()), "" if n == 0 else "_%d" % n, ext))
        urllib.request.urlretrieve(url, dst)
        paths.append(dst)

    res = {
        "image": paths[0],
        "telegram": "\n".join("MEDIA:" + p for p in paths),   # именно \n: через пробел гейтвей склеит пути и не отправит ничего
        "note": "вставь строку из поля telegram в ответ КАК ЕСТЬ; ссылки fal.media Telegram не грузит",
        "engine": "OpenAI GPT-Image-2.5 Sunburst" if used == "openai" else "Qwen-Image 2.0" + (" Pro" if "/pro/" in ep else ""),
        "route": why,
        "endpoint": ep,
        "inputs": len(a.image),
        "prompt_used": prompts[used],
        "prompt_source": src,
        "params": {"quality": quality, "background": background, "size": size or "auto"}
                  if used == "openai" else {},
        "build_s": build_s,
        "elapsed_s": round(time.time() - t0, 1),
    }
    if attempts:
        res["fallback"] = attempts
    if len(paths) > 1:
        res["images"] = paths
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
