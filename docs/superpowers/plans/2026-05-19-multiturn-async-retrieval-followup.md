# 多轮异步记忆检索与二次回复解耦计划

> **状态：已实现，2026-05-19。** 本计划接续
> [流式回复完成与记忆检索解耦改造计划](2026-05-18-streaming-reply-retrieval-decoupling.md)，目标是在多轮对话中支持每一轮 query 独立触发异步记忆检索，并在检索乱序完成时安全地产生或跳过二次回复，避免上下文串线和流式输出串线。

## 实现记录

- `src/services/dialogue_service.py` 已增加 `DialogueTurnSnapshot`，每轮 query 在进入系统后固定保存原始 `Model.md`、`User.md`、compact history、recent history、原始 user turn、initial reply 与 initial assistant turn。
- 后台 memory retrieval 已改为进程内 `ThreadPoolExecutor`，并通过 `config/app.yaml` 的 `dialogue.retrieval.max_workers` 控制并发数。
- `/chat/stream` 只输出 `meta`、initial `delta` 和 `done`，不再等待或输出二次回复。
- 新增 conversation 级 follow-up 投递队列和 `GET /followups/stream?conversation_id=...` SSE 通道，事件名为 `followup`，事件数据带 `request_id`、父 user turn、父 initial assistant turn、原始问题和初始回复。
- follow-up decision 输入已拆分为 Original Request Context 与 Latest Conversation Context，最新上下文窗口由 `dialogue.followup.context_window_turns` 控制。
- follow-up assistant turn 已写入父关系 metadata：`turn_kind`、`parent_request_id`、`parent_user_turn_id`、`parent_initial_reply_turn_id`、`followup_type`。
- request coordinator 已拆分记录 `retrieval_status/retrieval_error`、`followup_status/followup_error`、`delivery_status`，后台 retrieval 或 follow-up 失败不会把已经完成的 initial reply 标为全局 `failed`。

## 背景

当前 `/chat/stream` 已经将 initial reply 的完成信号从 memory retrieval 中解耦：

```text
用户 query
-> 创建 request
-> 保存 user turn
-> 捕获当轮上下文
-> 流式生成 initial reply
-> 保存 assistant initial turn
-> 标记 retrieval_pending
-> 后台执行 retrieval
-> 立刻 yield done
```

但新的交互形态会出现更复杂的时间线：

```text
T1 用户问 A
T2 A 的 initial reply 已完成，A 的 memory retrieval 仍在运行
T3 用户问 B
T4 B 的 initial reply 开始或完成
T5 A 的 memory retrieval 才完成，并可能触发二次回复
T6 B 的 memory retrieval 也可能完成，并可能触发二次回复
```

这会带来两个关键风险：

- **上下文串线**：A 的检索或二次回复判断误用 B 之后的上下文，导致模型补充的对象不再是 A。
- **流式输出串线**：B 的 initial reply 正在 streaming 时，A 的二次回复也到达，前端如果只按 `delta` 追加文本，可能把 A 的补充拼进 B 的回复里。

## 目标

- 每一轮 query 都创建独立 `request_id`，并触发独立 memory retrieval。
- 多个 retrieval 可以并发执行，也可以乱序完成。
- memory retrieval 必须只使用该 query 进入系统时捕获的原始上下文快照。
- follow-up decision 可以看到最新会话状态，但最新上下文只能用于判断二次回复的时机、措辞和是否需要明确指回原始问题，不能用于重新解释原始 query。
- `/chat/stream` 只承载当前 query 的 initial reply，不再等待或输出二次回复。
- 二次回复通过 conversation 级别 SSE 独立事件通道发送，避免与任意 initial reply stream 混在一起。
- 二次回复必须带清晰父关系：它补充的是哪个 `request_id`、哪个 user turn、哪个 initial assistant turn。
- 二次回复返回数据必须附带原始问题和初始回复相关信息，方便前端把补充消息关联回历史上下文。
- 检索失败或二次回复失败不能把已经成功的 initial reply 标记为失败。

## 非目标

- 不重写 memory retrieval agent 的核心判断逻辑。
- 不引入 Celery、Redis、外部消息队列或新 Web 框架。
- 不改变 memory item schema。
- 不要求本阶段实现跨进程持久化后台任务；MVP 仍可使用进程内队列。
- 不让旧的 `/chat/stream` 长连接继续承担历史二次回复交付。
- 不需要兼容旧的 `/chat/stream` follow-up 输出协议，前端可配合新设计调整。

