# API 与跨模块 JSON 约定

本文档列出 Person 3 在 `src/api/`、`src/services/`、`src/coordinator/` 与
`src/agents/memory_curator.py` 中实现的对外契约，供另外两位开发者对齐。

> 任何字段调整请先更新本文档并同步其他两位开发者。

---

## 1. HTTP API 总览

| Method | Path | 描述 |
| --- | --- | --- |
| `POST` | `/chat` | 处理一条用户消息，返回首条回复 |
| `POST` | `/chat/stream` | 处理一条用户消息，以 SSE 流式返回首条回复 |
| `POST` | `/voice/chat` | 处理一段用户语音，返回识别文本、首条回复和可选语音 |
| `POST` | `/voice/live/start` | 开始实时语音识别会话 |
| `POST` | `/voice/live/chunk` | 向实时语音识别会话提交一段 PCM 音频 |
| `GET`  | `/voice/live/transcript` | 查询实时语音识别会话的最新字幕 |
| `POST` | `/voice/live/finish` | 结束实时语音识别会话，并用最终文本生成回复和可选语音 |
| `POST` | `/voice/live/abort` | 中止实时语音识别会话，不生成回复 |
| `POST` | `/tools/voice-latency/finish-stream` | 结束实时语音识别会话，并以 SSE 流式返回延迟测试回复 |
| `GET`  | `/followups/pending` | 列出等待 followup 决策的请求 |
| `POST` | `/followups/{request_id}/run` | 触发某个请求的二次回复判断 |
| `POST` | `/memory/curate` | 对一段对话运行 Memory Curator 并落库 |
| `POST` | `/memory/profile/refresh` | 运行 Profile Consolidator 并按需更新 `User.md` |
| `GET`  | `/healthz` | 健康检查 |

请求和响应均为 `application/json; charset=utf-8`。错误统一返回：

```json
{"error": {"message": "human readable reason"}}
```

错误状态码：

- `400`：请求体不合 schema 或参数非法。
- `404`：路径不存在或 `request_id` 未知。
- `409`：状态机不允许该操作（例如 followup 前还没完成 retrieval）。
- `500`：服务器内部异常。

---

## 2. `POST /chat`

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
  "request_id": "req_<uuid4_hex>",
  "turn_id": "turn_<uuid4_hex>",
  "conversation_id": "string",
  "reply": "string",
  "retrieval_status": "completed | pending | failed",
  "retrieved_memory_ids": [int]
}
```

约定：

- `request_id` / `turn_id` 由 Request Coordinator 生成，绑定整次请求。
- 非流式 `/chat` 当前同步完成 retrieval，因此成功响应中的
  `retrieval_status` 通常为 `completed`。
- 成功后请求状态进入 `retrieval_completed`，会出现在
  `/followups/pending` 列表里，直到调用 `/followups/{request_id}/run`。

---

## 3. `POST /chat/stream`

请求与 `/chat` 相同：

```json
{
  "conversation_id": "string",
  "message": "string"
}
```

响应为 `text/event-stream; charset=utf-8`，事件顺序：

```text
event: meta
data: {"request_id":"req_<uuid4_hex>","turn_id":"turn_<uuid4_hex>","conversation_id":"string"}

event: delta
data: {"delta":"回复片段"}

