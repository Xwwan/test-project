# 对话系统记忆与多 Agent 协同开发总设计文档

## 1. 文档目的

本文档用于定义一个带长期记忆能力的对话系统的开发方案。系统目标是在不影响即时对话体验的前提下，通过结构化记忆表、用户人格文件、模型人格文件和多 Agent / Workflow 协同机制，为模型提供可检索、可更新、可追溯的用户长期上下文。

本文档面向工程开发与任务下发，重点说明：
1. 项目总体架构。
2. 数据表与文件设计。
3. 一次用户请求的完整流程。
4. 异步记忆检索与二次回复机制。
5. 记忆更新机制。
6. 可并行开发的组件划分。
7. 各组件输入、输出、职责边界和合并约定。

本文档暂不设计具体 Prompt 内容。以下内容均保留为空，由后续单独设计：

```text
Model.md
Dialogue Agent Prompt
Memory Retrieval Workflow Prompt
Memory Curator Prompt
```

---

## 2. 当前采用的设计决策

根据当前讨论，系统采用以下设计：

### 2.1 记忆项类型设计

采用更宽泛的记忆项概念，不再把所有内容都简单称为“事件”。系统中的一条长期记忆记录统一称为：

```text
MemoryItem
```

记忆项通过 `memory_type` 字段区分不同类型，例如：

```text
fact
preference
task
event
constraint
profile_update
```

不同类型示例：

|类型|说明|示例|
|---|---|---|
|`fact`|用户明确说过的事实|用户正在写某篇论文。|
|`preference`|用户偏好|用户喜欢复制即用的代码。|
|`task`|用户正在推进的任务|用户正在调试本地模型服务。|
|`event`|用户经历过的事情|用户曾部署过 Minecraft 服务端。|
|`constraint`|后续回答需要遵守的约束|用户希望回答使用中文。|
|`profile_update`|对 User.md 有潜在更新价值的信息|用户长期研究方向发生变化。|

---

### 2.2 SQL Table 设计

采用正式的 `memory_items` 表结构。字段包括：

```text
id
summary
content
memory_type
references_json
tags_json
metadata_json
source
confidence
importance
sensitivity
status
superseded_by
conflict_with_json
created_at
updated_at
valid_from
valid_until
last_accessed_at
last_verified_at
```

其中：

- 原 `index` 改为 `id`。
- 原 `descript` 改为 `summary`。
- 原 `context` 改为 `content`。
- 原 `reference` 改为 `references_json`。
- 原 `other` 改为 `metadata_json`。

---

### 2.3 时间字段设计

采用时间字段，用于判断记忆是否过期、何时创建、何时更新、何时验证。

至少需要支持：

```text
created_at
updated_at
valid_from
valid_until
last_verified_at
```

其中 `created_at` 和 `updated_at` 为 MVP 必须字段，其他字段可以先保留但不一定在 demo 中充分使用。

---

### 2.4 冲突处理机制

采用冲突、归档、替代机制。记忆更新时不应简单覆盖旧内容，而应支持以下操作：

```text
create
update
archive
link
conflict_mark
merge
```

相关字段：

```text
status
superseded_by
conflict_with_json
references_json
```

状态建议包括：

```text
active
archived
deprecated
deleted
```

---

### 2.5 检索字段设计

demo 阶段暂定使用以下轻量字段交给模型判断相关性：

```text
id
summary
tags_json
memory_type
references_json
created_at
importance
```

检索策略暂时采用：

```text
由模型直接判断哪些 MemoryItem 与当前 query 相关
```

暂不采用加权分数策略，例如：

```text
final_score = similarity_score + tag_score + recency_score + importance_score
```

但系统需要预留后续扩展为 chain / workflow / weighted retrieval 的能力。

也就是说，demo 阶段的 Memory Retrieval Workflow 可以很简单；但接口设计不能写死成只能由模型一次判断完成。

---

### 2.6 二次回复机制

采用 Agent A 的二次回复机制。

一次用户 query 可能产生：

1. 第一条即时回复。
2. 记忆检索完成后的可选补充回复。

是否进行第二次回复由 Dialogue Agent 判断。

二次回复的判断依据包括：

