# 技术选型记录

每做一个重要决定记一条：定了什么、为什么、否掉了什么。新条目放最下面，只追加不改写
（发现旧决定错了就记一条新的推翻它，注明被谁取代）。

---

## D1 分层：DDD 四层，src 下平铺三个包（2026-10-30）

**决定**：`domain / application / infrastructure / interfaces` 四层，`server/src/` 下各是一个包，
不套 `agentlearn` 外层包。

**理由**：学习项目的核心是把「谁依赖谁」看清楚。domain 是纯类型谁都不依赖；
infrastructure 翻译协议和存取；application 编排（循环住这里）；interfaces 只做输入输出。
映射到熟悉的三层：interfaces=controller，application=service，infrastructure=dao+第三方封装。

**否掉的备选**：

- 套一层 `src/agentlearn/`——只有一套代码，外层包没有第二个实现对照，白增嵌套（试过又拆掉）。
- 传统三层命名（service/dao）——概念能映射，但 application 这个名字时刻提醒「用例住这里」。

---

## D2 存储：JSONL 存正文，SQLite 存索引和日志（2026-10-30）

**决定**：一次会话一个 `data/sessions/<uuid>.jsonl`，一行一条完整消息只追加；
`data/agent.db` 两张表：sessions（索引）、logs（事件）。

**理由**：消息形状马上要变（第二层加 tool_use/tool_result），JSONL 无表结构，旧文件照样读；
对话只在末尾生长，追加天然契合。索引和日志是要查询的，放 SQLite。

**否掉的备选**：

- 全 SQLite——消息加字段要改表，学习期不想背迁移。
- 全 JSONL——「列出最近会话」「按会话查事件」是查询，顺序扫文件不合适。
- 两个库文件——两张表分两个 db 纯属折腾。

---

## D3 模型接入：双客户端 + adapter.py 翻译（2026-10-30）

**决定**：`AnthropicChat` / `OpenAIChat` 只管运输；字段差异全部收在 `adapter.py`；
domain 类型（四种内容块）的字段名照 Anthropic 线上格式定，OpenAI 方向在适配器里翻。

**理由**：循环层只应看见一套类型。选 Anthropic 词汇做基准，因为它的 `input` 已是字典，
比 OpenAI 的 JSON 字符串少一次解析。stop_reason 同样统一成 Anthropic 词汇
（OpenAI 的 stop/length/tool_calls 在 llm.py 翻成 end_turn/max_tokens/tool_use）。

**否掉的备选**：

- 只接一家——现实里中转/兼容端点两种协议都会遇到。
- LangChain 的统一模型接口——它连循环一起包了，正是要自己写的东西。

---

## D4 配置：config.ini + ${ENV} 引用（2026-11-01）

**决定**：`config.example.ini` 提交、`config.ini` gitignore；provider/base_url/api_key/model_id/max_tokens
五个键；任何值可写 `${VAR}` 表示从环境变量取；缺文件、缺键、环境变量没设、provider 非法，
一律启动即报错退出，不给默认值。

**理由**：config 是「启动前就有的结构化输入」（provider 这种枚举放 ini 顺手）；
密钥不落盘由 `${VAR}` 单独解决。fail-fast 让问题暴露在启动时而不是运行中。

**否掉的备选**：

- 纯 .env——provider/model 这类非环境配置放 env 语义别扭，没有 section 和注释位。
- 配置入 SQLite——密钥落盘；配置是输入不是运行时数据；db 还在 gitignore 里，换机器就丢。
- config 缺键时静默回退 env——出问题要查两个地方才知道值从哪来；`${VAR}` 让来源永远只有一个（文件）。

---

## D5 框架策略：不引入 LangChain / LangGraph（2026-11-01）

**决定**：学习期全部手写。LangChain 跳过；工具循环写完后读 LangGraph 的
StateGraph / Checkpointer 设计对照自写实现，写一篇对比笔记；做 trace 时参考
LangSmith 的 span 模型（父子关系 + 耗时）设计 logs 事件。

**理由**：框架的每个卖点都对应一块自建基建（见 README 的对照表）。没亲手痛过，
就看不懂它在解决什么；先手写再读是十倍效率。引入框架的时点：需要跨会话持久恢复、
人工审批门这类它做成产品的能力时，再按需评估。

---

## D6 桌面端：Tauri，前后端只通过 HTTP（2026-10-30）

**决定**：`app/` 用 Tauri（vanilla-ts 模板起步），与 server 之间只有 HTTP/SSE，不共享代码。

**理由**：注意力预算留给 agent 本身；Tauri 包小，壳薄。前后端进程独立，各自能单独跑。

**否掉的备选**：Electron（同语言排查省事，但包大；当时倾向 Tauri，用户拍板）。

---

## D7 session_id 一律 UUID（2026-10-30）

**决定**：会话 id 用 UUID，文件名即 id；`transcript.path_for` 校验格式并防路径穿越。

**理由**：id 会拼进文件路径，不校验就能被 `../` 写到目录外；UUID 无碰撞、无语义、可校验。

---

## D8 安全基线（2026-11-01）

**决定**：服务只绑 127.0.0.1（uvicorn 默认），CORS 只放行 Tauri 三个来源；
SQL 全参数化；工具执行永远禁止 eval/exec（calculator 用 ast 求值）。

**理由**：当前「无鉴权也安全」完全依赖只听本机回环。绑外网 / 加 --host 之前必须先加鉴权，
这一条记在 TODO「已知欠账」。

---

## D9 application 层落地：Conversation + 三个随路决定（2026-11-01）

**决定**：`application/conversation.py` 的 `Conversation.turn()` 成为唯一的会话循环，
cli / web 只剩输入输出。随路定死三件事：

1. **历史维护 = 每轮从 JSONL 整体重读**，不做内存累积。
   JSONL 文件是唯一事实来源，崩溃/重启后天然一致；本地小文件读取开销可忽略。
