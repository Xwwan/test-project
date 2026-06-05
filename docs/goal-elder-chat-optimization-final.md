# 老年多轮闲聊双 Agent 优化最终 Goal

## `/goal` Objective

优化本仓库的老年多轮闲聊双 Agent 回应流程，使系统能在不等待长期记忆检索的情况下先接住老人当下的话，再尽快用真实记忆生成自然续接。

最终效果不是两个 agent 各自生成完整回答，而是形成一次自然、连贯、像真人陪聊的完整回应：

- Agent A 负责即时承接：老人说完后马上输出 1-2 句温和、自然、低风险的话，先接住情绪和话题，不把话说满，不抢 Agent B 的空间。
- Agent B 负责记忆增强续接：在 Agent A 输出期间并行完成记忆检索、是否续接判断和第二段生成；只在有必要时回复，并把检索到的真实记忆自然融入聊天。
- Orchestration 负责低延迟编排：Agent A 和 Agent B 的检索阶段应同时启动或尽可能接近同时启动；Agent B 的第二段应尽量在 Agent A 模拟语音播放结束后 1 秒内就绪。
- 评估目标以“能否维护好一段老年陪伴对话”为核心，而不是只验证是否命中了某条记忆。

## 当前代码映射

- Agent A：`src/agents/dialogue_agent.py` 的 `generate_initial_reply` / `generate_initial_reply_stream`，以及 `prompts/dialogue_agent.md` 的即时回复规范。
- Agent B1：`src/agents/memory_retrieval_workflow.py` 的相关记忆选择，以及 follow-up 是否需要生成的判断。
- Agent B2：`src/agents/dialogue_agent.py` 的 `generate_followup_reply`，以及 `prompts/dialogue_followup_decision.md` 的第二段续接生成。
- Orchestration：`src/services/dialogue_service.py` 的 `/chat`、`/chat/stream`、后台检索、follow-up 发布和 `request_id` / `turn_id` 绑定。
- 评估与测试基础：现有 `tests/test_dialogue_agent.py`、`tests/test_memory_retrieval_workflow.py`、`tests/test_dialogue_service.py`、`tests/test_latency_benchmark.py`，以及需要新增的真实模型 benchmark / LLM judge 脚本。

## 协作与安全边界

这个 goal 在多人协作分支上执行，必须尽可能小范围修改，保持整体逻辑、架构和已有接口与当前项目一致。

必须满足：

- 优先修改与 Agent A、Agent B、流式编排、benchmark/judge 直接相关的文件；不要顺手重构无关模块。
- 保持现有分层：Agent 层不直接访问数据库，service/orchestration 层负责依赖注入和请求绑定，memory repository/schema 不因本 goal 改动。
- 保持现有 API/SSE 事件语义和字段兼容，只允许向后兼容地补充可选字段或新增独立工具脚本。
- 不修改 TTS、STT、音频、ASR、TTS 配置和相关测试。
- 不修改同事可能正在开发的无关页面、路由、工具或实验文件，除非它们是 benchmark/judge 必须新增的独立入口。
- 不执行任何 git 操作，包括 commit、checkout、reset、stash、merge、rebase、clean 等。
- 不执行任何大范围删除命令，不删除项目目录，不删除未知生成物，不清理不属于本 goal 的文件。
- 所有任务命令、测试命令、benchmark 命令和依赖安装都必须在名为 `memorae` 的 conda 环境中执行，优先使用 `conda run -n memorae ...`。
- 禁止使用 `sudo`，禁止系统级包安装或系统目录写入；如缺依赖，只能在 `memorae` 环境内用非 sudo 方式安装，并在需要网络或环境写入时按权限流程请求确认。
- 除了在 `memorae` 环境中安装明确需要的依赖外，不踏出本仓库目录做文件写入。环境变量、API key、真实模型配置应通过用户本地环境或 `.env` / `config/app.local.yaml` 管理，不写入仓库提交内容。
- 现有未跟踪文件或用户/同事改动不得回滚、覆盖或格式化；如果与本 goal 冲突，先说明冲突再处理。
- “五轮信息导入”或类似初始化/引入步骤不纳入自动测试；可以作为 benchmark 前置说明或手动准备步骤，但默认单元测试不得依赖它。
- benchmark 和 LLM judge 可以新增脚本、样例和报告输出目录，但应与默认单元测试分离，避免真实模型调用进入常规测试。

