# Onboarding 与普通聊天统一交互流架构计划

> **状态：计划中，2026-05-23。** 本计划描述目标态架构：重做对外暴露的交互接口，以统一的 interaction stream 承载文本输入、语音流式输入、文字流式输出和语音流式播放；后端按 `workflow` 分流到 `chat` 或 `onboarding`。本计划不要求兼容旧接口，不采用伪流式降级，不以 MVP 缩减能力为目标。

## 已确认决策

- 可以大改当前暴露给前端的接口。
- 不需要兼容旧的 `/api/text-chat-stream`、`/api/local-mic/finish-stream`、`/api/robot-mic/stop-stream` 等接口形状。
- 不接受“先生成完整 onboarding reply，再切片成 delta”的伪流式方案。
- Onboarding 的前端体验需要和普通聊天一致：
  - 支持文本输入。
  - 支持语音流式输入。
  - 支持 LLM 文字真实流式输出。
  - 支持 TTS 语音真实流式输出。
  - 支持文本流和语音流并发推进。
- `chat` workflow 继续支持普通回复、记忆检索和二次回复。
- `onboarding` workflow 只负责五阶段引导和资料采集，不触发记忆检索，不触发二次回复。
- 所有新协议从 `workflow` 维度设计，不让 onboarding 假装成 chat。

## 背景

当前普通聊天链路已经具备较完整的流式能力：

```text
文本输入
-> /chat/stream
-> LLM token delta
-> 分句/分段 TTS synthesize_stream
-> SSE delta/audio/done
-> 后台 memory retrieval
-> followups/stream 二次回复
```

语音输入链路：

```text
live ASR start
-> live ASR chunk
-> voice live finish-stream
-> transcript
-> /chat/stream
-> LLM token delta
-> TTS audio chunks
-> done
```

新写的 onboarding workflow 当前是隔离状态机：

```text
start_onboarding
-> handle_onboarding_message
-> run_onboarding_step 一次性 JSON
-> 保存 session
-> 返回完整 reply
```

它已经具备五阶段状态、required slots gate、SQLite session、final payload 等核心能力，但当前输出不是流式的。若只在 service 返回完整 reply 后再拆成若干 `delta`，前端看起来像流式，实际上不具备普通聊天已经实现的模型 token 流和低延迟 TTS 流能力，因此不满足本计划目标。

## 目标

- 建立统一 interaction API，替代旧的 chat/voice/onboarding 分散接口。
- 文本和语音输入只在 input adapter 层不同，一旦得到文本消息，进入同一套 interaction run。
- `workflow=chat` 和 `workflow=onboarding` 使用统一 SSE 事件协议。
- `workflow=onboarding` 具备真实 LLM token streaming。
- `workflow=onboarding` 的 TTS 与文本 delta 并发生成和播放，能力与普通聊天一致。
- Onboarding 的结构化状态更新和用户可见流式回复保持一致，不出现“前端已经说完，后端状态又判定成另一件事”的分裂。
- Onboarding 不触发 memory retrieval、不进入 request coordinator 的 retrieval/follow-up 状态、不连接 follow-up SSE。
- 允许删除或替换旧接口，前端按新协议重接。

## 非目标

- 不做伪流式回复。
- 不为了兼容旧前端保留旧协议语义。
- 不在 onboarding 阶段直接写 `User.md`。
- 不在 onboarding 阶段直接写 `memory_items`。
- 不把 onboarding message 写成普通 chat request。
- 不让 onboarding 触发二次回复。
- 不要求本计划中实现跨进程任务持久化。
- 不引入外部消息队列，除非后续负载证明进程内队列不够。

## 总体架构

目标架构分三层：

```text
Input Layer
- text
- local mic live ASR
- robot mic live ASR
- auto voice VAD utterance

Interaction Orchestrator
- 创建 interaction run
- 根据 workflow 选择 handler
- 统一输出 SSE event
- 统一调度 TTS stream
- 统一处理 playback_done/error

Workflow Handler
- chat workflow
- onboarding workflow
```

核心原则：

```text
输入能力统一
输出协议统一
workflow 状态隔离
```

普通聊天和 onboarding 共享的是 transport 与 streaming 能力，不共享业务状态机。

## 新对外接口

### 1. 创建 interaction session