2. **相对路径用 main.py 启动时 `os.chdir(Path(__file__).parent)` 锚定**。
   main.py 是进程唯一入口，chdir 安全；从任何目录启动，data/ 和 config.ini 都落在 server/ 下。
3. **配置惰性加载**（`get_config()` 缓存），import server 模块不再触发读文件。
   否则 main.py 启动 cli 也会连带读 web 的配置，报错出现在 import 阶段。

**理由**：两份循环已经开始漂移（cli 内存累积 vs web 整体重读、登记时机不同），
第三处（工具循环）即将加入——先收拢再扩展。会话登记改成惰性（第一次真有消息才
create_session），顺带消掉「启动即退出留下空会话行」的噪声。

**否掉的备选**：

- 路径锚定改成层层传参（main 传给 cli/web 再传给 Transcript/Store）——三处签名都要改，
  学习项目里 chdir 一行的确定性收益更高。
- 历史内存累积 + 落盘——两份真相（内存和文件），崩溃后不一致，还要处理半截消息。

---

## D10 部署形态：外部系统只持一个会话，其余全在本地（2026-11-01）

**决定**：企业级接入方式只有一种——企业系统开一个会话（HTTP 上就是 POST /chat 换回
session_id，之后每次带着它）。工具执行、消息正文、trace 记录全部发生在 agent
所在的本地机器上，外部系统不需要理解工具和轨迹的存在。

**理由**：HTTP 面保持极小（chat + 将来的会话列表），边界清楚——企业交出来的是一个
会话句柄，不是机器控制权；工具碰的是本地文件和进程，本来就该在哪跑就记在哪。
trace/评测数据留在本地，回放完全离线可做。现在的 interfaces/web 就是这个边界。

**否掉的备选**：

- 工具执行搬到远端服务——工具的价值就在本地上下文（文件、进程），搬走就没了意义。
- 外部系统参与 trace 上报——耦合增加，评测数据反而散在两边，拼不回完整轨迹。

---

## D11 trace：spans 表取代 logs 表（2026-11-01）

**决定**：logs 表（一行一个点事件）退役，spans 表接棒。一行一个 span——一段有起止的
操作，现在两种：turn（一轮对话，根）、llm_request（一次模型请求，子）；
第二层加 tool_call。字段：span_id / parent_id / name / start_time / end_time /
status（running / ok / error）/ data（JSON）。开始时 insert，结束时 update 回填。

**理由**：参考 LangSmith 的 span 模型（父子关系 + 耗时），这是评测回放的最小完备形状：
「每步多久、什么顺序、哪层出错」一次查询全出来。「开始写、结束改」让崩溃或断连
留下 status=running、end_time 为空的半截 span——本身就是证据（实测：SSE 被客户端
掐断的那轮在 trace 里显示「未结束」，正常轮显示耗时）。spans 只记控制流不记内容，
「说了什么」仍以 JSONL 为准（D2 不变），两边拼起来才是完整回放。

**否掉的备选**：

- 维持 logs 点事件——没有起止和父子，算耗时只能相邻行相减，脆弱且算不出嵌套。
- 事件对（span_start / span_end 两行纯追加）——读的时候要自己配对，写崩更容易出孤儿；
  SQLite 是本地索引不是只追加的日志流，update 没有代价，换取读侧简单。

---

## D12 自建 agent runtime，而不是站在 OpenCode 上做 MCP + Skills（2026-11-01）

**决定**：学习期自建循环（本项目的路线）。明确两门生意的区别：**交付生意**（把 AI 能力
接进某家公司）站在开源 runtime（OpenCode / Claude Code 等）+ 自写 MCP servers +
Skills 是合理解；**产品生意**（卖一个 agent）循环本身就是差异化，必须自建。

**理由**：站在开源 runtime 上，扩展点以内的东西改得动，最难的 30% 够不着——上下文
压缩策略、重试语义、权限模型、成本控制都在别人代码里，跟别人的 roadmap 走；且这些
runtime 的循环为编码场景调优（改文件、跑命令），做业务 agent 会一直和框架预设打架，
忍不下去 fork 之后，维护的其实就是自研 runtime，只是起点是别人的代码。
学到的判断标准：runtime 越来越像商品，MCP / Skills 是可携带资产——第五层做完 MCP 后，
自家 agent 与 OpenCode 是平级 runtime，消费同一生态，届时「自用还是换」才是知情选择。
（这是 D5 否掉 LangChain 的同一个理由，放大版。）

**否掉的备选**：

- 全部站在开源 agent 上只做外围——适合纯交付，但「我们开发有什么用」的答案是门后面的
  房子（循环、工具、上下文、trace、评测），接口只是门；让渡循环等于让渡产品。
- 只学自建、无视开源生态——另一侧的错。MCP/Skills 标准要吃透，它们决定了工具和知识
  的可携带性，也是评估别人 runtime 的坐标系。

---

## D13 思考强度：自己的五档，缺档夹紧，不支持就省略（2026-11-02）

**决定**：配置和循环只认 `off / low / medium / high / max`（空 = 不传）。
翻译在 adapter：Anthropic 发 `output_config.effort`，OpenAI 发 `reasoning_effort`。
对方没有的档位夹到最近的一档，trace 写成 `max→high`，不静默丢掉。
`off` 不翻译成「关掉思考」——一批模型对 `thinking: disabled` 或 `reasoning_effort: none`
直接 400，可移植的做法是省略参数、用服务端默认。模型或中转整个不支持时，配置留空。

**理由**：强度是我们的概念，字段名是协议差异，只能出现在 adapter（约定 2）。
中转背后的模型会换，按 model_id 硬编码能力表会立刻过期，所以能力差异用
「夹紧 + 留空」两招，不用查表。

**否掉的备选**：

- 按模型 ID 维护一张能力表——中转换模型表就错，维护成本高于收益。
- 400 了自动去掉参数重试——失败被吞掉，和 D4 的 fail-fast 反着来。
- 把 Anthropic 的 xhigh 单列一档——五档够用，需要再加。

