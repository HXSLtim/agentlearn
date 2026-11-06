# TODO

学习路线总览见 docs/roadmap.md，选型理由见 docs/decisions.md。
这里的条目完成后移进 CHANGELOG，不删历史。

## 未完成（2026-11-03 全量展开）

六层学习线已经完成。这里每条只做一件事，不再按「层 / P0 / P1 / P2」收成一句。
P2 仍是先不做，但逐条列出。desktop 前端重构的半成品还在未提交改动里，提交前先对齐。
文后只保留已完成记录，不再另留未勾项。

审查新发现：

- [x] 压缩预算按整份历史估算，已覆盖的前缀仍被算进去 ✅（2026-11-03）：`plan_cut` 改按「摘要 + covered 之后的尾部」决策。离线验证：covered=2 且尾部在预算内时不切割；covered=0 且全文超预算时仍切到 2。
- [x] 压缩请求失败时退回全文，同时 system 仍带旧摘要，covered 段出现两遍 ✅（2026-11-03）：失败时沿用旧摘要和原 covered 边界，只返回未覆盖尾部；离线假 client 抛错验证不重复发送已覆盖原文，span 记 error。
- [x] trace 把 `purpose=compaction` 的成功 `llm_request` 也拿去配 assistant 正文，后面的回答整体错位 ✅（2026-11-03）：共用配对函数识别 `purpose=compaction` 并返回空回答；web 的 dict data 与 TUI 的 JSON 字符串两种形状离线验证，主请求仍按顺序配对。
- [x] `tool_use` / `tool_approval` 已经 yield、工具结果还没落盘时客户端断开，JSONL 留下没有 `tool_result` 的 `tool_use` ✅（2026-11-03）：三个工具事件挂起点统一补齐缺失结果并落盘一条 user(tool_result)；离线生成器在首个 tool_use 事件处 close，两个 tool_use 均有 is_error 配对。span 仍按 D11 留证，不混入 aborted 统计。
- [x] `turn(append_user=False)` 收到 `images` 时静默丢掉 ✅（2026-11-03）：conversation 在读取历史、创建 turn span 和写 JSONL 前直接拒绝非空图片；离线验证抛 ValueError 且 transcript/spans 均为空。
- [x] `append_memory` 只 `strip` 首尾，中间换行会把一条 remember 写成多行 ✅（2026-11-03）：写入前把空白折叠成空格；离线验证 CRLF/LF 输入均只追加一行，既有文件缺结尾换行时仍先补齐。
- [x] file 触发器可以指向 `memory.md`，remember 写入会把自己再次触发 ✅（2026-11-03）：`build_trigger` 按 resolve 后的真实路径拒绝记忆文件，符号链接同拦；普通文件触发器不受影响。

眼睛、耐心、MCP：

- [x] 任务账本落盘，并在启动时认领 `data/sandbox/jobs/` 里还活着的进程 ✅（2026-11-03）：每个任务写 JSON 元数据；desktop lifespan 按 PID + 进程启动 token 认领，token 不一致报不可接管。离线验证 running 重启后仍可 stop、finished 保留 exit、伪造 token 拒绝接管。
- [x] 后台任务加墙上寿命，超时杀整个进程组 ✅（2026-11-03）：默认 30 分钟，janitor 到期杀进程组并把原因写进任务 log 与元数据；离线把检查周期和寿命调短，验证进程退出、状态非 running、log 含寿命原因。
- [x] desktop 渲染用户图片和 `tool_result` 上的图 ✅（2026-11-03）：恢复正文时解析顶层 image 与 `tool_result.image`，SSE 先用 `has_image` 占位并在轮次完成后重读 JSONL 回填；图片 MIME/base64 白名单校验，380px 宽度下用户图与截图 JPEG 均实际显示且无横向溢出。
- [x] MCP `tools/call` 的 image 内容块转成 `ImageBlock` ✅（2026-11-03）：MCP client 把首个 `image` 内容块映射为 D25 单图契约，`run_tool` 透传；真 MCP 子进程离线验证图片进 JSONL、下一圈请求和 Anthropic 出站形状，tool span 图片相关字段只有 `image_bytes`。
- [x] 同一个 MCP 别名的 `tools/call` 在响应回来之前串行 ✅（2026-11-03）：MCPManager 按别名加调用锁，`tools/list` 与同别名调用互斥，不同别名仍并行。真 stdio 子进程并发验证慢/快调用结果各归各、事件顺序为 slow end 后 fast start，双别名总耗时约一次慢调用。
- [x] MCP 子进程异常时返回形状与 `run_tool` 契约不一致 ✅（2026-11-03）：异常分支从二元组补齐为 `(text, is_error, image)`，不再让 `run_tool` 在 assistant `tool_use` 已落盘后解包失败。离线用缺失别名验证 `MCPManager.call` 与 `run_tool` 返回同一条三元组错误结果，`compileall` 通过。

主动性第二阶段：