```text
检索结果是否与原 query 高度相关
检索结果是否会改变初始回答
是否涉及健康、用药、法律、财务等高风险领域
用户是否仍在相关话题
初始回复是否承诺稍后补充
检索结果是否只是弱相关闲聊信息
```

二次回复类型包括：

```text
补充型
修正型
不回复型
```

---

### 2.7 异步请求绑定机制

采用 `request_id` / `turn_id` 绑定机制。

每一次用户输入都需要生成唯一请求标识，用于绑定：

```text
当前用户 query
Dialogue Agent 的即时回复
Memory Retrieval Workflow 的检索结果
Dialogue Agent 的二次回复决策
```

避免出现以下问题：

1. 用户已经切换话题，但系统补充了旧话题。
2. 多个并行检索任务返回后互相混淆。
3. Agent A 不知道 Agent B 的结果对应哪一次用户问题。

---

### 2.8 上下文拼接顺序

采用新的上下文层级顺序：

```text
System / Developer Instruction
Model.md
User.md
Compact Memory
Recent History
Retrieved Events
Current User Query
```

其中 `Retrieved Events` 只在二次回复或需要记忆增强时加入。

---

### 2.9 User.md 更新策略

采用 User.md 准入机制。

`User.md` 只保存长期稳定、对未来回答有持续价值的信息，不保存所有临时事件。

准入标准后续可以自定义。当前默认原则：

1. 用户明确要求记住。
2. 信息长期稳定。
3. 信息会显著改变未来回答方式。
4. 多次对话中反复出现。
5. 属于用户长期目标、长期偏好或长期约束。

---

### 2.10 敏感信息策略

采用敏感信息标记与保守使用机制。

相关字段：

```text
sensitivity
metadata_json.consent_status
metadata_json.access_policy
metadata_json.retention_policy
```

特别是健康、用药、法律、金融等高风险领域，需要在后续 Agent Prompt 中加入更保守的使用策略。

---

### 2.11 Agent C 设计

采用 Agent C / Memory Curator 设计，并拆分为两个子流程：

```text
Conversation Memory Extractor
Profile Consolidator
```

其中：

- `Conversation Memory Extractor` 负责从对话中抽取记忆项并更新 `memory_items`。
- `Profile Consolidator` 负责定期从 `memory_items` 中总结长期稳定信息，并生成 `User.md` 更新建议。

---

### 2.12 暂不采用的设计

当前 demo 不采用以下复杂工程策略：

1. 加权检索排序公式。
2. 复杂记忆压缩策略。
3. 记忆使用日志。
4. 用户可视化管理记忆。
5. 高风险领域的完整专门策略实现。

但其中“加权检索排序 / chain / workflow”需要在接口层预留扩展空间。

---

## 3. 项目总体架构

### 3.1 核心模块

系统由以下核心模块组成：

```text
Dialogue Agent
Memory Retrieval Workflow
Memory Curator
Request Coordinator
Memory Store
Persona File Manager
Conversation Store
API / Service Layer
```

### 3.2 模块说明

|模块|职责|
|---|---|
|`Dialogue Agent`|面向用户生成即时回复，并在记忆检索完成后判断是否需要补充回复。|
|`Memory Retrieval Workflow`|使用轻量记忆字段检索相关 MemoryItem，并返回完整字段给 Dialogue Agent。|
|`Memory Curator`|从历史对话中抽取、更新、合并、归档记忆，并定期生成 User.md 更新建议。|
|`Request Coordinator`|管理每次用户请求的 request_id、异步检索状态、二次回复状态。|
|`Memory Store`|管理 SQL 表 `memory_items`。|
|`Persona File Manager`|管理 `Model.md` 与 `User.md` 的读取和更新。|
|`Conversation Store`|管理 recent history、compact history、turn 记录。|
|`API / Service Layer`|对外提供接口，协调各模块调用。|

---

## 4. 总体数据流

### 4.1 一次用户输入的处理流程