## 已确认决策

- 二次回复交付使用 SSE：`GET /followups/stream?conversation_id=...`。
- 采用积极二次回复策略：只要检索记忆对原始问题有明确价值，即使用户已切换话题，也可以发送简短补充；回复必须明确指回原始问题，避免被理解为回答当前最新问题。
- Follow-up SSE 事件和相关 API 返回数据需要附带原始上下文引用信息，包括原始问题、初始回复、父 request/turn ID。
- 不考虑旧协议兼容，`/chat/stream` 可以彻底改成只输出 initial reply。
- Follow-up 判断的最新上下文窗口写入配置文件，`-1` 表示全部上下文；当前默认值为 `50`。

## 核心原则

### 1. 检索用原始上下文快照

每轮 query 进入系统时，需要捕获不可变的 `DialogueTurnSnapshot`：

```text
request_id
turn_id
conversation_id
user_message
model_profile
user_profile
compact_history
recent_history
created_at
initial_reply
initial_reply_turn_id
```

这个快照用于：

- initial reply prompt。
- memory retrieval prompt。
- follow-up decision 中的 Original Request Context。

一旦捕获，后续 query 不得改变该快照。比如 A 的检索还没结束时用户问了 B，A 的 retrieval 仍然只能用 `snapshot_A`。

### 2. 二次回复判断同时看原始问题和最新会话状态

follow-up decision 不应该只看旧上下文，因为用户可能已经切换话题；但它也不能把最新上下文混进原始问题解释里。

因此传给 Dialogue Agent 的 follow-up 输入需要拆成两层：

```text
Original Request Context
- 原始 user query
- 原始 initial reply
- 原始 Model.md
- 原始 User.md
- 原始 compact_history
- 原始 recent_history
- 本轮 retrieved_items

Latest Conversation Context
- 当前最新 recent_history
- 原始 request 之后新增的 turns
- 当前会话是否已经切换话题的判断线索
```

Prompt 必须明确约束：

```text
Original User Query 是二次回复要补充或纠正的对象。
Retrieved Events 只能用于补充或纠正 Original User Query 对应的 initial reply。
Latest Conversation Context 只能用于判断二次回复的时机、措辞和是否需要明确指回原始问题。
不要用 Latest Conversation Context 改写、扩展或重新解释 Original User Query。
```

采用积极策略时，用户切换话题不必自动 `no_followup`。如果 Retrieved Events 对原始问题有明确价值，可以发送二次回复，但回复需要显式指回原始问题，例如“补充一下刚才关于咖啡的问题……”。如果检索结果只是弱相关、重复信息或没有实际帮助，仍返回 `no_followup`。

### 3. 二次回复是独立消息，不是 initial stream 的尾巴

当前 `/chat/stream` 中如果继续使用 `stream_followup=True`，就会出现：

```text
meta
delta
done
delta phase=followup
followup_done
```

这要求前端同时维护多个仍未关闭的 stream，并按 `request_id` 与 `phase` 分流。只要前端简单地把所有 `delta` 追加到“当前 assistant message”，就可能在 B 的首回复中混入 A 的二次回复。

推荐改成：

```text
/chat/stream
-> 只输出当前 request 的 initial reply
-> done 后结束

/followups/stream?conversation_id=...
-> 输出该 conversation 下任意 request 的 generated follow-up
```

这样 initial reply 和二次回复从协议层就分开。

## 推荐架构

### 1. 请求快照

在 `src/services/dialogue_service.py` 增加内部 dataclass：

```python
@dataclass(frozen=True)
class DialogueTurnSnapshot:
    request_id: str
    turn_id: str
    conversation_id: str
    user_message: str
    model_profile: str
    user_profile: str
    compact_history: str
    recent_history: list[dict]
    created_at: str
    initial_reply: str | None = None
    initial_reply_turn_id: str | None = None
```

流程中先捕获不含 initial reply 的快照，initial reply 保存后再生成带 `initial_reply` 和 `initial_reply_turn_id` 的最终快照。

### 2. Retrieval 后台任务

将当前每次 `threading.Thread(...).start()` 的方式升级为受控 executor：

```text
ThreadPoolExecutor(max_workers=configured_max_workers)
```

