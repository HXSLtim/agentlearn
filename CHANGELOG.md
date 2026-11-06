# Changelog

每完成一层或一项基建记一版，不逐 commit。新条目放最上面。

## 2026-11-06（二十一）：断连的轮次不再假「运行中」

- 起因：会话 `1738cd58` 积了三对永久 `running` 的 turn + llm_request
  span，界面一直显示「还在跑」；服务端实测空闲——是 SSE 客户端在
  L4 压缩静默期断开后，GeneratorExit 按 D11 旧策略留下 running。
- 后端：`turn()` 改成 `yield from` 薄包装，捕获 GeneratorExit 后把
  本轮未收尾的 span（turn / llm_request / tool_call）统一收成
  `cancelled` + end_time + 断连注记；新增 `_start_span` / `_end_span`
  维护每轮清单。半截回答仍不落盘，tool_use/tool_result 配对补齐不变
  （D33 修订 D11）。
- 桌面端：Stop 收到 `POST /stop` 的 `active=false` 时把本地这轮就地
  收线——助手消息标「已取消」、卡在 waiting/running 的工具卡收成
  错误结果（不再永远「等待结果…」）、并 abort 还挂着的 SSE 读；
  abort 触发的异常不再覆盖成「错误」。
- trace：状态表补 `cancelled → 已取消` 中文标签（TUI 早已按 cancelled
  着色，两端口径一致）。
- 验证：离线假 client drill 覆盖流中途断开 / 工具结果处断开 / 请求后收尾前断开 / 正常
  跑完 / 抢占五条路径，span 终态全部符合预期；`npm run build`、
  `compileall` 通过。旧库里已积的 running 行不追改（TODO 清扫欠账）。

## 2026-11-05（二十）：可回放时间戳与细节收口

- JSONL 每行在落盘时由存储层加盖 `ts`（秒），`to_dict()` 仍只给
  模型请求形状；`GET /sessions/{id}/messages` 直接返回原始行。老数据
  没有 `ts` 时前端退回合成序时，并用 turn span 的真实起止回填（D32）。
- 会话列表行不再显示缩略 ID（UUID 留给工具和日志），右键会话行可复制
  完整 ID；未落盘的新会话给出提示，不弹空菜单。
- 焦点收成一套：按钮 / 链接一条琥珀发丝线，整行列表（恢复框、会话内
  查找、轨迹树、diff 文件头、左右会话列表）用内收脊线，输入框只改
  边框色；修掉六处「有焦点但看不出」的 `outline: 0`。
- 调色收口：删掉与 accent 分叉的第二个琥珀 token（浅色 `#b06a10`），
  红色描边改主题感知 `--red-line`；发送键回到墨字实心琥珀，hover 用
  单独的 `--accent-hover`（浅色往亮走，墨字不掉对比）；toast 从硬偏移
  阴影改回卡片阴影；空态卡片的 hover 不再写死深色字。
- 窄屏弹层：≤620px 的设置 / 续接 / 轨迹 / 变更铺满屏幕时方角无边框。
  收口层在文件末尾，原窄屏覆盖被反超，改用父级选择器压过。
- 类型与文案：设置值（模型 / token / URL / 密钥）统一 mono 12px；
  设置与 trace 的 effort 都叫「思考强度」；diff 摘要改「未跟踪 4
  （已计入）」；空回答卡片写「本轮没有返回内容」，被转录档位藏空的
  助手卡直接不渲染，连续助手消息不重复「A 智能体」抬头。
- 验证：`npm run build`、`compileall`、`git diff --check` 通过；
  CDP 在 500 / 880 / 1280 宽度与明暗两主题逐屏检查，键盘焦点态用
  computed style 核对；临时目录离线验证 `ts` 只进 JSONL、不进模型 payload。

## 2026-11-05（十九）：设计语言「墨与琥珀」

- 放弃照搬 Apple HIG 当脸（平台件保留：双主题、材质、焦点环、
  overlay 标题栏），换成自己的设计语言：暖纸白 / 墨黑底、琥珀批注
  强调、圆角 4/6/8 三档、字阶下限 11px（旧的 8px 微字号全部提上来），
  desktop 与 TUI 同一套词汇表（D30）。
- 签名元素 span-tick：每条助手消息页脚一条 2px 时轴，填充 = 本轮
  耗时 / 全会话最长轮；恢复的会话从 `/trace` 拉 turn span 水合，
  拉不到不造数据。
- 细节修补一轮：弹层材质改到 backdrop 层（此前 `> *` 给全屏遮罩
  画了圆角边框、遮罩三层叠黑）、状态点从方块改圆、`titleBarStyle`
  枚举大小写修正（小写 `overlay` 会被 schema 拒启）、红绿灯让位改到
  侧栏头（此前错垫在中栏头）、四处旧荧光绿残留换琥珀、内层控件
  （kbd / pre / select / 列表行）圆角体系落地。
- 文案收编：全大写标签清零（含 JS 载入前的静态初值）、输入区四层
  文字收成两层（去「输入」标签与重复脚注，Steering chip 改「插话」）、
  空态 READY 改就绪、trace / diff 抽屉与命令回执标题全部中文化。

## 2026-11-05（十八）：界面换装 Apple 设计语言

- desktop 全量换 Apple 风格：色彩收敛为语义 token（label 分层文本、
  systemBlue 强调、语义状态色），`prefers-color-scheme` 随系统明暗两套
  全自动切换，`<meta name="color-scheme">` + 双 theme-color 跟手。
- 字体换 `-apple-system` / `SF Mono` 栈（Tauri 的 WebKit 里就是真 SF），
  移除 IBM Plex 依赖；标题栏 `titleBarStyle: overlay` + 隐藏标题，
  头部 `data-tauri-drag-region` 接管拖拽，内容顶到窗口边。