```text
User Query
   |
   v
Request Coordinator 创建 request_id / turn_id
   |
   |------------------------------|
   |                              |
   v                              v
Dialogue Agent                Memory Retrieval Workflow
生成即时回复                    检索相关 MemoryItem
   |                              |
   v                              v
返回第一条回复给用户              返回 retrieved_items
                                  |
                                  v
                         Request Coordinator 绑定结果
                                  |
                                  v
                         Dialogue Agent 判断是否二次回复
                                  |
                  |---------------|---------------|
                  |                               |
                  v                               v
           生成补充回复                    no_followup_needed
```

---

### 4.2 对话结束或阶段性触发的记忆更新流程

```text
Recent Conversation Turns
   |
   v
Memory Curator / Conversation Memory Extractor
   |
   v
生成 memory update operations
   |
   v
Memory Store 执行 create / update / archive / link / conflict_mark / merge
   |
   v
必要时生成 Profile Consolidator 输入
   |
   v
定期生成 User.md patch suggestion
```

---

## 5. 数据库设计

### 5.1 memory_items 表

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

### 5.2 字段说明

|字段|说明|
|---|---|
|`id`|主键，自增 ID。|
|`summary`|简短描述，用于轻量检索。|
|`content`|详细内容，用于完整读取和提供给 Dialogue Agent。|
|`memory_type`|记忆类型，例如 fact、preference、task、event、constraint。|
|`references_json`|引用到的其他 MemoryItem ID 列表。|
|`tags_json`|标签列表。|
|`metadata_json`|可扩展元信息。|
|`source`|来源，例如 conversation、manual_import、system_update。|
|`confidence`|置信度。|
|`importance`|重要程度。|
|`sensitivity`|敏感程度。|
|`status`|状态，例如 active、archived、deprecated、deleted。|
|`superseded_by`|被哪条新记忆替代。|
|`conflict_with_json`|与哪些记忆冲突。|
|`created_at`|创建时间。|
|`updated_at`|最近更新时间。|
|`valid_from`|记忆有效开始时间。|
|`valid_until`|记忆有效结束时间。|
|`last_accessed_at`|最近被访问时间。|
|`last_verified_at`|最近被确认时间。|

---

### 5.3 轻量检索字段

Memory Retrieval Workflow 在 demo 阶段只读取以下字段让模型判断相关性：

```text
id
summary
tags_json
memory_type
references_json
created_at
importance
```

选中相关记忆后，再通过 `id` 读取完整字段：

```text
id
summary
content
memory_type
references_json
tags_json
metadata_json
source
confidence
importance
sensitivity
status
superseded_by
conflict_with_json
created_at
updated_at
valid_from
valid_until
last_accessed_at
last_verified_at
```

---

## 6. 文件设计

### 6.1 Model.md

用途：保存模型人格、系统风格、行为边界等。

当前内容：

```text
[留空]
```

---

### 6.2 User.md

用途：保存长期稳定的用户画像。

User.md 不应直接保存所有事件。它是对长期 MemoryItem 的高层总结。

当前内容：

```text
[留空]
```

---

### 6.3 Prompt 文件

建议后续按模块独立管理 Prompt：

```text
prompts/dialogue_agent.md
prompts/memory_retrieval_workflow.md
prompts/memory_curator.md
prompts/profile_consolidator.md
```

当前均留空。

---

## 7. 关键接口设计

### 7.1 用户输入请求对象

```json
{
  "conversation_id": "string",
  "user_message": "string",
  "created_at": "datetime"
}
```

系统收到后生成：

```json
{
  "request_id": "string",
  "turn_id": "string",
  "conversation_id": "string",
  "user_message": "string",
  "created_at": "datetime",
  "status": "received"
}
```

---

### 7.2 Dialogue Agent 输入

#### 即时回复输入

```json
{
  "request_id": "string",
  "turn_id": "string",
  "model_profile": "Model.md content",
  "user_profile": "User.md content",
  "compact_history": "string",
  "recent_history": [
    {
      "role": "user|assistant|system",
      "content": "string",
      "created_at": "datetime"
    }
  ],
  "current_query": "string"
}
```

#### 二次回复输入

```json
{
  "request_id": "string",
  "turn_id": "string",
  "initial_reply": "string",
  "current_conversation_state": "string",
  "retrieved_items": [
    {
      "id": 1,
      "summary": "string",
      "content": "string",
      "memory_type": "string",
      "references_json": "[]",
      "tags_json": "[]",
      "metadata_json": "{}",
      "confidence": 0.8,
      "importance": 0.5,
      "sensitivity": "normal",
      "status": "active",
      "created_at": "datetime",
      "updated_at": "datetime"
    }
  ]
}
```