- [ ] 把 bash / start_job / screenshot 放进只挂 sandbox 卷的容器。验收：容器内读不到宿主 `~/.zshrc` 和项目外密钥，sandbox 内文件仍可写回。依据：`sandbox.py` 写明 cwd 不是边界。这是对外绑非本机地址、以及无人值守升到 yolo 的共同前置。
- [x] 触发器加频率熔断 ✅（2026-11-03）：按触发器维护 5 分钟窗口，前 3 次成功轮可执行，第 4 次进入 15 分钟冷却；冷却期触发被消费并写 `notices.log`，但不发起模型轮。离线假触发器 + 可控时钟验证第 4 次丢弃、冷却期继续丢弃、冷却后恢复；重启持久化仍是下一条。
- [x] 无人值守加 token 或成本预算熔断 ✅（2026-11-03）：Conversation 按轮累计主请求与压缩请求的 usage 实数（input/cache read/cache write/output），调度器按触发器维护 24 小时 10 万 token 滚动预算；越界后写 notices 并暂停后续请求，窗口过期自动恢复。没有价目，不计算金额。离线验证工具循环累计 75 token，以及 60k+50k 后两次触发不请求、窗口后恢复。
- [x] 熔断计数、冷却截止时间和已用预算落盘 ✅（2026-11-03）：`data/trigger_states.json` 原子保存频率窗口、失败计数、token 用量、频率冷却与预算截止；加载用墙钟 epoch 并过滤过期记录。离线两次重建 Runner 验证 3/300s 冷却与 60k+50k token 熔断均不从零开始。
- [x] 无人值守 `auto` 的副作用工具会空等人工审批 300 秒 ✅（2026-11-03）：触发器轮改用零秒 `ApprovalBroker`，没有界面消费者时副作用工具立即按审批超时拒绝，`tool_result` 照常交回模型；人工 web 会话仍使用原全局 broker。离线假 Conversation 验证 `auto + bash` 立即得到 `None`，无人值守轮完成，`compileall` 通过。
- [ ] 只有容器和熔断都可用时才允许无人值守 yolo。验收：前置不满足时启动拒绝；满足时写操作只发生在容器里。

desktop / TUI 还没接上的界面：