- 头部三枚视图开关（轨迹 / 变更 / 日志）改成 segmented control，
  `aria-pressed` 表达选中态；卡面统一 10px 圆角 + 阴影，模态走
  backdrop-filter 材质，主按钮改胶囊形，焦点环换 systemBlue。
- 全大写标签整体改 sentence case / 中文：账本、设置、恢复框、抽屉、
  命令回执的状态枚举（approve/auto/yolo、queue/cooperative/preemptive）
  落屏前过 `format.ts` 的中文名映射。
- TUI 主题换 Apple 深色语义配色：画布 1C1C1E、浮层 2C2C2E、强调
  0A84FF、红/橙/青/绿各归其位；常量名沿用旧词汇表，chat/ 包 CSS 零改动。

## 2026-11-03（十七）：desktop diff 工作台第一步

- 新增只读 `GET /workspace/diff`：`git status --porcelain=v1 -z
  --untracked-files=all` 采集工作区状态，`GIT_OPTIONAL_LOCKS=0` 禁止
  status 顺手刷新索引；列出 modified / added / deleted / renamed 与 rename
  旧路径，`include_untracked=true` 时未跟踪文件也进入列表。
- desktop 增加 DIFF 抽屉：一键读取并刷新当前 Git 工作区变更文件，明确标注
  READ ONLY / NO INDEX WRITES；380px 窄屏下路径独占一行，列表无横向溢出。
- 已跟踪文件显示 unified diff 逐行内容：hunk 头、旧/新行号、context、
  红色删除、绿色新增、增删计数、binary / patch 截断标记。
- 未跟踪文件用 `git diff --no-index /dev/null <path>` 生成全新增 hunk，
  只读读取工作区内容。
- 每个文件是原生 `<details open>`：摘要行保留状态、路径与增删计数，
  点击文件头即可单独折叠 / 展开 hunk。
- TUI 新增 `/diff` 只读 pager：命令面板可发现，支持 ↑↓/Page↑↓/Home/End
  滚动、R 重载、ESC 关闭，与 desktop 共用同一份 workspace 事实。
- desktop 恢复框接入 `GET /sessions/search`：正文命中的会话即使标题 /
  id / 模型不匹配也会保留，并在列表行展示第一处 snippet；空词清空正文
  结果，异步结果用序号防串包。
- 恢复框支持重命名当前选中会话：表单校验空标题，调用
  `PATCH /sessions/{id}` 后同步 server 标题、本地列表与当前页头。
- 恢复框支持 PIN / UNPIN 当前选中会话：调用同一 PATCH 更新
  `pinned`，左侧会话列表与恢复列表均 pin 优先于最近活动。
- 恢复框增加 ACTIVE / ARCHIVED 视图：归档走同一 PATCH 的
  `archived`，默认列表移除会话；归档视图可直接续接正文，也可恢复。
- 恢复框提供独立归档与永久删除动作：删除先展示不可恢复警告，
  二次确认后才调用 `DELETE /sessions/{id}` 并清理本地索引。
- 恢复框支持 fork：按源会话 JSONL 消息总数选择前 N 条，
  调用 `POST /sessions/{id}/fork` 后自动切到新分支并恢复正文。
- 运行账本显示每轮 token 实数：Conversation 发出累计 `usage`
  事件，TUI 与 desktop 均展示 input/output/cache read/write/hit；
  修正 `/context` 读取旧 usage 字段名导致永远为空的问题。
- 运行账本按显式价目显示本轮成本：`[pricing:<model_id>]`
  声明 currency 与四类每百万 token 单价；没有当前模型的价目时显示
  “不算钱（无价目）” / `NOT CALCULATED`，绝不估算美元。TUI 用
  `Decimal` 计算，desktop 在启动、保存设置和 `/model` 后同步价目。
- 上下文工作集达到预算 80% 时发出一次 `context_warning`：
  估算与 `/context` 共用摘要 + 未覆盖尾部同一套账；`context_warning`
  span 作为跨轮去重事实，TUI 显示 warning 卡，desktop 显示 toast 与
  RUN LOG 警告，不随流式片段重复刷。
- desktop 输入 `@` 支持文件路径补全：新增只读
  `GET /workspace/paths`，只按前缀返回 Git tracked/untracked 路径名，
  ignored 不进入结果，也不读取文件内容；光标处活跃 `@token` 打开
  面板，Enter/Tab/点击只替换路径文本，第二次 Enter 才发送 `/chat`。
- desktop composer 支持拖拽图片：白名单校验并预览待发送附件，
  提交时作为 `POST /chat` 的 `images` 传给后端；带图只允许 queue，
  cooperative / preemptive 在前端拦截。RETRY 修正为发送
  `{"retry":true}` 与空 question，且不携带新的 `images`。
- desktop 支持粘贴图片：clipboard 图片进入与拖拽同一份附件管线，
  纯文本粘贴保持浏览器默认行为。
- TUI 新增 `/attach <path>`：白名单读取本地图片并挂到下一条 user 消息；
  支持 image-only 空提交，带图只走 queue，retry 不携带新图。
- 新增只读 `GET /models`：复用当前 provider/base_url 调用上游
  `models.list()`，只返回模型 ID；desktop 设置页的 MODEL ID 改为下拉，
  保存后按新配置刷新列表。TUI 设置页接入同一个 `list_models()` 组合根
  回调，列表加载不阻塞界面，失败时保留当前模型并显示错误。
- 两端新增 `/find [关键词]` 会话内查找：desktop 用当前 active thread 的
  已渲染消息索引弹出查找框并滚动高亮命中的 message；TUI 用当前时间线
  的 UserMessage / AssistantMessage 原件过滤、定位与高亮。查找不请求
  模型、不切换或新开会话。
- 两端新增 `/transcript [mode]`：normal 只看正文与审批，thinking 展开
  思考块，verbose 再显示工具入参和出参；档位切换只影响渲染，不改写
  JSONL，也不改变审批决策入口。