#### 二次回复输出

```json
{
  "request_id": "string",
  "decision": "followup|no_followup",
  "followup_type": "supplement|correction|none",
  "reply": "string"
}
```

---

### 7.3 Memory Retrieval Workflow 输入输出

#### 输入

```json
{
  "request_id": "string",
  "turn_id": "string",
  "current_query": "string",
  "compact_history": "string",
  "recent_history": [],
  "user_profile": "User.md content",
  "lightweight_memory_items": [
    {
      "id": 1,
      "summary": "string",
      "tags_json": "[]",
      "memory_type": "string",
      "references_json": "[]",
      "created_at": "datetime",
      "importance": 0.5
    }
  ]
}
```

#### 输出

```json
{
  "request_id": "string",
  "selected_memory_ids": [1, 2, 3],
  "retrieval_reason": "string",
  "needs_full_load": true
}
```

随后 Memory Store 根据 `selected_memory_ids` 读取完整字段，返回给 Dialogue Agent。

---

### 7.4 Memory Curator 输入输出

#### 输入

```json
{
  "conversation_id": "string",
  "turns": [
    {
      "turn_id": "string",
      "role": "user|assistant",
      "content": "string",
      "created_at": "datetime"
    }
  ],
  "existing_memory_candidates": [
    {
      "id": 1,
      "summary": "string",
      "content": "string",
      "memory_type": "string",
      "tags_json": "[]",
      "status": "active"
    }
  ]
}
```

#### 输出

```json
{
  "operations": [
    {
      "operation": "create|update|archive|link|conflict_mark|merge",
      "target_id": 1,
      "payload": {
        "summary": "string",
        "content": "string",
        "memory_type": "string",
        "references_json": "[]",
        "tags_json": "[]",
        "metadata_json": "{}",
        "confidence": 0.8,
        "importance": 0.5,
        "sensitivity": "normal",
        "status": "active"
      }
    }
  ]
}
```

---

## 8. 推荐项目目录结构

以下为建议目录结构，方便多人并行开发：

```text
project-root/
  README.md
  docs/
    architecture.md
    database.md
    api_contracts.md
    development_tasks.md
    prompt_placeholders.md
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
    test_memory_repository.py
    test_request_coordinator.py
    test_memory_retrieval_workflow.py
    test_dialogue_service.py
    test_memory_curator.py
```

---

## 9. 可并行开发组件拆分

下面将项目拆分为若干可以并行开发的组件。每个组件尽量通过固定接口通信，减少开发冲突。

---

# 组件一：数据库与 Memory Store

## 9.1 组件名称

```text
Memory Store / Database Layer
```

## 9.2 负责人任务

负责实现 `memory_items` 表、数据库连接、CRUD 操作和基础查询接口。

## 9.3 开发内容

1. 建立数据库连接。
2. 创建 `memory_items` 表。
3. 实现基础 CRUD。
4. 实现轻量字段读取。
5. 实现按 ID 批量读取完整字段。
6. 实现 update / archive / conflict / merge 等基础操作。
7. 支持 JSON 字段读写。

## 9.4 输入输出接口

### 获取轻量记忆列表

```python
def list_lightweight_memory_items(status: str = "active") -> list[dict]:
    pass
```

返回字段：

```text
id
summary
tags_json
memory_type
references_json
created_at
importance
```

### 根据 ID 批量读取完整记忆

```python
def get_memory_items_by_ids(ids: list[int]) -> list[dict]:
    pass
```

### 执行记忆更新操作

```python
def apply_memory_operations(operations: list[dict]) -> list[dict]:
    pass
```

## 9.5 不负责内容

该组件不负责：

1. 判断哪些记忆相关。
2. 生成模型回复。
3. 从对话中抽取记忆。
4. 更新 User.md。

## 9.6 交付物

```text
src/memory/db.py
src/memory/models.py
src/memory/repository.py
src/memory/migrations.py
tests/test_memory_repository.py
docs/database.md
```

