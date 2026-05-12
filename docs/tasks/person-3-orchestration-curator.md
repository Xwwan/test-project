# 任务文档三：请求编排、API 与记忆维护

## 1. 负责人范围

你负责把底层能力和 Agent 能力串成完整可运行流程，并实现后台记忆维护。

负责组件：

- Request Coordinator
- Dialogue Service / API Layer
- Memory Curator
- Profile Consolidator
- Memory Service

主要目录：

```text
src/coordinator/
src/api/
src/services/
src/agents/memory_curator.py
src/agents/profile_consolidator.py
prompts/
tests/
docs/
```

不负责：

- 不负责实现数据库底层 SQL。
- 不负责 Persona 文件底层读写。
- 不负责 Dialogue Agent 的具体回复逻辑。
- 不负责 Memory Retrieval Workflow 的相关性判断逻辑。

---

## 2. 全员统一约定

### 2.1 运行环境

如无特殊说明，使用项目约定环境：

```bash
conda activate toy
```

Python 版本和依赖管理方式在项目初始化后统一写入 `README.md`。在没有进一步约定前，所有代码应尽量使用 Python 标准库完成 MVP。若要引入 FastAPI / Flask 等框架，需要先和另外两位开发者同步。

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

你不直接写 SQL。所有记忆读写通过 Person 1 提供的 Memory Store：

```python
list_lightweight_memory_items()
get_memory_items_by_ids(ids)
apply_memory_operations(operations)
```

Demo 阶段默认使用 SQLite，数据库文件建议放在：

```text
data/app.db
```

`memory_items` 字段名固定，不在你的模块中重命名。

### 2.4 ID 与时间统一约定

Request Coordinator 负责生成：

```text
request_id = req_<uuid4_hex>
turn_id = turn_<uuid4_hex>
```

时间字段统一使用 ISO 8601 字符串，建议 UTC：

```text
2026-05-12T11:30:00Z
```

### 2.5 Prompt 文件统一约定

本阶段只建立 Prompt 文件占位，不写具体 Prompt：

```text
prompts/memory_curator.md
prompts/profile_consolidator.md
```

Prompt 内容后续单独设计。现在只保证路径存在、代码可读取或优雅跳过。

### 2.6 Git 与协作约定

建议分支：

```text
feature/orchestration-curator
```

你会接触公共接口最多。任何跨模块 JSON schema 调整都必须先更新：

```text
docs/api_contracts.md
```

并同步另外两位开发者。

---

## 3. 你的任务一：Request Coordinator

### 3.1 目标

管理一次用户请求的生命周期，保证即时回复、异步检索结果和二次回复判断都绑定到同一个 `request_id` / `turn_id`。

### 3.2 需要实现的文件

```text
src/coordinator/request_coordinator.py
src/coordinator/pending_queue.py
tests/test_request_coordinator.py
```

### 3.3 请求状态

状态枚举：

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

### 3.4 必须实现的接口

```python
def create_request(conversation_id: str, user_message: str) -> dict:
    pass


def mark_initial_reply(request_id: str, reply: str) -> None:
    pass


def mark_retrieval_pending(request_id: str) -> None:
    pass


def mark_retrieval_completed(request_id: str, retrieved_items: list[dict]) -> None:
    pass


def get_pending_followup_requests() -> list[dict]:
    pass


def mark_followup_decision(request_id: str, decision: dict) -> None:
    pass


def mark_failed(request_id: str, reason: str) -> None:
    pass
```

### 3.5 `create_request` 输出格式

```json
{
  "request_id": "req_xxx",
  "turn_id": "turn_xxx",
  "conversation_id": "string",
  "user_message": "string",
  "created_at": "datetime",
  "status": "received"
}
```

### 3.6 存储方式

MVP 可以使用内存 dict 或 SQLite 表。若使用内存 dict，要在文档中明确 demo 重启后状态丢失。

---

## 4. 你的任务二：Dialogue Service / API Layer

### 4.1 目标

对外提供聊天入口，并把 Person 1、Person 2 的模块串成完整闭环。

### 4.2 需要实现的文件

```text
src/api/routes.py
src/api/schemas.py
src/services/dialogue_service.py
src/services/memory_service.py
tests/test_dialogue_service.py
docs/api_contracts.md
src/main.py
```

### 4.3 推荐 API