- 两端新增 `/export`：共享导出器把 JSONL 会话整理成 Markdown（含
  thinking、工具入参/出参与图片 data URI）；desktop 请求只读
  `GET /sessions/{id}/markdown` 触发下载，TUI 可指定路径或默认写
  `data/exports/`，已存在文件拒绝覆盖；`/copy` 仍只复制最近一条回答。
- 审批增加作用域：TUI / desktop 都提供 `DENY`、`ACCEPT ONCE` 与
  `ACCEPT SESSION`。once 只放行当前 `approval_id`；session 按当前会话与
  工具名建立进程内授权，后续同名工具免问，跨会话不共享、不落盘、重启后
  失效；拒绝可附可选理由并回到模型上下文，同时写入 `tool_call` span；
  HTTP 契约新增 `scope:"once|session"` 与 `reason`（仅拒绝可用）。
- 新增 plan mode：TUI `/plan <任务>` 与 desktop 同名命令进入只读探索，
  只暴露内置只读工具白名单，写工具与 MCP 被应用层拒绝为 `plan_blocked`；
  模型输出独立 `plan` 事件和确认卡，`POST /plans/{id}/confirm` 后带
  `plan_id` 的 `/chat` 才一次性执行，未确认或重复执行返回 404。
- 能力面板两端对齐：能力快照抽到 application 层，`GET /capabilities`
  与 TUI `/status` 共用同一份事实；desktop 新增 `/capabilities` 命令，
  展示工具来源、MCP health、运行版本、触发器数量与 sandbox 边界警告。
- 验收：临时 Git 仓库确认默认排除未跟踪、显式可含未跟踪，调用前后
  `.git/index` SHA256 不变；TestClient、`compileall`、`npm run build` 通过；
  380×900 CDP 截图人工检查变更记录和逐行增删，`document/body/summary/list`
  `scrollWidth` 均为 380。

## 2026-11-03（十六）：重试去重契约 + 会话库/上下文/能力面板的后端

- **重试去重（P0 收口）**：`turn()` 加 `append_user=False` 模式——失败轮
  的 user 消息已在 JSONL，重试对既有历史重新发起请求，不再追加同文
  user。历史尾部必须是 user 消息：纯文本 = 请求前就失败；tool_result =
  工具循环中途失败，重试从工具结果续跑（正是想要的续跑语义）。
  `POST /chat` 加 `retry` 字段（推流前校验：尾部不是 user 报 409、
  只支持 queue 档）；TUI `/retry` 与 RETRY 卡片接 `append_user=False`；
  turn span 记 `origin=retry`。
- 会话库 pin 后端：sessions.pinned 列（`_migrate` 加列）、PATCH 字段、
  列表排序 pinned 优先于最近活动、GET /sessions 带 pinned。
- `/context` + `/compact`（P1 上下文与用量的机制层）：
  `Conversation.context_report()` 给三种真值——估算（摘要 + 尾部 + system
  的预估 token，管下次请求多大 / 何时压）、上次请求 usage 实数（含缓存
  命中）、工作集预算；`compact_now()` 复用圈界压缩的全部机制，只是把
  预算当 0（plan_cut 退到最深合法切割点）。TUI 两条命令 +
  `GET/POST /sessions/{id}/context|compact`；运行中压缩 409——摘要文件
  会被运行轮的圈界压缩和手动压缩互相踩。
- `GET /capabilities`（P1 能力面板的数据源）：工具清单带来源
  （builtin/mcp/skill）、`MCPManager.health()`（健康检查不主动起进程，
  惰性如实报 not_started）、sandbox 边界如实陈述 D17 取舍、运行时
  python/模型/effort/触发器数。两端 UI 消费这份 JSON。
- user 消息带图（@file / 粘贴图片的后端半边）：`turn(images=...)` 文字块
  前图块后；adapter 两方向补齐——Anthropic 顶层图块包 source（原来会以
  存储形状直发、必 400），OpenAI 带图 user 的 content 变数组
  （text + image_url，纯文字保持字符串）；`POST /chat` 加 images 字段
  （校验 media_type/data；与 retry 互斥、只支持 queue 档——软中断的
  inbox 只搬文字）；JSONL 往返、压缩渲染给「用户发来一张图」标记。
  前端的 @file 补全 / 拖拽 / 粘贴 UI 还没接。
- `GET /sessions/search?q=`（P1 会话库的按内容搜索）：线性扫 JSONL
  文字块，每会话报第一处命中（带前后文截断），总量封顶 50；归档会话
  也搜得到。没上 FTS——几十个会话毫秒级，索引维护比线性扫描贵。
- 验收（离线）：重试三路径（请求前失败重试不重落 user、成功后重试
  ValueError、从 tool_result 续跑历史尾部正确 + span origin=retry）；
  pin 排序压过最近活动、取消后还原；compact_now 压缩/无可压两态 +
  压后 context_report 对账 + 下一轮 turn 摘要进 system；capabilities
  字段与工具来源标注；user 图块双方向翻译 + 软中断合并 + JSONL 往返 +
  turn 带图请求历史正确；搜索命中/空词 422/每会话一条。两套既有离线
  套件全绿。

## 2026-11-03（十五）：主动性第一阶段——不在场也延续的存在（D27）

- 前置选型用户拍板「不在场也延续的存在」，主动性立项；耳朵搁置
  （当前中转模型不吃音频输入块，本地转写是用再说的备选）。
- 触发器三种（`application/triggers.py`，config.ini 的 `[trigger:*]` 段）：
  interval（≥60 秒防风暴）/ file（内容变化，内容由服务器读进消息，
  不靠模型 bash）/ jobdone（后台任务跑完带输出尾部）。check()/mark()
  分离：执行失败不消费触发，连续 3 败丢弃并写 notices。