```text
POST /interaction/sessions
```

请求：

```json
{
  "workflow": "onboarding",
  "conversation_id": "reachy-mini-voice",
  "tts_enabled": true,
  "input_mode": "text"
}
```

响应：

```json
{
  "interaction_session_id": "isess_xxx",
  "workflow": "onboarding",
  "conversation_id": "reachy-mini-voice",
  "onboarding_session_id": "onb_xxx",
  "stage": 1,
  "stage_name": "认识你",
  "status": "active"
}
```

对于 `workflow=chat`，可以不返回 `onboarding_session_id`。

### 2. 文本输入并返回流式输出

```text
POST /interaction/runs/text-stream
```

请求：

```json
{
  "interaction_session_id": "isess_xxx",
  "workflow": "onboarding",
  "message": "我叫王叔，今年七十多了",
  "tts_enabled": true
}
```

响应：

```text
Content-Type: text/event-stream; charset=utf-8
```

事件：

```text
meta
delta
audio
state_delta
done
playback_done
error
```

### 3. live ASR 输入

```text
POST /interaction/live/start
POST /interaction/live/chunk
POST /interaction/live/finish-stream
POST /interaction/live/abort
GET  /interaction/live/transcript
```

`finish-stream` 请求：

```json
{
  "interaction_session_id": "isess_xxx",
  "workflow": "onboarding",
  "live_session_id": "live_xxx",
  "tts_enabled": true
}
```

`finish-stream` 内部流程：

```text
finish ASR
-> emit transcript
-> 将 transcript 作为本轮 message
-> 进入 workflow handler
-> emit meta/delta/audio/state_delta/done
```

语音输入不得为 onboarding 调用普通聊天接口。所有语音输入统一通过 interaction orchestrator 分流。

### 4. interaction 状态查询

```text
GET /interaction/sessions/{interaction_session_id}
```

响应：

```json
{
  "interaction_session_id": "isess_xxx",
  "workflow": "onboarding",
  "conversation_id": "reachy-mini-voice",
  "onboarding_session_id": "onb_xxx",
  "stage": 3,
  "stage_name": "日常生活",
  "status": "active",
  "onboarding_complete": false,
  "collected": {}
}
```

## 统一 SSE 事件协议

### `transcript`

语音输入完成 ASR 后输出：

```json
{
  "workflow": "onboarding",
  "interaction_session_id": "isess_xxx",
  "run_id": "irun_xxx",
  "transcript": "我平时一个人住",
  "is_final": true
}
```

文本输入可以不发 `transcript`，也可以发同形事件以简化前端。

### `meta`

每轮输出开始：

```json
{
  "workflow": "onboarding",
  "interaction_session_id": "isess_xxx",
  "run_id": "irun_xxx",
  "conversation_id": "reachy-mini-voice",
  "onboarding_session_id": "onb_xxx",
  "stage": 2,
  "stage_key": "family",
  "stage_name": "家庭情况"
}
```

普通聊天：

```json
{
  "workflow": "chat",
  "interaction_session_id": "isess_xxx",
  "run_id": "irun_xxx",
  "conversation_id": "reachy-mini-voice",
  "request_id": "req_xxx"
}
```

### `delta`

用户可见文本的真实模型流：

```json
{
  "workflow": "onboarding",
  "run_id": "irun_xxx",
  "delta": "王叔，"
}
```

`delta` 必须来自 LLM streaming，不允许由完整 reply 后处理切片产生。

### `audio`

TTS 流式音频：

```json
{
  "workflow": "onboarding",
  "run_id": "irun_xxx",
  "playback_key": "onboarding-tts-irun_xxx",
  "audio_base64": "...",
  "sample_rate": 24000,
  "audio_format": "pcm",
  "chunk_index": 0,
  "segment_index": 0
}
```

### `state_delta`

Workflow 状态变化。onboarding 用于阶段进展、缺失字段、完成状态等：

```json
{
  "workflow": "onboarding",
  "run_id": "irun_xxx",
  "stage": 2,
  "stage_name": "家庭情况",
  "status": "active",
  "missing_required_slots": ["children"]
}
```

普通聊天可以用于 retrieval 状态：

```json
{
  "workflow": "chat",
  "run_id": "irun_xxx",
  "request_id": "req_xxx",
  "retrieval_status": "pending"
}
```