#### 发送用户消息

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
  "request_id": "req_xxx",
  "turn_id": "turn_xxx",
  "reply": "string",
  "retrieval_status": "pending"
}
```

#### 查询 pending followup

```http
GET /followups/pending
```

#### 触发某个 request 的 followup 判断

```http
POST /followups/{request_id}/run
```

#### 触发记忆更新

```http
POST /memory/curate
```

### 4.4 `/chat` 编排流程

```text
1. 接收 conversation_id 和 message。
2. Request Coordinator 创建 request_id / turn_id。
3. Conversation Store 保存用户 turn。
4. Persona File Manager 读取 Model.md 和 User.md。
5. Conversation Store 读取 compact history 和 recent history。
6. Dialogue Agent 生成即时回复。
7. Request Coordinator 记录 initial reply。
8. Conversation Store 保存 assistant turn。
9. Memory Store 读取 lightweight_memory_items。
10. Memory Retrieval Workflow 判断 selected_memory_ids。
11. 如果有 selected ids，Memory Store 读取完整 retrieved_items。
12. Request Coordinator 记录 retrieval completed。
13. 返回第一条 reply 给用户，retrieval_status 可为 completed 或 pending。
```

MVP 如果暂时不做真正异步，可以同步完成检索，但 API 响应中仍保留 `retrieval_status` 字段，以便后续切换为异步。

### 4.5 followup 编排流程

```text
1. 读取 request 状态和 retrieved_items。
2. 构造 Dialogue Agent 二次回复输入。
3. 调用 generate_followup_reply。
4. Request Coordinator 记录 decision。
5. 如果 decision=followup，保存 assistant followup turn。
6. 返回 decision。
```

### 4.6 API schema 统一文件

`src/api/schemas.py` 至少定义或约定以下结构：

```text
ChatRequest
ChatResponse
FollowupDecision
MemoryCurateRequest
MemoryCurateResponse
```

MVP 可用 dataclass / TypedDict / 普通 dict 校验函数，不强制 Pydantic。

---

## 5. 你的任务三：Memory Curator

### 5.1 目标

从对话中抽取长期有价值的记忆，生成 Memory Store 可执行的 operations。

Memory Curator 分为：

```text
Conversation Memory Extractor
Profile Consolidator
```

### 5.2 需要实现的文件

```text
src/agents/memory_curator.py
src/agents/profile_consolidator.py
tests/test_memory_curator.py
prompts/memory_curator.md
prompts/profile_consolidator.md
```

### 5.3 Conversation Memory Extractor 接口

```python
def extract_memory_operations(
    conversation_id: str,
    turns: list[dict],
    existing_memory_candidates: list[dict],
) -> dict:
    pass
```

输入：

```json
{
  "conversation_id": "string",
  "turns": [
    {
      "turn_id": "turn_xxx",
      "role": "user",
      "content": "string",
      "created_at": "datetime"
    }
  ],
  "existing_memory_candidates": [
    {
      "id": 1,
      "summary": "string",
      "content": "string",
      "memory_type": "preference",
      "tags_json": [],
      "status": "active"
    }
  ]
}
```

输出：

```json
{
  "operations": [
    {
      "operation": "create",
      "target_id": null,
      "payload": {
        "summary": "string",
        "content": "string",
        "memory_type": "preference",
        "references_json": [],
        "tags_json": [],
        "metadata_json": {},
        "confidence": 0.8,
        "importance": 0.5,
        "sensitivity": "normal",
        "status": "active"
      }
    }
  ]
}
```

支持操作：

```text
create
update
archive
link
conflict_mark
merge
```

### 5.4 Memory Curator 策略

MVP 可以先使用规则或 fake model adapter，但接口必须像模型输出一样稳定。

候选记忆类型：

```text
fact
preference
task
event
constraint
profile_update
```

敏感度：

```text
normal
sensitive
high_risk
```

不应该写入的内容：

- 一次性寒暄。
- 没有长期价值的临时细节。
- 无法确认的推测。
- 没有用户授权的高度敏感信息。

### 5.5 Profile Consolidator 接口

```python
def generate_user_profile_patch(memory_items: list[dict], current_user_profile: str) -> dict:
    pass
```

输出：

```json
{
  "should_update": true,
  "patch": {
    "operation": "replace",
    "content": "new User.md content"
  },
  "reason": "string"
}
```

### 5.6 User.md 准入原则

`User.md` 只保存长期稳定、对未来回答有持续价值的信息。

默认准入标准：

- 用户明确要求记住。
- 信息长期稳定。
- 信息会显著改变未来回答方式。
- 多次对话中反复出现。
- 属于用户长期目标、长期偏好或长期约束。

---

## 6. 与其他人的对接点

### 6.1 调用 Person 1

你会使用：

```python
read_model_profile()
read_user_profile()
write_user_profile()
apply_user_profile_patch()
append_turn()
get_recent_history()
get_compact_history()
update_compact_history()
list_lightweight_memory_items()
get_memory_items_by_ids()
apply_memory_operations()
```

不要绕过这些接口直接读写底层数据。

### 6.2 调用 Person 2

你会使用：

```python
generate_initial_reply(input_data)
retrieve_relevant_memory_ids(...)
generate_followup_reply(input_data)
```

你负责构造输入、校验输出、记录状态。

---

## 7. 测试要求

至少覆盖：

- `create_request` 会生成唯一 `request_id` 和 `turn_id`。
- 请求状态可以从 `received` 推进到 `initial_reply_generated`。
- retrieval completed 后 pending followup 可被查到。
- `/chat` 或 `handle_chat_message()` 能返回第一条 reply。
- 空检索结果时 followup 返回 `no_followup`。
- 有 selected IDs 时会调用完整记忆加载。
- Memory Curator 输出合法 operations。
- Profile Consolidator 能返回 `should_update=false` 的安全默认结果。
- Memory Service 能把 operations 交给 Memory Store 执行。

---

## 8. 完成标准

完成后，系统应能跑通 Demo 闭环：

```text
用户发送消息
→ 创建 request_id / turn_id
→ 保存用户消息
→ 读取 Model.md / User.md / history
→ 生成即时回复
→ 保存助手回复
→ 检索轻量记忆
→ 加载相关完整记忆
→ 判断是否二次回复
→ 可选保存二次回复
→ 对话结束后触发记忆抽取
→ 写入 memory_items
```

即使模型接口暂时使用 fake adapter，也要保证整体接口和状态流稳定，方便后续替换真实模型。