---

## D14 单启动器与单 TUI 门面：main.py 只装配，CLI 退场（2026-11-02）

**决定**：`server/main.py` 是唯一进程启动器和组合根，只解析模式、装配依赖、分发：
`tui` 进终端工作台，`trace` 进只读终端检查器，`desktop` 启 Tauri 后端服务，
`web` 保留为 `desktop` 的兼容别名。删除 `interfaces/cli.py` 和纯文本逐行模式。

终端只有一个公开入口包 `interfaces/tui`，门面只导出 `run_chat` / `run_trace`；
`chat.py`、`trace.py` 是包内实现，不是两个并列的 UI 入口。两者的 Theme 共用，
但应用类、加载依赖和生命周期分开：对话模式需要模型与 worker，trace 只读
Store/JSONL，不读 `config.ini`。

**理由**：入口越少，「怎么启动、会走到哪一层」越确定；`main.py` 负责对象装配，
界面层只消费已经装好的依赖。对话和 trace 的工作台骨架相似，但状态机完全不同，
硬塞进一个 App 会让每个方法都带 mode 分支；而拆成顶层 `tui.py` / `trace_tui.py`
又会重新长出两个公开入口。包内分实现、包外单门面是这两者之间最小的边界。

**否掉的备选**：

- 保留 cli.py 作纯文本回退——用户已明确不要，且会让对话 I/O 重新出现第二条路径。
- 做成 `tui.py` + `trace_tui.py` 两个顶层文件——调用方又要理解两个入口，
  和「只留一个 TUI 给启动器」相反。
- 一个 App 类里用 mode 分支——对话 worker、输入台和 trace 的树/详情没有共享状态，
  条件分支只会把两套生命周期绑死，改一边容易碰坏另一边。

---

## D15 运行中模型设置：只改 [llm]、密钥不回显、下一请求生效（2026-11-03）

**决定**：TUI 和 desktop 都通过 `GET /settings` / `PUT /settings` 读写同一份
`server/config.ini`。设置项限定为 provider、model ID、base URL、max tokens、
effort 和 API key；`GET` 只返回密钥是否配置及其来源，不返回真实值；`PUT` 的
API key 为空表示保留原值。写入时只替换 `[llm]` 中已知的键，保留注释、未知键和
其他段落；先写临时文件并交给真实加载器校验，通过后才原子替换原文件。

保存成功后不做进程重启：TUI 立即重建模型客户端，desktop 清掉配置缓存，
下一轮模型请求读取新配置。正在流式输出的当轮不切客户端。

**理由**：`config.ini` 已经是唯一配置源，界面不应该再维护第二份状态；用户改完
模型或中转地址要能马上验证，不必退出重开。密钥输入框保持空白既避免把 secret
送进 WebView，也避免误把界面里的占位值写回文件；保留注释是因为配置模板本身承担
教学说明，设置器不能把手工维护的内容洗掉。

**否掉的备选**：

- 设置单独存 JSON / localStorage——和 `config.ini` 形成两个真相，TUI 与 desktop
  会漂移，命令行启动也读不到。
- 把密钥读回表单方便编辑——增加泄露面，也没有必要；输入新值或留空即可。
- 保存后强制重启进程——中断当前工作，且桌面端还要额外做进程编排。
- 当前流式请求中途换模型——同一条 SSE 会混两套模型输出，trace 也无法解释。

---

## D16 TUI / desktop 视觉：黑绿硬边缘控制台，不做通用 AI 工作台（2026-11-03）

**决定**：TUI 与 desktop 使用同一套视觉方向：近黑画布、酸性绿信号色、硬边框、
无圆角、无柔和光晕。运行中状态用 amber，错误用 red，其余交互只用 green 与 line
两级强调。desktop 宽屏是三段式会话轨 / 执行流 / 运行日志，窄屏收起会话轨；
TUI 宽屏是执行流 / `RUN LOG`，窄终端收起日志轨。

TUI 的时间线严格按事件顺序挂载：thinking、正文片段、tool_use 卡片出现在各自发生
的位置，tool_result 回填原卡片，恢复会话时也按 JSONL 的 `content` 块顺序渲染。
thinking 折叠标题实时显示字符数和最新尾部片段，形成一行滚动预览；展开后仍可看全文。
空态使用 `awaiting input`、命令行提示和纵向示例，不再使用 Hero 或 starter card。

两端的色值分别维护，CSS 不直接消费 Python 常量；共享的是视觉语义、状态色映射和
信息结构，改色时需要同时核对两端。

**理由**：旧版暖纸三栏把执行工具排成了落地页，视觉重心落在营销文案和 feature
card 上，和本地 agent 的实际工作流不一致。黑绿控制台把“输入、执行、日志”直接
排成操作台，减少装饰层，也避免通用 AI 产品的圆角白卡 + 渐变光晕语汇。把思考、
工具和正文按真实顺序排开，用户看到的过程才和 trace 一致，而不是三个互不相干的分区。

**否掉的备选**：

- 继续走暖纸编辑风——单看精致，但首屏仍像产品官网，用户已明确说“有 AI 味”。
- 通用暗色 SaaS——只换颜色不改信息结构，三栏卡片和 Hero 仍然存在。
- 黑绿 + 大圆角 + 毛玻璃——会变成常见的赛博 AI 模板，和“纯野兽”相反。
- TUI 继续保留暖橙 / 青绿——两端会像两个产品，同一套状态语义也会漂移。
- thinking / 正文 / 工具三块分别聚合——屏幕顺序和真实执行顺序不一致，工具还会
  全部堆到时间线末尾。

---

## D17 bash 边界：纸糊沙箱是明知的取舍，不做命令过滤（2026-11-03）

**决定**：bash 工具维持现状——完整 shell、cwd 只是起跑点、无网络隔离，
本机单用户学习期接受；对外暴露前的硬前提是进程级隔离（容器或独立用户 +
断网/白名单）。不做命令关键词过滤。这条取舍一直写在代码注释和 TODO 里，
本次借一次实测审计正式立据。