- [x] desktop diff 工作台列出变更文件 ✅（2026-11-03）：新增只读 `GET /workspace/diff`，用 `git status --porcelain=v1 -z --untracked-files=all` 且 `GIT_OPTIONAL_LOCKS=0`；desktop 增加 DIFF 抽屉，显示已跟踪修改/新增/删除/重命名和旧路径。临时 Git 仓库验证默认排除未跟踪、显式可含未跟踪，调用前后 `.git/index` SHA256 不变；`npm run build`、`compileall`、TestClient API 通过；380×900 CDP 截图人工检查 3 条文件记录，document/body/summary/list `scrollWidth` 均为 380。
- [x] desktop diff 工作台显示逐行增删 ✅（2026-11-03）：`GET /workspace/diff` 对每个已跟踪文件读取 `git diff --no-ext-diff --no-textconv --no-color --unified=3 HEAD`，解析 hunk、context/add/delete/meta、旧新行号、增删计数、binary 与截断标记；desktop 用行号 + 红删绿增渲染。临时 Git 仓库验证 context/add 行号内容正确；TestClient 验证当前 `app/src/main.ts` 有真实 hunk；`compileall`、`npm run build` 通过；380×900 CDP 截图人工检查，正文列 188px 单行显示，document/body/list `scrollWidth` 均 380。
- [x] desktop diff 工作台显示未跟踪文件 ✅（2026-11-03）：`include_untracked=true` 时用 `git diff --no-index /dev/null <path>` 生成全新增 hunk，desktop 请求该档位并显示 `UNTRACKED`、路径与新增行。临时 Git 仓库验证未跟踪文件产生 `new_line=1` 的 add 行且 `.git/index` SHA256 不变；TestClient、`compileall`、`npm run build` 通过；380×900 CDP 截图人工检查未跟踪文件可见，document/body/list `scrollWidth` 均 380。
- [x] desktop diff 工作台可以按文件折叠 ✅（2026-11-03）：每个文件改为原生 `<details open>`，摘要行保留状态、路径、旧路径、增删计数、binary/truncated 标记与折叠箭头，点击文件头只折叠该文件 hunk，不写工作区。`npm run build` 通过；380×900 CDP 点击第二个文件后 `open=[true,false]`、可见 hunk 从 2 变 1、闭合内容 `display:none`，document/body/list `scrollWidth` 均 380；截图人工检查折叠后的摘要行。
- [x] TUI 增加 `/diff`，打开只读 pager ✅（2026-11-03）：命令面板新增 `/diff`，ModalScreen 复用 `changed_files(include_untracked=true)` 渲染根路径、已跟踪/未跟踪计数、文件状态、旧路径、增删计数、binary/truncated、hunk 与红删绿增行；支持 ↑↓/Page↑↓/Home/End/R/ESC，不写文件。Textual headless 验证打开当前工作区、向下滚动 `scroll_y=1`、reload、ESC 关闭，调用前后 `.git/index` SHA256 不变；`compileall` 通过；导出 TUI 截图并人工检查。
- [x] desktop 内容搜索接 `GET /sessions/search`，展示每个会话的第一处命中 ✅（2026-11-03）：恢复框输入非空关键词时同时保留本地 id/标题/模型/stop reason 匹配与 server 正文命中，异步防串包；每个正文命中会话在列表行展示第一处 snippet，空词清空正文结果。`npm run build` 通过；380×900 mock 页面验证请求 `q=blueprint` 后只保留正文命中会话、snippet 可见，document/body/list `scrollWidth` 均 380，截图人工检查。
- [x] desktop 会话重命名，接 `PATCH /sessions/{id}` ✅（2026-11-03）：恢复框对当前选中会话提供 RENAME 表单，空标题前端拦截；保存时发送 `{"title":"..."}` 到 PATCH，成功后用 server 返回标题更新本地索引、列表和当前页头，Cancel/Esc 关闭表单。`npm run build` 通过；380×900 mock 验证 PATCH body、200 响应、表单隐藏、新标题显示，document/body/list `scrollWidth` 均 380，表单与保存后截图人工检查。
- [x] desktop 会话 pin，接同一 PATCH，列表 pin 优先 ✅（2026-11-03）：恢复框对当前选中会话提供 PIN / UNPIN，发送 `{"pinned":true|false}` 到 PATCH，成功后同步本地索引并保持选中；左侧会话列表与恢复列表均按 pin 优先于最近活动排序。`npm run build` 通过；380×900 mock 页面验证 PATCH body、200 响应、置顶排序、UNPIN/PIN 标记，document/body/list `scrollWidth` 均 380；截图人工检查。
- [x] desktop 会话 archive，接同一 PATCH ✅（2026-11-03）：恢复框增加 ACTIVE / ARCHIVED 视图；归档发送 `{"archived":true}` 到 PATCH，默认恢复列表与左侧会话列表都移除该会话，归档视图继续列出并可直接恢复正文；归档视图提供 UNARCHIVE。`npm run build` 通过；380×900 mock 验证 GET active/archived、PATCH body、默认列表消失、归档视图打开 user/assistant 正文，document/body/list `scrollWidth` 均 380；归档视图与打开后截图人工检查。
- [x] desktop 永久删除前确认，再 `DELETE /sessions/{id}` ✅（2026-11-03）：恢复框保留独立的 ARCHIVE 与 DELETE 按钮；DELETE 先显示不可恢复警告，CANCEL 不发请求，二次确认后才调用 DELETE 并移除本地索引，若删除当前会话则切到活跃会话或新建空会话。`npm run build` 通过；380×900 mock 验证确认前无 DELETE、取消无 DELETE、确认后恰好一次 DELETE 且列表移除，document/body/list `scrollWidth` 均 380；确认框与删除后截图人工检查。
- [x] desktop fork，选择前 N 条消息后 `POST /sessions/{id}/fork` ✅（2026-11-03）：恢复框提供 FORK 表单，先读取源 JSONL 消息得到真实总数，数字选择器限制 `through=1..N` 并预览第 N 条；提交发送 `{"through":N}`，成功后刷新索引、切到新分支会话并按需恢复正文。`npm run build` 通过；380×900 mock 验证源消息数 4、选择 3、POST body、新会话只含前 3 条，document/body `scrollWidth` 均 380；表单与新分支截图人工检查。
- [x] 两端运行账本显示每轮 `input` / `output` / `cache_read` / `cache_write` / `cache_hit_ratio` ✅（2026-11-03）：Conversation 在每次成功模型请求后累计本轮 usage 并发出 `usage` 事件，TUI 与 desktop 运行账本显示五个实数；缓存命中率按整轮输入合计重算。修正 `context_report.last_usage` 旧字段名导致永远为空的问题。desktop 1180px 以下增加 LOG 右侧 overlay。离线假模型两次请求验证累计 11/11/29/30/414‰ 与两个 `llm_request` span 的四项 token 总和一致；TUI headless 检查五个值并导出截图，380×900 mock SSE 检查 desktop 五个值、document/body `scrollWidth` 均 380 并截图人工检查；`npm run build`、`compileall`/`py_compile` 通过。
- [x] 成本只在有一份显式价目时显示金额 ✅（2026-11-03）：新增可选 `[pricing:<model_id>]`，五个键必须显式配置且为非负有限数；没有匹配当前模型的价目时 `SettingsSnapshot.pricing=None`，TUI 显示“不算钱（无价目）”、desktop 显示 `NOT CALCULATED`，都不估算美元。TUI 用 `Decimal` 按本轮 input/output/cache read/cache write 计算；desktop 在启动与设置保存/切模型后同步价目并显示金额。配置矩阵（无价目、有效价目、负数、非数字、缺 currency、NaN）与 TUI headless 两态通过；380×900 mock SSE 下 usage 11/11/29/30/414‰，无价图为 `NOT CALCULATED`、有价图为 `0.0003192 USD`，document/body 宽度均 380，截图人工检查；`npm run build`、`compileall`/`py_compile` 通过。
- [x] 两端在 `context_report` 接近预算时告警一次 ✅（2026-11-03）：Conversation 与 `/context` 共用同一份「摘要 + 未覆盖尾部」估算，成功模型请求后达到预算 80% 时发出 `context_warning`；每个会话写入一条 `context_warning` span 作去重事实，web 每轮新建 Conversation 也不会重复发。TUI 在时间线显示 warning 卡并通知一次，desktop 弹 toast 且 RUN LOG 显示 `CONTEXT NEAR BUDGET`。离线假模型 4 个流片段只产生 1 条 warning（事件序 `text×4 → usage → context_warning`），第二次新建 Conversation 不重复且 span 仍只有 1 条；TUI headless 截图人工检查；380×900 mock SSE 验证 toast 与 RUN LOG 各一条、document/body 宽度均 380 并截图人工检查；`npm run build`、`compileall`/`py_compile` 通过。
- [x] desktop `@file` 路径补全 ✅（2026-11-03）：新增只读 `GET /workspace/paths`，用 `git ls-files --cached --others --exclude-standard` 只取 tracked/untracked 路径名并按前缀过滤，忽略 ignored 文件，响应只有 root/prefix/paths/total/limited，不读取文件内容。desktop 在光标处活跃 `@token` 时打开文件面板，120ms 防抖并用序号 + tokenStart 防串包；↑↓ 选择，Enter/Tab/点击只把路径文本替换进 prompt，第二次 Enter 才 POST `/chat`；斜杠命令面板行为保留。临时 Git 仓库验证 tracked/untracked/ignored/prefix/limit 与确定性排序，TestClient 验证响应形状和 `limit=0/101` 422；380×900 mock 验证请求日志、Enter/Tab、二次 Enter 才发 `/chat`、`/config` 回归与 380px 宽度，截图人工检查；`npm run build`、`compileall`/`py_compile` 通过。
- [x] desktop 拖拽图片，作为 `POST /chat` 的 `images` 发送 ✅（2026-11-03）：composer 支持拖入 PNG/JPEG/WebP/GIF，前端用白名单 MIME + base64 校验生成待发送缩略图，可逐张移除；拖入后自动聚焦 prompt，图片可单独发送，也可随文字一起发送。带图时前端强制当前会话处于 queue，否则拦截并提示切档；queue 下运行中同样排队发送。desktop RETRY 修正为真正发送 `retry:true`、`question:""` 且不带新 `images`。380×900 mock 验证 cooperative 拦截无请求、queue 请求携带原图 base64、失败后再次拖图点 RETRY 时第二个请求为 queue + retry 且无 `images`，document/body `scrollWidth` 均 380，截图人工检查；TestClient 验证后端 `images+retry` 与 `images+cooperative` 均 422；`npm run build` 通过。
- [x] desktop 粘贴图片，契约与拖拽相同 ✅（2026-11-03）：prompt 捕获 paste 事件，只处理 clipboard 里 `kind=file` 且 MIME 在既有白名单内的图片，进入同一份待发送缩略图/移除/base64 校验管线；纯文本 paste 不 `preventDefault`。发送、queue 限制、图片-only、与 retry 互斥完全复用拖拽实现。380×900 mock 验证文本 paste 未拦截、图片 paste 生成附件、第一个 `/chat` 为 queue 且携带原图 base64、失败后再粘贴并 RETRY 时第二个请求为 `retry:true` 且无新 `images`，document/body `scrollWidth` 均 380，截图人工检查；`npm run build` 通过。
- [x] TUI `/attach` 把图片放进下一轮 user 消息 ✅（2026-11-03）：命令面板新增 `/attach <path>`，按扩展名白名单读取 PNG/JPEG/WebP/GIF 并转 base64 `ImageBlock`；composer 显示待附图数量，图片-only 允许空提交，下一条 user 消息落为 text + image。带图只允许 queue：cooperative / preemptive 提交前拦截；queue 下运行中则作为带图队列项等当前轮结束。retry 仍走 `append_user=False` 且不携带新图，并修复 image-only 失败轮空 question 被误判不可重试的问题。Textual headless 验证 `/attach` 挂起、image-only `turn(images=...)`、失败后 `/retry` 无新图、cooperative 拦截；导出 TUI 截图并人工检查；`compileall`/`py_compile` 通过。
- [x] 两端会话内查找 ✅（2026-11-03）：desktop 新增 `/find [关键词]` 与当前会话查找框，只检索 active thread 已渲染消息（含 thinking 与工具入参/出参文本），↑↓/Enter/点击用 `data-message-id` 滚动定位并高亮对应 article；流式重绘后高亮会重应用，无匹配清空高亮。TUI `/find [关键词]` 直接定位，`/find` 打开当前时间线搜索 modal，选中 UserMessage / AssistantMessage 原 widget 后加高亮并 `scroll_to_widget`。两端均不请求模型、不切会话、不新开会话。380×900 mock 验证 desktop 命中最后一条消息、modal 1 MATCH、定位后目标在滚动窗口内、请求日志只有 `/settings` 且无 `/chat` / `/sessions`，无匹配正常，document/body/list `scrollWidth` 均 380，定位与 modal 截图人工检查；Textual headless 验证直接查找与 modal 查找均命中同一个原 widget、无匹配清空高亮不崩，TUI 两张截图人工检查；`npm run build`、`compileall`/`py_compile`、`git diff --check` 通过。
- [x] 两端 transcript 视图分成 normal / thinking / verbose ✅（2026-11-03）：两端新增 `/transcript [mode]`，空参数按 normal→thinking→verbose 循环。normal 只保留用户消息、回答和审批（desktop 审批卡仍显示工具入参以便决策）；thinking 档显示并展开思考块，非审批工具仍隐藏；verbose 档显示思考块、工具入参和出参。desktop 在流式重绘时沿用当前档位，TUI 用 Screen 类切换已挂载的 ReasoningPanel / ToolCard，不重开会话、不改 JSONL。Textual headless 验证 normal 隐藏 thinking/tool、thinking 显示 `secret reasoning block` 且 tool 隐藏、verbose 显示 `bash(command='echo tool-output-value')` 与 `tool-output-value`，两张截图人工检查；380×900 mock 验证 desktop 三档 DOM：normal/thinking 各 1 个审批卡与 1 份审批入参、thinking 1 个展开 reasoning，verbose 2 个工具卡/2 份入参并包含工具输出，审批按钮全程保留，切换只产生本地命令处理、无 `/chat` / `/sessions` 请求，document/body/list `scrollWidth` 均 380，两张截图人工检查；`npm run build`、`compileall`/`py_compile`、`git diff --check` 通过。
- [x] 两端把当前会话导出为 Markdown ✅（2026-11-03）：共享导出器输出会话元信息、正文、thinking、工具入参/出参与图片 data URI；server 新增只读 `GET /sessions/{id}/markdown` 附件下载，TUI `/export [path]` 默认写 `data/exports/` 且用 `open("x")` 拒绝覆盖，desktop `/export` 用当前 session 触发 `.md` 下载。四段 fence、图片、HTTP 404/422、TUI 默认导出与重复路径拒绝、desktop 380×900 下载内容/文件名/宽度均验证；`/copy` 两端仍只复制最近一条完整回答，不触碰导出内容。
- [x] 审批增加 once ✅（2026-11-03）：TUI / desktop 审批提供 `ACCEPT ONCE`，Web 请求带 `scope:"once"`；离线验证同名工具下一次仍触发审批，`scope` 非法返回 422。
- [x] 审批增加 this session ✅（2026-11-03）：审批提供 `ACCEPT SESSION`，授权只按 `(session_id, tool_name)` 保存在进程内；当前会话后续同名工具免问，别的会话 / 别的工具仍要问，拒绝不建立授权，进程重启失效，approve / auto / yolo 等级判断仍在授权外层生效。离线行为与 HTTP session/422 验证通过；380×900 desktop 截图与 TUI headless 截图均人工检查，POST body 为 `scope:"session"`，TUI 返回 `ApprovalChoice(True, "session")`。
- [x] 拒绝可以附理由 ✅（2026-11-03）：TUI / desktop 审批弹层都提供可选拒绝理由输入，HTTP `POST /approval` 接收 `reason` 并拒绝批准带理由、超 500 字符的请求；理由经 `ApprovalDecision` 回到执行线程，写入 `tool_result` 文本（`Reason: ...`）与 `tool_call` span 的 `denial_reason`。离线假会话验证文本、span 输出与 `denied` 状态；HTTP 验证拒绝带理由 200、批准带理由 / 超长 422；TUI headless 验证理由输入、三个按钮宽度均为 20 并返回 `ApprovalChoice(False, reason="不要改工作区")`；desktop 380×900 验证三枚审批按钮同一行且等宽（差值小于 0.1px）、body/document 宽度 380、点击 DENY 的 POST body 携带同一理由，两端截图人工检查。
- [x] plan mode 的只读探索 ✅（2026-11-03）：新增请求级 `plan_mode`，模型只暴露 `now_time` / `screenshot` / `read_skill` / `wait_job` / `job_status` 白名单；`bash`、`start_job`、`stop_job`、`remember` 与 MCP 工具不暴露，幻觉调用在执行层变成 `plan_blocked` tool_result，tool_call span 记 `plan_mode=true`。离线假模型诱导 `touch` 未执行、marker 不存在、span 为 `plan_blocked`。
- [x] plan mode 产出一份计划文本 ✅（2026-11-03）：最终回答产生独立 `plan` 事件与进程内 `PlanBroker` 记录，TUI 显示计划确认 modal，desktop 显示计划确认卡；不再复用 `boundary` 预置 prompt。TUI 截图与 desktop 380×900 截图均人工检查。
- [x] plan mode 在人确认之后才执行计划 ✅（2026-11-03）：`POST /plans/{id}/confirm` 只记录确认，带 `plan_id` 的 `/chat` 才一次性消费并注入执行轮；未确认 / 重复执行返回 404，`plan_mode + plan_id` 返回 422。TUI Enter 确认后进入待执行；desktop 点击确认后请求顺序为 confirm → `/chat(plan_id)`。未确认前无工作区写入。
- [x] TUI `/status` 补上与 `GET /capabilities` 相同的事实 ✅（2026-11-03）：能力快照抽到 application 层，TUI 与 HTTP 共用同一份工具来源、MCP health、sandbox 边界、Python/provider/model/effort、触发器数量数据；`/status` 保留原会话运行态并追加 `python/provider/triggers/tools/mcp/sandbox/boundary`。TestClient 验证 `/capabilities` 与 `capability_snapshot()` 完全一致；headless `/status` 断言全部事实并截图人工检查。
- [x] desktop 用 RUN LOG 或同等面板渲染 `GET /capabilities` ✅（2026-11-03）：新增 `/capabilities` 命令，在同等命令结果面板展示 Python/provider/model/effort、触发器数量、builtin/skill/MCP 工具来源、MCP health、sandbox 路径与「不是安全边界」说明。380×900 mock 验证全部字段、`body/document scrollWidth` 均 380，截图人工检查。

