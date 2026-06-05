# 老年多轮闲聊双 Agent 优化交付说明

本文记录 `docs/goal-elder-chat-optimization-final.md` 对应实现的修改清单、测试方式和 benchmark 状态。

## 修改清单

- Agent A：更新 `prompts/dialogue_agent.md`，明确即时回复必须以 `[emo:key][act:key]` 开头，输出 1-2 句自然中文口语，不编造童年、家庭、健康、用药、财务等具体事实，并为记忆续接留空间。
- Agent A：更新 `src/agents/dialogue_agent.py`，非流式和流式初始回复都会保留或补齐 `[emo]/[act]` tag，并限制为前 1-2 句。
- Agent B：更新 `prompts/dialogue_followup_decision.md`，要求二次回复像同一轮回答的自然第二段，避免重复问候、否定首答、系统化汇报记忆或使用“数据库显示”等说法；`src/agents/dialogue_agent.py` 也会把少量系统化记忆短语柔化为自然表达。
- Agent B：二次回复输入会压缩超长 profile/history 和检索条目，并保留或补齐开头 `[emo]/[act]` tag，让第二段在格式上也保持用户可见回复规范。
- Agent B：二次回复 prompt 去掉固定示例句，改为要求先承接 Agent A 的最后一句或核心动作，再自然带入记忆；代码只对明显固定的“我记得/你以前/说到”等集中开头做最小改写，避免 benchmark 层强行套模板导致上下文割裂。
- Agent B1：更新 `prompts/memory_retrieval_workflow.md`，检索只选择对回答、纠正、上下文补充或陪伴续接有明确价值的记忆；普通寒暄、弱相关或会打扰用户的记忆不选。
- Agent B1：新增本地轻量预筛，真实大记忆库下只把相关候选送入 LLM，并对普通问候、重复自嘲、明显急症/用药自调和弱相关窗外观察直接跳过检索。
- Orchestration：更新 `src/services/dialogue_service.py`，流式 `/chat/stream` 在上下文快照准备后先标记 retrieval pending 并调度后台 B1，再进入 Agent A 流式输出；B2 等待 B1 和完整 Agent A 首答，不阻塞 Agent A 的 `delta`。
- Orchestration：更新 `src/coordinator/request_coordinator.py`，允许 retrieval pending 先于 initial reply，并保持 `request_id` / `turn_id` / parent turn 绑定。
- Benchmark/Judge：新增 `scripts/elder_chat_benchmark.py`，使用 DeepSeek OpenAI-compatible 接口，Agent 使用 `deepseek-v4-flash`，judge 使用 `deepseek-v4-pro`；报告包含案例、Agent A、Agent B 决策与回复、检索记忆、延迟指标、judge 分数、失败原因和 `acceptance_passed`。Judge 只接收精简案例视图，并在空评分输出时用最小 prompt 重试一次。
- Benchmark/Judge：默认内置案例从 12 个扩展到 60 个，覆盖亲情、求学、留学、学术、翻译、自然、师生、晚年身体、高风险用药/财务/法律、弱相关闲聊，以及 12 个专门测试 Agent A 尾句与 Agent B 首句衔接的 case。
- Benchmark/Judge：summary 新增 Agent B follow-up 开头重复率统计：只统计有用户可见 B 回复的 case，`followup_opener_max_share` 必须不超过 `25%`，并纳入 `acceptance_passed`。
- Benchmark/Judge：judge 评分范围改为 1-10 分，新增 `a_b_context_continuity`、`tail_head_transition` 和 `judge_average_score`，要求 judge 通过率达到 `90%`、1-10 均分达到 `8.5`，同时保留延迟和 opener 验收。
- Benchmark/Judge：新增本地 `tail_head_transition` 指标，单独拼接 Agent A 最后一小句和 Agent B 第一小句评分；Agent B 复读 Agent A 尾句记为 0，A 以问句收尾但 B 未自然承接会被重罚，benchmark 总均分必须严格超过 `8.8`。
- 文档：新增 `scripts/render_elder_chat_benchmark_dialogues.py`，可把 benchmark JSON 渲染成包含数据位置、时间标注、逐 case 对话、首尾衔接分和 judge 结果的 markdown。
- 文档：更新老年多轮闲聊 benchmark 交付说明，包含环境变量、运行命令、报告目录和退出码。