### `done`

Onboarding：

```json
{
  "workflow": "onboarding",
  "interaction_session_id": "isess_xxx",
  "run_id": "irun_xxx",
  "onboarding_session_id": "onb_xxx",
  "conversation_id": "reachy-mini-voice",
  "stage": 3,
  "stage_key": "daily",
  "stage_name": "日常生活",
  "status": "active",
  "reply": "王叔，那你平时一天大概怎么过？",
  "onboarding_complete": false,
  "profile_updated": false,
  "collected": {},
  "missing_required_slots": []
}
```

Onboarding 完成：

```json
{
  "workflow": "onboarding",
  "interaction_session_id": "isess_xxx",
  "run_id": "irun_xxx",
  "onboarding_session_id": "onb_xxx",
  "status": "completed",
  "reply": "我已经了解得差不多了，以后聊天我会尽量记住这些。",
  "onboarding_complete": true,
  "profile_updated": false,
  "final_payload": {
    "kind": "onboarding_collected_profile",
    "status": "ready_for_downstream_agent"
  }
}
```

普通聊天：

```json
{
  "workflow": "chat",
  "interaction_session_id": "isess_xxx",
  "run_id": "irun_xxx",
  "request_id": "req_xxx",
  "conversation_id": "reachy-mini-voice",
  "reply": "完整初始回复",
  "retrieval_status": "pending"
}
```

## Onboarding 真实流式方案

Onboarding 的难点是同时满足：

```text
用户可见回复真实流式
结构化 collected/state 可靠
阶段切换和 required slots gate 正确
```

不允许的方案：

```text
一次性 JSON agent
-> 得到完整 reply
-> 按字符或句子切成 delta
```

推荐采用 **双通道流式 agent 协议**。

### Onboarding Streaming Agent 输出协议

新增 onboarding streaming agent，模型流式输出事件，而不是一次性 JSON：

```jsonl
{"type":"reply_delta","text":"王叔，"}
{"type":"reply_delta","text":"那我以后就这么称呼你。"}
{"type":"reply_delta","text":"你平时住在哪个城市呀？"}
{"type":"control","stage_complete":false,"onboarding_complete":false,"collected_patch":{"preferred_name":"王叔","age_or_life_stage":"七十多岁"},"next_stage":1,"missing_required_slots":["location"],"summary":"用户叫王叔，七十多岁。","confidence":0.86}
```

服务端行为：

- 收到 `reply_delta` 时立刻 emit SSE `delta`。
- `reply_delta` 同时进入 TTS segmenter，按标点或短句触发 `synthesize_stream()`。
- 收到 `control` 后执行 service 层 required slots gate。
- 如果模型控制信息与 service gate 冲突，以 service gate 为准。
- 最终状态通过 `state_delta` 和 `done` 输出。

### 控制信息可靠性约束

模型输出的 `control` 不是最终权威。service 层仍然负责：

- merge `collected_patch`。
- 检查 required slots。
- 判定是否允许 stage transition。
- 生成最终 `final_payload`。
- 保存 session。

如果模型流式回复已经自然过渡到下一阶段，但 service gate 判定缺字段，则 service 必须在本轮末尾用 `state_delta` 和 `done` 记录真实状态。为避免这种冲突，prompt 必须要求：

```text
在确定当前阶段 required_slots 都有值前，不要在用户可见回复中明确进入下一阶段。
如果需要追问缺口，只追问一个最关键缺口。
```

### 更稳的替代方案：先轻量控制，再真实流式回复

如果双通道流式 agent 的解析稳定性不足，可以采用“控制前置 + 回复真流式”：

```text
1. control agent 快速输出结构化 decision
2. service 执行 required slots gate，确定本轮目标 reply_intent
3. reply agent 根据确定后的 state 真实 streaming 生成用户可见文本
4. TTS 与 reply token 并发
5. 保存 session
```

该方案不是伪流式，因为用户可见文本仍由 LLM streaming 产生，不是完整 reply 后切片。代价是首 token 前多一次轻量结构化调用。若对可靠性要求高，优先采用该方案。

本计划推荐实现路径：

```text
第一选择：控制前置 + 回复真流式
第二选择：双通道流式 agent
禁止：完整 reply 后切片伪流式
```