event: done
data: {"request_id":"req_<uuid4_hex>","turn_id":"turn_<uuid4_hex>","conversation_id":"string","reply":"完整回复","retrieval_status":"pending","retrieved_memory_ids":[]}
```

约定：

- `meta` 会在模型开始生成前返回，便于客户端提前绑定 `request_id` 与
  `turn_id`。
- `delta` 可出现多次，每次只包含新增文本片段。
- `done` 表示首条回复已经完整生成并保存，`data` 包含聚合后的完整
  `reply`。
- 流式接口的 retrieval 默认在后台继续执行，因此 `done.data.retrieval_status`
  通常为 `pending`，`retrieved_memory_ids` 在该事件中为空数组。
- 后台 retrieval 完成后，请求会进入 `/followups/pending`，客户端可按需
  调用 `/followups/{request_id}/run`。
- 如果流式过程中出错，服务端发送：

```text
event: error
data: {"message":"human readable reason"}
```

---

## 4. `POST /voice/chat`

请求：

```json
{
  "conversation_id": "string",
  "audio_base64": "base64 encoded 16kHz 16-bit mono PCM",
  "audio_format": "pcm",
  "tts_enabled": true
}
```

响应：

```json
{
  "request_id": "req_<uuid4_hex>",
  "turn_id": "turn_<uuid4_hex>",
  "conversation_id": "string",
  "transcript": "string",
  "reply": "string",
  "retrieval_status": "completed | pending | failed",
  "retrieved_memory_ids": [int],
  "audio_base64": "base64 encoded PCM | null",
  "audio_format": "pcm"
}
```

约定：

- `audio_format` 当前只保证 `pcm`。默认值为 `pcm`。
- `tts_enabled=false` 时跳过 TTS，响应里的 `audio_base64` 为 `null`。
- 语音识别后的 `transcript` 会复用 `/chat` 的现有文本对话流程，因此同样会写入对话历史并进入 retrieval/followup 生命周期。

---

## 5. 实时语音接口

实时语音接口面向 Reachy app 一类的外部采集端：采集端负责录音、降采样和分块；
`test-project` 负责持有火山 ASR WebSocket、维护实时字幕，并在结束时复用现有
文本对话和 TTS 流程。

音频约定：

- PCM little-endian，16kHz，16-bit，mono。
- 推荐 chunk 为 160ms，即 `5120` bytes。
- 服务端接受最后一个普通 chunk 小于 `5120` bytes。
- 停止录音时应调用 `/voice/live/finish`，不要再次把整段音频发到 `/voice/chat`。

### 5.1 `POST /voice/live/start`

请求体可省略；默认值如下：

```json
{
  "sample_rate": 16000,
  "channels": 1,
  "audio_format": "pcm"
}
```

响应：

```json
{
  "session_id": "live_<uuid4_hex>",
  "sample_rate": 16000,
  "channels": 1,
  "audio_format": "pcm",
  "chunk_duration_ms": 160,
  "chunk_bytes": 5120
}
```

约定：

- 当前只接受 `sample_rate=16000`、`channels=1`、`audio_format=pcm`。
- 成功响应表示服务端已创建实时 ASR 会话，并已发送火山 ASR full client request。

### 5.2 `POST /voice/live/chunk`

请求：

```json
{
  "session_id": "live_<uuid4_hex>",
  "audio_base64": "base64 encoded PCM chunk",
  "is_final": false
}
```

响应：

```json
{
  "ok": true,
  "accepted_bytes": 5120
}
```

约定：

- `audio_base64` 必须是合法且非空的 base64。
- 第一阶段推荐 `is_final=false`，统一由 `/voice/live/finish` 发送 ASR final packet。

### 5.3 `GET /voice/live/transcript?session_id=...`

响应：

```json
{
  "session_id": "live_<uuid4_hex>",
  "transcript": "你好 Reachy",
  "is_final": false,
  "error": null
}
```

约定：

- 采集端可以每 200-300ms 轮询一次。
- `error` 非空时表示 ASR 会话已出现可展示错误，采集端应停止继续提交 chunk。

### 5.4 `POST /voice/live/finish`

请求：

```json
{
  "session_id": "live_<uuid4_hex>",
  "conversation_id": "reachy-mini-voice",
  "tts_enabled": true
}
```

响应沿用 `/voice/chat`：

```json
{
  "request_id": "req_<uuid4_hex>",
  "turn_id": "turn_<uuid4_hex>",
  "conversation_id": "reachy-mini-voice",
  "transcript": "你好 Reachy",
  "reply": "你好！",
  "retrieval_status": "completed | pending | failed",
  "retrieved_memory_ids": [],
  "audio_base64": "base64 encoded PCM | null",
  "audio_format": "pcm"
}
```

约定：

- 服务端会向火山 ASR 发送空音频 final packet，并等待最终文本。
- 拿到最终 `transcript` 后直接复用现有文本对话和 TTS 流程，不再二次 STT。
- finish 成功或失败后，该 live session 都会从内存 manager 中移除。

### 5.5 `POST /voice/live/abort`

请求：

```json
{
  "session_id": "live_<uuid4_hex>"
}
```

响应：

```json
{"ok": true}
```

约定：

- 用于取消录音或页面关闭。
- 只关闭 ASR 会话，不调用 chat、memory retrieval 或 TTS。

### 5.6 `POST /tools/voice-latency/finish-stream`

该接口只供本地语音延迟测试页面使用。请求与 `/voice/live/finish` 相同：

```json
{
  "session_id": "live_<uuid4_hex>",
  "conversation_id": "voice-latency-demo",
  "tts_enabled": true
}
```

响应为 `text/event-stream; charset=utf-8`，事件顺序：

```text
event: transcript
data: {"session_id":"live_<uuid4_hex>","conversation_id":"voice-latency-demo","transcript":"最终识别文本"}

event: meta
data: {"request_id":"req_<uuid4_hex>","turn_id":"turn_<uuid4_hex>","conversation_id":"voice-latency-demo"}

event: delta
data: {"delta":"回复片段"}

event: audio
data: {"audio_base64":"base64 encoded 24kHz 16-bit mono PCM chunk","audio_format":"pcm","sample_rate":24000,"chunk_index":0,"segment_index":0}