每个任务独立执行：

```text
run_retrieval(snapshot)
-> mark_retrieval_completed(request_id, retrieved_items)
-> run_followup_decision(snapshot, retrieved_items, latest_context)
-> 保存 follow-up turn 或 no_followup
-> 如有 follow-up，加入 generated follow-up delivery queue
```

这样可以支持多轮并发，同时避免无限创建线程。

### 3. Follow-up 判断输入

当前 `_run_followup_decision` 会在 retrieval 完成后重新读取最新 `user_profile`、`compact_history` 和 `recent_history`，这会导致原始上下文与最新上下文混在一起。

应改为：

```text
原始上下文：来自 DialogueTurnSnapshot
最新上下文：retrieval 完成时单独读取，用于判断二次回复措辞、时机和是否需要明确指回原始问题
新增 turns：根据 snapshot.created_at 或 turn 序定位原始 request 后发生的 turns
```

推荐输入结构：

```json
{
  "request_id": "req_A",
  "original_user_query": "A 当时的问题",
  "initial_reply": "A 的第一次回复",
  "original_context": {
    "model_profile": "...",
    "user_profile": "...",
    "compact_history": "...",
    "recent_history": []
  },
  "retrieved_items": [],
  "latest_context": {
    "recent_history": [],
    "newer_turns_since_original_request": []
  },
  "current_conversation_state": "retrieval_completed"
}
```

### 4. Conversation 级二次回复交付

新增一个 conversation 级别的二次回复交付机制。已确认使用 SSE：

#### SSE 事件流

```http
GET /followups/stream?conversation_id=conv-1
```

事件示例：

```json
{
  "event": "followup",
  "data": {
    "conversation_id": "conv-1",
    "request_id": "req_A",
    "parent_user_turn_id": "turn_A",
    "parent_initial_reply_turn_id": "turn_A_initial",
    "followup_turn_id": "turn_A_followup",
    "original_user_query": "A 当时的问题",
    "initial_reply": "A 当时的第一次回复",
    "followup_type": "supplement",
    "reply": "顺便补充一下，刚才那个问题里……"
  }
}
```

返回数据中附带的“之前的信息”指：

- `parent_user_turn_id`：它补充的是哪条历史用户消息。
- `parent_initial_reply_turn_id`：它补充或纠正的是哪条初始 assistant 回复。
- `original_user_query`：当时用户问的原始问题。
- `initial_reply`：当时系统已经给出的第一次回复。
- `request_id`：这次异步检索和二次回复判断所属的原始请求。

前端可以用这些字段把二次回复渲染成独立 assistant 消息，也可以视觉上挂到原始问题或原始回复下面。

### 5. Conversation turn metadata

follow-up assistant turn 应保存父关系：

```json
{
  "turn_kind": "followup",
  "parent_request_id": "req_A",
  "parent_user_turn_id": "turn_A",
  "parent_initial_reply_turn_id": "turn_A_initial",
  "followup_type": "supplement"
}
```

这样即使实际历史顺序是：

```text
user A
assistant A initial
user B
assistant B initial
assistant A followup
```

系统和前端也能知道最后一条 assistant 消息补充的是 A，而不是 B。

### 6. 状态与错误归属

当前后台 retrieval 失败可能调用 `mark_failed(request_id, reason)`。多轮异步场景下，这会把已经成功的 initial reply 也表现成失败。

建议区分：

```text
initial reply 状态
retrieval 状态
follow-up 状态
delivery 状态
```

可以新增字段：

```text
retrieval_status: pending | completed | failed
retrieval_error: str | None
followup_status: pending | generated | no_followup | failed
followup_error: str | None
delivery_status: pending | delivered | none
```

这样：

- initial reply 失败才是本轮回复失败。
- retrieval 失败只影响记忆增强。
- follow-up 失败只影响二次回复。
- 已经展示给用户的 initial reply 不应因为后台错误被回滚为失败。

## 新时间线

### 多轮正常流程