- 常驻调度线程挂 desktop server 的 lifespan（TUI 不跑）：串行闸门、
  每 5 秒一 tick、tick 异常不杀线程。无人值守执行端 `_run_unattended`：
  SessionManager 会话锁零等待（拿不到就放弃这 tick）、auto 权限地板、
  轮数上限 4、事件丢弃（JSONL + spans 是全部输出）、turn span 记
  origin="trigger:<名>"。每个触发器一个 uuid5 固定会话——跨重启稳定，
  白天人聊夜里它醒是同一个存在；data/notices.log 是它的值班日志。
- 新工具 `remember`（auto 档唯一被预授权的写）：往 memory.md 追加
  带 agent 时间戳的一行，作用域授权等价 /memory 命令；read_memory /
  append_memory 从 conversation.py 搬进 tools.py（memory.md 是 sandbox
  数据）并加 author 参数。
- 顺手修两个既有问题：jobs 账本 exit_code 懒缓存没人 poll 就看不见
  「跑完」（jobdone 触发器自己 poll）；SANDBOX/CHILD_ENV 的
  tools↔jobs import 环（jobs 先导入必炸）拆进 application/sandbox.py。
- 验收（真机）：file 触发器 → 注入带内容 → 模型答对 1+1 → remember
  自动放行 → 记忆落盘 → span origin 正确 → notices 记 end_turn；
  config.ini 按字节还原。离线：三触发器全路径、调度失败计数、
  remember 空内容 is_error、auto 放行集、双 import 顺序。
- 第二阶段（未做）：容器化（D17 攻击链的「读密钥」一步清零）之后
  地板才可提 yolo；届时熔断和任务账本落盘必须一起做。

## 2026-11-03（十四）：TUI / desktop P0 工作流闭环

- desktop 接上 server 会话索引、正文恢复、三档 steering、独立 STOP 和只读
  TRACE 抽屉。停止状态为 `RUNNING → STOPPING → CANCELLED / ERROR`；运行中
  的发送按钮保留 `STEER`，避免“改方向”和“收线”挤在同一按钮上。
- trace 抽屉消费 `GET /sessions/{id}/trace`，左侧 span 树、右侧
  status / duration / 模型参数 / cache / effort / 工具入参出参 / 配对回答；
  窄屏改为上下分栏。TUI `/trace` 仍走本地 Store + JSONL，两端不共享 UI 代码。
- 两端错误卡都能直接 `RETRY`；已完成工具结果保留，界面不会重复插入同一
  user 卡片。JSONL 层的重试去重仍需后端新契约，已记入 TODO。
- desktop 长会话布局修掉：Grid 增加 `minmax(0, 1fr)` 行约束后，历史再长
  composer 也固定在视口底部；thinking 运行态改成最多约 6 行的尾部滚动预览，
  完成后折叠为完整 reasoning。
- TUI 补齐错误重试卡、`/retry`，并保持 thinking / text / tool 严格按事件
  顺序原位挂载。验收：tsc + vite build、TUI 三个 headless 冒烟、真实
  `GET /sessions` + `/messages` + `/trace` 接线，以及 1280×800 / 375×812
  真实截图（长历史、thinking、steering、STOPPING、trace、错误重试）全过。

## 2026-11-03（十三）：会话索引与回放端点（前端接线需求落地）

- 四个新端点（形状按 TODO 契约，desktop 会话轨 / 首屏恢复 / 停止 / 只读
  trace 面板的数据源）：`GET /sessions`（最近活动倒序）、
  `GET /sessions/{id}/messages`（完整内容块，Message.to_dict 原样）、
  `POST /stop`（设取消标志，复用抢占档 TurnCancelled 语义：SSE 收
  steering_cancelled + done，不丢已完成的 tool_result；无运行轮幂等 no-op）、
  `GET /sessions/{id}/trace`（spans 树 + 配对回答）。
- Store.list_sessions 重写：turns / updated_at / stop_reason 用子查询从
  spans 推导（派生值不落库，落了要维护两处），排序从创建时间改为最近活动；
  新增 set_title_if_empty——标题列建表时预留的「起标题的地方」兑现，
  Conversation 第一轮 turn 用第一条用户消息截断补标题，不覆盖后来的正式名。
- answers_for_requests / answer_texts 从 tui/trace.py 抽到
  application/trace_view.py：web 端点要同一套「ok llm_request 按序配对
  assistant 回答」逻辑，而 web 进程不该 import TUI 包（会拖进 Textual）。
- P1 会话管理一并落地：`PATCH /sessions/{id}`（rename / archive，归档是
  软删除、`GET /sessions?archived=true` 归档视图）、`DELETE /sessions/{id}`
  （永久删除：索引 + spans + JSONL + 摘要一起清，运行中 409）、
  `POST /sessions/{id}/fork`（`{"through": N}` 保留前 N 条复制新会话，
  spans 不复制）。sessions 表加 archived 列，旧库由 Store._migrate 幂等
  ALTER 补列；Store 新增 rename_session / set_archived / delete_session，
  Transcript 新增 delete（摘要派生物跟着正文一起没）。
- /chat UUID 回传、approval-mode 三条契约核对为既有实现已满足，TODO 勾掉。
- 验收：离线契约测试（临时 SQLite + JSONL 直调端点函数）——排序/派生列/
  消息块形状/thinking signature/trace 配对/stop 置标志/404 vs 422/
  标题只写一次，全过；P1 那组再加：旧库无 archived 列的迁移、归档进出
  默认列表、fork 边界（through 0/超界 422、复制内容与标题后缀）、
  运行中删除 409、删除后 spans/正文/摘要全清，全过。

## 2026-11-03（十二）：欠账清理——/memory、/rounds、压缩档位

- `/memory [内容]`（两端命令表）：用户主动往 data/sandbox/memory.md 记一条，
  读写收在 application/conversation.py（read_memory / append_memory，带
  「用户 + 本地时间」前缀和模型自己写的行区分来源）；不带参数查看现有记忆。
  TUI 直调 application 函数，desktop 走新端点 GET /memory / POST /memory。
  两端都不等当前轮——记忆是每圈圈界重读的，追加只影响之后发出的请求。
  system 注入文案同步注明用户也会写、改写时保留原意。
