# 流式回复完成与记忆检索解耦改造计划

> **状态：已实现，2026-05-18。** 本计划描述 `test-project` 的修改方案。目标是优化流式调用的尾部延迟，并让长连接请求不阻塞其它 HTTP 请求：用户已经收到完整初始回复后，不再继续等待 memory retrieval 才收到本轮完成信号；同时服务端默认使用多线程 HTTP server 承载并发请求。

## 背景

当前 `/chat/stream` 的核心流程在 `src/services/dialogue_service.py` 的 `handle_chat_message_stream` 中：

```text
创建 request
-> 保存 user turn
-> 读取 Model.md / User.md / compact history / recent history
-> yield meta
-> 流式生成 initial reply delta
-> 保存 assistant turn
-> 同步执行 memory retrieval
-> mark_retrieval_completed
-> yield done
```

这意味着首 token 已经可以很快返回，但 `done` 事件仍然被记忆检索拖住。只要 `_run_retrieval` 触发 LLM 判断，前端在模型文字已经完整显示后，仍可能继续处于 loading、占用麦克风交互状态，或者延迟进入下一轮操作。

用户当前确认采用两个配套优化方向：

- **调整流式调用内部的完成顺序**：让 initial reply 完成信号不等待 retrieval。
- **默认启用 `ThreadingHTTPServer`**：让一个长请求或 SSE 流式连接不阻塞其它 HTTP 请求。

本文仍不调整整体项目架构，不引入额外 Web 框架。

## 目标

- 保持 `/chat/stream` 的首 token 行为不退化。
- initial reply 的文本 delta 全部输出后，尽快向前端发送“回复已完成”的事件。
- memory retrieval 继续执行，但从用户可感知的 initial reply 完成路径中移走。
- 默认使用 `ThreadingHTTPServer` 承载 HTTP 请求，避免单个流式或语音请求阻塞其它请求。
- 保留 request coordinator、follow-up 队列和 retrieval 状态语义。
- 尽量兼容现有 SSE 客户端，避免一次性破坏 `done` 事件的基本含义。

## 非目标

- 不修改本计划以外的业务代码。
- 不重写 memory retrieval agent。
- 不改变非流式 `/chat` 的行为，除非后续明确要一起优化。
- 不引入 FastAPI、aiohttp、uvicorn、gunicorn 等新的服务框架。
- 不引入 Celery、Redis、数据库任务表等重型后台任务系统。
- 不改变 memory item、conversation turn、persona profile 的数据结构。

## 当前问题

### 1. `done` 语义混合了两件事

现在 `done` 同时表示：

- initial reply 已经完整生成。
- retrieval 已经同步完成。

对前端而言，真正影响“用户是否还能继续操作”的通常是第一件事；第二件事可以延后完成，用于 follow-up 或后续状态展示。

### 2. 检索 LLM 会增加流式尾部等待

`_run_retrieval` 会先读取 lightweight memory items。如果存在候选记忆，会调用 `retrieve_relevant_memory_ids`，该步骤可能再发起一次模型调用。

因此实际时间线可能变成：

```text
delta... delta...
模型 initial reply 结束
等待 retrieval LLM
等待 full memory items DB 读取
done
```

这段等待不会影响已显示的文字，但会影响前端状态机。

### 3. 错误归属不清晰

如果 initial reply 已经完整输出，但后续 retrieval 抛错，当前 generator 会进入 `except`，标记 request failed，并通过 SSE error 结束。前端可能会把它理解为“回复失败”，但用户实际上已经收到了可用的初始回复。

### 4. 单线程 HTTP server 会放大长请求影响

当前 `build_app` 默认使用标准库 `HTTPServer`。它一次只处理一个请求。只要某个请求正在等待模型流式输出、ASR final、TTS 或同步 chat，其它请求都可能排队。

对实时语音尤其明显：

```text
/chat/stream 或 /tools/voice-latency/finish-stream 正在长连接输出
-> /voice/live/chunk 可能排队
-> /voice/live/transcript 可能排队
-> /voice/live/finish 可能排队
```

即使流式请求自身首 token 很快，单线程 server 也可能让同一页面的其它请求出现卡顿。

## 推荐方案

采用两项配套改造：

