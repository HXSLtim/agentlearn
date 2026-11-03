# 路线

两条线并行：**学习线**（六层，自下而上盖楼）和**基建线**（工程能力，随用随建）。
每层有验收标准，过了才进下一层。当前进度同步在根目录 TODO.md。

## 学习线

### 第 1 层 一次调用 ✅（2026-10-30 完成）

发一条消息，拿回流式回答。内容块、事件流、stop_reason 的形状都亲眼看过。

验收：终端逐字输出思考和回答，最后单独一行 `end_turn`。✅

### 第 2 层 一个工具 + 手写循环 ✅（2026-11-01 完成）

给模型一个 calculator，自己写循环：收到 tool_use → 执行 → tool_result 追加进历史 →
再请求，直到 stop_reason 不是 `tool_use`。

验收：

- 一道多步算术题，模型调工具、拿到结果、给出正确答案；✅
  实测 (123*456)+789：模型调了两轮 calculator，答 56877（正确），
  trace 树 turn → llm_request / tool_call / llm_request / tool_call / llm_request。
- JSONL 里四种块齐全，重开会话能续上；✅ 续接会话直接答出 56877。
- 全程禁止 eval（ast 求值）。✅ 攻击样本（import/dunder/函数调用/巨型幂/变量名）
  全部拒绝，错误结果交回模型而不抛异常。

### 第 3 层 多工具、并行、失败 ✅（2026-11-02 完成）

多个工具同时给；一次回复里并行多个 tool_use 靠 id 配对；工具失败回 `is_error` 让模型调整。
工具集换血：calculator 下架，换成 now_time（无参、永不失败）+ bash（有副作用、会失败）。

验收：

- 并行两问两答互不串线；✅ 一条回复里同时要 now_time 和 bash（算 123*456+789），
  两个 tool_call 挂在同一个 llm_request 下，id 各自配对，答案都正确
  （时间 11:23:44 UTC、56877）。
- 故意让一个工具抛错，模型能换路子或如实报告。✅ 实测两档：
  `cat report.txt` exit 1 → 模型 pwd/ls 定位 → 如实报告不存在并主动看 note.txt；
  更强的一档：模型自作主张 `find /` 扫全盘超时（10s 上限触发）→ 逐步降级到
  朴素命令 → 最终正确回答。超时那次的错误消息还暴露并修掉了一个真 bug
  （shell=True 超时路径 TimeoutExpired 给 bytes，text=True 不生效）。
- 执行仍是串行的（一个跑完再跑下一个）；验收要的是配对不串线，同时执行
  （asyncio/线程池）与工具间资源冲突留到以后。

### 第 4 层 上下文管理 ✅（2026-11-03 完成）

历史变长之后：prompt caching、截断策略、压缩摘要。对应 LangGraph 的 compaction 一类能力。

形态（D22）：JSONL 只追加、永不改写；压缩走派生层——`data/sessions/<id>.summary.json`
存模型生成的摘要和它覆盖到第几条，请求只发「摘要（进 system 第四段）+ 尾部」。
切割只落在轮次边界，配对不变式（D19）不受影响。缓存断点打在最后一条消息上
（够 2048 估算 token 才打，低于最短可缓存长度会 400）。

验收：预算 1000 token 的 7 轮会话（首轮埋暗号），主请求 input 稳定在
236–547（无压缩时会逐轮线性涨），cache_read 爬到 448–512、命中率 655‰；
最后一轮 covered=10（大半历史只剩摘要）仍答对暗号「蓝鲸潜航九号」。

### 第 5 层 MCP ✅（2026-11-03 完成）

工具从外部进程来：起一个 MCP server，把它的工具接进注册表。翻译后仍是同一个 ToolUseBlock。

形态（D23）：手写 stdio 客户端（JSON-RPC 一行一条，不用 SDK），config.ini 的
`[mcp:<别名>]` 段声明服务器，外部工具以「别名__工具名」进注册表，inputSchema
零翻译。进程死了自动重启，坏服务器只跳过不拖垮列表。

验收：不改正循环代码，只加配置，就能用上一个外部工具。✅ 真机：加
`[mcp:cnt]` 一段后模型调 `cnt__count_words` 答对「8 字符、3 词」，span ok；
离线四案（错参、幻觉名、内置不受影响、kill 后重启重握手）全过。

### 第 6 层 Skills ✅（2026-11-03 完成）

按需加载的指令包（名字、说明、正文，必要时带脚本），教 agent 某类任务怎么做。

形态（D24）：`skills/*.md` 一个文件一个技能（简化 frontmatter）；索引
（名字+说明）挂在 `read_skill` 工具的 description 里，是唯一常驻开销；
正文模型自己调工具拉进来，是普通 tool_result，长会话由第 4 层压缩收走。
脚本走 bash。加技能 = 丢文件，零代码改动。