- `/rounds [N]`（TUI）：工具轮数上限 per-session 可调（欠账那条）。全局默认
  仍是 8，Conversation 加 max_tool_rounds 参数（钳到 ≥1），当前轮不追改、
  下一次 turn 生效。web 构造暂不传，前端要这个旋钮时再加。
- 压缩请求不再继承用户思考档位：llm.stream 加单次 effort 覆盖参数
  （空串 = client 默认），压缩固定 low——摘要不需要深度思考，用户开 max
  时别跟着烧。span data 记 effort=low，trace 里分得清两处档位为何不同。
- 截图转 JPEG（sips 质量 75，实测 625KB PNG → 106KB base64）：省的是
  JSONL 正文和请求体的体积；上下文 token 按像素算，缩图那步才省 token。
- 验收：记忆读写往返（含无结尾换行的既有文件）；离线 compaction /
  steering 套件全绿（假 client 签名跟上新参数）；imports / tsc 干净
  （desktop 遗留报错均为并行改名半成品，与本条无关）。

## 2026-11-03（十一）：耐心——后台任务

- `application/jobs.py`（新，D26）：start_job / wait_job / job_status /
  stop_job 四个工具。命令后台跑（sandbox 起跑、环境只给 PATH、独立进程组），
  输出写 data/sandbox/jobs/任务id.log；wait_job 单次上限 120 秒、超时如实
  报「还没结束」；stop_job 连子进程一起杀（修掉 bash 超时路径的子进程
  残留坑）。轮询式，conversation 零改动。
- wait_job / job_status 进 auto 档自动放行集；start_job / stop_job 与
  bash 同级需确认。轮询烧工具轮数、wait 不烧的取舍写进工具说明。
- 验收（真机）：模型自己走 start_job → wait_job 跑通 14 秒命令
  （bash 十秒必超时），exit 0、span 全 ok。

## 2026-11-03（十）：眼睛——图像进 tool_result

- 新内置工具 `screenshot`（D25）：screencapture 截屏、sips 缩到最长边
  1000px、base64 进 ImageBlock，临时文件即用即删；需要「屏幕录制」权限，
  失败转 is_error。run_tool 返回值变三元组 (文本, 是否出错, 图|None)。
- domain 新增 ImageBlock；ToolResultBlock 可带一张图（JSONL 存完整块，
  老数据无 image 键读回 None）。to_dict 从此只是存储形状——线上翻译
  全在 adapter：Anthropic 的 tool_result content 变块列表（图前文后，
  无图消息剥掉 asdict 带出的 "image": null）；OpenAI 图作为紧跟
  role="tool" 的一条 user 消息补发（data URI）。
- 压缩的摘要给带图结果留「[图]」标记；spans 只记 image_bytes；
  SSE 事件只报 has_image。
- 验收（真机）：「看一眼我的屏幕」→ 模型答出屏幕实播内容（综艺、
  嘉宾、角标广告）；JSONL 带图可续发、span 记大小、缓存仍命中。

## 2026-11-03（九）：第 6 层——Skills 指令包

- `skills/*.md` 一个文件一个技能（D24）：头部两行简化 frontmatter
  （name/description），其余是正文。每圈 all_tools() 重扫目录，
  加技能 = 丢文件，零代码改动、不用重启。
- 新内置工具 `read_skill(name)`：索引（名字+说明）嵌在它的 description 里，
  是 Skills 唯一的常驻上下文开销；正文按需拉取，读进来是普通 tool_result，
  长会话由第 4 层压缩收走。技能引用的脚本走 bash，加载与执行分界同 MCP。
- `skills/report.md` 作为示例技能随库（运行报告的固定格式）。
- 验收（A/B 真机，同问「写运行报告」）：无技能 0/4 格式标记；有技能
  先调 read_skill 再输出 4/4 全对。conversation.py 未改动。

## 2026-11-03（八）：第 5 层——MCP 外部工具

- `infrastructure/mcp_client.py`（新，D23）：手写 MCP stdio 客户端——
  子进程 stdin/stdout 上的 JSON-RPC 2.0，一行一条 JSON。initialize 握手、
  tools/list、tools/call 三个方法；读线程 + 队列实现 30 秒超时；进程死了
  自动重启；单个服务器起不来只跳过，不拖垮工具列表。
- config.ini 新增 `[mcp:<别名>]` 段（config.py 的 load_mcp_servers，
  别名只许字母数字下划线，command 过 shlex）。没有这类段 = 行为与从前
  完全一致。
- 外部工具以「别名__工具名」并入 all_tools()/run_tool()（tools.py），
  inputSchema 即 domain 的 input_schema，零翻译；内置名不含 `__`，
  路由不撞。examples/mcp_count.py 是手写的最小 MCP 服务器（验收对象）。
- 验收：真机只加 `[mcp:cnt]` 配置，模型调 `cnt__count_words` 答对
  「8 字符、3 词」；离线含 kill 重启。conversation.py 未改动。
- 已知欠账：同一 MCP 服务器的并发调用未真正串行化（现在工具顺序执行，
  不构成问题）；图片类 content 块跳过（「眼睛」缺口）。

## 2026-11-03（七）：第 4 层——上下文压缩与缓存断点

- `application/compaction.py`（新，D22）：圈界处估算「摘要 + 历史」超预算
  就把前缀压成摘要。摘要存 `data/sessions/<id>.summary.json`（派生层，
  JSONL 只追加不改写）；切割只落轮次边界，tool_use/tool_result 配对不拆散；
  压缩本身是一次 llm_request（span 记 purpose=compaction），失败沿用旧
  摘要和原 covered 尾部。预算默认 24000 估算 token。
