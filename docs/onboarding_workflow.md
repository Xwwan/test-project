# Onboarding Workflow

本文档说明当前新增的独立 onboarding 引导模块，供后续接入主框架或交接给其他
agent 开发时参考。

## 目标

Onboarding Workflow 用于在正式聊天前，通过自然中文对话收集用户长期资料。它不是
普通 `/chat` 流程，也不直接更新 memory 或 `User.md`。当前实现保持隔离：

- 不修改主 `src/api/routes.py`。
- 不修改主 `src/memory/migrations.py`。
- 不修改主 `src/services/__init__.py`。
- 不写 `data/User.md`。
- 不写 `memory_items`。

删除新增的 onboarding 文件后，原框架仍可运行。

## 新增文件

```text
src/agents/onboarding_agent.py
src/services/onboarding_service.py
src/onboarding/__init__.py
src/onboarding/store.py
src/onboarding/dev_server.py
src/onboarding/static/onboarding_test.html
src/onboarding/start_dev.sh
prompts/onboarding_agent.md
tests/test_onboarding_agent.py
tests/test_onboarding_service.py
tests/test_onboarding_store.py
tests/test_onboarding_dev_server.py
docs/onboarding_workflow.md
```

## 五阶段状态机

核心状态机在 `src/services/onboarding_service.py`。

当前阶段配置：

| stage | key | name | 目标 |
| --- | --- | --- | --- |
| 1 | `greeting` | 认识你 | 称呼、年纪或人生阶段、所在地 |
| 2 | `family` | 家庭情况 | 家里有谁、子女情况、居住情况 |
| 3 | `daily` | 日常生活 | 日常安排、兴趣爱好、常做事项 |
| 4 | `health` | 健康关注 | 身体状况、不适或关注、睡眠饮食 |
| 5 | `wishes` | 心愿期望 | 近期心情与事件、烦心事、希望聊的话题 |

每个阶段有 `required_slots`。即使模型返回 `stage_complete=true`，service 也会检查
当前阶段所有 required slots 是否已经写入 `collected`。缺字段时不会进入下一阶段，
而是继续追问缺口。

阶段切换时不再直接发送固定 `first_question`。`first_question` 只作为 fallback 和
模型提示。正常路径会调用 `generate_onboarding_question()`，把下一阶段目标、已收集
信息、最近 turns 和上一阶段信息交给模型生成自然过渡问题。

## Agent

模型逻辑在 `src/agents/onboarding_agent.py`：

- `run_onboarding_step()`：判断当前阶段是否充分，返回 `collected_patch` 和下一问。
- `generate_onboarding_question()`：开始或切阶段时生成自然开场/过渡问题。
- `build_deepseek_client()`：只为 onboarding 构造 DeepSeek 官方 OpenAI-compatible client。

onboarding agent 会把 `data/Model.md` 拼进 system prompt，和其他 agent 的人格上下文
保持一致。

当前模型配置：

```text
base_url = https://api.deepseek.com
model = deepseek-v4-flash
env = DEEPSEEK_API_KEY
```

该配置不依赖主 `config/app.yaml`，避免误走云雾 provider。

## 数据持久化

状态持久化在 `src/onboarding/store.py`，使用同一个 SQLite 数据库路径
`APP_DB_PATH`，但迁移逻辑由 onboarding 自己维护，不写入主 memory migrations。

表名：

```text
onboarding_sessions
```

字段：

```sql
CREATE TABLE IF NOT EXISTS onboarding_sessions (
    session_id TEXT PRIMARY KEY,
    conversation_id TEXT,
    status TEXT NOT NULL,
    stage INTEGER NOT NULL,
    collected_json TEXT DEFAULT '{}',
    turns_json TEXT DEFAULT '[]',
    final_payload_json TEXT DEFAULT '{}',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    completed_at DATETIME
);
```

`status` 当前使用：

```text
active
completed
abandoned
```

### collected_json

每轮模型返回的 `collected_patch` 会 merge 到 session 的 `collected_json`。

示例：