## Agent A 行为要求

Agent A 必须是“先接住”的 agent，不是完整回答 agent。

必须满足：

- 每次回复开头必须保留 `[emo:key][act:key]` 形式的情绪和动作 tag。
- 回复长度按句子约束为 1-2 句，不强行限制字数，但要口语、短、自然。
- 先回应老人这一刻的情绪、语气或话题，不做长篇解释，不提前替 Agent B 展开记忆。
- 可以问轻量、自然的问题，但不能问得太重、太具体，也不能和 Agent B 可能基于记忆给出的续接相矛盾。
- 不要过度补全事实。例如老人说“我小时候可贪玩了”，Agent A 不应自行编出“那时候没有手机电脑，大家都在外面跑”等细节。
- 要为 Agent B 留出空间。好的 Agent A 更像：“哈哈，听起来你小时候一定很有活力呀。那会儿是不是经常一玩起来就忘了回家？”
- 高风险场景必须低风险承接。极端危险、摔倒、自伤、明显急症等场景应优先建议联系家人、医生或紧急服务；外部报警 API 暂不作为本 goal 必做项。
- 用药、健康、法律、财务等非极端但敏感场景，Agent A 可以先说“我想一想”“这个得谨慎些”，为 Agent B 检索相关记忆留出空间，但不能直接给确定性专业建议。

## Agent B 行为要求

Agent B 的目标是“带着记忆继续陪人聊天”，不是生硬地报告检索结果。

必须满足：

- Agent B 不一定每轮都回复。打招呼、弱相关闲聊、无价值记忆、可能打扰用户的场景，应返回 `no_followup`。
- 如果检索到的记忆能明显让回复更懂老人、更有陪伴感、更能引起老人继续聊下去，Agent B 应积极续接。
- 第二段不是固定长度任务，原则上保持自然、口语、不过长；重点是情绪价值、具体感和可继续聊。
- 引用记忆时要柔和，避免让老人感觉被监控。可以使用“你以前说过”“我记得你提过”这类自然表达，但不要出现“数据库显示”“记忆库里有”等系统化说法。
- 记忆真实性是底线但不是唯一评分目标。Agent B 不应编造检索记忆中不存在的具体经历；如果记忆带日期、冲突或不确定性，先交给模型根据上下文判断，必要时用不确定表达。
- 不要把旧记忆当作当前事实。尤其健康、用药、法律、财务场景，必须提示不确定性，并建议以医生、家人或专业人士意见为准。
- Agent B 需要能接住 Agent A 的语气，不重复问候，不否定 Agent A，不突兀切换话题。

## Orchestration 要求

核心目标是让 Agent B 的耗时隐藏在 Agent A 的输出和模拟语音播放时间里。

必须满足：

- 流式路径优先优化。用户消息进入、上下文快照准备好后，应尽早同时启动 Agent A 流式回复和 Agent B1 记忆检索，不再等 Agent A 完整输出后才开始检索。
- Agent B2 生成需要同时拿到 Agent A 的完整即时回复和 B1 检索结果。谁先完成就等待另一个，但不得阻塞 Agent A 的流式 `delta` 输出。
- 保持现有 `request_id` / `turn_id` 绑定，避免多轮并发、乱序返回、用户切话题时串线。
- 保持现有 SSE/API 协议兼容。现有 `meta`、`delta`、`done`、`followup` 事件不能破坏；如需要增加延迟指标或状态事件，只能做向后兼容的补充。
- 当前 follow-up 作为独立事件和独立 assistant turn 的结构可以保留，但前端/使用者应能把它理解为同一轮回答的第二段或紧随其后的插入续接。
- 禁止修改或启用 TTS/STT 相关模块作为本 goal 的一部分；延迟评估只模拟语音播放时间。
- 极端危险场景的报警 API 可预留扩展点，但本 goal 不要求接入真实报警服务。

## Benchmark 与 LLM Judge

本 goal 必须新增真实模型 benchmark / judge 能力，用来评估“对话维护效果”，不是只看单元测试。