**证据**（2026-11-03 让自家 agent 用 bash 审计自己的运行环境，全部实测）：

- `ls ..` 逃出 sandbox，`../..` 可达 server 根（早于本次已实测 cat config.ini 可读）
- `whoami` 是本机真实用户；`say` 出声——文件系统之外的副作用同样开放
- `curl example.com` 直连 200：出站畅通，且子进程环境已剥到只剩 PATH，
  没有代理变量也照样出网
- 串起来是完整攻击链：提示注入（模型读到的文件/网页）→ 读本地文件 →
  POST 出网。config.ini 磁盘上只有 `${LLM_API_KEY}` 引用，但 ~/.zshrc
  里的 export 对模型可读，链路依然闭合
- 每条命令都有 tool_call span，整个审计过程可在 trace 里回放

**为什么不做命令过滤**：过滤器只能看命令字符串，看不到 shell 展开后真正
执行的东西——`$(...)`、变量、引号编码都绕得过。它拦得住模型的失误，
拦不住对抗，纸糊的沙箱上再糊一层纸只换来虚假安全感。真边界只有进程级：
Docker，或 macOS sandbox-exec 的 profile（限文件系统范围 + 断网）。

**否掉的备选**：

- 关键词黑名单（`..`、sudo、rm）——可绕，且和「description 必须如实」的
  立场矛盾：过滤后描述就得写「已拦截危险命令」，那是撒谎。
- 回到 shlex.split 直 exec——等于向模型承诺残缺的 shell（`$((...))`、`>`
  全是字面量），第二层已否（见 tools.py 注释）。
- 现在就容器化——学习期单用户，收益抵不过 Docker 依赖的复杂度；
  作为「对外前」的前置条件挂在 TODO。

---

## D18 斜杠命令：界面层快捷动作，不混入模型对话（2026-11-03）

**决定**：TUI 和 desktop 的输入台都以 `/` 触发命令面板，支持方向键选择、Tab
补全、Enter 执行、Esc 收起。首批命令固定为 `/model <id>`、`/config`、`/now`、
`/files`、`/boundary`、`/help`。

命令由界面层解析，不发进 `Conversation.turn()`，也不执行任意 shell。`/model <id>`
只修改 `config.ini` 的 model ID 并保留其他设置：TUI 走已装配的 settings saver，
desktop 走 `GET/PUT /settings`；下一轮请求生效。`/now`、`/files`、`/boundary`
只是把预置 prompt 交给正常的会话流程。未知斜杠命令在本地报错，绝不退化成发给模型
的普通问题。完整的 config 编辑统一从 `/config` 或 `Ctrl+,` 进入，顶栏不再保留
独立的 `CFG` 按钮；`/settings` 仅作为兼容别名。

**理由**：模型设置和示例 prompt 属于界面快捷动作，不属于 agent 的业务循环。放在
输入台附近能降低记忆成本，同时保持 application 层只处理真实用户问题；命令集合
显式白名单化，也避免把“命令输入”误解成在宿主 shell 里执行任意内容。

**否掉的备选**：

- 让用户自己记 `/model xxx` 等隐藏字符串，不做可发现面板——新用户不知道有哪些命令。
- 把斜杠命令当普通 prompt 发给模型——浪费请求，且无法可靠完成设置修改。
- 为命令新增一套后端路由——当前动作都能复用 settings 和现有 `/chat`，多一层 API
  只会增加两端同步成本。
- 在 TUI 里把 `/` 命令直接交给 shell——越过工具边界，安全性和可审计性都会退化。

## D19 同会话消息 steering：三档等待颗粒度，抢占让位于配对不变式（2026-11-03）

同会话连发消息时两轮 turn 不能交错写 JSONL。服务端按请求体的 `steering` 字段
分三档（`application/session_manager.py`，web 进程单例）：

- `queue`：等当前轮 end_turn，再跑自己的轮。等待单位：一整轮。
- `cooperative`：消息塞进运行轮的 inbox 立刻返回 ack（本连接 done 是
  `injected`）；运行轮在下一个圈界（厂商请求间隙）排干 inbox、落盘、发
  `steering_injected` 事件，下一圈请求里模型就看得见。等待单位：一次请求。
- `preemptive`：设取消标志；运行轮在下一个检查点抛 `TurnCancelled`，半截回答
  不落盘，turn span 记 `cancelled`（新增状态，区别于 error 和断连留下的
  running）；新消息等它退场后跑自己的轮。等待单位：趋近于零。

**抢占的真实上限**：检查点只能设在生成器恢复执行的地方（事件 yield 之后、圈界）。
阻塞中的网络读打断不了——厂商请求挂死时，抢占者最多等 120 秒
（`_PREEMPT_WAIT_SECONDS`）后放弃等待。真·零等待做不到，不粉饰。

**配对不变式优先于抢占及时性**：工具执行段内部没有检查点。assistant(tool_use)
已经落盘，对应的 user(tool_result) 不跟上，这个会话就再也续不上（Anthropic
要求每个 tool_use 有配对的 tool_result）。所以工具批次一旦开跑就完整落盘，
取消推迟到下一圈圈界生效——离线测试抓到的就是这个 bug：检查点原来放在
tool_result 事件之后、结果落盘之前，抢占会让 tool_result 丢失。

**随路**：

- adapter 新增 `history_to_anthropic`：圈界插入后历史尾部必然出现连续 user
  消息（tool_result + 新消息），Anthropic 要求交替，合并成一条再发。
- Store 收归进程级单例并加 RLock（`_locked` 装饰全部方法）：省的只是建连接
  开销，SQLite 写锁本来就是库级的；锁防的是同一条连接上两个线程交错
  execute/commit。`threading.Lock` 不保证 FIFO，先来后到是近似的。