## TTS 并发策略

Onboarding 与普通聊天使用同一套 TTS worker 机制：

```text
LLM delta stream
-> segment buffer
-> 以中文标点、长度和时间阈值切分 segment
-> TTS worker 调用 synthesize_stream(segment)
-> emit audio chunks
```

要求：

- LLM 尚未完整结束时，TTS 可以开始合成已完成 segment。
- TTS 音频以 `audio` SSE 输出。
- 每个 interaction run 使用独立 `playback_key`。
- 文本和音频事件允许交错。
- TTS 失败不回滚 onboarding 状态，只发 `error` 或 `state_delta` 标记 playback error。

## Chat Workflow

`workflow=chat` 使用现有普通聊天能力，但接入统一 interaction orchestrator：

```text
interaction run
-> 保存 user turn
-> generate_initial_reply_stream
-> emit delta
-> TTS worker emit audio
-> 保存 assistant turn
-> emit done retrieval_status=pending
-> 后台 retrieval
-> follow-up stream 独立交付
```

仍保留：

- request_id。
- conversation turns。
- retrieval pending/completed。
- follow-up SSE。
- 二次回复。

## Onboarding Workflow

`workflow=onboarding` 不使用 chat request coordinator：

```text
interaction run
-> 获取 onboarding session
-> append user onboarding turn
-> control agent 判断 slots/stage target
-> service gate 确定本轮目标
-> reply agent 真实 streaming 生成 reply
-> emit delta/audio
-> 保存 assistant onboarding turn
-> update onboarding session
-> emit state_delta/done
```

不做：

- 不创建 chat request。
- 不进入 retrieval pending。
- 不写 memory_items。
- 不连接 followups/stream。
- 不创建二次回复。

完成后：

```text
stage 5 complete
-> build final_payload
-> session status completed
-> done onboarding_complete=true
```

## 数据模型

### interaction_sessions

建议新增表：

```sql
CREATE TABLE interaction_sessions (
    interaction_session_id TEXT PRIMARY KEY,
    workflow TEXT NOT NULL,
    conversation_id TEXT,
    onboarding_session_id TEXT,
    status TEXT NOT NULL,
    input_mode TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
```

### interaction_runs

建议新增表：

```sql
CREATE TABLE interaction_runs (
    run_id TEXT PRIMARY KEY,
    interaction_session_id TEXT NOT NULL,
    workflow TEXT NOT NULL,
    input_mode TEXT,
    transcript TEXT,
    reply TEXT,
    status TEXT NOT NULL,
    error TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    completed_at DATETIME
);
```

`chat` run 可关联 `request_id`。`onboarding` run 可关联 `onboarding_session_id` 和 stage。

该数据层用于统一前端调试和恢复状态，不替代现有 conversation turns 或 onboarding_sessions。

## 前端改造

### 删除旧发送路径的业务假设

前端不再以函数名区分 chat/onboarding：

```text
sendManualText
stopLocalRecordingAndReply
sendRecording
```

应改成：

```text
sendTextInteraction
finishVoiceInteraction
renderInteractionStream
```

当前 workflow 由 UI 状态决定：

```js
const interactionState = {
  workflow: "chat", // "chat" | "onboarding"
  interactionSessionId: "",
  onboardingSessionId: "",
};
```

### timeline message 类型

统一消息基础结构：

```js
{
  id,
  workflow,
  role,
  kind,
  content,
  status,
  runId,
  createdAt
}
```

Chat assistant：

```js
{
  workflow: "chat",
  kind: "initial",
  retrievalStatus: "pending"
}
```

Onboarding assistant：

```js
{
  workflow: "onboarding",
  kind: "onboarding",
  stage: 2,
  stageName: "家庭情况",
  retrievalStatus: "idle"
}
```

Onboarding 消息不得显示“记忆检索中”。

### follow-up 连接规则

```text
workflow=chat
-> 可以连接 followups/stream

workflow=onboarding
-> 必须关闭 followups/stream
```

切换 workflow 时：

- 从 chat 切到 onboarding：关闭 follow-up SSE。
- 从 onboarding 切回 chat：按 conversation_id 重新连接 follow-up SSE。

### 语音输入规则

语音录音 start/chunk 对前端保持统一：

```text
start live input
chunk live input
finish interaction stream
```

