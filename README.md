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