已知欠账拆开：

- [ ] OpenAI 路径的 thinking `signature` 仍是空串。验收：换到会校验历史的接口之前，先确认它不校验，或者把可回传的签名补上。依据：`llm.py` 写明 OpenAI 流不给 signature。
- [ ] 在准则段后面再打一个缓存断点。验收：只改 `memory.md` 或摘要时，准则前缀仍能 `cache_read`。依据：D21 要求断点在准则后；D22 已实现的是最后一条消息上的断点（`adapter.py` 的 `mark_cache_breakpoint`），这一条还没做。
- [ ] SQLite 打开 `PRAGMA journal_mode=WAL`。验收：读写不再互相堵住；不上连接池、不上 ORM。依据：`store.py` 仍写着这是欠账，连接上没有 WAL。
- [ ] bash 超时改为杀掉进程组，和 `stop_job` 一样。验收：超时后 `sleep && ...` 的子进程不残留。依据：`tools.py` 的 `subprocess.run(timeout=...)` 只杀 shell。
- [ ] 按 session 分开 sandbox 工作目录，或给每个工具一个机器可读的「能否并行」属性。验收：两个会话的相对路径文件互不可见，或者并行执行器会拒绝冲突工具。
- [ ] 一轮里的多个 tool_use 可以真正并行执行。验收：配对仍靠 id，不靠完成顺序；不能并行的工具仍排队。
- [ ] web 在绑定非 127.0.0.1 之前先加鉴权。验收：现在 `uvicorn.run(app, port=port)` 没有 host，默认只本机；没有鉴权时拒绝 `--host 0.0.0.0`。
- [ ] Tauri 启动时拉起 desktop server，并去掉模板 `greet` 命令。依据：`app/src-tauri/src/lib.rs` 只有 greet。
- [ ] 前端 API 地址可配置。验收：`app/src/main.ts` 不再写死 `http://127.0.0.1:8000`，`main.py desktop --port` 改端口后前端能跟上。
- [ ] 替换 Tauri 模板图标为 AgentLearn 图标。
- [ ] 顶层包名出现冲突时，再把 `domain` / `application` / `infrastructure` / `interfaces` 收成 `agentlearn.*`。验收：现在不改；`server/pyproject.toml` 的 `packages.find.where = src` 就是现状。
- [ ] 有成功率统计需求时，再把客户端断开的 `GeneratorExit` 收成 `aborted`。验收：没有这个需求之前，span 继续停在 running（D11）。这一条不和上面的「断连补齐 tool_result」混为一谈。
- [ ] 「墨与琥珀」浅色主题里 11px 琥珀小字（eyebrow / 状态 / 快捷键提示）对纸底只有约 3.5:1，低于 AA 的 4.5:1；大标题与描边不受影响。验收：要么加一档 `--accent-text`（浅色用 `#a85510`、深色仍用 `#eda03c`）只套小字，要么明确记录 accent 小字只是装饰、正文信息不用它。依据：2026-11-05 细节巡检按 WCAG 公式算得，属既有取色，不阻塞当前工作。

