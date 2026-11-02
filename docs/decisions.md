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