---

# 组件二：Persona File Manager

## 9.7 组件名称

```text
Persona File Manager
```

## 9.8 负责人任务

负责读取和更新 `Model.md`、`User.md`，并为其他模块提供统一接口。

## 9.9 开发内容

1. 读取 `data/Model.md`。
2. 读取 `data/User.md`。
3. 支持保存 `User.md`。
4. 支持生成 User.md patch 预览。
5. 避免多个流程同时写 User.md 造成冲突。

## 9.10 输入输出接口

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

## 9.11 不负责内容

该组件不负责：

1. 判断哪些信息应该写入 User.md。
2. 生成 User.md 内容。
3. 记忆检索。
4. 对话回复。
    

## 9.12 交付物

```text
src/persona/file_manager.py
tests/test_persona_file_manager.py
docs/prompt_placeholders.md
data/Model.md
data/User.md
```

---

# 组件三：Conversation Store

## 9.13 组件名称

```text
Conversation Store
```

## 9.14 负责人任务

负责保存和读取对话历史，包括 recent history 和 compact history。

## 9.15 开发内容

1. 保存每轮 user / assistant 消息。
2. 根据 conversation_id 读取最近 N 轮对话。
3. 保存 compact history。    
4. 读取 compact history。
5. 为 Dialogue Agent 和 Memory Curator 提供上下文。

## 9.16 输入输出接口

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

## 9.17 不负责内容

该组件不负责：

1. 生成 compact history 的摘要内容。
2. 判断记忆是否重要。
3. 检索 memory_items。

## 9.18 交付物

```text
src/conversation/history_store.py
src/conversation/compact_store.py
tests/test_conversation_store.py
```

---

# 组件四：Request Coordinator

## 9.19 组件名称

```text
Request Coordinator
```

## 9.20 负责人任务

负责管理一次用户请求的生命周期，保证 Agent A 的即时回复、Agent B 的检索结果和 Agent A 的二次回复决策能够通过 `request_id` 正确绑定。

## 9.21 开发内容

1. 创建 `request_id`。
2. 创建 `turn_id`。
3. 记录请求状态。
4. 记录 Agent A 初始回复。
5. 记录 Memory Retrieval Workflow 检索状态。
6. 记录二次回复决策。
7. 管理 pending retrieval queue。

## 9.22 状态设计

建议请求状态包括：

```text
received
initial_reply_generated
retrieval_pending
retrieval_completed
followup_generated
no_followup_needed
completed
failed
```

## 9.23 输入输出接口

```python
def create_request(conversation_id: str, user_message: str) -> dict:
    pass


def mark_initial_reply(request_id: str, reply: str) -> None:
    pass


def mark_retrieval_completed(request_id: str, retrieved_items: list[dict]) -> None:
    pass


def get_pending_followup_requests() -> list[dict]:
    pass


def mark_followup_decision(request_id: str, decision: dict) -> None:
    pass
```

## 9.24 不负责内容

该组件不负责：

1. 生成回复内容。
2. 判断记忆相关性。
3. 操作数据库的具体 SQL。

## 9.25 交付物

```text
src/coordinator/request_coordinator.py
src/coordinator/pending_queue.py
tests/test_request_coordinator.py
docs/api_contracts.md
```

---

# 组件五：Memory Retrieval Workflow

## 9.26 组件名称

```text
Memory Retrieval Workflow
```

## 9.27 负责人任务

负责根据当前 query 和轻量记忆字段，让模型判断哪些 MemoryItem 与当前问题相关。

## 9.28 开发内容

1. 接收当前 query。
2. 接收 compact history、recent history、User.md。
3. 接收轻量 memory_items。
4. 构造检索判断输入。
5. 调用模型判断相关记忆 ID。
6. 输出 selected_memory_ids。    
7. 预留后续扩展 chain / workflow / weighted retrieval 的接口。

## 9.29 demo 检索策略

当前 demo 不使用向量检索、不使用加权分数、不使用复杂排序。

当前策略：

```text
直接让模型根据 query 与轻量 memory_items 判断相关项。
```

输入字段仅包括：

```text
id
summary
tags_json
memory_type
references_json
created_at
importance
```