- 工具能否中途取消算工具自己的属性，留到 MCP 层。

**验收**（2026-11-03，真机三档并发，每档一个会话，A 跑 `sleep 6` 期间发 B）：
queue 下 B 等 A 答完「完」再答「乙」；cooperative 下 B 立即拿 ack，A 的流在
tool_result 后出现 steering_injected 并直接答「乙」；preemptive 下 A 收到
steering_cancelled（JSONL 为 user/assistant(tool_use)/user(tool_result)，
turn span cancelled），B 自己的轮正常答「乙」。非法档位推流前 422。

**否掉的备选**：

- 真·零等待抢占（杀掉阻塞中的请求线程）——线程强杀不安全，且厂商请求的
  socket 读取消不掉；检查点轮询是能做到的最细颗粒。
- 抢占时丢弃整个未完成的工具批次（连 tool_use 一起回滚）——JSONL 只追加
  不改写是持久化的基本约定，回滚比「推迟一个圈界」复杂得多，收益只是
  少等一个正在跑的工具。
- asyncio 重写整个服务换成可取消的 task——三档用线程锁和 Event 就表达完了，
  为 steering 一个需求换并发模型不值。

## D20 执行等级：approve / auto / yolo，审批不靠命令过滤（2026-11-03）

工具执行前加一道人工门，三个等级，运行态、不进 config.ini。TUI 和 desktop
都用 Shift+Tab 循环切换，而且当前轮运行中也能切：每个工具调用前实时读取
`RunningTurn` 的执行等级，只影响后续工具调用。

- `approve`（默认）：每次工具调用都暂停。界面给 Accept / Deny，Accept 才
  真正 `run_tool`。
- `auto`：只读工具（现在只有 now_time）直接放行，bash 仍走 Accept。
- `yolo`：全部直接执行，不询问。这是加这道门之前的行为。

拒绝和超时（300 秒没人答）都变成带 `is_error` 的 tool_result 交回模型，
不抛异常，配对不变式不受影响（D19）。等待发生在执行线程里：TUI 模态框
回填，desktop 收到 `tool_approval` 事件后 `POST /approval`。

运行中切到更宽的等级时，已经挂起的审批也要当场处理，而不是等下一次工具：
`RunningTurn` 记录本轮的 pending approval id，TUI 直接调用 broker 放行并关闭
旧模态框；desktop 用 `POST /approval-mode` 更新对应 session 的当前轮，响应里的
`auto_approved` 让前端撤掉过期按钮。`auto` 只放行无副作用工具，所以 bash
的待审批项在切到 auto 后仍然保留；切到 yolo 才全部放行。
若请求已发出但服务端还没 `begin` 运行轮，端点把新等级暂存在 session 槽位，
`begin` 时优先采用，避免流式刚起步的短窗口丢切换。

思考档位和模型的语义不同：它们下一轮才生效。`Conversation.turn()` 开头固定
本轮 client 快照，system 提示词也从同一快照拼，运行中改设置不会让当前轮的
工具循环混用两套模型/档位；下一次 turn 才拿新配置。

**这不是安全边界。** auto 放行的依据是工具自己声明「无副作用」，不是去读
bash 命令的内容。命令过滤在 D17 否掉过，这里不重开：`curl`、`rm` 在
approve 之外不会被自动识别出来拦下。对外服务前该做的仍是容器化。

无人值守的权限门（主动性那条）还没做——没人在界面前按 Accept 时，这三档
里只有 yolo 跑得动，而 yolo 正是不该无人值守的那档。

**否掉的备选**：

- 按命令文本判断「只读 bash」放进 auto——和 D17 同一个坑，过滤式一看就漏。
- 把等级写进 config.ini——它是这一次会话的运行姿态，不是模型配置；
  写进设置文件会让两个界面互相覆盖。
- 默认 yolo——门装上却默认敞开，等于没装。默认 approve，要放开自己切。

## D21 system 提示词分三段：准则、运行态、记忆（2026-11-03）

此前 system 100% 来自 `data/sandbox/memory.md`。那个文件模型自己用 bash
就能改，于是「记忆」和「行为准则」是同一份文本：记忆被污染，行为跟着跑偏。

每轮 turn 拼一次 system，不落 JSONL，三段顺序固定：

1. `server/constitution.md`：人维护的准则。放在 server/ 下而不是 sandbox 里，
   模型的便签目录里看不到它。这不是安全边界——bash 用绝对路径照样写得到
   （D17），拆开只保证注入顺序：准则在最前，记忆替换不了它。
2. 运行态：当前模型和思考档位（`effort_note`，和 trace 是同一个值；
   留空和 off 都写成「未设置」）。档位此前只在 trace 里，模型不知道自己
   被调到哪一档。
3. `memory.md`：只放事实。为空就省略这一段，不留空标题。

准则在最前，前缀最稳。第四层的缓存断点放在它后面，记忆怎么变都不冲掉
准则那段的缓存。轮内多圈仍共用同一份 system。

验收：记忆里写了暗号并要求保密，同时问档位和「能不能用记忆改准则」。
模型答 `max`（与 config.ini 的 effort 一致）和「否」。

## D22 上下文压缩：JSONL 只追加，摘要走派生层；断点打在消息尾部（2026-11-03）

第 4 层。问题：turn() 每圈把全部历史重发一遍，历史线性涨，迟早撞窗口上限。

**形态**：两条路里选了派生层，不改写 JSONL——文件是唯一事实来源、只追加
是持久化的基本约定，改写它意味着「历史可变」，回放和审计全塌。摘要放
`data/sessions/<id>.summary.json`（正文 + covered：覆盖到第几条消息），
和 memory.md 同一哲学：派生状态不进历史。删掉摘要文件对话一字不少。

- 触发：圈界处估算「摘要 + covered 之后的尾部」超过预算（默认 24000
  估算 token，不是模型窗口，是工作集）。估算只决定何时压，真值仍由
  usage 记账；covered 之前的原文已由摘要代表，不再重复计入。