- system 第四段挂会话摘要，四段固定：准则、运行态、记忆、摘要（D21 之上）。
  llm_request span 的 messages 记尾部条数，压缩后再加 compaction_covered。
- prompt caching：Anthropic 请求在最后一条消息打 cache_control 断点
  （估算前缀 ≥2048 token 才打，低于最短可缓存长度会 400）；OpenAI 自动
  前缀缓存无标记。断点跟尾巴走，工具循环每圈追加的历史命中上一圈前缀。
- 验收（预算 1000、7 轮、首轮埋暗号）：主请求 input 平在 236–547，
  cache_read 最高 512、命中率 655‰；covered=10 时仍答对暗号。
  离线五案：边界安全（无悬空 tool_use）、压缩发生/不发生、已有摘要
  只发尾部、压缩失败退全长。

## 2026-11-03（六）：模型/思考档位顺延，执行等级实时生效

- 思考档位和模型可以在当前轮运行中切换；`Conversation.turn()` 固定本轮
  client 快照，system 提示词也读这份快照，所以当前轮不会混两套配置，下一次
  `turn()` 才使用新档位。TUI `Alt+T`、desktop `Alt+T` / `POST /settings` 均可。
- 执行等级改为 `RunningTurn` 的运行态字段，每个工具调用前实时读取。
  `Shift+Tab` 在流式中也能切；后续工具立即按新等级判断。若审批框已经打开，
  切到 `auto` / `yolo` 后不再需要人工确认的那一条会立即放行并关闭审批 UI。
- desktop 在请求前由客户端固定 session UUID，新增
  `POST /approval-mode {session_id, mode}`，运行中精确更新对应轮次；响应带
  `auto_approved`，前端据此撤掉过期的 Accept/Deny 状态。没有运行轮时端点
  返回 `active:false` 并记住新等级，下一次轮次 `begin` 时采用，覆盖「请求
  已发出、服务端还没建运行轮」的短窗口；前端也会在后续 `/chat` 再带上它。

## 2026-11-03（五）：system 提示词分三段

- system 不再 100% 来自模型自己能改的 `memory.md`（D21）。每轮拼三段，
  顺序固定：`server/constitution.md`（人维护的准则）、当前模型与思考档位、
  memory.md（只放事实，空则省略）。不落 JSONL。
- 档位用 `effort_note`，和 trace 是同一个值。留空和 off 写成「未设置」。
  模型改不了档位，但知道自己在哪一档。
- 准则放在 server/ 下只是为了不和便签混在一个文件里，bash 绝对路径仍写得到
  （D17），不把它当成安全边界。验收：记忆里藏了暗号，模型仍答出档位 max，
  并拒绝「用记忆改掉准则」。

## 2026-11-03（四）：执行等级 approve / auto / yolo

- `application/approval.py`（新）：工具执行前的人工门。approve（默认）
  每次都等 Accept；auto 只放行无副作用工具（now_time），bash 仍要 Accept；
  yolo 直接跑。拒绝和超时变成 is_error 的 tool_result，不打断配对。
- 两端都用 Shift+Tab 切换，一轮进行中不让切。TUI 弹模态框；desktop 在
  工具卡上给 Accept / Deny，经 `POST /approval` 回填。运行态，不进 config.ini。
- 明确不是安全边界（D20）：auto 不读命令内容，命令过滤仍按 D17 否掉。

## 2026-11-03（三）：同会话消息 steering 三档

- `application/session_manager.py`（新）：web 进程单例，按 session_id 管槽位
  （串行锁 + 正在跑的轮次）。`POST /chat` 新增 `steering` 字段，三档
  （D19）：queue 等整轮、cooperative 在圈界并入当前轮（本连接拿 ack）、
  preemptive 在检查点打断（半截回答不落盘）。没有轮次在跑时三档等价。
- spans 新增 `cancelled` 状态：打断与崩溃分开记；trace TUI 单独着色。
  断连仍留 running（D11 语义不变）——只有抢占才记 cancelled。
- adapter 新增 `history_to_anthropic`：圈界插入产生的连续 user 消息
  （tool_result + 新消息）合并成一条，满足 Anthropic 的交替要求。
- Store 收归进程级单例，`_locked`（RLock）包住全部方法：防同一条 sqlite
  连接上两个请求线程交错 execute/commit。
- 配对不变式：工具执行段内不设抢占检查点。离线测试抓到的 bug——检查点放在
  tool_result 事件之后、结果落盘之前，抢占会让 tool_use 失去配对的
  tool_result，会话从此续不上。工具批次完整落盘，取消推迟到下一圈圈界。
- 验收：离线三档（脚本化假客户端）+ 真机三档并发（A 跑 `sleep 6` 期间发 B）：
  queue 顺序作答、cooperative 并入并 ack、preemptive 打断且 JSONL 保持
  配对（user/assistant/user(tool_result)）；非法档位 422。

## 2026-11-03（二）：跨会话记忆注入

- agent 自盘点五缺口的第一个落地：`data/sandbox/memory.md` 每轮 turn 读一次，
  拼成 system 提示词随每次请求重发——醒来自动带行李，不靠「恰好记得去读
  某个文件」。system 不落 JSONL（派生状态不是历史）；轮内多圈请求共用同一份，
  前缀稳定（第四层 prompt caching 的入口就埋在这）。协议差异收在 adapter：
  Anthropic 是顶层 `system` 字段，OpenAI 是首条 `role="system"` 消息
  （新函数 `prepend_system`）。llm_request span 新增 `system_chars`（记长度
  不记内容）。验收：全新会话直接答出只写在 memory.md 里的暗号。