验收：一个没加载 skill 时答不好的任务，加载后明显变好，且不占用常驻上下文。✅
A/B 真机同问「写运行报告」：无技能 0/4 格式标记，有技能先调 read_skill
再输出 4/4（AGENTLEARN 运行报告 / SEC-A / SEC-B / 落款全对）。

## 待排期：意图识别模型（2026-11-03 记入）

在主模型之前加一个小而快的分类模型，先判用户这句话想干什么，再决定怎么走。
动机：现在每句话都进完整的 agent loop（带工具定义、带记忆、带全部历史），
而大量输入其实是闲聊、改口、或一句话就能答的简单问题——分类先行可以把
「要不要进循环、带哪些工具、用哪一档 steering」这些决定从主模型身上拿下来。

候选用途（做的时候挑，不预设全做）：

- 路由：闲聊直接答 / 任务进 agent loop / 明确的命令走快捷路径；
- 工具裁剪：只把相关工具的定义放进请求，工具多了以后省 token（和第 5 层 MCP 衔接）；
- steering 档位建议：判断新消息是补充（cooperative）还是改口推翻（preemptive），
  现在这个选择完全靠前端传参。

约束：分类器是新增的一次模型调用，本身有延迟和成本，省不下主模型的开销就
不值得加；分类结果记进 span（和 effort 一样留痕），错分要能从 trace 里看见。
选型（用谁、什么粒度、失败时的降级）定了记 decisions.md。

## 基建线（与学习线并行，按需启动）

| 项 | 内容 | 状态 |
| --- | --- | --- |
| 会话持久化 | JSONL + SQLite（见 docs/decisions.md D2） | ✅ |
| 配置体系 | config.ini + ${ENV}（D4） | ✅ |
| conversation.py | TUI/desktop 共用 application 层一份会话循环（D9/D14） | ✅ |
| TUI | 黑绿控制台：流式正文、thinking 滚动预览、按事件顺序挂载工具卡、运行账本、窄终端布局；停止、重试、历史搜索、外编辑器与复制闭环（D14/D16） | ✅ |
| trace | spans 表（D10/D11）+ TUI `/trace` 与 desktop 只读抽屉：span 树、耗时、token、工具入参/出参、回答配对 | ✅ |
| 设置界面 | TUI / desktop 共用 `GET/PUT /settings`；只改 `[llm]`，密钥不回显，下一请求生效（D15） | ✅ |
| 快捷命令 | TUI / desktop 输入 `/` 打开面板；模型切换与预置 prompt 均走界面层白名单（D18） | ✅ |
| 跨会话记忆 | `data/sandbox/memory.md` 每轮注入为 system 提示词；不进 JSONL，协议差异在 adapter；span 记 system_chars | ✅ |
| steering | 同会话连发消息三档（D19）：queue 等整轮 / cooperative 圈界并入 / preemptive 检查点打断；spans 加 cancelled；抢占让位于 tool_use/tool_result 配对不变式 | ✅ |
| 执行等级 | approve / auto / yolo（D20）：Shift+Tab 运行中实时切换；approve 每次 Accept 才执行，auto 只放行无副作用工具，yolo 直接跑。不是安全边界 | ✅ |
| 眼睛 | screenshot 工具 + ImageBlock（D25）：图进 tool_result，两方向翻译在 adapter，存储形状与线上形状分家 | ✅ |
| 耐心 | 后台任务四工具 start_job/wait_job/job_status/stop_job（D26）：轮询式、conversation 零改动，进程组连子进程一起杀 | ✅ |
| 会话索引 API | 前端接线契约落地：`GET /sessions`（活动倒序，turns/updated_at/stop_reason 从 spans 推导）、`/sessions/{id}/messages`、`POST /stop`、`/sessions/{id}/trace`；P1 rename/archive/fork/永久删除。desktop 摘掉 localStorage 唯一索引的帽子 | ✅ |
| 主动性（一阶） | 常驻调度 + 三种触发器（interval/file/jobdone）只做消息注入，无人值守钉死 auto 地板、remember 唯一预授权写、uuid5 固定会话、notices 值班日志（D27）；二阶（容器化后放开手脚）未做 | ✅ |
| 评测 | 从 trace 回放打分：工具调用对不对、答案准不准、花了多少 token | 未开始 |
| 桌面端 | app 已接 POST /chat + SSE、server 会话索引与正文恢复、三档 steering、停止、失败重试、运行账本、设置、trace 抽屉和只读变更文件列表；Tauri 自动拉起 server 未做 | 🟡 |

## 文档维护节奏

- `docs/decisions.md`：每做一个选型决定加一条。
- `docs/roadmap.md`（本文件）：每层完成时勾状态、记验收结果。
- 根目录 `TODO.md`：工作队列，随改随动。
- 根目录 `CHANGELOG.md`：每完成一层或一项基建记一版，不逐 commit。
