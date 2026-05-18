# Long-term Memory Chat Demo

这是一个本地长期记忆聊天 Demo，当前实现了三部分能力：

- Person 1：Memory Store、Persona File Manager、Conversation Store。
- Person 2：Dialogue Agent、Memory Retrieval Workflow、模型调用适配层。
- Person 3：Request Coordinator、API/Service 编排、Memory Curator、Profile Consolidator。

当前项目定位是 MVP / Demo：可以跑通聊天、记忆检索、二次回复判断、记忆抽取和用户画像刷新闭环；还不是生产级服务。

## 环境

项目约定使用 `toy` conda 环境：

```bash
conda activate toy
```

如果环境缺少 PyYAML：

```bash
conda run -n toy python -m pip install -r requirements.txt
```

## 配置

模型配置在：

```text
config/app.yaml
```

配置分为两层：

- `providers`：模型服务连接信息，例如 provider 类型、base URL、API key 环境变量名、超时时间。
- `routes`：不同用途使用的 provider、model 和调用参数。

当前 route 示例：

```yaml
routes:
  dialogue:
    initial:
      provider: yunwu
      model: deepseek-v4-flash

    followup:
      provider: yunwu
      model: deepseek-v4-flash

  memory:
    retrieval:
      provider: yunwu
      model: deepseek-v4-flash

    curator:
      provider: yunwu
      model: deepseek-v4-flash
```

真实 API key 放在项目根目录 `.env`，不要提交 `.env`：

```env
YUNWU_API_KEY=你的真实 key
```

语音输入/输出的 API key 也放在同一个 `.env`：

```env
VOLCENGINE_APP_ID=你的火山引擎 App ID
VOLCENGINE_ACCESS_KEY=你的火山引擎 Access Key
VOLCENGINE_RESOURCE_ID=你的火山引擎 ASR Resource ID
DASHSCOPE_API_KEY=你的 DashScope API Key
```

如果使用本地 OpenAI-compatible 服务，可以把 route 的 `provider` 改成 `local_openai_compatible`，并按实际服务修改 `base_url` 和 `model`。

## 测试

运行全量测试：

```bash
conda run -n toy python -m unittest discover -s tests -p 'test_*.py'
```

当前应通过全部测试。

## 延迟测试

文本模型延迟测试不会放进全量单元测试自动跑，因为真实模型调用会受网络、服务商负载和 API key 配置影响。可以手动运行：

```bash
conda run -n toy python scripts/latency_benchmark.py --repeat 3
```

默认会使用 `dialogue.initial` route 和内置的多段中文场景，按流式模型调用统计
首个文本 delta 到达耗时，并输出首 delta 平均值、中位数、最小值、最大值和标准差。
脚本会默认把完整结果保存到 `data/latency-results/` 下的时间戳 JSON 文件，文件里
包含每段 prompt、完整模型回复、简洁 delta 列表、首 delta 耗时和完整回复总耗时。

如果要调整文本场景，准备一个文本文件，每行一段输入：

```bash
conda run -n toy python scripts/latency_benchmark.py --prompts data/latency-prompts.txt --repeat 3
```

如果要改保存目录：

```bash
conda run -n toy python scripts/latency_benchmark.py --repeat 3 --output-dir data/my-latency-results
```

语音延迟测试复用服务端已有实时 ASR 流程。先启动服务：

```bash
conda run -n toy python -m src.main --host 127.0.0.1 --port 8000 --log-level DEBUG
```

然后在浏览器打开：

```text
http://127.0.0.1:8000/tools/voice-latency
```

页面会用浏览器麦克风采集音频，按 16kHz PCM chunk 提交到 `/voice/live/*`。点击“停止并生成回复”后，页面会调用 `/tools/voice-latency/finish`，统计从结束输入到收到模型回复的耗时，并显示实时字幕和模型回复。

这个语音延迟测试 endpoint 只使用真实 ASR 和真实模型调用，conversation/history/memory 都使用 no-op 依赖，不会写入本地 SQLite 数据库。

## 启动服务

```bash
conda run -n toy python -m src.main --host 127.0.0.1 --port 8000 --log-level DEBUG
```

如果 8000 被占用，可换端口：

```bash
conda run -n toy python -m src.main --host 127.0.0.1 --port 12312 --log-level DEBUG
```

按 `Ctrl+C` 可正常停止服务。

## API Smoke Test

健康检查：

```bash
curl -s http://127.0.0.1:8000/healthz
```

发送聊天消息：

```bash
curl -s -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "conv-demo-001",
    "message": "你好，请用一句话介绍你现在能做什么。"
  }'
```

流式聊天消息使用 SSE：

```bash
curl -N -X POST http://127.0.0.1:8000/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "conv-demo-001",
    "message": "你好，请流式回答一句话。"
  }'
```

