# 任务文档一：基础数据层与本地状态

## 1. 负责人范围

你负责系统的底层数据与文件能力，目标是让其他两位开发者可以稳定地读写记忆、人格文件和对话历史。

负责组件：

- Memory Store / Database Layer
- Persona File Manager
- Conversation Store

主要目录：

```text
src/memory/
src/persona/
src/conversation/
data/
tests/
docs/
```

不负责：

- 不负责调用模型。
- 不负责判断记忆是否相关。
- 不负责生成用户回复。
- 不负责 API 路由编排。
- 不负责 Memory Curator 的抽取逻辑。

---
~
## 2. 全员统一约定

### 2.1 运行环境

如无特殊说明，使用项目约定环境：

```bash
conda activate toy
```

Python 版本和依赖管理方式在项目初始化后统一写入 `README.md`。在没有进一步约定前，所有代码应尽量使用 Python 标准库完成 MVP。

### 2.2 推荐目录结构

三个人均按以下目录协作，不要随意新增同级核心目录：

```text
project-root/
  README.md
  docs/
    architecture.md
    database.md
    api_contracts.md
    prompt_placeholders.md
    tasks/
      person-1-foundation.md
      person-2-dialogue-retrieval.md
      person-3-orchestration-curator.md
  config/
    app.yaml
  data/
    Model.md
    User.md
  prompts/
    dialogue_agent.md
    memory_retrieval_workflow.md
    memory_curator.md
    profile_consolidator.md
  src/
    main.py
    api/
      routes.py
      schemas.py
    agents/
      dialogue_agent.py
      memory_retrieval_workflow.py
      memory_curator.py
      profile_consolidator.py
    coordinator/
      request_coordinator.py
      pending_queue.py
    memory/
      db.py
      models.py
      repository.py
      migrations.py
    persona/
      file_manager.py
    conversation/
      history_store.py
      compact_store.py
    services/
      dialogue_service.py
      memory_service.py
    utils/
      ids.py
      time.py
      json_utils.py
  tests/
```

统一关键路径：

```text
data/Model.md
data/User.md
prompts/dialogue_agent.md
prompts/memory_retrieval_workflow.md
prompts/memory_curator.md
prompts/profile_consolidator.md
```

### 2.3 数据库统一约定

Demo 阶段默认使用 SQLite，数据库文件建议放在：

```text
data/app.db
```

`memory_items` 表结构固定如下，字段名不要自行改动：

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

JSON 字段统一以字符串形式存入 SQLite：

```text
references_json
tags_json
metadata_json
conflict_with_json
```

模块返回给其他代码时，可以返回 Python `dict` / `list`，但写入数据库前必须序列化为合法 JSON 字符串。

### 2.4 ID 与时间统一约定

`request_id` 与 `turn_id` 由 Request Coordinator 生成，其他模块只接收和透传，不自行生成。

建议格式：

```text
request_id = req_<uuid4_hex>
turn_id = turn_<uuid4_hex>
```

时间字段统一使用 ISO 8601 字符串，建议 UTC：

```text
2026-05-12T11:30:00Z
```

### 2.5 跨模块接口统一约定

所有模块之间通过公开函数传递 `dict` / `list[dict]`。MVP 阶段先不强制引入复杂框架。

错误处理统一返回可读异常，测试中应覆盖主要失败场景。不要静默吞掉数据库、文件读写、JSON 解析错误。

### 2.6 Prompt 文件统一约定

Prompt 文件本轮只建空文件或占位，不写具体 Prompt：

```text
prompts/dialogue_agent.md
prompts/memory_retrieval_workflow.md
prompts/memory_curator.md
prompts/profile_consolidator.md
```

### 2.7 Git 与协作约定

建议分支：

```text
feature/foundation-state
```

你主要修改自己的负责目录。若必须修改公共 schema 或接口文档，先同步另外两位开发者。

---

## 3. 你的任务一：Memory Store / Database Layer

### 3.1 目标

实现 `memory_items` 表、数据库初始化、基础 CRUD、轻量检索字段读取，以及 Memory Curator 所需的批量更新操作。

### 3.2 需要实现的文件

```text
src/memory/db.py
src/memory/models.py
src/memory/repository.py
src/memory/migrations.py
tests/test_memory_repository.py
docs/database.md
```

### 3.3 建议职责划分

`src/memory/db.py`：

- 提供 SQLite 连接。
- 管理数据库路径。
- 提供事务上下文。

`src/memory/migrations.py`：

- 创建 `memory_items` 表。
- 可重复执行，表已存在时不报错。

`src/memory/models.py`：

- 定义 MemoryItem 字段常量或轻量数据结构。
- 定义允许的 `memory_type`、`status`、`sensitivity` 值。

`src/memory/repository.py`：

- 对外提供 Memory Store API。
- 封装 SQL，不让其他模块直接写 SQL。

### 3.4 必须实现的接口

```python
def init_db() -> None:
    pass


def create_memory_item(payload: dict) -> dict:
    pass


def update_memory_item(memory_id: int, payload: dict) -> dict:
    pass


def list_lightweight_memory_items(status: str = "active") -> list[dict]:
    pass


def get_memory_items_by_ids(ids: list[int]) -> list[dict]:
    pass


def apply_memory_operations(operations: list[dict]) -> list[dict]:
    pass
```