路线图后段（层本身已完成，这里是剩下的事）：

- [ ] main.ts 继续按特性拆分：types/dom/slashCommands/api/format/trace/workspaceDiff 已出（2026-11-05，intent-funnel 分支），settings/resume/palette/images/审批还留在 main.ts（4232 行）。模式已立：特性模块自带状态与 DOM 句柄、自接线自己的按钮，从 main 取活绑定（activeThread 等，core 抽取后改指 core）。验收：每步 `npm run build` + 真机 380×900（本机窗口下限 500 亦可）开抽屉截图。
- [ ] 意图分类做路由：闲聊直接答、任务进 agent loop、明确命令走快捷路径。验收：分类器省不下主模型开销就不启用。设计见 docs/superpowers/specs/2026-11-05-intent-funnel-design.md（三层漏斗：规则 → few-shot → 微调兜底）。
- [ ] 意图分类做工具裁剪：请求里只放相关工具定义。
- [ ] 意图分类给 steering 档位建议。验收：前端传入的档位仍可覆盖建议。
- [ ] 分类结果写入 span。验收：错分能在 trace 里看见；分类失败降级为完整 agent loop。
- [ ] 写对照笔记：LangGraph 的 StateGraph / Checkpointer 对上现在的 Conversation、只追加的 JSONL、以及 summary 派生层。