事件顺序为 `meta`、多次 `delta`、最后 `done`。`done` 里包含完整
`reply`、`retrieval_status` 和 `retrieved_memory_ids`。

发送语音聊天消息，`audio_base64` 当前约定为 16kHz、16-bit、mono PCM 的 base64：

```bash
curl -s -X POST http://127.0.0.1:8000/voice/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "conv-demo-voice-001",
    "audio_base64": "把PCM音频转成base64后放这里",
    "audio_format": "pcm",
    "tts_enabled": true
  }'
```

返回里会包含 `transcript`、模型 `reply`，以及开启 TTS 时的 `audio_base64` PCM 音频。

实时语音流程适合 Reachy app 这类外部采集端。采集端负责用机器人麦克风录音、降采样到
16kHz 16-bit mono PCM，并按 160ms、约 5120 bytes 的粒度提交 chunk；服务端负责持有
火山 ASR WebSocket、提供实时字幕，并在结束时直接用最终文本生成回复：

```bash
curl -s -X POST http://127.0.0.1:8000/voice/live/start \
  -H "Content-Type: application/json" \
  -d '{
    "sample_rate": 16000,
    "channels": 1,
    "audio_format": "pcm"
  }'
```

把返回的 `session_id` 保存下来，录音过程中持续提交 chunk：

```bash
curl -s -X POST http://127.0.0.1:8000/voice/live/chunk \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "live_xxx",
    "audio_base64": "把单个PCM chunk转成base64后放这里",
    "is_final": false
  }'
```

页面可以每 200-300ms 轮询最新字幕：

```bash
curl -s "http://127.0.0.1:8000/voice/live/transcript?session_id=live_xxx"
```

停止录音时调用 finish。服务端会发送 ASR final packet，并复用现有 chat + TTS 流程；
无需再把整段音频提交到 `/voice/chat` 做第二次 STT：

```bash
curl -s -X POST http://127.0.0.1:8000/voice/live/finish \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "live_xxx",
    "conversation_id": "reachy-mini-voice",
    "tts_enabled": true
  }'
```

如果用户取消录音或页面关闭，可以中止会话，不生成回复：

```bash
curl -s -X POST http://127.0.0.1:8000/voice/live/abort \
  -H "Content-Type: application/json" \
  -d '{"session_id": "live_xxx"}'
```

查询待处理 followup：

```bash
curl -s http://127.0.0.1:8000/followups/pending
```

执行某个 request 的 followup 判断，把 `req_xxx` 换成真实 `request_id`：

```bash
curl -s -X POST http://127.0.0.1:8000/followups/req_xxx/run \
  -H "Content-Type: application/json"
```

触发记忆抽取：

```bash
curl -s -X POST http://127.0.0.1:8000/memory/curate \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "conv-demo-001",
    "history_limit": 20
  }'
```

刷新用户画像：

```bash
curl -s -X POST http://127.0.0.1:8000/memory/profile/refresh \
  -H "Content-Type: application/json"
```

## 主要目录

```text
config/                 模型 provider 和 route 配置
data/                   Model.md / User.md，本地 SQLite 数据库也会生成在这里
docs/                   设计文档、任务文档、API 契约
prompts/                各 Agent 的 prompt
src/agents/             Dialogue Agent、Retrieval、Curator、Profile Consolidator
src/api/                HTTP route 和 schema
src/conversation/       对话历史和 compact history
src/coordinator/        request 生命周期和 pending followup 队列
src/audio/              STT/TTS 适配器、语音聊天编排和音频协议
src/memory/             SQLite memory store
src/models/             模型调用适配层
src/persona/            Model.md / User.md 文件管理
src/services/           业务编排层
tests/                  unittest 测试
```

## 本地文件注意事项

以下文件不应提交：

- `.env`
- `data/app.db`
- Python `__pycache__/`
- 本地 IDE 配置

如果你想本地修改 `config/app.yaml`、`data/Model.md` 或 `data/User.md` 但不提交，可以使用：

```bash
git update-index --skip-worktree config/app.yaml
git update-index --skip-worktree data/Model.md
git update-index --skip-worktree data/User.md
```

取消本地忽略：

```bash
git update-index --no-skip-worktree config/app.yaml
git update-index --no-skip-worktree data/Model.md
git update-index --no-skip-worktree data/User.md
```

## 当前限制

- Request Coordinator 使用内存存储，服务重启后 pending request 会丢失。
- Retrieval 当前在 `/chat` 流程中同步完成，尚未接入真正后台队列。
- HTTP 服务基于 Python 标准库 `http.server`，适合 Demo，不是生产级 Web 服务。
- Memory Curator 和 Retrieval 的效果依赖真实模型输出，仍需要持续人工评估和 prompt 调整。