```text
用户问 A
-> 创建 request_A
-> 捕获 snapshot_A
-> /chat/stream 返回 A initial reply
-> /chat/stream done 并结束
-> 后台 retrieval_A 继续运行

用户问 B
-> 创建 request_B
-> 捕获 snapshot_B
-> /chat/stream 返回 B initial reply
-> /chat/stream done 并结束
-> 后台 retrieval_B 继续运行

retrieval_A 完成
-> 使用 snapshot_A 做记忆结果归属
-> 同时读取 latest context 判断是否适合补充
-> 如需要，生成 followup_A
-> 通过 conversation follow-up stream 推送 followup_A

retrieval_B 完成
-> 使用 snapshot_B 做记忆结果归属
-> 同时读取 latest context 判断是否适合补充
-> 如需要，生成 followup_B
-> 通过 conversation follow-up stream 推送 followup_B
```

### B 首回复 streaming 时 A 二次回复到达

```text
B 的 /chat/stream:
  meta request_id=req_B
  delta phase=initial
  delta phase=initial
  done

conversation follow-up stream:
  followup request_id=req_A parent_user_turn_id=turn_A
```

两者使用不同事件通道，因此不会在同一条 SSE response 中混写。前端渲染时也不应把 `followup_A` 追加到 `B initial reply` 的消息气泡中，而应创建一条独立 assistant follow-up 消息，并根据 `parent_user_turn_id` 关联到 A。

## Prompt 调整

新增可编辑 prompt 模板：

```text
prompts/dialogue_followup_decision.md
```

该文件专门用于“二次回复判断同时看原始问题和最新会话状态”的场景，模板中使用 `{{变量名}}` 表示运行时填充项。后续实现时，Dialogue Service 或 Dialogue Agent adapter 负责把结构化上下文序列化为文本或 JSON 后填入模板。

模板需要表达以下规则：

```text
二次回复时，你会收到 Original Request Context、Retrieved Events 和 Latest Conversation Context。

Original Request Context 是二次回复要服务的原始问题上下文。
Retrieved Events 只用于判断是否应补充或纠正该原始问题的 initial reply。
Latest Conversation Context 只用于判断二次回复的措辞、时机和是否需要明确指回原始问题。

不要用 Latest Conversation Context 改写、扩展或重新解释 Original User Query。
采用积极二次回复策略：如果 Retrieved Events 对原始问题有明确价值，即使用户已经切换到新话题，也可以发送简短二次回复。
```

### Prompt 变量填充说明

`prompts/dialogue_followup_decision.md` 中的变量含义如下：

| 变量 | 填充内容 |
| --- | --- |
| `{{request_id}}` | 当前二次回复判断对应的原始 request ID，例如 `req_A`。 |
| `{{conversation_id}}` | 当前会话 ID。 |
| `{{parent_user_turn_id}}` | 原始用户消息的 turn ID。 |
| `{{parent_initial_reply_turn_id}}` | 原始 initial assistant reply 的 turn ID；如果尚未记录可为空字符串。 |
| `{{current_conversation_state}}` | 当前 request 状态，例如 `retrieval_completed`、`followup_pending`。 |
| `{{original_user_query}}` | 原始用户问题，即触发本次 retrieval 的那一轮 query。 |
| `{{initial_reply}}` | 该原始问题对应的第一次回复文本。 |
| `{{original_model_profile}}` | 原始 query 进入时读取到的 `Model.md` 内容。 |
| `{{original_user_profile}}` | 原始 query 进入时读取到的 `User.md` 内容。 |
| `{{original_compact_history}}` | 原始 query 进入时读取到的 compact memory / compact history。 |
| `{{original_recent_history}}` | 原始 query 进入时的 recent history 快照，建议序列化为 JSON 或清晰的 role/content 列表。 |
| `{{retrieved_items}}` | 该原始 query 的 memory retrieval 结果，必须只包含该 request 检索得到的 full memory items。 |
| `{{latest_recent_history}}` | retrieval 完成并准备判断二次回复时读取到的最新 recent history。只用于判断二次回复措辞、时机和是否需要明确指回原始问题。 |
| `{{newer_turns_since_original_request}}` | 原始 user turn 之后新增的 turns，建议包含 role、content、turn_id、created_at。只用于判断二次回复措辞、时机和是否需要明确指回原始问题。 |

变量分区要求：

- `original_*` 变量来自 `DialogueTurnSnapshot`，必须在原始 query 进入时固定，不受后续 query 影响。
- `{{retrieved_items}}` 只来自该 `request_id` 的检索结果，不能混入其它 request 的检索结果。
- `latest_*` 与 `newer_*` 变量在 follow-up decision 执行时读取，只用于判断二次回复措辞、时机和是否需要明确指回原始问题。
- 模型不得用 `latest_*` 或 `newer_*` 变量重新解释 `{{original_user_query}}`。

