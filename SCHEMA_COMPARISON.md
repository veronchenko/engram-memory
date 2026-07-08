# Старая vs новая схема записи — пример и сравнение поиска

Ниже — реальный вывод, полученный прогоном кода (`database.py` + `SQLiteBackend`) с двумя записями об одном и том же инциденте: одна в старом формате (только `id/title/tags`), другая в новом (`type`/`resource`).

Изначально в схему добавлялось и поле `description`, но оно оказалось избыточным: правило атомарности Engram (v0.6.0) и так требует, чтобы `content` был одним предложением-решением, поэтому `description` почти всегда дублировал бы то же самое, что уже видно в auto-generated `snippet` (первые 200 символов `content`). Убрано из схемы — итоговая версия ниже содержит только `type` и `resource`.

## Документ 1 — старый формат

Тип записи закодирован неявно, первым тегом (`diagnostic`).

```markdown
---
{id: daa98cbc-682e-4f2f-8d3d-6710b22ebe84, title: PostgreSQL connection pool exhausted
    under load, tags: [diagnostic, engram_memory, postgresql]}
---

Root cause: pgbouncer pool_size was set to 20 while the app spun up 50 workers. Fix: raised pool_size to 60 and added PgBouncer transaction pooling mode.
```

`recall`/`get()` возвращает:

```json
{
  "id": "daa98cbc-682e-4f2f-8d3d-6710b22ebe84",
  "title": "PostgreSQL connection pool exhausted under load",
  "tags": ["diagnostic", "engram_memory", "postgresql"],
  "type": "",
  "resource": "",
  "content": "Root cause: pgbouncer pool_size was set to 20 while the app spun up 50 workers. Fix: raised pool_size to 60 and added PgBouncer transaction pooling mode."
}
```

`type`/`resource` пустые — код читает их с дефолтом `""` (обратная совместимость), но по факту тип нужно доставать парсингом `tags[0]`.

## Документ 2 — новый формат

Тот же факт, но с явным `type` и `resource`.

```markdown
---
{id: 11f8c804-3d6e-4561-86f9-92f3b0b56f1b, title: pgbouncer pool exhaustion under
    load, tags: [engram_memory, postgresql], type: diagnostic, resource: 'https://github.com/org/infra/blob/main/pgbouncer.ini'}
---

Raised pgbouncer pool_size from 20 to 60 and enabled transaction pooling mode to handle 50 concurrent app workers.
```

`recall`/`get()` возвращает:

```json
{
  "id": "11f8c804-3d6e-4561-86f9-92f3b0b56f1b",
  "title": "pgbouncer pool exhaustion under load",
  "tags": ["engram_memory", "postgresql"],
  "type": "diagnostic",
  "resource": "https://github.com/org/infra/blob/main/pgbouncer.ini",
  "content": "Raised pgbouncer pool_size from 20 to 60 and enabled transaction pooling mode to handle 50 concurrent app workers."
}
```

`tags` теперь чисто топикальные (без служебного первого тега), `type` явный и фильтруемый, `resource` указывает на конкретный конфиг-файл.

## Сравнение результатов поиска

### `search("pool")` — до/после в одном ответе

```json
[
  {
    "id": "daa98cbc-682e-4f2f-8d3d-6710b22ebe84",
    "title": "PostgreSQL connection pool exhausted under load",
    "tags": ["diagnostic", "engram_memory", "postgresql"],
    "type": "",
    "snippet": "Root cause: pgbouncer pool_size was set to 20 while the app spun up 50 workers. Fix: raised pool_size to 60 and added PgBouncer transaction pooling mode.",
    "score": 0
  },
  {
    "id": "11f8c804-3d6e-4561-86f9-92f3b0b56f1b",
    "title": "pgbouncer pool exhaustion under load",
    "tags": ["engram_memory", "postgresql"],
    "type": "diagnostic",
    "snippet": "Raised pgbouncer pool_size from 20 to 60 and enabled transaction pooling mode to handle 50 concurrent app workers.",
    "score": 0
  }
]
```

**Разница:** у старой записи `type` пустой — клиенту нужно парсить `tags[0]`, чтобы узнать тип. У новой — `type: "diagnostic"` сразу доступен для фильтрации (`type:` в Xapian), без парсинга тегов. `snippet` в обоих случаях одинаково хорош, потому что `content` и так короткий по правилу атомарности — отдельное поле-превью тут не нужно.

### `list_entries()`

```json
[
  {
    "id": "11f8c804-3d6e-4561-86f9-92f3b0b56f1b",
    "title": "pgbouncer pool exhaustion under load",
    "tags": ["engram_memory", "postgresql"],
    "type": "diagnostic"
  },
  {
    "id": "daa98cbc-682e-4f2f-8d3d-6710b22ebe84",
    "title": "PostgreSQL connection pool exhausted under load",
    "tags": ["diagnostic", "engram_memory", "postgresql"],
    "type": ""
  }
]
```

### `rebuild()` — conformance-проверка

```json
{
  "count": 2,
  "warnings": {
    "missing_type": ["daa98cbc-682e-4f2f-8d3d-6710b22ebe84"],
    "malformed_resource": []
  }
}
```

`rebuild` явно указывает, какие записи (по id) не заполнили `type` — старая запись помечена, новая — чистая.

## Итог

- Обратная совместимость подтверждена: старая запись читается, ищется, листается без ошибок с пустыми значениями по умолчанию.
- Новая запись даёт структурированный `type`/`resource` во всех операциях чтения (`recall`, `search`, `list`), без дублирования того, что уже даёт `snippet`.
- `rebuild` даёт видимость того, сколько записей в базе ещё не проставили `type`.