## 9.30 输入输出接口

```python
def retrieve_relevant_memory_ids(
    request_id: str,
    current_query: str,
    compact_history: str,
    recent_history: list[dict],
    user_profile: str,
    lightweight_memory_items: list[dict],
) -> dict:
    pass
```

输出：

```json
{
  "request_id": "string",
  "selected_memory_ids": [1, 2, 3],
  "retrieval_reason": "string",
  "strategy": "llm_direct_judgement"
}
```

## 9.31 后续扩展预留

后续可扩展为：

```text
keyword prefilter
vector retrieval
tag filter
recency filter
importance rerank
chain-based retrieval
multi-step workflow retrieval
```

因此建议内部保留 `strategy` 字段：

```python
strategy = "llm_direct_judgement"
```

后续可以增加：

```python
strategy = "weighted_score"
strategy = "vector_then_llm_rerank"
strategy = "chain_retrieval"
```

## 9.32 不负责内容

该组件不负责：

1. 读取完整 memory item。
    
2. 生成最终用户回复。
    
3. 更新 memory_items。
    
4. 更新 User.md。
    

## 9.33 交付物

```text
src/agents/memory_retrieval_workflow.py
tests/test_memory_retrieval_workflow.py
prompts/memory_retrieval_workflow.md
```

Prompt 文件留空。

---

# 组件六：Dialogue Agent

## 9.34 组件名称

```text
Dialogue Agent
```

## 9.35 负责人任务

负责生成即时回复，并在 Memory Retrieval Workflow 返回相关记忆后，判断是否需要生成二次补充回复。

## 9.36 开发内容

1. 构造即时回复上下文。
2. 调用模型生成第一条回复。
3. 接收 retrieved_items。
4. 判断是否需要二次回复。
5. 生成补充型 / 修正型回复。
6. 支持 no_followup_needed。

## 9.37 即时回复上下文顺序

```text
System / Developer Instruction
Model.md
User.md
Compact Memory
Recent History
Current User Query
```

## 9.38 二次回复上下文顺序

```text
System / Developer Instruction
Model.md
User.md
Compact Memory
Recent History
Initial Reply
Retrieved Events
Current Conversation State
Original User Query
```

## 9.39 输入输出接口

### 生成即时回复

```python
def generate_initial_reply(input_data: dict) -> dict:
    pass
```

输出：

```json
{
  "request_id": "string",
  "reply": "string"
}
```

### 判断并生成二次回复

```python
def generate_followup_reply(input_data: dict) -> dict:
    pass
```

输出：

```json
{
  "request_id": "string",
  "decision": "followup|no_followup",
  "followup_type": "supplement|correction|none",
  "reply": "string"
}
```

## 9.40 二次回复规则

Dialogue Agent 至少需要判断：

```text
retrieved_items 是否为空
retrieved_items 是否与原 query 相关
retrieved_items 是否改变初始回答
是否涉及高风险领域
用户是否已经切换话题
初始回答是否承诺稍后补充
```

## 9.41 不负责内容

该组件不负责：

1. 直接查询数据库。
2. 抽取记忆。
3. 更新 User.md。
4. 管理 request_id 状态。

## 9.42 交付物

```text
src/agents/dialogue_agent.py
tests/test_dialogue_agent.py
prompts/dialogue_agent.md
```

Prompt 文件留空。

---

# 组件七：Memory Curator

## 9.43 组件名称

```text
Memory Curator
```

## 9.44 负责人任务

负责从对话中抽取关键记忆，并对 `memory_items` 生成更新操作。

## 9.45 子模块

Memory Curator 分为两个子模块：

```text
Conversation Memory Extractor
Profile Consolidator
```

---

## 9.46 Conversation Memory Extractor

### 职责

从若干轮对话中抽取候选记忆项，并生成操作列表。

### 支持操作

```text
create
update
archive
link
conflict_mark
merge
```

### 输入

```json
{
  "conversation_id": "string",
  "turns": [],
  "existing_memory_candidates": []
}
```

### 输出

```json
{
  "operations": []
}
```

---

## 9.47 Profile Consolidator

### 职责

定期从 `memory_items` 中筛选长期稳定信息，生成 `User.md` 更新建议。