评估对象：

- 默认服务对象围绕季羡林先生展开，使用 `APP_DB_PATH=data/app.db` 中已有的季羡林相关记忆。
- benchmark 可以设计不同类别的 `User.md` / 场景配置，但记忆核心始终围绕季羡林先生。
- 案例要设想季羡林可能会问什么、聊什么、期待怎样的回应，包括家常、回忆、身体、读书、写作、饮食、幽默争论、孤独、重复表达、沉默低落等开放场景。

LLM judge 需要多维打分，至少覆盖：

- Agent A 是否短、自然、先接住情绪，并为 Agent B 留出空间。
- Agent A 是否合理引入或不阻碍 Agent B。
- Agent B1 是否正确判断需要第二段回复。
- Agent B2 是否自然衔接 Agent A，而不是另起炉灶。
- Agent B2 是否提供足够情绪价值，符合老年陪伴系统设定。
- Agent B2 是否灵活、像真人、有具体感，而不是古板地复述记忆。
- 两段合起来是否能引起老人继续回复的兴趣。
- 高风险场景是否足够保守。

通过标准：

- 单元测试和离线 fake 测试必须稳定通过。
- 真实模型 benchmark 中，按 judge 总体结论计算，至少 80% 案例达到通过标准。
- 延迟 benchmark 中，按 Agent A 文本长度以每 token 300ms 模拟播放；Agent B2 在 Agent A 模拟播放结束后 1 秒内就绪的案例比例至少达到 80%。
- benchmark 输出需要保存案例输入、Agent A 输出、Agent B 决策、Agent B2 输出、延迟数据、judge 分数和失败原因，便于复盘。

## 测试要求

保留现有 `unittest` 风格，同时新增面向对话质量的案例评估。

必须覆盖：

- Agent A 输出 1-2 句，并以 `[emo:key][act:key]` 开头。
- Agent A 不把回复说满，不主动编造童年、家庭、健康等具体事实。
- Agent A 在普通闲聊、回忆往事、情绪低落、重复表达、健康/用药风险中都有合理承接。
- Agent B 在打招呼、弱相关记忆、无价值检索时可不回复。
- Agent B 在强相关记忆场景中能自然续接，并让两段话像一次完整回应。
- B1/B2 与 Agent A 并行编排，不阻塞 Agent A 流式输出。
- 并发多轮请求不串线，旧请求的 B2 不误接到新话题上。
- 检索失败或 B2 失败不能影响 Agent A 已经发出的即时回复。
- SSE 协议保持向后兼容。
- 引入/初始化五轮信息导入不加入自动化测试；测试应使用 fake 数据、固定样例或已有数据库状态，避免把导入流程变成单元测试前置条件。
- 真实模型 judge benchmark 可手动运行，不进入默认全量单元测试。

## 运行配置

本 goal 的真实模型测试使用 OpenAI-compatible DeepSeek 接口。

不要把明文 API key 写入代码、测试、文档或提交记录；使用 `.env` 或 shell 环境变量注入。

建议运行变量：

```env
APP_DB_PATH=data/app.db
APP_DATA_DIR=data
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_API_KEY=<set-locally>
AGENT_MODEL=deepseek-v4-flash
JUDGE_MODEL=deepseek-v4-pro
```

模型分工：

- Agent A、Agent B1、Agent B2：`deepseek-v4-flash`
- LLM judge：`deepseek-v4-pro`

如需要接入现有配置文件，优先通过 `config/app.local.yaml` 或环境变量完成，不污染 `config/app.yaml` 中的默认共享配置。

所有测试和工具命令应在 `memorae` 环境中运行，例如：

```bash
conda run -n memorae python -m unittest discover -s tests -p 'test_*.py'
```

## 交付物

本 goal 完成时需要提供：

- 代码修改清单，说明 Agent A、Agent B、orchestration、benchmark/judge 分别改了什么。
- 更新后的 prompt 文档。
- 新增或更新的单元测试。
- 新增真实模型 benchmark / LLM judge 脚本及使用说明。
- 一份 benchmark 结果文件或报告，包含案例结果、分数、通过率、延迟统计和典型失败案例。
- API/SSE 如有向后兼容补充，需要同步更新相关文档。

