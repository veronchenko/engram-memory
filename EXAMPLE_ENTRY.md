---
{id: 3f9c2e1a-7b44-4c3a-9e2d-1a2b3c4d5e6f, title: Example Entry — demonstrates the entry file format, tags: [snippet, engram_memory], type: snippet, resource: kb://3f9c2e1a-7b44-4c3a-9e2d-1a2b3c4d5e6f}
---

Пример содержимого записи (тело файла — обычный Markdown, пишется как угодно, но конвенция проекта — короткое summary в начале).

### Формат файла
- Имя файла: `<id>.md`, где `<id>` совпадает с `id` из frontmatter (UUID v4).
- Frontmatter — YAML в flow-style (`default_flow_style=True`), одна строка между `---`.
- Обязательные поля: `id`, `title`, `tags`, `type`.
- Опциональное поле (пишется только если непустое — `_write_entry` иначе его опускает): `resource`.

### Ограничения
- `type` обязателен при вызове `remember()` — без него записи не будет (`{"error": "entry_type is required"}`), и в файле присутствует всегда, безусловно.
- `id` должен быть строго lowercase UUID (`8-4-4-4-12` hex) — проверяется regex `_UUID_RE`, иначе путь считается небезопасным и отклоняется (защита от path traversal).
- `resource`, если указан, должен быть похож на URI (`"://"` в строке) или абсолютный путь (`/...`) — иначе `rebuild()` пометит его в `warnings.malformed_resource`.
- Одна запись = один факт/решение (атомарность, с v0.6.0) — не сваливать несколько несвязанных фактов в одно тело.

### Связи (graph relations)
Ссылки вида `[label](kb://<uuid>#<type>)` в теле — это рёбра графа. Без `#type` тип связи по умолчанию — `related`.

Пример: связано с [Engram — Persistent Knowledge Base MCP Server](kb://b39a46a0-65ea-4bda-a4d4-3ebe395bfccd#hub).

### Запись на диске
Пишется атомарно: сначала во временный `*.md.tmp`, затем `rename()` в целевой `<id>.md` — чтобы сбой записи (диск полон, нет прав) не оставил битый файл.