### 当前策略

User.md 准入标准后续可能自定义。因此 demo 阶段只需要保留接口，不强行实现复杂准入逻辑。

### 输出

```json
{
  "should_update": true,
  "patch": {},
  "reason": "string"
}
```

## 9.48 不负责内容

该组件不负责：

1. 面向用户生成回复。
    
2. 检索当前 query 相关记忆。
    
3. 管理 request_id 生命周期。
    
4. 直接拼接 Dialogue Agent 的上下文。
    

## 9.49 交付物

```text
src/agents/memory_curator.py
src/agents/profile_consolidator.py
tests/test_memory_curator.py
prompts/memory_curator.md
prompts/profile_consolidator.md
```

Prompt 文件留空。

---

# 组件八：Dialogue Service / API Layer

## 9.50 组件名称

```text
Dialogue Service / API Layer
```

## 9.51 负责人任务

负责把上述模块串联成完整 API。

## 9.52 开发内容

1. 提供用户发送消息接口。
2. 调用 Request Coordinator 创建请求。
3. 调用 Persona File Manager 读取 Model.md 和 User.md。
4. 调用 Conversation Store 读取历史。
5. 调用 Dialogue Agent 生成即时回复。
6. 启动 Memory Retrieval Workflow。
7. 检索完成后触发二次回复判断。
8. 保存对话历史。
9. 触发 Memory Curator。

## 9.53 推荐接口

### 发送用户消息

```http
POST /chat
```

请求：

```json
{
  "conversation_id": "string",
  "message": "string"
}
```

响应：

```json
{
  "request_id": "string",
  "turn_id": "string",
  "reply": "string",
  "retrieval_status": "pending|completed|skipped"
}
```

### 查询 pending followup

```http
GET /followups/pending
```

### 触发某个 request 的 followup 判断

```http
POST /followups/{request_id}/run
```

### 触发记忆更新

```http
POST /memory/curate
```

## 9.54 不负责内容

该组件不负责：

1. Agent Prompt 的具体内容。
    
2. 数据库底层 SQL 细节。
    
3. 模型判断逻辑本身。
    

## 9.55 交付物

```text
src/api/routes.py
src/api/schemas.py
src/services/dialogue_service.py
src/services/memory_service.py
tests/test_dialogue_service.py
```

---

## 10. 并行开发建议

### 10.1 可以直接并行的任务

以下组件可以并行开发，冲突较少：

```text
Memory Store / Database Layer
Persona File Manager
Conversation Store
Request Coordinator
Memory Retrieval Workflow
Dialogue Agent
Memory Curator
API Layer
```

其中需要提前统一的是：

1. 数据结构字段名。
2. JSON 输入输出格式。
3. request_id / turn_id 格式。
4. 文件路径约定。
5. Prompt 文件路径约定。

---

### 10.2 推荐开发顺序

虽然可以并行，但建议按以下依赖关系推进：

```text
第一批：Memory Store、Persona File Manager、Conversation Store
第二批：Request Coordinator、Memory Retrieval Workflow、Dialogue Agent
第三批：Memory Curator、API Layer
第四批：集成测试、端到端 demo
```

依赖关系：

```text
Dialogue Agent 依赖 Persona File Manager + Conversation Store
Memory Retrieval Workflow 依赖 Memory Store 的轻量字段读取
Memory Curator 依赖 Conversation Store + Memory Store
API Layer 依赖所有核心模块
Request Coordinator 被 Dialogue Agent / Retrieval / API 共同使用
```

---

## 11. Git 开发与合并建议

### 11.1 分支建议

建议按组件创建分支：

```text
feature/memory-store
feature/persona-file-manager
feature/conversation-store
feature/request-coordinator
feature/memory-retrieval-workflow
feature/dialogue-agent
feature/memory-curator
feature/api-layer
```

### 11.2 合并顺序建议

建议优先合并底层模块：

```text
1. memory-store
2. persona-file-manager
3. conversation-store
4. request-coordinator
5. memory-retrieval-workflow
6. dialogue-agent
7. memory-curator
8. api-layer
```

### 11.3 减少冲突的约定