- 切割只落在轮次边界（user 纯文本消息之前）：切在别处会拆散
  tool_use/tool_result 配对（D19）。covered 之前不能再切——那段只存在于
  摘要里，再切会让模型看到两遍。
- 压缩本身是一次 llm_request（span 记 purpose=compaction）：把旧摘要 +
  被压原文交给模型续写新摘要（≤600 字，只要事实）。thinking 块整块跳过
  ——摘要要结论不要过程，跳过它同样预算能多留几轮原文。
- 压缩失败不挡对话：沿用旧摘要和原 covered 尾部，错误记在 span 上；
  不把已覆盖原文连摘要一起重发两遍。
- system 第四段挂会话摘要，四段固定顺序不变（D21）。

**缓存断点**：Anthropic 方向在最后一条消息的最后一个内容块打
`cache_control`（估算前缀 ≥2048 token 才打：低于最短可缓存长度直接 400）。
断点跟着尾巴走，工具循环每圈追加的历史都命中上一圈前缀，system 整段
（准则/运行态/记忆/摘要）在前缀里一起被缓存。OpenAI 自动前缀缓存，
无标记可打——协议差异收在 adapter（mark_cache_breakpoint）。

**验收**（预算 1000、7 轮、首轮埋暗号）：主请求 input 236–547 平住，
cache_read 最高 512、命中率 655‰；covered=10 时仍答对暗号。

**否掉的备选**：

- 改写 JSONL 成「摘要 + 尾部」——历史可变，回放/审计/崩溃恢复全塌；
  摘要生成失败时连原文都保不住。
- 按 token 数精确截断——要么引 tokenizer 依赖，要么相信估算；估算误差
  只影响「早压或晚压一轮」，不影响正确性，够用。
- 摘要存进 SQLite——它是正文的派生物，生命周期跟会话文件走，放一起删一起。

## D23 MCP：手写 stdio 客户端，外部工具以「别名__工具名」进注册表（2026-11-03）

第 5 层。工具从外部进程来：config.ini 每一个 `[mcp:<别名>]` 段 = 一个
MCP 服务器子进程。循环代码一行不改——Conversation 认的还是 all_tools() /
run_tool()，外部工具只是注册表里多出来的条目。

**协议手写，不用官方 SDK**。MCP 的 stdio 传输就是子进程 stdin/stdout 上的
JSON-RPC 2.0、一行一条 JSON；我们要会的只有三个方法：initialize（握手）、
tools/list（拿说明书）、tools/call（执行）。SDK 把这些全藏掉，而看清
「工具定义是怎么从另一个进程声明出来的」正是这一层的课题。例子服务器
（examples/mcp_count.py）也手写，两边看的是同一份协议。

**命名**：`别名__工具名`（如 cnt__count_words）。不同服务器可能有同名工具，
前缀保证全局唯一；内置工具名永远不含 `__`，run_tool 先查内置注册表、
再按前缀分流，路由不会撞。inputSchema 就是 JSON Schema，和 domain.Tool
的 input_schema 同形，零翻译。

**生命周期是真正的难点**（协议只有三行）：

- 读线程 + 队列：readline 阻塞没法带超时，超时（30 秒）只能靠线程包出来。
- 响应按 id 归属，等待途中到的通知（无 id）读了就丢。
- 进程死了自动重启（下次调用重新握手）；单个服务器起不来只跳过它，
  工具列表不被一个坏进程拖垮，错误在被点名调用时如实报给模型。
- stderr 丢弃（DEVNULL）：服务器日志不弄脏 TUI，调试时再接文件。

**欠账**：单服务器内的并发调用没有真正串行化——现在靠「工具循环顺序执行」
这一事实兜底，两个线程同时调同一个服务器时响应归属可能读串。循环并行化
（第 3 层欠账）之前不需要解。工具级失败走 isError（协议层错误才抛），
图片类 content 块跳过——那是「眼睛」缺口的事。

**验收**（2026-11-03，真机）：config.ini 加 `[mcp:cnt]` 一段，工具列表多出
cnt__count_words；问「统计这句话」，模型调它、拿到「字符数（不含空格）: 8；
词数: 3」并答对；tool_call span ok。离线：错误参数、幻觉工具名、内置工具
不受影响、kill 子进程后自动重启重握手。

## D24 Skills：索引常驻、正文按需，技能就是 skills/ 里的一个 md（2026-11-03）

第 6 层。技能 = 教 agent 某类任务怎么做的指令包：名字、说明、正文。

**加载机制就是已有的工具机制**，没有新架构：

- `skills/*.md` 一个文件一个技能，头部是两行「键: 值」的简化 frontmatter
  （不引 YAML 库——只有两个字符串键，手写解析比依赖诚实）。
- 常驻上下文的只有索引：每个技能一行「名字：说明」，挂在 `read_skill`
  工具的 description 里随工具定义每次重申。这是 Skills 唯一的常驻开销。
- 正文由模型自己调 `read_skill(name)` 拉进来——读进来是普通 tool_result，
  长会话里被第 4 层的压缩自然收走。「不占常驻上下文」由这两层叠出来，
  不是 skills 自己实现的。
- 加技能 = 丢一个 md 文件进 skills/，每圈 all_tools() 重扫，不用改代码
  不用重启——和 MCP「加配置即得工具」同一个取向。
- 技能要用的脚本：正文里写「跑 xxx.py」，执行走 bash。加载归 skills、
  执行归工具，边界和 MCP 一致。

**验收**（A/B 真机，同一句「写运行报告」）：无技能时模型答 0/4 格式标记
（项目格式猜不出）；有技能时先调 read_skill 再输出 4/4（标题、SEC-A、
SEC-B、落款全对）。技能正文只在 tool_result 里走过一遍。

**否掉的备选**：

- 正文整段进 system——那正是「占常驻上下文」，验收过不了；技能多了
  system 无限膨胀。
