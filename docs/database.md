# 数据库与本地状态

Person 1 负责 demo 系统使用的本地持久化层：

- Memory Store / Database Layer
- Persona File Manager
- Conversation Store

默认 SQLite 数据库路径为：

```text
data/app.db
```

测试和本地工具可以通过 `APP_DB_PATH`、项目根目录的 `.env` 文件，或调用
`src.memory.db.set_database_path(path)` 覆盖数据库路径。

默认本地 `.env` 配置如下：

```text
APP_DB_PATH=data/app.db
APP_DATA_DIR=data
```

## 初始化

应用启动时调用一次：

```python
from src.memory.repository import init_db

init_db()
```

迁移逻辑是幂等的。重复执行不会删除已有数据，只会创建缺失的表和索引。

## `memory_items`

`memory_items` 表严格遵守项目共享 schema：

```sql
CREATE TABLE memory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    memory_type TEXT DEFAULT 'event',
    references_json TEXT DEFAULT '[]',
    tags_json TEXT DEFAULT '[]',
    metadata_json TEXT DEFAULT '{}',
    source TEXT DEFAULT 'conversation',
    confidence REAL DEFAULT 0.8,
    importance REAL DEFAULT 0.5,
    sensitivity TEXT DEFAULT 'normal',
    status TEXT DEFAULT 'active',
    superseded_by INTEGER,
    conflict_with_json TEXT DEFAULT '[]',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    valid_from DATETIME,
    valid_until DATETIME,
    last_accessed_at DATETIME,
    last_verified_at DATETIME
);
```

以下 JSON 字段在 SQLite 中以合法 JSON 字符串保存：

```text
references_json
tags_json
metadata_json
conflict_with_json
```

公开的 repository API 会把这些字段还原为 Python 值：

- `references_json`: `list`
- `tags_json`: `list`
- `metadata_json`: `dict`
- `conflict_with_json`: `list`

## Memory Store API

```python
from src.memory.repository import (
    init_db,
    create_memory_item,
    update_memory_item,
    list_lightweight_memory_items,
    get_memory_items_by_ids,
    apply_memory_operations,
)
```

`create_memory_item(payload)` 至少需要：

```json
{
  "summary": "short retrievable summary",
  "content": "full memory content"
}
```

其他字段会使用共享 schema 中的默认值。repository 写入前会校验
`memory_type`、`status`、`sensitivity` 和所有 JSON 字段。

`list_lightweight_memory_items(status="active")` 只返回：

```text
id
summary
tags_json
memory_type
references_json
created_at
importance
```

它不会返回 `content`，避免轻量检索阶段读取完整记忆正文。

`get_memory_items_by_ids(ids)` 会按请求 ID 的顺序返回完整 MemoryItem 字典。

`apply_memory_operations(operations)` 支持：

```text
create
update
archive
link
conflict_mark
merge
```

操作格式：

```json
{
  "operation": "update",
  "target_id": 1,
  "payload": {
    "summary": "new summary"
  }
}
```

MVP 阶段行为：

- `create`: 将 `payload` 插入为一条新的 MemoryItem。
- `update`: 用 `payload` 更新 `target_id`，并刷新 `updated_at`。
- `archive`: 将 `status` 设置为 `archived`。
- `link`: 将相关 ID 追加到 `references_json`。
- `conflict_mark`: 将冲突 ID 追加到 `conflict_with_json`。
- `merge`: 更新主记忆，并将被合并的 source IDs 标记为 `deprecated`，
  同时把它们的 `superseded_by` 设置为主记忆 ID。

## Conversation Tables

Person 1 也会在同一个 SQLite 数据库中初始化两个对话相关表。

`conversation_turns` 保存 recent history：

```text
conversation_id
turn_id
role
content
created_at
metadata_json
```

使用方式：

```python
from src.conversation.history_store import append_turn, get_recent_history
```

`append_turn(conversation_id, turn)` 要求调用方提供 `turn_id`。
`request_id` 和 `turn_id` 由 Request Coordinator 生成，不由 Person 1 模块生成。

`compact_histories` 为每个 conversation 保存一份 compact history 文本：

```python
from src.conversation.compact_store import (
    get_compact_history,
    update_compact_history,
)
```

如果某个 conversation 还没有 compact history，
`get_compact_history(conversation_id)` 会返回空字符串。

## Persona Files

Persona profile 不存入 SQLite，而是保存在文件中：

```text
data/Model.md
data/User.md
```

使用方式：

```python
from src.persona.file_manager import (
    read_model_profile,
    read_user_profile,
    write_user_profile,
    apply_user_profile_patch,
)
```

如果 profile 文件不存在，读取时会创建为空文件。`write_user_profile()` 会先写入临时文件，
再原子替换 `data/User.md`，避免写入中断导致文件损坏。