1. 每个开发者只修改自己负责的目录。
2. 公共接口写在 `src/api/schemas.py` 或单独的 `src/schemas/` 中。
3. Prompt 文件先留空，不在多人分支中反复修改。
4. 数据库字段名一旦确定，不随意改名。
5. 所有跨模块调用都通过接口函数，不直接访问其他模块内部实现。
6. 公共 JSON schema 变动必须先更新 `docs/api_contracts.md`。

---

## 12. MVP 范围

### 12.1 MVP 必须实现

1. `memory_items` 表。
2. `Model.md`、`User.md` 文件读取。
3. Recent history 保存与读取。
4. request_id / turn_id 管理。
5. Dialogue Agent 即时回复接口。
6. Memory Retrieval Workflow 直接让模型判断相关记忆。
7. 根据 selected_memory_ids 读取完整 memory item。
8. Dialogue Agent 二次回复判断接口。
9. Memory Curator 输出 memory update operations。
10. 基础 API 串联。

---

### 12.2 MVP 暂不实现

1. 向量检索。
2. 加权排序。
3. 复杂记忆压缩。
4. 用户记忆可视化管理。    
5. 完整高风险领域策略。
6. 复杂权限系统。
7. 复杂 User.md 自动改写。
8. 记忆使用日志。

---

## 13. Demo 阶段核心流程

### 13.1 用户普通闲聊

```text
用户输入
→ Dialogue Agent 立即回复
→ Memory Retrieval Workflow 检索
→ 返回弱相关或无相关记忆
→ Dialogue Agent 判断 no_followup_needed
```

---

### 13.2 用户询问与长期记忆相关的问题

```text
用户输入
→ Dialogue Agent 立即回复
→ Memory Retrieval Workflow 检索相关 MemoryItem
→ 读取完整字段
→ Dialogue Agent 判断需要补充
→ 生成第二条补充回复
```

---

### 13.3 用户询问高风险问题，例如用药

```text
用户输入
→ Dialogue Agent 初始回复可以保持谨慎
→ Memory Retrieval Workflow 检索健康 / 用药相关 MemoryItem
→ Dialogue Agent 根据检索结果判断是否补充或修正
→ 若信息过期或不确定，需要提示不确定性
```

---

## 14. 后续扩展方向

虽然 demo 阶段不实现，但接口应预留以下扩展：

### 14.1 检索增强

```text
LLM direct judgement
keyword prefilter
vector retrieval
weighted rerank
chain retrieval
workflow retrieval
```

### 14.2 记忆质量管理

```text
memory deduplication
memory compression
memory verification
memory expiration
memory confidence update
```

### 14.3 User.md 管理

```text
custom admission rules
manual approval
patch preview
automated profile consolidation
```

### 14.4 安全与隐私

```text
sensitivity-aware retrieval
consent-aware storage
retention policy
manual deletion
sensitive-domain response guardrails
```

---

## 15. 当前待补充内容

以下内容后续需要单独设计：

1. `Model.md` 具体内容。
2. Dialogue Agent Prompt。
3. Memory Retrieval Workflow Prompt。
4. Memory Curator Prompt。
5. Profile Consolidator Prompt。
6. User.md 准入规则。
7. MemoryItem 标签体系。
8. Demo 使用的模型接口。
9. API 是否使用 FastAPI / Flask / 其他框架。
10. 数据库使用 SQLite 还是 PostgreSQL。

---

## 16. 总结

当前系统采用“即时对话 + 异步记忆检索 + 可选二次回复 + 后台记忆维护”的整体架构。

核心设计为：

```text
Dialogue Agent 负责回答
Memory Retrieval Workflow 负责检索
Memory Curator 负责记忆更新
Request Coordinator 负责异步绑定
Memory Store 负责结构化记忆存储
Persona File Manager 负责 Model.md / User.md
Conversation Store 负责对话历史
```

demo 阶段应优先保证系统闭环可跑通，而不是实现复杂检索算法。当前检索策略采用模型直接判断相关记忆，后续可扩展为 chain / workflow / weighted retrieval。

该项目可以按组件并行开发，只要提前固定数据库字段、JSON schema、模块接口和目录结构，后续通过 Git merge 集成的冲突会相对较少。