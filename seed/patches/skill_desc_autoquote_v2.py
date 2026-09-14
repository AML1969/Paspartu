#!/usr/bin/env python3
"""Патч v2: авто-починка YAML-frontmatter в skill_manage (двоеточие в description).

Причина (найдено в аудите 16.08.2026): модель создаёт скилл с описанием вида
`description: ... photos accurately. Trigger: user sends a photo` — двоеточие
внутри plain-скаляра ломает YAML, skill_manage отбивает
"YAML frontmatter parse error: mapping values are not allowed here",
и попытка самообучения молча теряется. Тот же дефект уже лежал на диске:
skills/openclaw-imports/ai-marketing-skillbook/SKILL.md не парсился месяцами
и скилл просто не загружался.

Что делает: перед валидацией пытается нормализовать frontmatter — если YAML
не парсится, переписывает `description` в блочный скаляр `>-` (двоеточия внутри
блочного скаляра безопасны) и валидирует повторно. Если после починки YAML
валиден — используется исправленный текст. Если нет — поведение прежнее (ошибка).

Конвенции v2: путь через env HERMES_SITE_PACKAGES (приоритет) или pipx-glob;
маркер идемпотентности, abort если якорь не найден, timestamped .bak,
py_compile с автооткатом.
Маркер: "skill-desc-autoquote-patch".

Совместимость: якорь `    err = _validate_frontmatter(content)` присутствует
и в 0.16.0, и в 0.19.0 (проверено на обоих).
"""
import glob
import os
import py_compile
import shutil
import sys
import time

SP = os.environ.get("HERMES_SITE_PACKAGES", "")
if not SP:
    cands = glob.glob("/root/.local/share/pipx/venvs/hermes-agent/lib/python3*/site-packages")
    SP = cands[0] if cands else ""
if not SP or not os.path.isdir(SP):
    print("[skill_desc_autoquote] FATAL: site-packages не найден (HERMES_SITE_PACKAGES/pipx)")
    sys.exit(1)

TARGET = SP + "/tools/skill_manager_tool.py"
MARKER = "skill-desc-autoquote-patch"

HELPER = '''
def _autofix_frontmatter(content: str) -> str:
    """skill-desc-autoquote-patch.

    Если frontmatter не парсится YAML-ом, чаще всего виновато двоеточие внутри
    plain-скаляра `description:` ("... Trigger: user sends a photo").
    Переписываем description в блочный скаляр `>-` и проверяем ещё раз.
    Возвращаем исправленный текст только если он стал валидным; иначе — исходный
    (тогда сработает обычная валидация со своим сообщением об ошибке).
    """
    try:
        if not content.startswith("---"):
            return content
        end_match = re.search(r'\\n---\\s*\\n', content[3:])
        if not end_match:
            return content
        fm = content[3:end_match.start() + 3]
        rest = content[end_match.start() + 3:]
        try:
            parsed = yaml.safe_load(fm)
            if isinstance(parsed, dict):
                return content          # уже валиден — не трогаем
        except yaml.YAMLError:
            pass

        m = re.search(r'^description:[ \\t]*([\\s\\S]+?)(?=^[A-Za-z_][\\w-]*:|\\Z)', fm, re.M)
        if not m:
            return content
        desc = " ".join(m.group(1).split())
        if not desc:
            return content
        block = "description: >-\\n" + "".join(
            "  " + desc[i:i + 4000] + "\\n" for i in range(0, len(desc), 4000)
        )
        fixed_fm = fm[:m.start()] + block + fm[m.end():]
        candidate = "---" + fixed_fm + rest
        try:
            reparsed = yaml.safe_load(candidate[3:re.search(r'\\n---\\s*\\n', candidate[3:]).start() + 3])
        except Exception:
            return content
        if isinstance(reparsed, dict) and "description" in reparsed:
            return candidate
        return content
    except Exception:
        return content

'''

OLD = "    err = _validate_frontmatter(content)\n"
NEW = ("    content = _autofix_frontmatter(content)  # skill-desc-autoquote-patch\n"
       "    err = _validate_frontmatter(content)\n")

ANCHOR_DEF = "def _validate_frontmatter(content: str) -> Optional[str]:"


def main() -> int:
    if not os.path.isfile(TARGET):
        print("[skill_desc_autoquote] FATAL: нет %s" % TARGET)
        return 1
    src = open(TARGET, encoding="utf-8").read()
    if MARKER in src:
        print("[skill_desc_autoquote] already applied")
        return 0

    if src.count(ANCHOR_DEF) != 1:
        print("[skill_desc_autoquote] FATAL: якорь-определение найден %d раз" % src.count(ANCHOR_DEF))
        return 1
    n_calls = src.count(OLD)
    if n_calls < 1:
        print("[skill_desc_autoquote] FATAL: якорь-вызов не найден")
        return 1

    new_src = src.replace(ANCHOR_DEF, HELPER.lstrip("\n") + "\n" + ANCHOR_DEF, 1)
    new_src = new_src.replace(OLD, NEW)

    bak = TARGET + ".bak-descquote-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(TARGET, bak)
    open(TARGET, "w", encoding="utf-8").write(new_src)
    try:
        py_compile.compile(TARGET, doraise=True)
    except Exception as exc:
        shutil.copy2(bak, TARGET)
        print("[skill_desc_autoquote] FATAL: compile failed, rolled back: %s" % exc)
        return 1
    print("[skill_desc_autoquote] applied (call sites patched: %d)" % n_calls)
    return 0


if __name__ == "__main__":
    sys.exit(main())
