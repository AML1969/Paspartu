# seed/skills-main — скиллы, раскатанные ТОЛЬКО на main-бота (Андрей)

⚠️ Эта папка **не входит** в `seed/skills.tar.gz` и не попадает в образы копий (bif).
Здесь лежат версии скиллов, которые по решению владельца раскатаны только на
main-бота `paspartu-andrew`, пока копии (Руслан, Игорь) остаются на прежней версии.

Цель папки — версионировать такие правки в читаемом виде: иначе единственный
экземпляр живёт на сервере и молча теряется при пересоздании контейнера.

| Скилл | Версия здесь | Версия в `seed/skills.tar.gz` (копии) |
|---|---|---|
| `qwen-image-edit` | 14.09.2026 — автопромптинг по гайду GPT-Image-2.5 (секции Task/Subject/Composition/Style/Text/Preserve/Avoid для Sunburst, спеллинг кириллицы, авто quality/background/size, lean-повтор при 422) | 11.09.2026 — маршрут Sunburst/Qwen без автопромптинга |
| `parcel-tracking` | 14.09.2026 — Nova Global переведён на прямой API (`personal.novaposhtaglobal.ua/tracking.php`), Playwright и команда его установки убраны | в сидах копий этого скилла нет |
| `mcp-first-check` | 14.09.2026 — верное имя `mcp__kiwi__search_flight`, инструкция Kiwi переписана под Docker (mcp 1.26.0 запечён в образ) | у копий имя тоже исправлено, но текст инструкции — сидовый, под образ bif |

**Если решите раскатать копиям:** скопировать отсюда в рабочее дерево копии,
перепаковать `seed/skills.tar.gz`, пересобрать образ — и удалить строку из таблицы выше.

**Живые пути main-бота:** скилл `/root/paspartu-andrew-data/hermes/skills/media/qwen-image-edit/`,
сид на хосте `/root/paspartu-2.0/seed/skills.tar.gz` (обновлён 14.09), сид в контейнере
`paspartu-andrew:/opt/hermes-seed/skills.tar.gz`. Откат: `/root/backups/image-prompting-20260914/`.