## 配置

Follow-up 判断的最新上下文窗口写入 `config/app.yaml`：

```yaml
dialogue:
  followup:
    context_window_turns: 50
```

语义：

- `context_window_turns: 50`：`latest_recent_history` 和 `newer_turns_since_original_request` 最多各填充 50 条 turn。
- `context_window_turns: -1`：填充所有可用上下文。
- 如果实际上下文超过限制，建议保留离当前时间最近的 turns，并在填充文本中标注已截断。

## 测试计划

### 1. 第二个 query 不等待第一个 retrieval

构造：

```text
A retrieval 卡住
B query 进入
B initial reply 正常完成
释放 A retrieval
A followup 正常完成或 no_followup
```

断言：

- B 的 initial reply 不被 A retrieval 阻塞。
- A retrieval 收到的是 A 的 `current_query`。
- B retrieval 收到的是 B 的 `current_query`。
- 两个 request 的 retrieved items 不混。

### 2. Follow-up 使用原始上下文和最新上下文分区

构造：

```text
A: 用户询问一个需要记忆补充的问题
A retrieval 慢
B: 用户切换话题
A retrieval 完成
```

断言：

- `original_user_query` 仍是 A。
- `initial_reply` 仍是 A 的第一次回复。
- `original_context.recent_history` 是 A 当时的快照。
- B 只出现在 `latest_context` 或 `newer_turns_since_original_request`。

### 3. Retrieval 乱序完成

构造：

```text
A retrieval 慢
B retrieval 快
B followup 先生成
A followup 后生成
```

断言：

- 两条 follow-up event 都有正确 `request_id`。
- 两条 follow-up turn 都有正确 parent metadata。
- pending/generated queue 不丢、不重复。

### 4. B 正在 streaming 时 A follow-up 到达

构造：

```text
B initial reply 仍在输出 delta
A followup 生成
```

断言：

- B 的 `/chat/stream` 只输出 B 的 initial events。
- A 的 follow-up 从 conversation follow-up stream 输出。
- A 的 follow-up event 不使用普通 initial `delta` 事件。

### 5. Retrieval 失败不污染 initial reply

构造：

```text
initial reply 成功
后台 retrieval 抛错
```

断言：

- initial assistant turn 已保存。
- request 不应表现为 initial reply failed。
- retrieval 状态为 failed，并记录错误。
- 不产生 follow-up delivery event。

### 6. No follow-up 不产生消息

构造：

```text
retrieval completed
generate_followup_reply 返回 no_followup
```

断言：

- 不保存 follow-up assistant turn。
- 不推送 follow-up event。
- followup 状态为 no_followup。

## 分阶段实施

### 阶段一：上下文快照与 prompt 分区

- 增加 `DialogueTurnSnapshot`。
- `_run_retrieval` 使用 snapshot。
- `_run_followup_decision` 改为接收 snapshot。
- Follow-up 输入拆成 `original_context` 与 `latest_context`。
- 新增并接入 `prompts/dialogue_followup_decision.md`。
- 补上下文隔离测试。

### 阶段二：二次回复独立交付

- `/chat/stream` 默认不再 `stream_followup=True`。
- 新增 generated follow-up queue。
- 新增 conversation follow-up SSE 或 generated follow-up polling API。
- Follow-up turn 写入 parent metadata。
- 补 stream 不串线测试。

### 阶段三：后台执行器与状态细化

- 将每次创建 daemon thread 改为受控 executor。
- 增加 retrieval/followup/delivery 细粒度状态。
- 后台失败不再覆盖 initial reply 成功状态。
- 补并发、乱序、失败隔离测试。

## 文件影响范围

预计涉及：

- `src/services/dialogue_service.py`
- `src/coordinator/request_coordinator.py`
- `src/coordinator/pending_queue.py`
- `src/api/routes.py`
- `src/api/schemas.py`
- `src/agents/dialogue_agent.py`
- `prompts/dialogue_agent.md`
- `prompts/dialogue_followup_decision.md`
- `tests/test_dialogue_service.py`
- `tests/test_api_routes.py`

本计划只描述修改方向，不在本文档中直接改变业务代码。