- 随路：config.ini 新增可选键 `no_proxy`（缺省完全尊重环境代理，写了才把
  主机列表合并进进程 `NO_PROXY`）；没装 socksio 的机器上 SOCKS 代理导致的
  ImportError 翻译成带解法的中文报错；desktop 端客户端构造失败改走 error
  事件而不是断流。decisions 补 D17（bash 边界实测审计：出网、say、攻击链
  成立；命令过滤正式否掉），TODO 挂入五缺口排队与主动性三件套。

## 2026-11-03：TUI / desktop 工作台、trace 检查器、设置界面

- 启动器收口（D14）：`server/main.py` 成为唯一组合根，子命令改成
  `tui` / `trace` / `desktop`，`web` 保留兼容别名；删除 `interfaces/cli.py`
  和纯文本逐行模式，终端只留 `interfaces/tui` 一个公开门面。
- TUI 重做：流式正文、思考折叠、tool_use/tool_result 卡片、运行账本、
  窄终端布局、续接会话自动滚到最新；thinking 折叠标题实时显示最新片段，
  thinking / 正文 / 工具卡按事件顺序挂载，tool_result 在原卡片回填。
  视觉同步改成近黑 / 酸性绿硬边框控制台，并统一 Textual 光标、选区和滚动条语义色。
  工具调用数改为会话累计，不再在提交后突变。
- trace TUI：span 树、详情、耗时、TTFT、tokens、cache、effort、工具入参/出参和
  对应回答；只有 `status == ok` 的 `llm_request` 消费回答，错误或 running 请求
  不会再让后续回答整体错位。
- desktop 工作台：接 `POST /chat` + SSE，加入会话侧栏、工具/思考时间线、
  运行账本、窄屏布局和模型设置抽屉。视觉从暖纸编辑稿改成近黑 / 酸性绿控制台：
  去掉营销 Hero、三张 starter card、圆角光晕和柔和投影，改成命令行空态、
  硬边框主舞台和窄日志轨；设置弹层沿用同一套黑绿语义色。
- 设置界面（D15）：TUI 顶栏 `SETTINGS` / `Ctrl+,`，desktop 齿轮 / `Ctrl+,`；
  新增 `GET /settings` / `PUT /settings`。写入只改 `[llm]` 已知键、保留注释，
  临时文件校验通过才替换；API key 留空保留原值且响应绝不回显，下一轮请求生效。
- 斜杠命令（D18）：TUI / desktop 输入 `/` 打开可筛选命令面板，支持方向键选择、
  Tab 补全、Enter 执行、Esc 收起。首批支持 `/model <id>`、`/config`、`/now`、
  `/files`、`/boundary`、`/help`；命令只在界面层处理，未知命令不会发给模型。
  配置统一走 `/config` / `Ctrl+,`，两端顶栏的 `CFG` 按钮已移除。
- review 修复：补 `127.0.0.1:1420` CORS；孤儿 tool_result 恢复时挂载工具卡；
  修 Textual `Collapsible._title` 字段冲突导致思考片段到达即报错；trace 补 effort。

## 2026-11-02（二）：第 3 层——多工具、并行、失败

- 工具集换血：calculator 下架（ast 白名单的写法留在 git 46ef2e1 和
  learning-path 第 2 层），换成 now_time（无参、永不失败）+ bash
  （shell=True，管道/重定向/$() 全真实语义，10 秒超时，cwd 在 data/sandbox）。
  先试过 shlex.split 直 exec，等于向模型承诺残缺 shell（$((...))、> 全是
  字面量），改回 shell=True——安全面无实质差别（python -c 本就是任意代码执行）。
- 并行验收 ✅：一条回复里同时要 now_time 和 bash，两个 tool_call 挂同一
  llm_request，id 配对不串线（时间 + 56877 都正确）。
- 失败验收 ✅ 两档：exit 1 → 模型 pwd/ls 定位后如实报告；模型自发 find /
  全盘扫描超时 → 逐步降级到朴素命令最终答对。
- 修一个真 bug：shell=True 超时路径 TimeoutExpired.stdout/stderr 是 bytes
  （text=True 不生效），原代码 str+bytes 直接 TypeError，靠 run_tool 兜底
  才没断链；现在统一 `_as_text` 转换，超时报错带部分输出、说人话。
- 已知边界如实记录（TODO）：bash 的 cwd 不是安全边界，绝对路径可绕过，
  学习期接受，对外前必须容器化；超时只 kill shell，后台子进程可能残留。

## 2026-11-02：用量与缓存比例进 span

- llm_request span 增加五个字段：input_tokens / output_tokens /
  cache_read_tokens / cache_write_tokens / cache_hit_ratio（千分比）。
  翻译在 adapter.py：usage_from_anthropic / usage_from_openai 翻成同一套键。
  两家口径不同——Anthropic 的 input_tokens 不含缓存，OpenAI 的 prompt_tokens
  是总量，要减掉 cached_tokens 才对齐，这个减法收在 adapter 里。
- OpenAI 路径加 stream_options.include_usage：用量在 [DONE] 前最后一个 chunk，
  且该 chunk 的 choices 为空，原来「choices 为空就跳过」会把它丢掉。
- 实测中转透传这些字段。同一轮工具循环：第一圈 cache 0/0，第二圈
  cache_read=192、hit=747‰——没写 cache_control 也有命中，第四层是稳住它
  而不是从零打开。

## 2026-11-01（四）：第 2 层——工具循环

- agent loop 本体落在 conversation.py：stop_reason == "tool_use" 时执行工具、
  tool_result 作为 user 消息交回、整体重读历史再请求，直到模型不再要工具；
  MAX_TOOL_ROUNDS=8 兜底（用尽时 turn span 记 note，stop_reason 如实留 tool_use）。
- 第一个工具 calculator（application/tools.py）：ast 白名单求值，禁 eval。
  实测攻击样本全部拒绝：函数调用、属性探路、语句、lambda、变量名、巨型幂、bool。
  run_tool 永不抛异常——出错/未知工具/参数错都变成 is_error 结果交回模型。