P2（先不做，逐条列出）：

- [ ] P2：后台终端。
- [ ] P2：给人看的长任务界面，不把 `start_job` 工具当成这个界面。
- [ ] P2：任务列表。
- [ ] P2：任务完成通知。
- [ ] P2：待审批通知。
- [ ] P2：通知中心。
- [ ] P2：project / workspace 选择。
- [ ] P2：文件树。
- [ ] P2：编辑器。
- [ ] P2：集成终端。
- [ ] P2：应用预览浏览器。
- [ ] P2：worktree 隔离。
- [ ] P2：并行 agent 编排。
- [ ] P2：跨会话消息。
- [ ] P2：skills 管理界面。现在技能只是 `server/skills/*.md` 加 `read_skill`。
- [ ] P2：plugins 管理界面。
- [ ] P2：MCP server 管理界面。现在只写在 `config.ini` 的 `[mcp:<别名>]`。
- [ ] P2：语音输入。搁置：当前中转模型不吃音频输入块。本地转写是要用再说的备选，不另开一条。
- [ ] P2：云会话。
- [ ] P2：手机遥控。
- [ ] P2：定时任务的产品界面。这和已落地的 interval 触发器不是同一件事。
- [ ] P2：PR 监控。
- [ ] P2：computer use。

## 排队中：同会话消息的 steering（三档）✅（2026-11-03，D19）

用户在同一个会话里连发消息时不能交错写 JSONL。前端做防抖（一次只飞一条），
服务端按三档处理（`application/session_manager.py`，web 进程单例）。
三者的唯一差别是等待颗粒度：一整轮 / 一次请求 / 不等。

术语对照（agent 圈把「执行中途改方向」统称 steering；抢占/协作这对区分
来自操作系统调度，1960 年代的词，比 agent 圈的叫法更精确）：

- [x] queuing（顺延）：本轮 end_turn 后按队列顺序处理新消息。等待单位：一整轮。
      近似先来后到——threading.Lock 不保证 FIFO，不粉饰
- [x] cooperative steering（软中断）：消息入运行轮 inbox 立即 ack；圈界（请求间隙）
      排干、落盘、发 steering_injected，下一圈请求看得见。等待单位：一次请求
- [x] preemptive steering（强制插入）：检查点抛 TurnCancelled，半截回答不落盘，
      span 记 cancelled。等待单位趋近于零：阻塞中的网络读打断不了，上限是
      下一个事件或下一圈圈界；厂商请求挂死时抢占者最多等 120 秒
- [x] SessionManager：session_id → 槽位（串行锁 + 正在跑的轮次 + inbox/取消标志）。
      Store 收归进程级单例并加 RLock（防同一连接上交错 execute/commit；
      复用不消除库级写锁，省的只是建连接开销）
- [x] 随路三件事：spans 加 cancelled 状态（trace TUI 单独着色）；adapter 的
      history_to_anthropic 合并连续 user 消息；工具是否可取消留到 MCP 层
- [x] 配对不变式：工具执行段内不设检查点——assistant(tool_use) 落盘后
      user(tool_result) 必须跟上，否则会话续不上。取消推迟到下一圈圈界
      （离线测试抓到的 bug，见 D19）

## 前端对后端的接线需求（2026-11-03，归属 Claude Code）

前端和 TUI 已经按下面的契约接线；后端由 Claude Code 维护，改实现时不要漂移。

- [x] `POST /chat` 接受客户端生成的 UUID `session_id`，并在流结束的 `done`
      事件里返回同一个 id（含 steering_cancelled 收线路径）。
- [x] 提供 `POST /approval-mode`：
      `{"session_id":"...","mode":"approve|auto|yolo"}`。有运行轮时立即更新
      当前轮，后续工具调用生效；没有运行轮时返回 `active:false`，并让下一次
      同 session 的轮次采用这个等级。
- [x] `POST /approval-mode` 返回
      `{"ok":true,"active":bool,"mode":"...","auto_approved":["..."]}`。
      `auto_approved` 必须是本次切换后已不再需要人工确认、并已被放行的
      `approval_id` 列表；desktop 用它撤掉过期的 ACCEPT/DENY 按钮。
- [x] `tool_approval` 事件保持包含 `id`、`approval_id`、`name`、`input`、
      `mode` 五个字段；`POST /approval` 继续用 `approval_id` 回填。
- [x] 执行等级是当前轮实时状态；思考等级和模型是下一轮状态。当前轮必须固定
      自己的 client/system 快照，运行中切 effort/model 不能污染当前轮。