- 用轻量后台线程将 retrieval 从流式完成路径中移出。
- 将默认 HTTP server 从 `HTTPServer` 切换为 `ThreadingHTTPServer`。

新的 `/chat/stream` 时间线：

```text
创建 request
-> 保存 user turn
-> 读取上下文
-> yield meta
-> 流式生成 initial reply delta
-> 保存 assistant turn
-> mark_initial_reply
-> mark_retrieval_pending
-> 启动后台 retrieval
-> yield done，retrieval_status=pending

后台线程：
-> 执行 _run_retrieval
-> mark_retrieval_completed
-> 请求进入 pending follow-up queue
```

这样用户看到完整回复后可以立刻收到完成事件，而 retrieval 的结果仍然会进入现有 coordinator 状态机。

新的 HTTP 承载方式：

```text
build_app 默认 server_class=ThreadingHTTPServer
-> 每个 HTTP 请求在线程中处理
-> 一个 SSE 长连接不阻塞其它请求
-> live ASR chunk/transcript/finish 可以和流式 reply 并发进入服务端
```

这项改动不改变任何 API route 的请求/响应形状，只改变请求处理的并发能力。

## SSE 事件设计

第一阶段建议保持已有事件名，减少客户端改造：

### `meta`

保持不变：

```json
{
  "request_id": "req_xxx",
  "turn_id": "turn_xxx",
  "conversation_id": "conv_xxx"
}
```

### `delta`

保持不变：

```json
{
  "delta": "文本增量"
}
```

### `done`

语义调整为：**initial reply 已经完成，retrieval 可能仍在后台进行。**

建议响应：

```json
{
  "request_id": "req_xxx",
  "turn_id": "turn_xxx",
  "conversation_id": "conv_xxx",
  "reply": "完整初始回复",
  "retrieval_status": "pending",
  "retrieved_memory_ids": []
}
```

兼容性说明：

- 保留 `retrieval_status` 字段。
- 当后台检索尚未完成时，值为 `pending`。
- `retrieved_memory_ids` 在 `done` 时可以为空数组。
- 前端如果只关心回复展示，可以继续以 `done` 作为关闭 loading 的信号。
- 前端如果关心检索结果，需要改为后续查询 `/followups/pending` 或新增 retrieval 状态查询接口。

### 可选新增事件：`retrieval_done`

第一阶段不强制新增，因为当前 generator 在发出 `done` 后通常会结束连接。如果后续希望同一条 SSE 连接继续承载 retrieval 结果，可以新增：

```json
{
  "request_id": "req_xxx",
  "retrieval_status": "completed",
  "retrieved_memory_ids": [1, 2]
}
```

但这会让连接持续到 retrieval 完成，和“尽快结束 initial reply”目标有冲突。因此本阶段更推荐后台完成后由现有 follow-up/pending 机制承接。

## 后台任务设计

### 新增内部 helper

建议在 `src/services/dialogue_service.py` 增加内部函数：

```python
def _run_retrieval_in_background(
    *,
    deps: DialogueDependencies,
    request_id: str,
    user_message: str,
    compact_history: str,
    recent_history: list[dict],
    user_profile: str,
) -> None:
    ...
```

内部启动 `threading.Thread(..., daemon=True)`。

线程执行逻辑：

```text
try:
  retrieval_status, retrieved_items = _run_retrieval(...)
  mark_retrieval_completed(request_id, retrieved_items)
except Exception as exc:
  mark_failed(request_id, reason)
```

注意点：

- `DialogueDependencies` 中的依赖必须可以跨线程使用。
- 当前真实依赖大多是函数和无状态 model client 构造逻辑，基本可行。
- 如果未来注入的 fake/client 不是线程安全的，测试需要覆盖。
- 后台线程应只处理 retrieval，不再写 initial reply。

### 状态流转

流式主线程：

```text
received
-> initial_reply_generated
-> retrieval_pending
-> yield done
```

后台线程成功：

```text
retrieval_pending
-> retrieval_completed
-> enqueue pending follow-up
```

后台线程失败：

```text
retrieval_pending
-> failed
```

这里有一个产品语义需要明确：initial reply 已经成功交付，但 retrieval 失败时，整个 request 是否应该是 `failed`？

建议第一阶段沿用现有 `mark_failed`，保持实现简单；后续如果要更准确，可以新增 `retrieval_failed` 状态，避免把“初始回复成功但检索失败”显示成整轮失败。