finish 时只发送到 `/interaction/live/finish-stream`，由后端按 workflow 分流。

前端不再直接判断：

```text
chat -> /voice/live/finish-stream
onboarding -> /voice/live/finish-transcript
```

这个判断属于后端 orchestrator。

## Reachy 代理层改造

注：目前本项目只设计后端，这个部分是前端，只把修改建议列出来，不进行实际的修改

`reachy_dialogue_app` 应从“chat proxy”改为“interaction proxy”。

职责：

- 暴露统一 `/interaction/*`。
- 管理 service_url。
- 管理机器人麦克风和本机麦克风输入。
- 代理 live ASR。
- 透传或包装 SSE。
- 管理机器人播放队列。
- 按 workflow 决定是否触发表情/动作。

行为触发建议：

- `chat` workflow：保留现有从回复中解析 `[emo:*]`、`[act:*]` 的能力。
- `onboarding` workflow：默认不触发动作，或只允许非常温和的表情；避免引导阶段因结构化询问触发不必要动作。

## test-project 改造

### 新增 interaction API

主服务新增：

```text
POST /interaction/sessions
POST /interaction/runs/text-stream
POST /interaction/live/start
POST /interaction/live/chunk
POST /interaction/live/finish-stream
POST /interaction/live/abort
GET  /interaction/live/transcript
GET  /interaction/sessions/{id}
```

`src/api/routes.py` 只暴露 interaction API。旧 `/chat/stream`、`/voice/live/finish-stream` 可以删除或保留内部 helper，但不作为新前端依赖。

### 新增 Onboarding Streaming Service

建议新增：

```text
src/services/interaction_service.py
src/services/onboarding_streaming_service.py
src/agents/onboarding_streaming_agent.py
```

职责：

- `interaction_service`：创建 session/run，按 workflow 调度，统一 SSE。
- `onboarding_streaming_service`：onboarding run 状态机、控制前置、reply streaming、TTS 调度。
- `onboarding_streaming_agent`：结构化 control agent 和 streaming reply agent。

### 保留现有 onboarding_service

现有 `src/services/onboarding_service.py` 可继续作为状态机核心，但需要拆出更细粒度函数：

```python
prepare_onboarding_step(...)
apply_onboarding_control(...)
complete_onboarding_step(...)
```

避免 streaming service 只能调用一次性 `handle_onboarding_message()`。

## 自动语音

自动语音也应接入 interaction session。

启动：

```json
{
  "workflow": "onboarding",
  "interaction_session_id": "isess_xxx",
  "input_mode": "local",
  "tts_enabled": true
}
```

每个 VAD utterance：

```text
finish ASR
-> /interaction/runs/text-stream 或内部 run
-> emit transcript/meta/delta/audio/done 到 auto voice event stream
```

要求：

- Auto voice 不直接调用 `/chat/stream`。
- Auto voice 不直接调用 `/onboarding/message`。
- Auto voice 只创建 interaction run。

## 阶段计划

### 阶段一：接口与 orchestrator 重构

- 新增 interaction session/run 数据模型。
- 新增 `/interaction/sessions`。
- 新增 `/interaction/runs/text-stream`。
- `workflow=chat` 先接入现有 `handle_chat_message_stream` 逻辑。
- 前端切到 interaction API。
- 删除前端对旧 chat endpoint 的直接依赖。

验收标准：

- 普通文本聊天能力不下降。
- 普通聊天仍有真实 delta、真实 audio、done、follow-up。
- 前端不再依赖旧 `/api/text-chat-stream`。

### 阶段二：Onboarding 控制前置 + 真实流式回复

- 新增 onboarding control agent。
- 新增 onboarding streaming reply agent。
- 拆分 onboarding service 的状态机函数。
- `workflow=onboarding` 文本输入接入真实 LLM streaming。
- TTS 与 onboarding reply streaming 并发。

验收标准：

- Onboarding 文本回复的 `delta` 来自 LLM stream。
- 不能出现完整 reply 后切片。
- Onboarding 能推进阶段。
- Required slots gate 正确。
- Onboarding 不触发 retrieval/follow-up。

### 阶段三：统一语音流式输入