## 测试说明

默认离线测试不调用真实模型，不依赖五轮信息导入，不写仓库外固定路径。

已验证命令：

```bash
conda run -n memorae python -m unittest discover -s tests -p 'test_*.py'
```

最近验证结果：

```text
Ran 230 tests in 1.811s
OK
```

重点覆盖：

- Agent A tag、1-2 句规范、流式 tag 补齐和截断。
- Agent A prompt 不编造具体事实、为 Agent B 留空间。
- Agent B no_followup、supplement、correction、高风险保守措辞。
- Agent B 用户可见回复不暴露“数据库显示”“记忆库里有”“检索结果显示”等系统化记忆短语。
- Agent B follow-up 固定记忆引用开头和“说到……”集中开头会被改写；benchmark summary 会统计开头重复率并将 25% 阈值纳入验收。
- Judge 使用 1-10 分，覆盖 Agent A/B 上下文连续性、首尾衔接评分、B 回复 tag、均分 8.5 阈值和 90% judge 通过率。
- B1/B2 与 Agent A 流式路径并行启动，不阻塞 `delta` / `done`。
- 多轮并发 request snapshot 不串线，旧请求 follow-up 通过 conversation follow-up stream 独立投递。
- retrieval / follow-up 失败不影响已经发出的 initial reply。
- SSE `meta`、`delta`、`done`、`followup` 事件兼容。
- benchmark helper 的 token 估算、延迟窗口、summary、not-run 报告和 CLI 退出码。

## Benchmark 使用

真实模型 benchmark 需要本地配置：

```env
APP_DB_PATH=data/app.db
APP_DATA_DIR=data
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_API_KEY=<set-locally>
AGENT_MODEL=deepseek-v4-flash
JUDGE_MODEL=deepseek-v4-pro
```

运行：

```bash
conda run -n memorae bash -ilc 'CHAT_DEBUG_PROMPTS=0 python scripts/elder_chat_benchmark.py'
```

退出码：

- `0`：真实 judge 通过率达到 90%、judge 1-10 均分达到 8.5、首尾衔接均分严格超过 8.8、延迟通过率达到 80%，且 Agent B follow-up 开头最高重复占比不超过 25%。
- `1`：真实 benchmark 已运行，但未达到上述验收阈值。
- `2`：缺少 `DEEPSEEK_API_KEY`，只生成 `run_status=not_run` 报告。

## 当前 Benchmark 状态

已在 2026-06-02 使用 `bash -ilc` 继承 shell 环境中的 `DEEPSEEK_API_KEY` 和
`DEEPSEEK_BASE_URL`，并显式关闭 prompt debug 后执行完整 60 例真实 DeepSeek benchmark：

```bash
conda run -n memorae bash -ilc 'CHAT_DEBUG_PROMPTS=0 python scripts/elder_chat_benchmark.py'
```

通过报告：

- `data/benchmark-results/elder-chat-benchmark-20260602-174645.json`

完整测试对话范例：

- `docs/elder-chat-benchmark-dialogues-20260602-174645.md`
- `docs/elder-chat-benchmark-dialogues-latest.md`

该报告确认：

- `case_count=60`
- `judge_pass_count=54`
- `judge_pass_rate=90.0%`
- `judge_pass_rate_threshold=90.0%`
- `judge_average_score=8.84`
- `judge_average_score_threshold=8.5`
- `tail_head_transition_count=48`
- `tail_head_transition_score=9.75`
- `tail_head_transition_score_threshold=8.8`
- `tail_head_transition_threshold_met=true`
- `latency_pass_count=51`
- `latency_pass_rate=85.0%`
- `latency_pass_rate_threshold=80.0%`
- `followup_opener_count=48`
- `followup_opener_max_share=6.2%`
- `followup_opener_max_share_threshold=25.0%`
- `judge_pass_threshold_met=true`
- `judge_average_score_threshold_met=true`
- `latency_threshold_met=true`
- `followup_opener_threshold_met=true`
- `acceptance_passed=true`