## 对现有接口的影响

### `/chat/stream`

会改变 `done.data.retrieval_status`：

- 旧行为：多数情况下返回 `completed`。
- 新行为：返回 `pending`，后台完成后 coordinator 状态变为 `retrieval_completed`。

### `/followups/pending`

保持不变。后台 retrieval 完成后，`mark_retrieval_completed` 仍会把 request 加入 pending follow-up queue。

### `/followups/{request_id}/run`

保持不变。调用方需要等 request 进入 `retrieval_completed` 后再运行 follow-up。

### `/chat`

本计划不改非流式 `/chat`。它仍会同步等待 retrieval 后返回。

### HTTP server 默认实现

`build_app` 的默认 `server_class` 会从 `HTTPServer` 改成 `ThreadingHTTPServer`。

对调用方的影响：

- 继续可以显式传入自定义 `server_class`，测试可按需使用 fake server。
- 现有 route 和 handler 不变。
- 并发请求会进入不同线程，因此测试 fake、注入依赖和共享状态需要考虑线程安全。

## 前端建议

前端对 `/chat/stream` 的状态机建议调整为：

```text
收到 meta:
  记录 request_id / turn_id

收到 delta:
  追加显示文本

收到 done:
  停止“正在生成回复”的 UI
  允许用户继续输入或结束本轮语音交互
  如果 retrieval_status=pending，不阻塞用户

后续:
  周期性或按需调用 /followups/pending
  如果有当前 request_id 的 follow-up，再调用 /followups/{request_id}/run
```

如果是语音页面，`done` 到达后即可释放“模型正在说话/思考”的状态；TTS 是否另行等待，取决于语音播放链路是否仍需要完整文本。

## 实施步骤

### Task 1: 增加 ThreadingHTTPServer 默认行为测试

**文件：**

- 修改：`tests/test_api_routes.py`

测试要点：

- `build_app(... )` 默认使用 `ThreadingHTTPServer` 或其兼容子类。
- 仍允许调用方传入自定义 `server_class`。
- 不需要真正启动网络端口；优先通过 monkeypatch/fake server class 验证 `build_app` 选择的默认类型。

### Task 2: 修改默认 HTTP server

**文件：**

- 修改：`src/api/routes.py`

建议改动：

- 从 `http.server` 引入 `ThreadingHTTPServer`。
- 将 `build_app` 的默认参数从：

```python
server_class: Callable[..., HTTPServer] = HTTPServer
```

改为：

```python
server_class: Callable[..., HTTPServer] = ThreadingHTTPServer
```

注意：

- 保持参数名和可注入行为不变。
- 如类型检查不舒服，可使用 `type[HTTPServer]` 或保留当前 `Callable[..., HTTPServer]`。

### Task 3: 增加流式 pending 行为测试

**文件：**

- 修改：`tests/test_dialogue_service.py`

测试要点：

- 使用 fake `generate_initial_reply_stream` 产出多个 delta。
- 使用 fake `retrieve_relevant_memory_ids` 模拟慢检索。
- 验证 stream 中 `delta` 后能够收到 `done`。
- 验证 `done.data.retrieval_status == "pending"`。
- 验证 `done` 不需要等检索函数返回。

如果单元测试不适合真实等待线程，可以用 `threading.Event` 控制：

```text
retrieval_started Event 用于确认后台线程已启动
allow_retrieval_finish Event 用于阻塞检索
主测试在 allow_retrieval_finish set 之前读取到 done
```

### Task 4: 增加后台 retrieval 完成测试

**文件：**

- 修改：`tests/test_dialogue_service.py`

测试要点：

- `done` 先返回 `pending`。
- 放行后台 retrieval。
- 最终 `request_coordinator.get_request(request_id)` 状态变成 `retrieval_completed`。
- `get_pending_followup_requests()` 包含该 request。

### Task 5: 修改 `handle_chat_message_stream`

**文件：**

- 修改：`src/services/dialogue_service.py`

建议改动：

- 在保存 initial assistant turn 后调用 `mark_initial_reply`。
- 调用 `mark_retrieval_pending`。
- 启动 `_run_retrieval_in_background(...)`。
- 立即 yield `done`，其中：
  - `retrieval_status` 为 `RETRIEVAL_STATUS_PENDING`
  - `retrieved_memory_ids` 为 `[]`