## 非目标

以下内容不纳入本 goal：

- 不重构记忆数据库 schema。
- 不接入 TTS、STT 或语音播放模块。
- 不实现真实报警、拨打电话或外部通知 API。
- 不把所有老人画像限制为固定几类；测试可以覆盖多种场景，但系统应保持开放。
- 不追求 Agent B 每轮必答；自然、不打扰比强行补充更重要。

## 可直接使用的 `/goal` 文本

```text
/goal objective:
优化本仓库的老年多轮闲聊双 Agent 回应流程，使 Agent A 在用户说完后立即输出 1-2 句带 [emo]/[act] tag 的温和自然承接话，Agent B 在同一请求中并行完成记忆检索、续接判断和个性化第二段生成。最终两段回复必须像一次完整的人类陪伴回应：Agent A 不说满、不编造、不阻碍 Agent B；Agent B 只在有价值时基于真实检索记忆自然续接，重点提升“懂老人、能继续聊”的效果。编排上必须让 Agent B1 与 Agent A 尽早并行，不阻塞 Agent A 流式 delta，并力争 B2 在 Agent A 模拟语音播放结束后 1 秒内就绪。实现后需提供单元测试、真实模型 benchmark、LLM judge 多维评分和结果报告。

scope:
- 更新 `prompts/dialogue_agent.md`、`prompts/dialogue_followup_decision.md`、必要时更新 `prompts/memory_retrieval_workflow.md`。
- 修改 `src/agents/dialogue_agent.py`、`src/agents/memory_retrieval_workflow.py` 中必要的输出规范和保护逻辑。
- 优化 `src/services/dialogue_service.py` 的流式编排，让 Agent A 与 Agent B1 尽早并行，同时保持现有 SSE/API 兼容。
- 增加或调整 `tests/` 中的 fake model、fake memory、orchestration 测试，覆盖并发、失败隔离、tag、短回复、自然衔接和高风险保守行为。
- 新增真实模型 benchmark / LLM judge 脚本，使用 DeepSeek OpenAI-compatible 接口，Agent 用 `deepseek-v4-flash`，judge 用 `deepseek-v4-pro`，API key 通过环境变量注入。
- 严格小范围修改，不重构无关模块，不修改 TTS/STT/音频路径，不改变整体架构和已有接口语义。
- 五轮信息导入或类似初始化流程不加入自动测试，只作为 benchmark 前置说明或手动准备步骤。
- 禁止修改或启用 TTS/STT 相关模块。
- 所有任务、测试、benchmark 和依赖安装都必须在 `memorae` conda 环境中执行。
- 禁止使用 `sudo` 或系统级安装；如需新增依赖，只能在 `memorae` 环境内以非 sudo 方式安装。
- 禁止执行任何 git 操作，禁止执行大范围删除命令；除 `memorae` 环境依赖安装外，禁止写入本仓库以外的文件。

acceptance:
- Agent A 每次回复以 `[emo:key][act:key]` 开头，保持 1-2 句，能接住情绪和话题，并为 Agent B 留空间。
- Agent B 可以选择 `no_followup`；需要续接时，B2 与 Agent A 自然衔接，不重复问候、不突兀、不生硬报告记忆。
- 高风险场景保守处理，极端危险建议联系家人、医生或紧急服务，用药/健康/法律/财务不做确定性专业建议。
- 流式编排证明 Agent B1 与 Agent A 并行启动或尽早启动，Agent B 不阻塞 Agent A 的 `delta` 和 `done`。
- 现有 SSE/API 事件保持向后兼容，`request_id` / `turn_id` 绑定正确，多轮并发不串线。
- 修改范围只覆盖本 goal 直接相关文件，保留原有框架、分层和依赖注入方式，不回滚或覆盖同事/用户已有改动。
- 离线单元测试稳定通过；真实模型 benchmark 中至少 80% 案例通过 LLM judge；延迟 benchmark 中至少 80% 案例的 B2 在 Agent A 模拟播放结束后 1 秒内就绪。
- 最终提交包含修改清单、prompt 更新、测试说明、benchmark 结果和典型案例报告。
```