- 新增 `/interaction/live/*`。
- 本机麦克风和机器人麦克风都只使用 interaction live API。
- `workflow=chat` 语音进入 chat workflow。
- `workflow=onboarding` 语音进入 onboarding workflow。
- 前端移除对旧 voice finish endpoint 的直接判断。

验收标准：

- 语音输入在 chat 和 onboarding 下都能工作。
- Onboarding 语音输入不会误入 `/chat/stream`。
- Chat 语音输入仍有记忆检索和 follow-up。
- Onboarding 语音输入仍有真实文本流和真实 TTS 流。

### 阶段四：前端状态与调试面板重构

- timeline 引入 `workflow`。
- inspector 显示 interaction session/run。
- Onboarding 显示 stage/status/final_payload。
- Chat 显示 request/retrieval/follow-up。
- 切换 workflow 时正确连接或关闭 follow-up。

验收标准：

- Onboarding 不显示“记忆检索中”。
- Chat 保持现有 request/follow-up 调试能力。
- Onboarding 完成后能切回 chat。

### 阶段五：自动语音接入 interaction

- Auto voice start 增加 `interaction_session_id`。
- Auto voice utterance 只创建 interaction run。
- 支持 `workflow=chat` 和 `workflow=onboarding`。

验收标准：

- 自动语音 chat 不退化。
- 自动语音 onboarding 可连续推进阶段。
- wake gate、半双工和 onboarding completion 状态不冲突。

## 测试计划

### 单元测试

- interaction session/run create/get/update。
- `workflow` 参数校验。
- `chat` workflow 输出 `meta/delta/audio/done`。
- `onboarding` workflow 输出真实 streaming delta。
- Onboarding control agent 输出结构验证。
- Onboarding required slots gate。
- Onboarding 完成后生成 `final_payload`。
- TTS worker 在 delta 未结束时可以开始输出 audio。

### 集成测试

- 文本 chat interaction。
- 文本 onboarding interaction。
- 语音 chat interaction。
- 语音 onboarding interaction。
- Onboarding 不触发 follow-up。
- Chat 触发 retrieval pending 和 follow-up。
- TTS 失败不回滚 onboarding session。
- Onboarding control agent 失败时返回 error 并保留 run 状态。

### 前端验证

- 文本 chat。
- 文本 onboarding。
- 本机麦克风 chat。
- 本机麦克风 onboarding。
- 机器人麦克风 chat。
- 机器人麦克风 onboarding。
- TTS 开启/关闭。
- 工作流切换。
- onboarding 完成后查看 final payload。

## 风险与缓解

### 风险一：真实流式 onboarding 与结构化状态不一致

缓解：

- 优先采用“控制前置 + 回复真流式”。
- service gate 永远是最终权威。
- Prompt 禁止在 slots 未齐前明确过渡到下一阶段。
- 测试覆盖控制结果与 service gate 冲突场景。

### 风险二：首 token 延迟增加

控制前置会增加一次轻量结构化调用。

缓解：

- control prompt 保持短输入、低温度、JSON 输出。
- 控制调用只做状态判断，不生成长文本。
- 如果延迟不可接受，再评估双通道流式 agent。

### 风险三：统一接口一次性重构影响普通聊天

缓解：

- 先让 `workflow=chat` 接入现有成熟逻辑。
- 以 interaction API 包裹现有 service，而不是同时重写 chat internals。
- 增加 chat 回归测试。

### 风险四：TTS 与文本流乱序

缓解：

- 每个 run 使用独立 `playback_key`。
- audio 事件带 `segment_index` 和 `chunk_index`。
- 前端 playback scheduler 按 segment/chunk 排序。

### 风险五：自动语音状态组合复杂

缓解：

- 自动语音最后接入。
- 手动文本和手动语音先跑通。
- Auto voice 只面向 interaction run，不直接调用具体 workflow endpoint。

## 推荐结论

推荐采用目标态重构：

```text
废弃旧前端接口语义
-> 新增统一 interaction API
-> input_mode 只描述输入来源
-> workflow 只描述业务链路
-> chat 与 onboarding 共享真实 streaming 能力
-> onboarding 不复用 chat 的记忆检索和二次回复语义
```

Onboarding 的回复必须使用真实 LLM streaming，并与 TTS streaming 并发。完整 reply 后切片的伪流式方案不满足本计划目标，不应作为实现路径。