- 工具注册 = 每次请求重申定义：domain/tool.py 的 Tool，all_tools() 随请求带上。
  出站翻译 tool_to_anthropic / tool_to_openai 收在 adapter.py。
- llm.py：stream 增加 tools 参数；落盘块改从完整消息取（Anthropic：最终消息，
  顺带把 thinking 的 signature 回填，消掉该欠账；OpenAI：流里自拼）。
  新增 "tool_use" 片段（只报工具名，给入口做提示）。
- OpenAI 方向适配器补齐（离线验证，未接真实端点）：出站 message_to_openai 改为
  返回列表——工具结果拆成独立 role="tool" 消息、助手调用变 tool_calls 数组
  （arguments 为 JSON 字符串）；入站 merge_tool_call_delta + tool_uses_from_acc
  按 index 拼分片，arguments 解析回字典，与 Anthropic 的 input 对齐。
- tool_call span 接入 trace：挂在 turn 下，data 记工具名/入参/出参/是否出错。
- 验收：真实模型算 (123*456)+789，调两轮 calculator，答 56877（正确）；
  JSONL 四种块齐全；重开会话续接直接答出 56877。
- 记录选型 D12：自建 runtime 而不是站在 OpenCode 上做 MCP+Skills——
  交付生意与产品生意的区别，站在别人 runtime 上最难的 30% 够不着。

## 2026-11-01（三）：trace 骨架

- store.py 的 logs 表退役，spans 表接棒（D11）：一行一个 span（turn / llm_request，
  第二层加 tool_call），span_id / parent_id / start_time / end_time / status / data；
  开始 insert 结束 update，参考 LangSmith 的 span 模型（父子 + 耗时）。
  start_span / end_span / spans_for 三个方法，log / logs_for 删除。
- conversation.py 埋点：每轮一个 turn 根 span，每次模型请求一个 llm_request 子 span
  （开始记模型名和消息条数，结束补 stop_reason）；出错两个 span 都标 error。
  客户端断开时生成器收到 GeneratorExit（不是 Exception），span 停在 running——
  如实反映「这轮没跑完」。
- 新增 `main.py trace <session_id>` 子命令：把 spans 画成缩进树打印（名称/状态/耗时），
  cli.py 的 show_trace。实测正常轮显示耗时、被掐断的轮显示「未结束」。
- 记录部署形态选型 D10：外部系统只持一个会话（POST /chat 换 session_id），
  工具执行、正文、trace 全在本地；HTTP 面保持极小。
- 评测欠账显式化：token 用量尚未记进 span（两条路径取法不同，第四层前补上）。

## 2026-11-01（二）：application 层成立

- `application/conversation.py`：Conversation 成为两入口唯一的会话循环——
  登记、user 落盘、整体重读历史、流转发、assistant 落盘、request/error 日志都在 turn() 里；
  cli.py / web/server.py 缩成纯输入输出。选型与三个随路决定见 docs/decisions.md D9。
- 路径锚定：main.py 启动时 `os.chdir` 到 server/，从仓库根启动 data/ 与 config.ini
  也落在 server/ 下（实测仓库根启动一轮对话，正文进 server/data/sessions/）。
- 配置惰性加载：import server 模块不再读 config.ini（实测 `_cached_config is None`）；
  web 启动时 fail-fast 读一次。
- 会话登记改惰性：第一次真有消息才进 sessions 表，启动即退出的会话不再留空行
  （实测空会话不登记、已用会话登记且 logs 有 request 记录）。
- 验证：cli 续接记忆正常；web 非法 session_id 仍 422、SSE 出流、done 事件回传
  session_id、正文落盘。

## 2026-11-01

- 协议适配器 `infrastructure/adapter.py` 成立：出站两个方向（message_to_anthropic /
  message_to_openai）已接进 llm.py 的请求路径；入站 block_from_anthropic 用真实返回
  验证过一轮 calculator tool_use，等工具循环接线。
- domain 新增 ToolUseBlock / ToolResultBlock，内容块从两种扩到四种。
- 配置体系：config.example.ini 模板 + `infrastructure/config.py`（支持 `${环境变量}` 引用，
  四类错误启动即报）+ `build_chat` 按 provider 挑客户端。url/key/model 不再硬编码。
- 会话 id 统一为 UUID，`transcript.path_for` 校验并防路径穿越。
- review 一轮可读性：两份入口重复的「片段并块」收成 `merge_fragment`；
  `_to_openai` 迁入 adapter；README 结构清单、CLAUDE.md、TODO.md 建立。
- 安全自查：无鉴权仅靠绑定 127.0.0.1；CORS 收紧到 Tauri 三来源；SQL 全参数化。
  结论与遗留见 TODO「已知欠账」。
- sub agent 可维护性 review 落地：stop_reason 归一化（OpenAI 的 stop/length/tool_calls
  翻成统一词汇，防工具循环在 OpenAI 路径静默失效）；SSE 增加 error 事件、非法
  session_id 在推流前返回 422（此前 200 已发出只能断流）；cli 出错不再裸退 traceback，
  一轮作废继续下一轮；SQLite 关闭同线程检查（SSE 同步生成器跑在线程池）；
  空 choices chunk 防护；三处说错的注释纠正；list_sessions/logs_for 标注预留用途。
  review 其余结构性发现（路径锚定、配置惰性加载、循环收拢）并入 conversation.py 一并做。
- 建立文档体系：docs/decisions.md（D1-D8 选型记录）、docs/roadmap.md（六层学习线 +
  基建线与验收标准）、CLAUDE.md（项目认知）、TODO.md、CHANGELOG.md。

## 2026-10-30

- 初始骨架（commit 39fcaa2）：server 按 domain / infrastructure / interfaces 分层，
  AnthropicChat + OpenAIChat 流式调用，多轮历史，JSONL 会话正文 + SQLite 索引与日志，
  main.py 子命令入口；app/ 为 Tauri 模板（vanilla-ts）。
