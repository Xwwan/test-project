# 任务文档二：Dialogue Agent 与 Memory Retrieval Workflow

## 1. 负责人范围

你负责系统中和模型判断最相关的两部分：即时回复、二次回复判断、轻量记忆相关性检索。

负责组件：

- Dialogue Agent
- Memory Retrieval Workflow

主要目录：

```text
src/agents/
prompts/
tests/
docs/
```

不负责：

- 不负责直接写 SQL。
- 不负责管理 `request_id` 生命周期。
- 不负责保存对话历史。
- 不负责更新 `User.md`。
- 不负责 API 路由。

---

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

你不直接访问数据库。记忆数据由 Person 1 的 Memory Store 提供。

轻量记忆字段固定为：

```text
id
summary
tags_json
memory_type
references_json
created_at
importance
```

完整记忆字段固定为：

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

### 2.4 ID 与时间统一约定

`request_id` 与 `turn_id` 由 Request Coordinator 生成。你的模块只接收、返回、透传。

建议格式：

```text
request_id = req_<uuid4_hex>
turn_id = turn_<uuid4_hex>
```

时间字段统一使用 ISO 8601 字符串，建议 UTC。

### 2.5 Prompt 文件统一约定

本阶段只建立 Prompt 文件占位，不写具体 Prompt：

```text
prompts/dialogue_agent.md
prompts/memory_retrieval_workflow.md
```

代码中可以预留读取 Prompt 的接口，但不要把 Prompt 内容写死到业务逻辑里。

### 2.6 Git 与协作约定

建议分支：

```text
feature/dialogue-retrieval
```

你主要修改 `src/agents/dialogue_agent.py`、`src/agents/memory_retrieval_workflow.py` 和对应测试。公共输入输出格式如需调整，先更新 `docs/api_contracts.md` 并同步另外两位开发者。

---

## 3. 你的任务一：Memory Retrieval Workflow

### 3.1 目标

根据当前用户问题、对话上下文、用户画像和轻量记忆列表，判断哪些 MemoryItem 与当前问题相关，返回相关 ID。

Demo 阶段策略固定为：

```text
llm_direct_judgement
```

也就是直接让模型根据 query 和轻量 memory items 判断相关性。暂不做向量检索、加权排序和复杂 chain。

### 3.2 需要实现的文件

```text
src/agents/memory_retrieval_workflow.py
tests/test_memory_retrieval_workflow.py
prompts/memory_retrieval_workflow.md
```

### 3.3 必须实现的接口

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

### 3.4 输入格式

```json
{
  "request_id": "req_xxx",
  "turn_id": "turn_xxx",
  "current_query": "string",
  "compact_history": "string",
  "recent_history": [
    {
      "role": "user",
      "content": "string",
      "created_at": "datetime"
    }
  ],
  "user_profile": "User.md content",
  "lightweight_memory_items": [
    {
      "id": 1,
      "summary": "string",
      "tags_json": [],
      "memory_type": "task",
      "references_json": [],
      "created_at": "datetime",
      "importance": 0.5
    }
  ]
}
```

### 3.5 输出格式

```json
{
  "request_id": "req_xxx",
  "selected_memory_ids": [1, 2, 3],
  "retrieval_reason": "string",
  "needs_full_load": true,
  "strategy": "llm_direct_judgement"
}
```

### 3.6 MVP 行为建议

如果模型接口暂未确定，可以先实现可替换的 adapter：

```python
def call_retrieval_model(input_data: dict) -> dict:
    pass
```

测试中可以使用 fake model / stub，保证 workflow 的输入输出稳定。

空记忆列表时必须返回：

```json
{
  "selected_memory_ids": [],
  "needs_full_load": false,
  "strategy": "llm_direct_judgement"
}
```

### 3.7 后续扩展预留

内部保留 `strategy` 字段，后续可以扩展为：

```text
keyword_prefilter
weighted_score
vector_then_llm_rerank
chain_retrieval
workflow_retrieval
```

不要把当前实现写死成唯一不可替换的流程。

---

## 4. 你的任务二：Dialogue Agent

### 4.1 目标

Dialogue Agent 负责两件事：

1. 用户输入后立即生成第一条回复。
2. 记忆检索完成后判断是否需要二次回复，并生成补充 / 修正 / 不回复决策。

### 4.2 需要实现的文件