- [x] `GET /sessions`：按最近活动倒序返回
      `id / title / model / created_at / updated_at / turns / stop_reason`。
      Desktop 不能再把 localStorage 当成唯一会话索引。
- [x] `GET /sessions/{session_id}/messages`：返回可渲染的完整内容块，
      用于 Desktop 首屏恢复；JSONL 仍是唯一事实来源。
- [x] `POST /stop`：请求体 `{"session_id":"..."}`，设置当前运行轮的取消标志，
      返回 `{"ok":true,"active":bool,"cancelled":bool}`；没有运行轮时是幂等
      no-op。停止沿用现有 `TurnCancelled` / `cancelled` 语义，不丢已完成的
      tool_result，也不留下半截 assistant 正文。
- [x] `GET /sessions/{session_id}/trace`：返回 spans 和按 ok `llm_request`
      顺序配对的 assistant 回答，供 Desktop 只读 trace 面板使用。

后端已全部落地（2026-11-03）：/chat 与 approval 三条是执行等级那轮的
既有实现，核对后勾掉；/sessions、/messages、/stop、/trace 这轮补齐——
turns / updated_at / stop_reason 由 Store.list_sessions 用子查询从 spans
推导（派生值不落库），标题从第一条用户消息截断补进 sessions.title
（set_title_if_empty，不覆盖后来的正式名）；/stop 复用抢占档的取消标志
（TurnCancelled 语义，SSE 收 steering_cancelled + done）；/trace 的配对
逻辑 answers_for_requests 从 tui/trace.py 抽到 application/trace_view.py
（web 进程不 import TUI 包）。离线契约测试全过。
- [x] P1 会话管理 ✅（2026-11-03）：`PATCH /sessions/{id}` 管 rename/archive
      （归档 = 软删除，默认列表不出现，`GET /sessions?archived=true` 是归档
      视图）；`POST /sessions/{id}/fork` body `{"through": N}` 保留前 N 条
      消息复制新会话（spans 不复制，分支的树自己长）；永久删除是独立的
      `DELETE /sessions/{id}`（索引 + spans + JSONL + 摘要一起清，有轮次
      在跑时 409）——「收进归档」和「彻底没了」不同动词，混淆留给前端
      按钮设计去防。旧库加列迁移：Store._migrate 幂等 ALTER。
      前端的确认弹窗还没做（属于 desktop 会话库 UI）。

## 前端功能补齐：TUI / desktop（2026-11-03 调研）

定位：TUI 是键盘优先的单会话操作台；desktop 是会话管理与审阅工作台。
两端共享会话语义、运行状态和事件顺序，不追求每个按钮一一对应。

P0 = 单会话能完整干活；P1 = 高频工作流；P2 = 需要新架构的平台能力。

### P0：先补完整闭环

- [x] 停止当前轮（两端，2026-11-03）：TUI 用 `Esc` / `/stop`，desktop 给运行轮
      独立 `STOP`；状态机统一为
      `READY → RUNNING → STOPPING → CANCELLED / ERROR`。
      验收：长请求或工具等待中可停止，已完成的 tool_result 保留，spans 记
      `cancelled`，同会话下一轮仍能继续。
      Desktop 保留 `STEER` 作为运行中发消息入口，另给独立 `STOP`，避免
      一个按钮同时承担“改方向”和“收线”两个语义。
- [x] desktop steering 三档（2026-11-03）：输入台显示当前档位，`Alt+S` 循环，
      `/chat` 请求体带 `steering`，运行中消息显示 ACK / DELIVERED / FAILED。
      验收：运行中连发三条消息，queue / cooperative / preemptive 的等待
      颗粒度分别可见，queued 计数和 ack 状态不丢。
- [x] 服务端会话索引与双端恢复（2026-11-03）：desktop 改用 `GET /sessions`，
      TUI `/resume` 支持按 id、title、model 模糊过滤，列表显示最近活动时间。
      验收：清 WebView 缓存不丢会话，server 重启后两端都能打开同一会话。
- [x] trace 跟着会话走（两端，2026-11-03）：TUI `/trace` 打开当前会话检查器；
      desktop 增加只读 TRACE 侧栏/抽屉，沿用 span 树 + 详情 + 对应回答。
      验收：同一 session 在两端看到的 span 顺序、状态和回答配对一致。
- [x] 失败恢复与重试界面（两端，2026-11-03）：错误卡片常驻，保留已完成的工具结果，
      提供明确的 `RETRY`；重试是新请求，界面不重复渲染旧 user 卡片。
      前端已经不在 UI 里重复插入 user 卡片；真正避免 JSONL 再落一条 user
      消息还需要后端重试契约，见下方“重试去重”。
- [x] TUI 键盘闭环（2026-11-03）：`↑/↓` 输入历史、`Ctrl+R` 反向搜索、
      `Ctrl+G` 外部编辑器、`/copy` 或 `Ctrl+O` 复制最近回答。

- [x] 重试去重 ✅（2026-11-03）：turn() 加 append_user=False 模式——失败轮
      的 user 已落盘，重试对既有历史重新发起请求，不再追加同文 user
      （历史尾部必须是 user：纯文本 = 请求前失败，tool_result = 从工具
      循环中断点续跑）。`POST /chat` 加 retry 字段（推流前校验，尾部
      不是 user 报 409，只支持 queue 档）；TUI 的 /retry 和 RETRY 卡片
      都已接 append_user=False，turn span 记 origin=retry。

P1 / P2 里没做完的条目已逐条拆到文首，这里只留已完成记录。

- [x] desktop 多会话运行状态（2026-11-03）：会话轨显示 streaming / waiting approval /
      error / done，可切换查看而不取消后台轮次；运行态已按 thread 独立持有。

## 排队中：agent 自盘点的五个缺口（2026-11-03）

