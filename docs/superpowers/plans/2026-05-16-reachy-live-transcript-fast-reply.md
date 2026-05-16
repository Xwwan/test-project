# Reachy 机器人麦克风低延迟 STT 与快速回复改造计划

> **状态：待实现。** 本计划只描述 `test-project` 的修改方案，暂不改业务代码。新的约束是：**STT 必须在 `test-project` 中完成**；Reachy app 只负责采集机器人麦克风、转发音频块、显示结果和播放返回音频。本文已参考 `/Users/xwan/code/demo` 中现有实时识别实践，尤其是 `SttRecorder.kt`、`WebSocketClient.kt`、`VolcengineAsrSession.kt`。

## 背景

当前系统存在两条语音识别路径：

- `test-project` 的 `/voice/chat`：接收整段 `audio_base64`，在服务端做 STT，然后 chat + TTS。
- Reachy app 临时实现的实时字幕：在 app 侧通过本地 `WebSocketClient` 创建 `VolcengineAsrSession`，直接连接 Volcengine ASR。

`demo` 中已经验证过的实时识别链路是：

```text
Android AudioRecord
-> 48kHz mono PCM16 采集
-> SttRecorder.downsampleTo16k()
-> 每 160ms 产出 16kHz mono PCM16 chunk，约 5120 bytes
-> VolcengineAsrSession.sendAudio()
-> finish 时发送空音频 last packet
-> 收到 result.text，is_final=true 后关闭 ASR socket
```

用户希望最终架构改成：

```text
机器人麦克风采集仍在 Reachy app
STT 统一放在 test-project
同时保留低延迟实时字幕
停止录音后尽量不要二次 STT
```

因此需要把“实时 ASR 会话”能力从 Reachy app 迁到 `test-project`，但保留 `demo` 中已经验证的音频格式、160ms chunk 粒度、火山 ASR frame 语义和 final packet 语义。

## 设计约束

- 不破坏 `test-project` 当前结构：实时语音能力放在 `src/audio/` 和 `src/api/routes.py` 的既有边界内，不引入 Reachy app 专属目录。
- 不把 Android、Reachy 或 8042 UI 逻辑写入核心 dialogue、memory、conversation 模块。
- 复用 `src/audio/volcengine_asr.py` 已有协议工具，不把 `demo/VolcengineAsrSession.kt` 的二进制帧代码复制一份到新文件。
- `/voice/chat` 保持兼容，继续作为“整段音频输入”的路径。
- 第一阶段不要求 `test-project` 对外提供 WebSocket 服务；先用现有 HTTP route 模型承载 start/chunk/transcript/finish，降低对项目结构的冲击。

## 目标架构

### 录音开始

```text
Reachy app
-> POST /voice/live/start
-> test-project 创建 LiveAsrSession
-> LiveAsrSession 连接 Volcengine ASR WebSocket
-> 发送 full client request:
   format=pcm, codec=raw, rate=16000, bits=16, channel=1,
   model_name=bigmodel, enable_itn=true, enable_punc=true,
   show_utterances=true, result_type=full
-> 返回 session_id
```

### 录音过程中

```text
Reachy app:
  AudioRecord 采集 48kHz mono PCM16
  本地降采样到 16kHz mono PCM16
  每 160ms 提交一个约 5120 bytes chunk

Reachy app
-> POST /voice/live/chunk {session_id, audio_base64}
-> test-project 把 PCM chunk 写入该 session 的 ASR WebSocket

Reachy app 每 200-300ms:
-> GET /voice/live/transcript?session_id=...
-> 页面显示 test-project 返回的实时 transcript
```

### 停止录音

```text
Reachy app
-> POST /voice/live/finish {session_id, conversation_id, tts_enabled}
-> test-project 向 ASR 发送 empty audio + last packet
-> 等待最终 result.text
-> 直接 chat + TTS
-> 返回 VoiceChatResponse
```

这样 STT 全部发生在 `test-project`，录音期间已经在流式识别，停止时不再上传整段音频做第二次 STT。

## 非目标

- 不在 `test-project` 采集机器人麦克风；它只接收已经降采样后的 PCM chunk。
- 不在第一阶段把 `test-project` HTTP 服务升级为 WebSocket 服务。
- 不删除现有 `/voice/chat`。
- 不改 memory、persona、conversation 的数据结构。
- 不把 Reachy app 的 UI 手势逻辑、保存/取消录音逻辑移入 `test-project`。

