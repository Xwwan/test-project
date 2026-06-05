# 老年多轮闲聊首尾衔接更新记录（2026-06-05）

本文记录本次老年多轮闲聊双 Agent 优化的完整更新范围、benchmark 数据位置、逐轮对话范例位置和最终验证结果。更详细的交付说明见 `docs/elder-chat-optimization-delivery.md`。

## Benchmark 数据在哪

- Benchmark case 定义：`scripts/elder_chat_benchmark.py` 的 `DEFAULT_CASES`
- 最终完整 JSON 报告：`data/benchmark-results/elder-chat-benchmark-20260602-174645.json`
- 最终逐轮对话范例：`docs/elder-chat-benchmark-dialogues-20260602-174645.md`
- 最新逐轮对话别名：`docs/elder-chat-benchmark-dialogues-latest.md`
- 对话 markdown 生成脚本：`scripts/render_elder_chat_benchmark_dialogues.py`

`docs/elder-chat-benchmark-dialogues-20260602-174645.md` 和 latest 文件已经按 case 展示每一轮输入、Agent A 即时回复、Agent B 检索/决策/二次回复、首尾衔接分、judge 分数、延迟和时间标注。

## 本次更新范围

- Agent A prompt：要求即时回复以 `[emo:key][act:key]` 开头，保持 1-2 句自然中文口语，避免编造童年、家庭、健康、用药、财务等具体事实，并给 Agent B 的记忆续接留空间。
- Agent A 代码：非流式和流式初始回复都会保留或补齐 `[emo]/[act]` tag，并限制为前 1-2 句。
- Agent B prompt：二次回复改为同一轮回答的自然第二段，要求先承接 Agent A 最后一小句或核心动作，再自然带入记忆；避免重复问候、否定首答、系统化汇报记忆或使用“数据库显示”等说法。
- Agent B 代码：二次回复会压缩超长 profile/history/检索条目，保留或补齐开头 tag，并柔化“我记得/你以前/说到/数据库显示/检索结果显示”等集中或系统化开头。
- Agent B1 检索 prompt：只选择对回答、纠正、上下文补充或陪伴续接有明确价值的记忆；普通寒暄、弱相关或会打扰用户的记忆不选。
- Agent B1 本地预筛：真实大记忆库下先做轻量候选筛选，再交给 LLM；普通问候、重复自嘲、明显急症/用药自调和弱相关窗外观察直接跳过检索。
- Orchestration：`/chat/stream` 在上下文快照准备后先标记 retrieval pending 并调度后台 B1，再进入 Agent A 流式输出；B2 等待 B1 和完整 Agent A 首答，不阻塞 Agent A 的 `delta`。
- Request coordinator：允许 retrieval pending 先于 initial reply，并保持 `request_id`、`turn_id` 和 parent turn 绑定，避免多轮并发串线。
- API/调试页：保留 SSE `meta`、`delta`、`done`、`followup` 事件兼容，并新增/更新 `src/api/static/chat_debug.html` 方便本地观察首答和二次回复。
- Benchmark/Judge：新增真实模型 benchmark 脚本，Agent 使用 `deepseek-v4-flash`，judge 使用 `deepseek-v4-pro`；报告包含案例、Agent A、Agent B 决策与回复、检索记忆、延迟指标、judge 分数、失败原因和 `acceptance_passed`。
- Benchmark case：默认案例从 12 个扩展到 60 个，覆盖亲情、求学、留学、学术、翻译、自然、师生、晚年身体、高风险用药/财务/法律、弱相关闲聊和首尾衔接专项 case。
- Benchmark 指标：judge 评分改为 1-10 分，新增 `a_b_context_continuity`、`tail_head_transition`、`judge_average_score`、Agent B opener 多样性和本地首尾拼接评分。
- 文档：新增逐轮对话 markdown 渲染脚本、最终逐轮对话文档、交付说明和本文索引。
- 测试：补充 Agent A/B、B1/B2 编排、request coordinator、benchmark helper、latency benchmark、memory retrieval workflow 等单元测试。

## 新增首尾衔接专项 case

本次新增 12 个 `category="首尾衔接"` 的专项 case：

- `tail_family_warmth`
- `tail_students_visit`
- `tail_teacher_specific`
- `tail_hospital_answer`
- `tail_departure_bridge`
- `tail_reading_not_question`
- `tail_old_friend_empty`
- `tail_birthday_awkward`
- `tail_lotus_image`
- `tail_translation_flow`
- `tail_finance_safety`
- `tail_legal_safety`

这些 case 专门检查 Agent A 尾句和 Agent B 首句拼起来是否像同一轮自然连续回答，避免 Agent B 复读 Agent A 尾句、空泛桥接、突然堆资料，或在高风险场景里冲淡安全建议。

## 验收口径

真实 benchmark 退出码为 `0` 需要同时满足：

- judge 通过率达到 `90%`
- judge 1-10 均分达到 `8.5`
- 首尾衔接均分严格超过 `8.8`
- 延迟通过率达到 `80%`
- Agent B follow-up 开头最高重复占比不超过 `25%`

缺少 `DEEPSEEK_API_KEY` 时脚本退出码为 `2`，只生成 `run_status=not_run` 报告，不能作为验收通过结果。

## 最终验证结果

离线单元测试：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

结果：

```text
Ran 230 tests in 1.811s
OK
```

真实 DeepSeek benchmark：

```bash
conda run -n memorae bash -ilc 'CHAT_DEBUG_PROMPTS=0 python scripts/elder_chat_benchmark.py'
```

最终报告 `data/benchmark-results/elder-chat-benchmark-20260602-174645.json` 确认：

- `case_count=60`
- `judge_pass_count=54`
- `judge_pass_rate=90.0%`
- `judge_average_score=8.84`
- `tail_head_transition_count=48`
- `tail_head_transition_score=9.75`
- `latency_pass_count=51`
- `latency_pass_rate=85.0%`
- `followup_opener_count=48`
- `followup_opener_max_share=6.2%`
- `acceptance_passed=true`

## 提交范围

本次提交应包含以下类别：

- Prompt：`prompts/dialogue_agent.md`、`prompts/dialogue_followup_decision.md`、`prompts/memory_retrieval_workflow.md`
- Agent/Service/Coordinator/API：`src/agents/dialogue_agent.py`、`src/agents/memory_retrieval_workflow.py`、`src/services/dialogue_service.py`、`src/coordinator/request_coordinator.py`、`src/api/routes.py`、`src/api/static/chat_debug.html`
- Benchmark：`scripts/elder_chat_benchmark.py`、`scripts/render_elder_chat_benchmark_dialogues.py`、`data/benchmark-results/elder-chat-benchmark-20260602-174645.json`
- Tests：本次新增或更新的 dialogue、retrieval、coordinator、latency 和 benchmark 单元测试
- Docs：`README.md`、`docs/elder-chat-optimization-delivery.md`、`docs/elder-chat-benchmark-dialogues-20260602-174645.md`、`docs/elder-chat-benchmark-dialogues-latest.md`、本文