来源：让 agent 用 bash 盘点自己的能力，它报了五个缺失（眼睛、耳朵、耐心、
记忆的另一半、主动性）。下面按学习性价比排，不按它渴望的程度：

- [x] 跨会话记忆注入 ✅（2026-11-03）：data/sandbox/memory.md 每轮 turn 读一次，
      拼成 system 提示词随每次请求重发；不落 JSONL（派生状态不是历史），
      轮内多圈共用同一份（前缀稳定，第四层缓化的入口）；协议差异收在 adapter
      （Anthropic 顶层 system / OpenAI 首条 role=system）。system prompt 这笔
      基建随此落地。验收：全新会话直接答出只写在 memory.md 里的暗号，
      llm_request span 记 system_chars=94
- [x] 眼睛：图像进 tool_result ✅（2026-11-03，D25）：screenshot 工具
      （screencapture + sips 缩到 1000px）+ ImageBlock；线上翻译全收在
      adapter（Anthropic 的 tool_result 变块列表 / OpenAI 补发 user 消息），
      to_dict 从此只是存储形状。验收：模型答出屏幕实播内容。第四层压缩
      顺路可用——带图结果被压掉时留「[图]」标记
- [x] 耐心：异步任务 ✅（2026-11-03，D26）：轮询式四个工具
      （start_job / wait_job / job_status / stop_job），conversation 零改动；
      事件注入式否掉（要 turn 级暂停/唤醒的新架构）。验收：模型自己走
      start_job → wait_job 跑通 14 秒命令（bash 十秒必超时）

耳朵 / 语音输入已搁置，条目在文首 P2，不在这里重复。
- [x] 主动性第一阶段 ✅（2026-11-03，D27）：前置选型用户拍板
      「不在场也延续的存在」。触发器（interval / file / jobdone，
      application/triggers.py）只做消息注入——服务器自己读内容拼进
      user 消息，不靠模型的 bash；常驻调度线程挂 desktop server 的
      lifespan（TUI 不跑）；无人值守执行端钉死 auto 地板 + remember
      唯一预授权写 + 轮数上限 4 + 会话锁零等待；每触发器一个
      uuid5 固定会话（跨重启稳定，白天人聊夜里它醒是同一个存在）；
      turn span 记 origin，notices.log 是它的「昨晚干了什么」。
      真机验收过：file 触发→注入→答对→remember→origin 落 span。
      第二阶段已拆到文首：容器、频率熔断、预算熔断、熔断落盘、yolo 开关。权限门已由 D20 覆盖。

## 第二层：一个工具 + 手写循环 ✅（2026-11-01）

- [x] `llm.py` 的 stream 增加 tools 参数
- [x] tool_use 块接进请求路径：block_from_anthropic 接线，落盘块改从最终消息取
- [x] calculator 工具：**禁止 eval**，用 ast 解析表达式树求值（白名单）
- [x] 循环本体：收到 tool_use → 执行 → tool_result 追加进历史 → 再请求，
      直到 stop_reason 不是 "tool_use"（MAX_TOOL_ROUNDS=8 兜底）
- [x] 工具执行处挂 tool_call span（parent 是当轮 turn span），
      data 记工具名/入参/出参/是否出错
- [x] OpenAI 方向适配器补齐：入站 tool_calls（arguments 逐片拼接后解析回字典）；
      出站工具结果拆成单独的 role="tool" 消息（离线验证，未接真实 OpenAI 端点）

## 已知欠账

未勾的欠账已逐条拆到文首。D17 的取舍不变：`data/sandbox` 只是起跑点，不是安全边界；对外服务前要容器化或独立用户。断连 span 停在 running 仍是 D11 的刻意选择，和文首「断连补齐 tool_result」不是同一件事。

- [x] 换挡对模型不透明（2026-11-03，D21）：system 的运行态段写着当前模型和
      effort_note（留空和 off 写成「未设置」），和 trace 是同一个值。
      验收：config 里 effort=max，模型只回答档位时答 max
- [x] system 提示词拆成三段（2026-11-03，D21）：server/constitution.md（人维护，
      在 sandbox 外）+ 运行态 + memory.md。记忆里的指令替换不了准则——
      验收时记忆要求保密一个暗号，模型仍拒绝「用记忆改掉行为准则」。
      这不是安全边界：bash 用绝对路径仍写得到 constitution.md（D17）。
      第四层的缓存断点放在准则后面，还没放
- [x] MAX_TOOL_ROUNDS=8 per-session 可调 ✅（2026-11-03）：全局常量仍是
      默认值，Conversation 加 max_tool_rounds 参数（钳到 ≥1），TUI 新增
      /rounds 命令运行中查看/调整（调试长循环不用改代码）；当前轮不追改，
      下一次 turn 生效。web 构造暂不传（desktop 没有这个旋钮的需求，
      前端接线要时再加）
- [x] `/memory` 斜杠命令 ✅（2026-11-03）：用户把当前对话的关键结论写入
      data/sandbox/memory.md。模型自己用 bash 维护记忆已经能做（记忆注入那条）；
      这条是用户主动落记忆的入口，两端命令表一起加了。读写收在
      application/tools.py（read_memory / append_memory，带
      「用户 + 时间」前缀区分来源），desktop 走 GET/POST /memory；
      不等当前轮——记忆每圈圈界重读，下一圈请求就生效

- [ ] 模型请求超时已兜底（D31），但 `/stop` 仍打断不了「正在读」的
  单个请求——它要等读超时（最长 timeout_s）才回到检查点。真·即时取消
  需要把同步 SDK 调用挪到可取消的线程/异步模型，暂不值。
- [ ] 历史遗留的 `running` span（客户端断开/超时之前的旧轮）没有回收；
  trace 里会一直显示运行中。要么接受这是「如实记录」，要么做启动时
  清扫（按 span 起始时间超过阈值标 error）。