## `demo` 实践映射

### 可直接沿用的行为

- `SttRecorder.kt` 的音频输入约定：
  - 采集源：`MediaRecorder.AudioSource.MIC`
  - 输入：48kHz mono PCM16
  - 输出：16kHz mono PCM16
  - chunk：160ms
  - chunk 大小：`16000 * 2 * 160 / 1000 = 5120 bytes`
- `VolcengineAsrSession.kt` 的 ASR 配置：
  - `format=pcm`
  - `codec=raw`
  - `rate=16000`
  - `bits=16`
  - `channel=1`
  - `model_name=bigmodel`
  - `enable_itn=true`
  - `enable_punc=true`
  - `show_utterances=true`
  - `result_type=full`
- finish 语义：
  - 普通音频包：message type `0x2`，flags `0x1`，正 sequence。
  - 最后音频包：空 audio，flags `0x3`，负 sequence。
  - 服务端 result frame 中 `flags & 0x2 == 0x2` 时视为 final。
- UI 事件语义：
  - started：连接已建立并已发送 full client request。
  - result：返回 `text` 和 `isFinal`。
  - local_error：返回可展示错误。

### 需要在 `test-project` 中改造的部分

- `demo` 中 `WebSocketClient` 直接持有 `VolcengineAsrSession`；改造后应由 `test-project` 持有 session，Reachy app 只保存 `session_id`。
- `demo` 中 Kotlin 手写 frame 构造和解析；`test-project` 已有 `src/audio/volcengine_asr.py`，应继续让协议细节集中在该模块。
- `demo` 中 result 通过本地回调推给 UI；第一阶段改为 `GET /voice/live/transcript` 轮询最新状态。
- `demo` 中 final 后只得到 transcript；改造后 `test-project` 拿 final transcript 后继续调用现有 dialogue + TTS。

## API 设计

### 1. 开始实时语音会话

```text
POST /voice/live/start
```

