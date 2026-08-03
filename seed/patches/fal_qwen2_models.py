#!/usr/bin/env python3
"""Патч: добавляет Qwen-Image 2.0 в каталог FAL_MODELS штатного тула image_generate.

Зачем: tools/image_generation_tool.py валидирует model_id по словарю FAL_MODELS и
при незнакомом id МОЛЧА откатывается на DEFAULT_MODEL (flux) — то есть выставить
`image_gen.model: fal-ai/qwen-image-2/text-to-image` в конфиге без этого патча
бесполезно: бот будет рисовать флаксом и делать вид, что всё хорошо.

Схема эндпоинтов 2.0 (снята с fal openapi 14.07.2026) знает РОВНО четыре поля:
prompt, image_size, num_images, output_format. Ничего больше слать нельзя (422).

Идемпотентный, self-backup, py_compile с автооткатом. Маркер: qwen2-catalog-patch.
Слетает при `hermes update` — перезапустить (он в манифесте patches.txt).
"""
import py_compile
import shutil
import sys

TOOL = ("/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages/"
        "tools/image_generation_tool.py")
MARKER = "qwen2-catalog-patch"

ANCHOR = '    "fal-ai/qwen-image": {\n'

NEW = '''    # qwen2-catalog-patch: Qwen-Image 2.0 (генерация). Схема эндпоинта знает только
    # prompt/image_size/num_images/output_format — остальные поля fal вернёт 422.
    "fal-ai/qwen-image-2/text-to-image": {
        "display": "Qwen Image 2",
        "speed": "~10s",
        "strengths": "Qwen 2.0: текст на картинке, фотореализм, сложные сцены",
        "price": "$0.02/MP",
        "size_style": "image_size_preset",
        "sizes": {
            "landscape": "landscape_16_9",
            "square": "square_hd",
            "portrait": "portrait_16_9",
        },
        "defaults": {
            "num_images": 1,
            "output_format": "png",
        },
        "supports": {"prompt", "image_size", "num_images", "output_format"},
        "upscale": False,
    },
    "fal-ai/qwen-image-2/pro/text-to-image": {
        "display": "Qwen Image 2 Pro",
        "speed": "~20s",
        "strengths": "Qwen 2.0 Pro: максимальное качество и детализация",
        "price": "$0.05/MP",
        "size_style": "image_size_preset",
        "sizes": {
            "landscape": "landscape_16_9",
            "square": "square_hd",
            "portrait": "portrait_16_9",
        },
        "defaults": {
            "num_images": 1,
            "output_format": "png",
        },
        "supports": {"prompt", "image_size", "num_images", "output_format"},
        "upscale": False,
    },
'''


def main():
    src = open(TOOL, encoding="utf-8").read()
    if MARKER in src:
        print("already applied — no-op")
        return
    if ANCHOR not in src:
        print("ERROR: якорь FAL_MODELS['fal-ai/qwen-image'] не найден — версия Hermes сменилась?")
        sys.exit(1)
    bak = TOOL + ".bak-qwen2"
    shutil.copy2(TOOL, bak)
    open(TOOL, "w", encoding="utf-8").write(src.replace(ANCHOR, NEW + ANCHOR, 1))
    try:
        py_compile.compile(TOOL, doraise=True)
    except Exception as e:
        shutil.copy2(bak, TOOL)
        print("COMPILE FAILED, reverted:", e)
        sys.exit(1)
    # sanity: ключи реально попали именно в словарь FAL_MODELS (разбираем AST —
    # импортировать модуль нельзя: он живёт в пакете `tools` внутри venv Hermes)
    import ast
    try:
        tree = ast.parse(open(TOOL, encoding="utf-8").read())
        keys = []
        for node in ast.walk(tree):
            targets = getattr(node, "targets", []) or ([node.target] if hasattr(node, "target") else [])
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if "FAL_MODELS" in names and isinstance(node.value, ast.Dict):
                keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
        assert "fal-ai/qwen-image-2/text-to-image" in keys, "модели нет в FAL_MODELS"
        assert "fal-ai/qwen-image-2/pro/text-to-image" in keys, "pro-модели нет в FAL_MODELS"
    except Exception as e:
        shutil.copy2(bak, TOOL)
        print("SANITY FAILED, reverted:", e)
        sys.exit(1)
    print("patched OK (Qwen-Image 2.0 + 2.0 Pro в каталоге), backup:", bak)


if __name__ == "__main__":
    main()