- 专门的 skill 管理器和 skill 注册 API——目录扫描 + read_skill 两个函数
  就是全部，多一层抽象没有多一层能力。
- YAML/JSON 技能清单——md 的 frontmatter 人直接写，工具链零依赖。

## D25 眼睛：图像进 tool_result，两家的分歧是全套里最大的一处（2026-11-03）

agent 自盘点五缺口的第一个。新增内置工具 screenshot：screencapture 截屏
-> sips 缩到最长边 1000px（全屏原图一张 ≈5k token，缩图掉一个量级）
-> base64 进 ImageBlock，临时文件即用即删（截图不落盘，要留图模型自己
用 bash 存）。run_tool 的返回从二元组变三元组： (文本, 是否出错, 图|None)。

**domain/存储形状 vs 线上形状，第一次真正分家**。此前 to_dict 恒等于是
Anthropic 线上格式；ImageBlock 是我们自己的形状（media_type + data），
JSONL 按它存（约定 7：完整内容块落盘，图不存会话就续不上），出站翻译
全部收进 adapter：

- Anthropic：带图的 tool_result，content 从字符串变成块列表
  （image 块在前、text 块在后）。无图消息也不能恒等了——asdict 会给
  每个 tool_result 塞 "image": null，线上多一个协议外的键，一律剥掉。
- OpenAI：role="tool" 只能是文本，图作为紧跟其后的一条 user 消息补发
  （data URI）。图紧跟 tool 消息，模型仍对得上号。

压缩（D22）顺路：图所在的 tool_result 被压掉时正文只剩「[图] …」标记；
spans 只记 image_bytes 不记 base64（D11 控制流原则）。SSE 事件只报
has_image 布尔，不搬 base64——前端渲染图片是另一件事。

**验收**（真机）：「看一眼我的屏幕」→ screenshot（625KB PNG）→ 模型
答出屏幕上实际在播的内容（综艺名、嘉宾、角标广告，只有看到图才答得出）；
JSONL 带图、下一圈请求正常、tool span 记 image_bytes、前缀缓存仍命中。
离线：两方向协议形状、JSONL 往返、老数据兼容（无 image 键读回 None）。

**尾巴**：PNG 体积可再省（sips 转 JPEG 或再压边长）；TUI/desktop 尚不
渲染图（事件只有 has_image）；MCP 工具结果里的图片块仍跳过（同机制，
等有真实需求再接）。

## D26 耐心：后台任务走轮询式，四个纯工具，循环零改动（2026-11-03）

五缺口第二个。bash 的 10 秒超时是防挂死的，不是能力上限——正经的长活
（装依赖、跑测试）需要一个等得起的地方。

**选了轮询式**：start_job（后台起命令，立刻返回 job_id）/ wait_job（阻塞
等结束，单次上限 120 秒，超时如实报「还没结束」不算失败）/ job_status
（查状态和输出尾部）/ stop_job（杀整个进程组）。全部是普通工具：
conversation 一行不改，steering / 审批 / span 全部白拿。轮询烧
MAX_TOOL_ROUNDS（每查一次算一轮）、wait_job 不烧（一次调用慢慢返回）——
这个代价写进工具说明，模型自己选姿势。

**否掉事件注入式**（任务完成时像软中断一样把「job 好了」塞进圈界）：
要 turn 级的暂停/唤醒，是新架构；inbox 机制只在「轮还活着」时可用，
任务跨轮存活就意味着轮必须挂着等——那其实就是 wait_job。等第五层
MCP 生态里出现真实的长任务需求再重新评估。

工程细节：输出写 data/sandbox/jobs/<id>.log（不进内存，多大都装得下，
bash 和人都读得到）；start_new_session 独立进程组，stop_job 连子进程
一起杀（修掉了 bash 超时路径「只杀 shell、子进程残留」的老坑）；
账本写 `data/sandbox/jobs/<id>.json`，重启后按 PID + 进程启动 token 认领；
内存限量 50 条。任务默认 30 分钟墙上寿命，到期 janitor 杀整个进程组并把
原因写进 log 与元数据。wait_job / job_status 进 auto 档自动放行集（只读），
start_job / stop_job 和 bash 一样要确认。

**验收**（真机）：「跑 sleep 14 && echo …（超过 bash 上限），用后台任务」
→ 模型自己走 start_job → wait_job，exit 0、输出正确、tool span 全 ok。
离线：wait 超时路径、status、stop、log 可被 bash 读、幻觉 id、auto 档
放行集。

**尾巴**：重启后只能按元数据认领仍可确认身份的进程；PID 复用或退出码
丢失时如实报不可接管，不猜。

## D27 主动性：触发器只做消息注入，无人值守钉死 auto 档；动手能力等容器（2026-11-03）

前置选型（五缺口立项时留的题）用户拍板：要「不在场也延续的存在」，
不是「更聪明的回话者」。这一层因此立项；同一天用户确认当前中转模型
不吃音频输入块，耳朵（语音输入）无限期搁置——本地方案（whisper 之类
先转文字再进管道）要用再说，不占队列。

**形态**：常驻调度线程（desktop server 的 lifespan 启动，TUI 进程不跑
——「不在场也延续」需要一直开着的进程）+ 三种触发器
（`application/triggers.py`：interval / file / jobdone）+ 无人值守执行端
（web/server.py 的 _run_unattended：SessionManager 会话锁、auto 地板、
Conversation 一轮、事件丢弃——落盘就是全部输出）。

三条关键决定：

1. **触发器是消息注入器，不是行动授权**。文件触发器由服务器自己读
   文件、任务触发器自己读 log 尾部，把内容拼进 user 消息。如果触发
   靠模型自己去 cat，等于给 auto 档开了个 bash 旁门。能力在触发器
   手里，模型只有文字。