请求：

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
  "session_id": "live_xxx",
  "sample_rate": 16000,
  "channels": 1,
  "audio_format": "pcm",
  "chunk_duration_ms": 160,
  "chunk_bytes": 5120
}
```

### 2. 发送音频块

```text
POST /voice/live/chunk
```

请求：

```json
{
  "session_id": "live_xxx",
  "audio_base64": "...",
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

说明：

- 第一阶段推荐 `is_final=false`，finish 统一由 `/voice/live/finish` 触发。
- 服务端应接受小范围偏差的 chunk 大小，避免 Android 采集边界导致最后一个非 final chunk 不满 5120 bytes。

### 3. 读取实时字幕

```text
GET /voice/live/transcript?session_id=live_xxx
```

响应：

```json
{
  "session_id": "live_xxx",
  "transcript": "你好 Reachy",
  "is_final": false,
  "error": null
}
```

### 4. 结束会话并生成回复

```text
POST /voice/live/finish
```

请求：

```json
{
  "session_id": "live_xxx",
  "conversation_id": "reachy-mini-voice",
  "tts_enabled": true
}
```

响应沿用现有 `VoiceChatResponse` 形状：

```json
{
  "conversation_id": "reachy-mini-voice",
  "request_id": "req_xxx",
  "turn_id": "turn_xxx",
  "transcript": "你好 Reachy",
  "reply": "你好！",
  "retrieval_status": "completed",
  "retrieved_memory_ids": [],
  "audio_base64": "...",
  "audio_format": "pcm"
}
```

### 5. 可选：中止会话

如果实现成本很低，可以补充：

```text
POST /voice/live/abort
```

请求：

```json
{
  "session_id": "live_xxx"
}
```

用途是对应 `demo/WebSocketClient.kt` 中的 `"abort"` 行为：用户上滑取消或页面关闭时关闭 ASR socket，不进入 chat + TTS。

## 内部设计

新增 `src/audio/live_asr.py`，管理实时 ASR 会话。

建议核心对象：

```python
@dataclass
class LiveTranscriptState:
    transcript: str = ""
    is_final: bool = False
    error: str | None = None


class LiveAsrSession:
    def submit_audio(self, audio: bytes) -> None: ...
    def get_transcript(self) -> LiveTranscriptState: ...
    def finish(self, timeout_seconds: float = 2.0) -> LiveTranscriptState: ...
    def close(self) -> None: ...


class LiveAsrSessionManager:
    def start_session(self, sample_rate: int, channels: int, audio_format: str) -> str: ...
    def submit_chunk(self, session_id: str, audio: bytes) -> int: ...
    def get_transcript(self, session_id: str) -> LiveTranscriptState: ...
    def finish_session(self, session_id: str) -> LiveTranscriptState: ...
    def abort_session(self, session_id: str) -> None: ...
```

实现思路：

- `LiveAsrSession` 内部连接 Volcengine ASR WebSocket，并发送 full client request。
- full client request 的 payload 与 `demo/VolcengineAsrSession.kt` 保持一致。
- 构造初始请求和音频帧时，复用：
  - `build_full_client_request_frame()`
  - `build_audio_request_frame()`
  - `parse_server_frame()`
  - 必要时复用或扩展 `split_pcm_audio()`
- `submit_audio()` 接收 Reachy app 传来的 16kHz mono PCM16 chunk。
- WebSocket 收到 server frame 后调用 `parse_server_frame()`，把最新 `result.text` 写入 `LiveTranscriptState`。
- 收到 `AsrResultFrame.is_final=True` 后，标记 final，并允许 `finish()` 返回。
- 收到 `AsrErrorFrame` 或解析异常后，写入 `error`，并让 API 返回可理解错误。
- `finish()` 发送 `build_audio_request_frame(b"", is_final=True, sequence=...)`，等待 final transcript，然后关闭 socket。
- `LiveAsrSessionManager` 用内存 dict 保存 `session_id -> LiveAsrSession`。
- 为避免泄漏，session 应有超时清理；finish 或 abort 后必须从 manager 移除。

### WebSocket 客户端边界

`test-project` 当前已有 `src/audio/stt.py` 和 `src/audio/volcengine_asr.py`，但实时场景需要一个可注入的 WebSocket 边界，避免测试做真实网络请求。建议：

- 在 `live_asr.py` 中定义最小客户端协议，例如 `connect(headers) -> socket`、`send(bytes)`、`recv()`、`close()`。
- 默认实现可以使用项目已有依赖；如果项目没有固定 websocket 依赖，再选择最小新增依赖并写入文档。
- 测试使用 fake websocket client，直接喂入 `AsrResultFrame` 对应的 server frame。

## 文件修改范围

- 新增：`src/audio/live_asr.py`
- 修改：`src/audio/schemas.py`
- 修改：`src/audio/service.py`
- 修改：`src/audio/__init__.py`
- 修改：`src/api/routes.py`
- 新增：`tests/test_live_asr.py`
- 修改：`tests/test_audio_schemas.py`
- 修改：`tests/test_voice_service.py`
- 修改：`tests/test_voice_api_routes.py`
- 修改：`docs/api_contracts.md`
- 修改：`README.md`

不应修改：

- `src/memory/`
- `src/persona/`
- `src/conversation/`
- Reachy app 源码目录

## 实施步骤

### 任务 1：抽出“文本输入 -> chat + TTS”服务

**文件：**

- `src/audio/service.py`
- `tests/test_voice_service.py`

新增：

```python
def handle_voice_reply_from_text(
    conversation_id: str,
    transcript: str,
    *,
    tts_enabled: bool = True,
    dependencies: AudioDependencies | None = None,
    dialogue_dependencies: DialogueDependencies | None = None,
) -> VoiceChatResponse:
    ...
```

用途：

- `/voice/chat` 做完整段 STT 后调用它。
- `/voice/live/finish` 拿到最终实时 transcript 后也调用它。

- [ ] 校验 `conversation_id` 是非空字符串。
- [ ] 校验 `transcript.strip()` 非空。
- [ ] 调用现有 `chat_handler`。
- [ ] 根据 `tts_enabled` 调用现有 TTS。
- [ ] 返回 `VoiceChatResponse`。
- [ ] 修改 `handle_voice_chat()`，让它在 STT 后复用 `handle_voice_reply_from_text()`。
- [ ] 增加单元测试覆盖：
  - 文本输入不会调用 STT。
  - `tts_enabled=True` 时调用 TTS。
  - `tts_enabled=False` 时不调用 TTS。
  - 空 transcript 返回 `ValueError`。

验收：

```bash
conda run -n toy python -m pytest tests/test_voice_service.py -q
```

### 任务 2：新增实时语音 schema

**文件：**

- `src/audio/schemas.py`
- `tests/test_audio_schemas.py`

新增请求 dataclass：

- `LiveVoiceStartRequest`
  - `sample_rate: int = 16000`
  - `channels: int = 1`
  - `audio_format: str = "pcm"`
- `LiveVoiceChunkRequest`
  - `session_id: str`
  - `audio_bytes: bytes`
  - `is_final: bool = False`
- `LiveVoiceFinishRequest`
  - `session_id: str`
  - `conversation_id: str`
  - `tts_enabled: bool = True`
- 可选 `LiveVoiceAbortRequest`
  - `session_id: str`

新增响应 dataclass：

- `LiveVoiceStartResponse`
  - `session_id`
  - `sample_rate`
  - `channels`
  - `audio_format`
  - `chunk_duration_ms=160`
  - `chunk_bytes=5120`
- `LiveVoiceTranscriptResponse`
  - `session_id`
  - `transcript`
  - `is_final`
  - `error`

校验规则：

- `session_id` 非空。
- `audio_base64` 必须是合法且非空 base64。
- `sample_rate` 初期只接受 `16000`。
- `channels` 初期只接受 `1`。
- `audio_format` 初期只接受 `"pcm"`。
- `chunk` 初期按 PCM16 little-endian 处理，不需要在 schema 中解析采样值。

验收：

```bash
conda run -n toy python -m pytest tests/test_audio_schemas.py -q
```

### 任务 3：实现 `live_asr` 会话管理

**文件：**

- `src/audio/live_asr.py`
- `tests/test_live_asr.py`

- [ ] 复用 `src/audio/volcengine_asr.py` 的 frame builder/parser，不复制协议代码。
- [ ] ASR full client request payload 对齐 `demo/VolcengineAsrSession.kt`。
- [ ] 从现有 `build_default_stt_client()` 相同配置来源读取 Volcengine 凭据。
- [ ] 连接 header 对齐 demo：
  - `X-Api-App-Key`
  - `X-Api-Access-Key`
  - `X-Api-Resource-Id`
  - `X-Api-Connect-Id`
- [ ] `LiveAsrSession` 接收 audio chunk，通过后台线程或受控 socket 循环写入 ASR socket。
- [ ] 实时读取 ASR 返回，维护最新 transcript。
- [ ] `finish()` 发送空音频 final packet，等待最终文本，关闭 socket。
- [ ] `abort()` 或 `close()` 直接关闭 socket，不进入 chat + TTS。
- [ ] `LiveAsrSessionManager` 管理 session 生命周期。
- [ ] 测试中使用 fake socket/fake websocket client，不做真实网络请求。
- [ ] 覆盖：
  - start 后生成 session id。
  - start 后发送 full client request。
  - submit chunk 后发送 audio frame。
  - 收到 result frame 后 transcript 更新。
  - final result 后 `is_final=True`。
  - finish 后 session 被移除。
  - abort 后 session 被移除且不生成回复。
  - ASR error frame 会反映到 transcript error 或抛出可控异常。

验收：

```bash
conda run -n toy python -m pytest tests/test_live_asr.py -q
```

### 任务 4：新增 API route

**文件：**

- `src/api/routes.py`
- `src/audio/__init__.py`
- `tests/test_voice_api_routes.py`

新增路由：

```python
POST /voice/live/start
POST /voice/live/chunk
GET  /voice/live/transcript?session_id=...
POST /voice/live/finish
POST /voice/live/abort  # 可选
```

实现注意：

- `dispatch()` 当前只接收 `method, path, body`，可以用 `urlsplit(path).query` 解析 `session_id`。
- route 层只做 schema 解析和服务调用，不直接写 ASR 协议。
- `finish` 路由拿到最终 transcript 后调用 `handle_voice_reply_from_text()`。
- 需要一个可注入的 `LiveAsrSessionManager`，方便测试避免真实网络。

可选实现：

```python
def dispatch(..., live_asr_manager: LiveAsrSessionManager | None = None):
    manager = live_asr_manager or get_default_live_asr_manager()
```

测试覆盖：

- `/voice/live/start` 返回 session_id、160ms chunk 建议。
- `/voice/live/chunk` 接收 base64 PCM。
- `/voice/live/transcript?session_id=...` 返回最新文本。
- `/voice/live/finish` 返回 `VoiceChatResponse`，并调用 chat + TTS。
- `/voice/live/abort` 关闭 session，不调用 chat + TTS。
- 缺失 session 或未知 session 返回 404 或 400。

验收：

```bash
conda run -n toy python -m pytest tests/test_voice_api_routes.py -q
```

### 任务 5：文档更新

**文件：**

- `docs/api_contracts.md`
- `README.md`

- [ ] 说明 `/voice/chat` 是整段音频路径。
- [ ] 说明 `/voice/live/*` 是低延迟流式 STT 路径。
- [ ] 给出 curl 示例：
  - start
  - chunk
  - transcript
  - finish
  - abort，可选
- [ ] 标注音频格式：16kHz、16-bit、mono PCM、little-endian、base64。
- [ ] 标注 Reachy app 侧仍负责从 48kHz 采集降采样到 16kHz；`test-project` 不负责机器人麦克风采集。

## Reachy app 后续配套修改

`test-project` 合入后，Reachy app 应从“直接连接 Volcengine ASR”改成“转发 PCM chunk 到 `test-project`”：

```text
startRecording:
  POST test-project /voice/live/start
  保存 live session_id

record_loop:
  沿用 SttRecorder.kt:
    AudioRecord 48kHz mono PCM16
    downsampleTo16k()
    每 160ms 产出 5120 bytes
  POST /voice/live/chunk

UI:
  每 200-300ms GET /voice/live/transcript?session_id=...
  显示 test-project 返回的实时 transcript

stopRecording:
  POST /voice/live/finish {session_id, conversation_id, tts_enabled}
  播放返回 audio_base64

cancelRecording:
  POST /voice/live/abort {session_id}
```

然后删除或停用 Reachy app 中直接连接 Volcengine ASR 的临时代码，确保 STT 单一归属在 `test-project`。

## 延迟预期

低延迟来自：

- Reachy app 不再等待整段录音结束才上传。
- `test-project` 在录音开始时就打开 ASR WebSocket。
- 音频 chunk 沿用 demo 已验证的 160ms 粒度。
- 停止录音时只需要发送 final packet 并等待最终结果，然后直接 chat + TTS。

瓶颈仍可能来自：

- 机器人麦克风采集链路。
- Reachy app 到 `test-project` 的 HTTP chunk 往返。
- `test-project` HTTP server 并发能力。
- Volcengine ASR partial/final 返回速度。
- chat 和 TTS 服务耗时。

如果 HTTP chunk 的开销仍然明显，再考虑第二阶段把 `test-project` 升级为 WebSocket 接收音频流。但第一阶段先用现有 HTTP server 模型，改动更小、风险更低，也更不容易破坏 `test-project` 现有结构。

## 风险与注意事项

- `test-project` 当前是标准库 HTTP server，请确认多请求并发能力是否满足实时 chunk 提交和 transcript 轮询。如果当前 server 单线程，可能需要切到 `ThreadingHTTPServer`。
- `LiveAsrSession` 的 socket 读写需要线程安全；`transcript` 状态更新应有 lock 或等价保护。
- session 必须有超时清理，避免用户关闭 8042 页面后泄漏 ASR WebSocket。
- `finish` 应该幂等或至少返回可理解错误，避免用户重复点击停止。
- `/voice/chat` 保持不动，作为兼容兜底。
- 实时 transcript 为空时不应继续 chat + TTS，应返回明确错误，例如 `speech recognition produced an empty transcript` 或更友好的中文错误。
- 如果 Reachy app 传来的不是 16kHz mono PCM16，服务端第一阶段应直接拒绝，而不是自动猜测重采样。
- 如果 ASR 连接失败，`/voice/live/transcript` 应能返回 `error`，UI 可以显示“识别连接失败”。

## 全量验证

局部验证：

```bash
conda run -n toy python -m pytest tests/test_audio_schemas.py tests/test_live_asr.py tests/test_voice_service.py tests/test_voice_api_routes.py -q
```

项目基础验证：

```bash
conda run -n toy python -m unittest discover -s tests -p 'test_*.py'
```

手动联调：

1. 启动 `test-project`，确认 Volcengine 环境变量已配置。
2. 调 `/voice/live/start` 获取 `session_id`，确认返回 `chunk_duration_ms=160` 和 `chunk_bytes=5120`。
3. 连续向 `/voice/live/chunk` 发送 16kHz mono PCM16 chunk。
4. 调 `/voice/live/transcript`，确认能看到增量文本。
5. 调 `/voice/live/finish`，确认返回 reply 和 TTS `audio_base64`。
6. 修改 Reachy app 后，通过 8042 确认实时字幕来自 `test-project`，停止录音后不再二次 STT。