### 3.5 轻量字段返回格式

`list_lightweight_memory_items()` 只返回：

```json
{
  "id": 1,
  "summary": "string",
  "tags_json": [],
  "memory_type": "event",
  "references_json": [],
  "created_at": "datetime",
  "importance": 0.5
}
```

### 3.6 完整字段返回格式

`get_memory_items_by_ids()` 返回完整字段：

```json
{
  "id": 1,
  "summary": "string",
  "content": "string",
  "memory_type": "string",
  "references_json": [],
  "tags_json": [],
  "metadata_json": {},
  "source": "conversation",
  "confidence": 0.8,
  "importance": 0.5,
  "sensitivity": "normal",
  "status": "active",
  "superseded_by": null,
  "conflict_with_json": [],
  "created_at": "datetime",
  "updated_at": "datetime",
  "valid_from": null,
  "valid_until": null,
  "last_accessed_at": null,
  "last_verified_at": null
}
```

### 3.7 记忆操作要求

`apply_memory_operations()` 至少支持：

```text
create
update
archive
link
conflict_mark
merge
```

MVP 行为建议：

- `create`：插入一条新记忆。
- `update`：更新目标记忆字段，并刷新 `updated_at`。
- `archive`：将 `status` 改为 `archived`。
- `link`：把相关 ID 写入 `references_json`。
- `conflict_mark`：把冲突 ID 写入 `conflict_with_json`。
- `merge`：创建或更新主记忆，并将被合并项标记为 `deprecated`，设置 `superseded_by`。

---

## 4. 你的任务二：Persona File Manager

### 4.1 目标

统一读取 `Model.md` 和 `User.md`，并支持安全更新 `User.md`。

### 4.2 需要实现的文件

```text
src/persona/file_manager.py
tests/test_persona_file_manager.py
data/Model.md
data/User.md
docs/prompt_placeholders.md
```

### 4.3 必须实现的接口

```python
def read_model_profile() -> str:
    pass


def read_user_profile() -> str:
    pass


def write_user_profile(content: str) -> None:
    pass


def apply_user_profile_patch(patch: dict) -> str:
    pass
```

### 4.4 文件内容约定

`data/Model.md` 和 `data/User.md` 初始可以留空，但文件必须存在。

`User.md` 只保存长期稳定画像，不保存每条临时事件。判断是否应该写入 `User.md` 是 Person 3 的 Profile Consolidator 职责，你这里只负责读写和 patch 应用。

### 4.5 并发写入要求

MVP 可以先用简单文件锁或原子写策略，避免两个流程同时写坏 `User.md`。

建议：

- 写入前读取当前内容。
- 生成新内容后写入临时文件。
- 再替换原文件。

---

## 5. 你的任务三：Conversation Store

### 5.1 目标

保存 recent history 和 compact history，为 Dialogue Agent、Memory Retrieval Workflow、Memory Curator 提供对话上下文。

### 5.2 需要实现的文件

```text
src/conversation/history_store.py
src/conversation/compact_store.py
tests/test_conversation_store.py
```

### 5.3 必须实现的接口

```python
def append_turn(conversation_id: str, turn: dict) -> str:
    pass


def get_recent_history(conversation_id: str, limit: int = 20) -> list[dict]:
    pass


def get_compact_history(conversation_id: str) -> str:
    pass


def update_compact_history(conversation_id: str, compact: str) -> None:
    pass
```

### 5.4 turn 格式

```json
{
  "turn_id": "turn_xxx",
  "role": "user",
  "content": "string",
  "created_at": "datetime"
}
```

`role` 允许：

```text
user
assistant
system
```

### 5.5 存储方式

MVP 可以使用 SQLite 表，也可以使用本地 JSON 文件。但必须对外保持接口不变。若使用 SQLite，建议和 Person 1 的数据库初始化一起管理。

---

## 6. 与其他人的对接点

### 6.1 给 Person 2 的能力

Person 2 会调用：

```python
read_model_profile()
read_user_profile()
get_recent_history()
get_compact_history()
list_lightweight_memory_items()
get_memory_items_by_ids()
```

### 6.2 给 Person 3 的能力

Person 3 会调用：

```python
append_turn()
get_recent_history()
get_compact_history()
update_compact_history()
apply_memory_operations()
write_user_profile()
apply_user_profile_patch()
```

---

## 7. 测试要求

至少覆盖：

- 数据库可初始化。
- `memory_items` 可创建、读取、更新、归档。
- JSON 字段读写后仍是合法结构。
- 轻量字段不返回 `content`。
- 按 ID 批量读取能保持稳定结果。
- `Model.md` / `User.md` 不存在时能初始化或给出清晰错误。
- recent history 能按时间顺序读取最近 N 轮。
- compact history 可更新并读取。

---

## 8. 完成标准

完成后，其他开发者应该可以不关心底层存储细节，只通过公开函数完成：

- 读取人格文件。
- 读取对话上下文。
- 读取轻量记忆。
- 按 ID 读取完整记忆。
- 写入 Memory Curator 生成的记忆操作。