2. **无人值守的权限地板是 auto，结构性安全先于容器**。auto 档下
   bash / start_job / screenshot 全被拒，模型能改的只有会话本身；
   自触发环（触发→行动→再触发）在权限层就断了——文件触发器盯的
   路径模型写不了、start_job 被拒造不出任务完成事件。这是论证出来
   的不需要防风暴计数器，第二阶段放开手脚后此论证失效，熔断（次数/
   预算上限）从「以后再说」变成「必须做」。
3. **remember 是 auto 档唯一被预授权的写**：往 memory.md 追加一行带
   作者时间戳的记录，等价于用户自己的 /memory 命令。无人值守的
   「存在」靠它把观察到的东西攒下来——窄口子的作用域授权，和开
   bash 是两回事。

**会话**：每个触发器一个固定会话，uuid5(名字)——跨重启稳定（不能每晚
失忆）、合法 UUID（过 path_for 校验）、免得让操作者在 config.ini 手抄
一串 UUID。副作用是有人的轮次和无人值守轮可以共用同一个会话：同一个
存在，白天你跟它聊，夜里它自己醒。

**执行端细节**：会话锁拿不到（timeout=0）就放弃这一 tick 不排队——
调度线程被一个长轮次挂住会停摆所有触发器；check()/mark() 分离，
触发和执行解耦，执行失败不消费触发（连续 3 次失败后丢弃并写
notices，静默吞触发是调试地狱）；无人值守轮数上限 4（auto 档下
够用，上限是烧钱保险丝不是能力设定）；turn span 记 origin=
"trigger:<名>"，trace 里分得清人的轮次和它自己的轮次。

**验收**（真机）：config.ini 加 [trigger:smoke]（file 触发器）→ 起
desktop server → 往 inbox 写「1+1 等于几」→ 无人值守轮自己醒来：
注入消息带内容、模型答对、调 remember（auto 放行）、记忆落盘、
turn span origin=trigger:smoke、notices.log 记 stop_reason=end_turn。
离线：三种触发器的触发/记账/不触发路径、调度器失败计数与丢弃、
remember 空内容转 is_error、auto 档放行集。顺手修掉两个既有问题：
jobs 账本 exit_code 懒缓存没人 poll 就永远看不见「跑完」（触发器
自己 poll）；SANDBOX/CHILD_ENV 的 tools↔jobs import 环（jobs 先导入
必炸）拆进独立的 application/sandbox.py。

**第二阶段（未做，容器化是前置）**：把执行环境搬进容器（只挂 sandbox
卷，宿主的 ~/.zshrc 和密钥不存在，D17 攻击链的「读密钥」一步空手），
然后把无人值守地板提到可配置的 yolo——夜里真的动手干活。届时必须
一起做：触发/预算熔断（自触发环的权限论证已失效）、任务账本落盘
（daemon 重启的恢复语义变了）。

**否掉的备选**：

- APScheduler / cron 库——需求是「几个 if + sleep」，引调度库是
  用依赖换仪式感；interval 最小 60 秒在配置层挡住触发风暴。
- watchdog / inotify / FSEvents——mtime+size 轮询 5 秒粒度对「有人
  留了话」绰绰有余；文件系统事件 API 的平台差异和依赖不值得。
- 事件注入式（任务完成时像软中断一样叫醒对话）——同 D26 否掉的
  理由：要 turn 级暂停/唤醒的新架构；轮询 + 消息注入今天就能用。
- 每个触发器独立子进程——隔离收益在容器化后才真实，现在只是
  多一堆进程管理。
- 无人值守直接 yolo——「没人盯 + 纸糊沙箱 + 出网」= D17 攻击链
  无人值守运行，宁可第一阶段的主动性笨一点。

## D28 主动性频率熔断：按触发器限成功轮，冷却期消费但不执行（2026-11-03）

第二阶段先补防风暴的最小闭环：调度器按触发器维护 5 分钟窗口，窗口内
前 3 次成功无人值守轮可执行，第 4 次触发进入 15 分钟冷却。冷却期内新触
发仍调用 `mark()` 消费（文件签名/任务 seen/interval 到点不积压），但不
发起模型轮，并把丢弃原因写进 `data/notices.log`。

只统计 `ran=true` 的轮：执行失败已有连续 3 次失败丢弃的独立熔断，不把
“模型没跑起来”算成模型请求风暴。窗口和冷却先用代码常量，不进
config.ini——当前只有一个调度线程、触发器数量个位数，先避免配置面膨胀；
等预算熔断一起落盘时再统一评估可配置性。重启后计数从零开始仍是已知
缺口，TODO 单独保留，不把内存态误说成持久熔断。

**验收**（离线，假触发器 + 可控 monotonic 时钟）：0/10/20 秒三轮执行；
30 秒第 4 次触发被丢弃并写 `3 runs in 300s exceeded`；40 秒冷却期触发
继续消费且写 `rate limited`，不调用模型；1000 秒冷却结束后恢复执行。

## D29 无人值守 token 熔断：usage 实数累计，先跑超后停后续（2026-11-03）

预算口径只认厂商 `usage` 实数：`input + cache_read + cache_write + output`。
Conversation 新增本轮累计值，主请求和圈界压缩请求都计入，工具循环不再只看
被最后一次请求覆盖的 `chat.usage`。没有显式价目就不算金额，也不把 token 换算
成成本；这是刻意的保守缺口，不编造价格。

调度器按触发器维护 24 小时滚动预算，默认 10 万 token。模型响应的 usage 只能
事后拿到，所以语义是「本轮可能越过预算，越过后立刻熔断」：记录本轮实数、
写 notices，后续触发只消费不执行；窗口滚动清空后自动恢复。失败轮中已成功
的模型请求也通过 `token_count` 回传计费，不能因为最后一圈失败就漏算。

**验收**（离线）：假 Chat 两圈请求 usage 分别为 25/50，Conversation 累计 75；
假触发器两次运行分别消耗 60k/50k，第二次后写 `110000/100000`，随后两次触发
不调用模型并写 `token budget reached until`，24 小时窗口过期后第三次允许执行。