### Task 6: 增加后台 helper

**文件：**

- 修改：`src/services/dialogue_service.py`

建议实现：

- 使用标准库 `threading.Thread`。
- daemon 线程即可，符合当前 in-memory coordinator 的 MVP 形态。
- 捕获异常并调用 `request_coordinator.mark_failed`。
- helper 保持私有，不暴露到 public service API。

### Task 7: 更新 SSE API 测试

**文件：**

- 修改：`tests/test_api_routes.py`

测试要点：

- `/chat/stream` 的 SSE `done` 包含 `retrieval_status=pending`。
- 不再断言 stream done 中包含已完成的 memory ids。
- 如已有测试依赖 `completed`，改成查询 coordinator 后验证后台结果。

### Task 8: 文档同步

**文件：**

- 可选修改：`docs/api_contracts.md`
- 可选修改：`docs/design-doc.md`

需要说明：

- `/chat/stream` 的 `done` 表示 initial reply 完成。
- retrieval 可能在后台继续执行。
- follow-up 仍通过 pending follow-up 机制触发。
- 默认 HTTP server 使用 `ThreadingHTTPServer`，长连接不会阻塞其它请求。

## 验收标准

- `build_app` 默认使用 `ThreadingHTTPServer`。
- 显式传入自定义 `server_class` 的测试仍然通过。
- `/chat/stream` 首 token 行为不退化。
- initial reply delta 全部输出后，即使 retrieval 被阻塞，客户端仍能收到 `done`。
- `done.data.retrieval_status == "pending"`。
- 后台 retrieval 成功后，request 状态变成 `retrieval_completed`。
- 后台 retrieval 成功后，request 会出现在 `/followups/pending`。
- 后台 retrieval 失败不会影响已经发出的 initial reply；request 会被记录为失败或后续定义的 retrieval failure 状态。
- 非流式 `/chat` 行为保持不变。

## 风险与权衡

### 后台线程生命周期

当前 coordinator 是进程内存储，后台线程也是进程内任务；如果进程退出，pending retrieval 会丢失。这与当前 MVP 的 in-memory 设计一致，可以接受。

### HTTP handler 并发执行

切换到 `ThreadingHTTPServer` 后，多个 `ChatRequestHandler` 会并发执行。当前主要共享状态已有基本保护：

- `request_coordinator` 使用 `RLock`。
- `LiveAsrSessionManager` 使用 lock 管理 session 字典。
- SQLite helper 每次创建独立 connection。
- persona 文件写入使用 `_write_lock`。

仍需注意：

- 测试 fake 依赖如果在多线程场景共享可变状态，需要自行加锁或用 `threading.Event` 控制。
- 如果未来注入的 `model_client` 维护非线程安全连接，需要改为每线程 client、连接池，或在调用层加锁。

### 依赖线程安全

如果未来 `DialogueDependencies.model_client` 是带连接池或内部状态的对象，需要确认它能跨线程使用。保守做法是在后台线程中只调用现有函数依赖，让模型 client 按当前逻辑自行构造；更高性能做法是显式注入线程安全 client。

### `done` 语义变化

已有客户端如果把 `done.retrieval_status == completed` 当作强假设，需要同步调整。建议在文档中明确：流式 `done` 是 initial reply 完成，不是 retrieval 完成。

### 错误状态语义

初始回复已成功但 retrieval 失败时，当前可先标记 `failed`；这可能让后续状态看起来像整轮失败。若产品上需要区分，后续应新增：

```text
retrieval_failed
```

并让 follow-up 路径识别该状态。

## 后续可选优化

- 为 request 增加 `retrieval_error` 字段，避免覆盖 initial reply 成功状态。
- 新增 `GET /requests/{request_id}`，让前端可查询 retrieval 状态和 memory ids。
- 新增 `retrieval_done` SSE 事件，但只在客户端明确需要保持连接时启用。
- 非流式 `/chat` 也采用 initial reply 先返回、retrieval 后台完成的语义。
- 将后台任务从 daemon thread 演进为持久化任务队列。
- 如果并发规模继续增大，评估迁移到生产级 ASGI/WSGI server。