event: done
data: {"request_id":"req_<uuid4_hex>","turn_id":"turn_<uuid4_hex>","conversation_id":"voice-latency-demo","reply":"完整回复","retrieval_status":"pending","retrieved_memory_ids":[],"transcript":"最终识别文本","audio_base64":null,"audio_format":"pcm"}
```

约定：

- 页面用第一个 `delta` 到达时间统计模型首响应延迟，并继续读取流直到 `done`。
- 该接口使用 no-op conversation/history/memory 依赖，不写入本地 SQLite 数据库。
- 当 `tts_enabled=true` 时，服务端会把模型 `delta` 按句切分并并发提交给
  TTS。`audio` 事件可能在完整回复生成前到达，也可能与后续 `delta` 交错
  到达；前端应按 `audio` 事件到达顺序排队播放。
- TTS 文本仍会先经过 reply tag 过滤，`[emo:...]` / `[act:...]` 不会进入语音
  合成文本。
- `audio.chunk_index` 是整次响应内的音频 chunk 序号，`audio.segment_index` 是
  本次回复内的 TTS 文本片段序号。
- `done` 在文本流和最后一个 TTS 片段都发送完成后返回；`done.audio_base64`
  仍为 `null`，避免重复返回完整音频。
- 当 `tts_enabled=false` 时，不发送 `audio` 事件。

---

## 6. `GET /followups/pending`

响应：

```json
{
  "pending": [
    {
      "request_id": "req_xxx",
      "conversation_id": "string",
      "status": "retrieval_completed",
      "updated_at": "ISO-8601 UTC"
    }
  ]
}
```

---

## 7. `POST /followups/{request_id}/run`

请求体可省略或为 `{}`。

响应：

```json
{
  "request_id": "req_xxx",
  "conversation_id": "string",
  "decision": "followup | no_followup",
  "followup_type": "supplement | correction | none",
  "reply": "string"
}
```

约定：

- 只能在请求状态为 `retrieval_completed` 时调用；否则返回 `409`。
- 若 `decision=followup` 且 `reply` 非空，服务会自动把这条回复作为
  `assistant` turn 追加到 `conversation_turns`（metadata `turn_kind=followup`）。
- 当 `decision=no_followup`，`followup_type` 固定为 `none`，`reply` 固定为 `""`。

---

## 8. `POST /memory/curate`

请求：

```json
{
  "conversation_id": "string",
  "history_limit": 50
}
```

响应：

```json
{
  "conversation_id": "string",
  "operations": [
    {
      "operation": "create | update | archive | link | conflict_mark | merge",
      "target_id": null,
      "payload": { /* Memory Curator operation payload */ }
    }
  ],
  "applied": [ /* 来自 Person 1 apply_memory_operations 的结果 */ ]
}
```

约定：

- `operations` 即 Memory Curator 调用 LLM 后归一化得到的列表；为空时
  `applied` 也为空。
- `payload` 字段集合见下方第 9 节。

---

## 9. Memory Curator 输出 schema

参考 `docs/tasks/person-3-orchestration-curator.md` 第 5.3 节。Person 3
归一化后保证：

```json
{
  "operations": [
    {
      "operation": "create | update | archive | link | conflict_mark | merge",
      "target_id": null,
      "payload": {
        "summary": "string",
        "content": "string",
        "memory_type": "fact | preference | task | event | constraint | profile_update",
        "references_json": [],
        "tags_json": [],
        "metadata_json": {},
        "confidence": 0.0,
        "importance": 0.0,
        "sensitivity": "normal | sensitive | high_risk",
        "status": "active"
      }
    }
  ]
}
```

校验规则：

- `operation` 必须在白名单内。
- `target_id` 若非空必须是正整数。
- `create` 操作的 `payload.summary` 与 `payload.content` 不能为空。
- 不在白名单内的 `memory_type` / `sensitivity` / `status` 会抛 `ValueError`。

---

## 10. `POST /memory/profile/refresh`

请求体为空 `{}`。

响应：

```json
{
  "should_update": true,
  "patch": {
    "operation": "replace",
    "content": "new User.md content"
  },
  "reason": "string",
  "new_profile": "string | null"
}
```

约定：

- `should_update=false` 时 `patch` 与 `new_profile` 均为 `null`。
- `should_update=true` 时服务会调用 `apply_user_profile_patch` 写入
  `data/User.md`，`new_profile` 即写入后的内容。
- 与 Person 1 的 `apply_user_profile_patch` 兼容（支持 `content` /
  `operations` / `append` / `prepend` / `replace`）。

---

## 11. Request Coordinator 内部状态机

```
received
  → initial_reply_generated
  → retrieval_pending          (Dialogue Service 调用前)
  → retrieval_completed
  → followup_generated | no_followup_needed
failed                          (任意阶段失败)
```

`retrieval_completed` 是 `/followups/{request_id}/run` 的必要前置。终态
为 `followup_generated` / `no_followup_needed` / `failed`。

---

## 12. Dialogue Service 对 Person 1 / Person 2 的依赖

所有依赖通过 `DialogueDependencies` 注入，包括但不限于：

- Person 1：`read_model_profile`、`read_user_profile`、`apply_user_profile_patch`、
  `append_turn`、`get_recent_history`、`get_compact_history`、
  `update_compact_history`、`list_lightweight_memory_items`、
  `get_memory_items_by_ids`、`apply_memory_operations`。
- Person 2：`generate_initial_reply`、`generate_initial_reply_stream`、
  `generate_followup_reply`、`retrieve_relevant_memory_ids`。
- Person 3：`extract_memory_operations`、`generate_user_profile_patch`、
  `model_client`（任何符合 Person 2 `ModelClient` Protocol 的对象）。

生产环境下 `DialogueDependencies()` 不传入参数即可，Dialogue Service
会按需 lazy import 真实实现；测试可注入 fake。