```text
src/agents/dialogue_agent.py
tests/test_dialogue_agent.py
prompts/dialogue_agent.md
```

### 4.3 必须实现的接口

```python
def generate_initial_reply(input_data: dict) -> dict:
    pass


def generate_followup_reply(input_data: dict) -> dict:
    pass
```

### 4.4 即时回复输入格式

```json
{
  "request_id": "req_xxx",
  "turn_id": "turn_xxx",
  "model_profile": "Model.md content",
  "user_profile": "User.md content",
  "compact_history": "string",
  "recent_history": [
    {
      "role": "user",
      "content": "string",
      "created_at": "datetime"
    }
  ],
  "current_query": "string"
}
```

### 4.5 即时回复输出格式

```json
{
  "request_id": "req_xxx",
  "reply": "string"
}
```

### 4.6 即时回复上下文顺序

上下文拼接顺序固定为：

```text
System / Developer Instruction
Model.md
User.md
Compact Memory
Recent History
Current User Query
```

不要在即时回复里加入 Retrieved Events。记忆检索是异步返回后再判断是否补充。

### 4.7 二次回复输入格式

```json
{
  "request_id": "req_xxx",
  "turn_id": "turn_xxx",
  "initial_reply": "string",
  "current_conversation_state": "string",
  "original_user_query": "string",
  "model_profile": "Model.md content",
  "user_profile": "User.md content",
  "compact_history": "string",
  "recent_history": [],
  "retrieved_items": [
    {
      "id": 1,
      "summary": "string",
      "content": "string",
      "memory_type": "string",
      "references_json": [],
      "tags_json": [],
      "metadata_json": {},
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

### 4.8 二次回复输出格式

```json
{
  "request_id": "req_xxx",
  "decision": "followup",
  "followup_type": "supplement",
  "reply": "string"
}
```

允许值：

```text
decision = followup | no_followup
followup_type = supplement | correction | none
```

当 `decision` 为 `no_followup` 时：

```json
{
  "request_id": "req_xxx",
  "decision": "no_followup",
  "followup_type": "none",
  "reply": ""
}
```

### 4.9 二次回复判断规则

至少判断：

- `retrieved_items` 是否为空。
- 检索结果是否与原 query 高度相关。
- 检索结果是否会改变初始回答。
- 是否涉及健康、用药、法律、财务等高风险领域。
- 用户是否仍在相关话题。
- 初始回复是否承诺稍后补充。
- 检索结果是否只是弱相关闲聊信息。

二次回复类型：

```text
supplement：补充型，初始回答没错，但记忆能提供有价值信息。
correction：修正型，记忆会改变或纠正初始回答。
none：不需要回复。
```

### 4.10 高风险领域要求

健康、用药、法律、财务相关场景必须保守：

- 不把旧记忆当作当前事实。
- 需要提示不确定性。
- 不能用记忆替代专业意见。
- 过期或低置信度信息不能强行用于修正。

---

## 5. 与其他人的对接点

### 5.1 从 Person 1 获取的数据

你不会直接调用数据库，但 Dialogue Service 会把以下内容传给你：

```python
model_profile
user_profile
compact_history
recent_history
lightweight_memory_items
retrieved_items
```

你的代码必须只依赖这些输入，不要自行读取文件或数据库。

### 5.2 给 Person 3 的能力

Person 3 的 Dialogue Service 会调用：

```python
retrieve_relevant_memory_ids(...)
generate_initial_reply(...)
generate_followup_reply(...)
```

返回结构必须稳定，否则 API 编排会出问题。

---

## 6. 测试要求

至少覆盖：

- 空记忆列表时检索返回空 ID。
- 检索输出包含 `request_id`、`selected_memory_ids`、`strategy`。
- 即时回复输出包含同一个 `request_id`。
- 无 retrieved items 时二次回复返回 `no_followup`。
- 检索结果弱相关时二次回复返回 `no_followup`。
- 检索结果会改变初始回答时返回 `correction`。
- 检索结果只补充背景时返回 `supplement`。
- 高风险领域二次回复保持保守措辞。

---

## 7. 完成标准

完成后，Person 3 应该可以把你的模块接入完整 `/chat` 流程：

```text
用户输入
→ generate_initial_reply
→ retrieve_relevant_memory_ids
→ Memory Store 加载完整记忆
→ generate_followup_reply
```

你的模块不需要知道这些步骤由哪个 API 调用，只需要保证函数输入输出稳定。