```json
{
  "preferred_name": "小熊",
  "age_or_life_stage": "80岁",
  "location": "北京",
  "family_members": "儿子",
  "living_situation": "独居"
}
```

这是当前“要填的表格”的主要存储位置，不只存在内存里。

### final_payload_json

第 5 阶段完成后，service 会生成 `final_payload_json`，留给后续 agent 接管。

示例结构：

```json
{
  "kind": "onboarding_collected_profile",
  "version": 1,
  "status": "ready_for_downstream_agent",
  "collected": {
    "preferred_name": "小熊",
    "location": "北京"
  },
  "suggested_user_profile_section": "<!-- onboarding-profile:start -->\n## 前置引导信息\n...",
  "suggested_user_profile_patch": {
    "operation": "append_or_replace_onboarding_section",
    "section_start": "<!-- onboarding-profile:start -->",
    "section_end": "<!-- onboarding-profile:end -->",
    "content": "<!-- onboarding-profile:start -->\n## 前置引导信息\n..."
  }
}
```

注意：当前模块只生成建议产物，不应用 patch，不更新 `User.md`，也不写
`memory_items`。

## 独立测试服务

测试服务在 `src/onboarding/dev_server.py`，只服务 onboarding 测试，不接入主 API。

接口：

| Method | Path | 说明 |
| --- | --- | --- |
| `GET` | `/` | 打开测试页面 |
| `POST` | `/onboarding/start` | 创建 session，返回第一问 |
| `POST` | `/onboarding/message` | 处理一条用户回答 |
| `GET` | `/onboarding/status?session_id=...` | 查询 session |
| `POST` | `/voice/live/start` | 开始流式 STT session |
| `POST` | `/voice/live/chunk` | 上传 PCM chunk |
| `POST` | `/voice/live/finish-transcript` | 结束 STT，返回文本 |
| `POST` | `/voice/live/abort` | 中止 STT |
| `POST` | `/voice/tts/stream` | 流式 TTS，返回 PCM byte stream |

STT 复用现有火山 live ASR websocket 能力。前端录音后按 chunk 上传。

TTS 复用现有 DashScope realtime TTS client，但调用的是 `synthesize_stream()`，服务端
边生成边写 PCM，前端用 `ReadableStream` 边收边播。

## 一键启动

先在 `.env` 中准备：

```text
APP_DB_PATH=data/app.db
APP_DATA_DIR=data
DEEPSEEK_API_KEY=你的DeepSeek官方Key

VOLCENGINE_APP_ID=...
VOLCENGINE_ACCESS_KEY=...
VOLCENGINE_RESOURCE_ID=volc.bigasr.sauc.duration

DASHSCOPE_API_KEY=...
```

启动：

```bash
cd /home/ruochong/xrc/test-project
./src/onboarding/start_dev.sh
```

打开：

```text
http://127.0.0.1:8010/
```

停止：

```bash
Ctrl+C
```

如果后台残留：

```bash
pkill -f "python3 -m src.onboarding.dev_server"
```

## 后续接入建议

当前模块故意不在主 `src/api/routes.py` 注册路由。后续接主框架时，可以：

1. 在主 API 层新增 `/onboarding/*` 路由。
2. 调用 `src.services.onboarding_service` 中的 service 函数。
3. 让后续 profile/memory agent 读取 `final_payload_json`。
4. 由后续 agent 决定是否写 `User.md` 或 `memory_items`。

建议保持这个边界：onboarding 只做引导状态机和结构化资料采集，不直接拥有长期记忆
更新职责。

## 测试

运行 onboarding 相关测试：

```bash
python3 -m pytest \
  tests/test_onboarding_agent.py \
  tests/test_onboarding_service.py \
  tests/test_onboarding_store.py \
  tests/test_onboarding_dev_server.py
```

当前覆盖：

- agent JSON normalize。
- DeepSeek official client 构造。
- `Model.md` 拼接进 prompt。
- 阶段 required slots gate。
- 阶段切换自然问题生成。
- SQLite session create/get/update。
- 旧表自动补 `final_payload_json` 列。
- TTS stream 使用 `synthesize_stream()`。
