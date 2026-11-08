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
（examples/mcpCount.py）也手写，两边看的是同一份协议。

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

## D30 熔断账本落盘：墙钟 epoch + 原子替换（2026-11-03）

频率窗口、连续失败计数、token 用量、两个冷却截止统一写
`data/trigger_states.json`。时间从 monotonic 换成墙钟 epoch——monotonic 只在
单个进程生命周期内有意义，重启后不可比较；代价是系统时钟被改动会影响窗口，
这是持久化必须付的可接受代价。加载时按窗口过滤已过期记录，并把过期截止
钳到当前时间。

写入走同目录临时文件 + `replace()`：崩溃时要么旧账本完整，要么新账本完整。
账本损坏时退回空状态并继续运行，不在启动时让主动性线程死掉；这次丢失如实
体现在后续计数，而不伪装成仍有旧状态。触发器配置增删后只保存当前名单，
避免旧触发器的预算永远占着文件。

**验收**（离线，两次构造 Runner 模拟 daemon 重启）：三次成功运行后重建，
第 4 次触发仍因 3/300s 进入 900 秒冷却；60k+50k 用量后重建，下一次触发仍
因 token budget 被消费丢弃，文件保留 `86410` 截止和两条用量记录。

## D31 plan mode：白名单只读探索 + 一次性人工确认（2026-11-03）

plan mode 是请求级模式，不是第四个执行等级。`approve/auto/yolo` 描述工具
能不能跑；`plan_mode` 描述这一轮只允许探索并产出计划。两者分离，避免把
“autonomous 执行等级”和“是否处于规划阶段”混在同一个状态机里。

模型请求只暴露内置只读工具白名单（now_time、screenshot、read_skill、
wait_job、job_status）。bash、start_job、stop_job、remember 与全部 MCP 工具
都不暴露；模型幻觉出这些名字时，执行层直接返回 `plan_blocked` 的
`tool_result`，span 留 `plan_mode=true`。MCP 没有机器可读副作用声明，一律
按未知风险处理，不做描述文本猜测。

最终正文生成独立的 `plan` 事件；进程内 `PlanBroker` 绑定 session_id 并把
“确认”和“执行”拆开。确认接口只改状态；带 `plan_id` 的 `/chat` 才一次性
消费确认并注入“用户已确认以下计划”的执行轮。TUI 和 desktop 都必须点击
确认按钮（或按 Enter）才触发执行，重复使用同一个 plan_id 返回 404。计划
正文本身仍在 JSONL 里，broker 只保存进程内确认状态；重启后旧按钮不能再
执行，这是保守缺口。

**验收**（离线 + 假 HTTP + 双端 UI）：假模型在 plan mode 请求 bash 写
marker，实际未执行且 tool span 为 `plan_blocked`；未确认消费返回空，
确认后只能消费一次；HTTP 422/404/200/404 契约通过；TUI 两枚按钮外宽均
24，desktop 380×900 两枚按钮均 157px 且同一行，确认后请求依次为
`/plans/{id}/confirm` 与带 `plan_id` 的 `/chat`。

## D30 设计语言「墨与琥珀」：不再借 Apple 的脸，长自己的（2026-11-05）

先换 Apple HIG 是对的——跟随系统明暗、材质、焦点环、overlay 标题栏，
这些是平台规矩，谁家用都对。但换完发现它给不了身份：界面成了
「一台长得像 macOS 附件的 app」。AgentLearn 的脸应该从自己的世界里长：
这是一个学习仪器，每一层手写、每一轮落账本、每一次执行留 span。

**决定**（desktop 与 TUI 同一套词汇表）：

- **底色**：暖纸白 `#f7f5f1` / 墨黑 `#151412`（带暖调，不用冷灰）。
  纸和墨——学习项目的本体是笔记与账本。
- **强调**：琥珀 `#c96a15`（浅）/ `#eda03c`（深），替代 systemBlue。
  批注笔的颜色；也接住既有「运行中=琥珀」语义。红=错误、青=工具、
  绿=成功不变。
- **形状**：圆角三档 4/6/8（卡片 4、控件 6、弹层 8），胶囊只留给
  toast。仪器感靠发丝线和留白，不靠圆角堆软。状态点永远是圆的。
- **字**：中文标签 sentence case；一切数据（id / ms / token / model）
  永远 mono。**字阶下限 11px**——旧的终端密度把说明文字压到 8px，
  不可读就等于不存在。
- **签名元素 span-tick**：每条助手消息页脚一条 2px 时轴，填充 =
  本轮耗时 / 全会话最长轮。恢复的会话从 `/trace` 拉 turn span 水合
  （`hydrateTurnDurations`，每会话一次，拉不到不造数据）。
- **动效**：只有一条规矩——流式输出带终端光标脉动，其余不动。

**文案**：全大写标签全部 sentence case / 中文；同一件事只说一遍
（输入区的「输入」标签、重复脚注、与状态行重复的 chip 全部收掉）；
枚举落屏前过 `format.ts` 中文名映射，比较逻辑里的纯数据值不动。

**平台件保留**：`prefers-color-scheme` 双主题、backdrop-filter 材质、
focus ring、`titleBarStyle: Overlay` + `hiddenTitle` + 头部拖拽区
（注意 schema 枚举是 PascalCase `Overlay`，小写会拒启）。

## D31 模型请求必须有界：读超时 120s、连接 5s、重试 1 次（2026-11-05）

2026-11-05 晚一次卡死复盘：上游中转挂起（连接在、不回包），
`llm.py` 建客户端没设超时，用的是 SDK 默认 600s × 2 次重试——
挂起被放大成最长半小时。`/stop` 只设取消标志、检查点在流事件之间，
挂起的请求永远到不了检查点，界面就钉死在「停止中 · 等待检查点」。

**决定**：

- 两个客户端统一 `httpx2.Timeout(connect=5, read=timeout_s, write=10, pool=5)`，
  `max_retries=1`。`timeout_s` 进 `[llm]`，缺省 120，流式下约束的是
  相邻 chunk 的间隔而不是整轮时长（长回答不会被误杀）。
- 超时走 SDK 的 APITimeoutError → `turn()` 既有的错误通道：两个 span
  标 error、SSE 发错误、界面出「运行错误 + 重试」。不再需要 /stop
  去打断一个挂死的请求——它自己会在时限内收线。
- 注意这代 SDK 用 `httpx2`（不是 `httpx`），传错包的 Timeout 会被
  `_reject_httpx_object` 直接拒绝；`httpx2` 是 anthropic/openai 的
  传递依赖，不需要写进 pyproject。

**验收**：本地起一个只 accept 不回包的端口，`timeout_s=2` 时 4.5s
内抛 APITimeoutError（含 1 次重试），此前无限挂起。

## D32 JSONL 落盘时间由存储层加盖（2026-11-05）

恢复旧会话时，desktop 以前只能按下标合成「现在 − N + i」的假时间，
一整场对话的消息全挤在同一分钟里，span-tick 之外看不出节奏。时间
是存储事实，不是模型协议：`Message.to_dict()` 同时是 Anthropic /
OpenAI 的出站 payload，不能把 `ts` 混进发给模型的形状里。

**决定**：

- `Transcript.append` 在 `to_dict()` 的副本上 `setdefault("ts",
  time.time())` 再写行；秒级浮点，和 span 的起止时间同一时基。
- `GET /sessions/{id}/messages` 改为返回原始行（`read_rows`），
  `role/content` 形状不变，多一个可选 `ts`；老行没有这个键也算合法。
  `read()` 仍按 `Message.from_dict` 还原，`ts` 被自然忽略。
- 前端只在 `ts` 是有限数字时用它，否则退回合成序时；`/trace` 有
  turn span 时再用 span 起止回填用户/助手时间（老 JSONL 的补救）。
- SQLite 索引和 span 形状不动，JSONL 仍是唯一事实来源。

**验收**：新行带正的秒级 `ts`，而 `Message.to_dict()` 的键仍只有
`role/content`（离线临时目录验证）；旧行无 `ts` 仍能读取；desktop
恢复 18 轮会话时消息时间不再全部相同，`npm run build` 与
`compileall` 通过。

## D33 断连的轮次把 span 收成 cancelled（修订 D11，2026-11-06）

D11 的策略是「断连留 running，如实反映这轮没跑完」。实际运行下来
它撒的谎比说的真话多：会话 `1738cd58` 三天里积了三对永久 `running`
的 turn + llm_request span（两次在不该关窗时关窗，一次在 L4 压缩的
8 秒静默期里 SSE 客户端消失）。trace 里它和真在跑的轮次长得一样，
桌面端界面也分不清——而且「还在跑」的判断会一直传下去，Stop 按钮
拿着后端诚实的 `active=false` 却清不掉本地的 streaming 标志，只能
刷新页面。没跑完是事实，但「没结束」不是。

**决定**：

- 断连终态用 `cancelled`，不用 `aborted`（不新增状态，和一键停止 /
  抢占收尾同一个词），也不改成功率统计的口径。
- `turn()` 变成一层薄包装（循环本体移进 `_turn_impl`），`yield from`
  之下捕获 `GeneratorExit`，把本轮还开着的 span 统一收成 cancelled
  + end_time + `{"note": "客户端断开，本轮中止"}`。改用 `_start_span`
  / `_end_span` 记账：每个 turn 一份「未收尾清单」，正常收尾即摘除。
  循环里的三个 `except GeneratorExit`（补 tool_result 配对）不动。
- 内容落盘语义不变：半截回答仍然丢弃，配对补齐仍然照旧。
- desktop 的 Stop 收到 `active=false` 时把本地这轮收线：助手消息
  标 cancelled、卡在 running/waiting 的工具卡收成错误结果、abort
  还挂着的 SSE 读；runChatRequest 不再把 abort 的异常盖成「错误」。

**验收**：离线假 client drill 五条收场——流中途 close（turn 与
llm_request 均 cancelled 且有 end_time）、tool_result 事件处 close
（turn cancelled、llm_request 与 tool_call 已 ok）、请求已收尾但轮次
未走完时 close（turn cancelled、llm_request ok）、正常跑完（全 ok）、
抢占（TurnCancelled 语义不变，两个 span cancelled）全部通过；
`compileall` 与 `npm run build` 通过。旧库遗留的 running 行不追改，
按 TODO 的清扫欠账处理。

## D34 运行锁有界等待 + /health 监控面 + 孤儿 span 启动清扫（2026-11-07）

**背景**：真机事故。11-05 20:07 启动的 desktop server（早于 D31/D33 落库）
在 11-06 13:36 遇到 SSE 断连：`chat_events` 生成器停在 yield 上被挂起时
仍持有 `slot.turn_lock`，老代码没有 GeneratorExit 收尾，锁从此泄漏。用户
次日续接同一会话发消息，新请求阻塞在 `with slot.turn_lock:` 上——SSE 已
200 但生成器一步未走（零 span、零 JSONL 写入），前端「等待首个 token」
无限挂起。py 栈证据：两个 AnyIO worker 与 proactivity 线程全部停在
`lock_PyThread_acquire_lock`，无任何线程做 IO；SIGTERM 因关停等待卡死
线程而失效，需 SIGKILL。

**决定**（三个独立小件，都是把「无限等/永远脏」变成「有界且可见」）：

1. **等锁有界**（server.py）：`with slot.turn_lock:` 改为
   `acquire(timeout=600)` + try/finally。拿不到锁的排队连接收一条
   error + done 事件明确退场，不再无限等。600s 的依据：一轮的耗时
   上限 ≈ 每圈 120s 读超时（D31）× 有限工具圈数 + 工具耗时，正常排队
   不会命中；命中即锁泄漏，报错文案直接给出重启指引。
   排队期间收不到客户端断开（GeneratorExit 只能注入停在 yield 处的
   生成器）这个限制如实接受：泄漏被 600s 上限兜住，不再需要根治。
2. **GET /health**（server.py + store.py + sessionManager.py）：报
   uptime、未收尾 span 数与最老年龄、正持有 turn_lock 的会话。锁泄漏
   的形状（busy_sessions 有条目而 span 早已该结束）一眼可辨。监控不
   改状态，只读。
3. **孤儿 span 启动清扫**（store.py `close_stale_spans`）：进程启动时
   把超过 1 小时仍 running 的 span 收成 cancelled 并打
   `orphan: process restart` 标记。阈值取 1 小时是防误伤：正常一轮远
   短于此，并行进程（TUI）正在写的 span 也不会被碰。D33 只管新断连，
   存量僵尸靠这里一次性清干净。

**验收**：真机复跑会话恢复（重启后消息正常流回，agent 用 say 回应）；
`/health` 高频采样在 10.6s 轮次上连续 23 个采样点可见 busy/running 非零、
轮后归零；启动清扫收掉 11-05/11-06 的 3 条僵尸（209/210/216，带 orphan
标记），10 分钟前的 221 未被误碰；`compileall` 通过。

## D35 会话工作目录：跟会话走，不跟某一轮（2026-11-07）

**背景**：界面里没有「选工作目录」的入口，bash / start_job 的起跑目录
写死在 `sandbox.py` 的 `SANDBOX`。想在某个项目里干活只能改常量重启。
用户明确否决了「某一轮换个目录」的形态——目录是工作上下文，不是
命令参数。

**决定**：

1. **目录是会话的属性**（`sessions.workdir` 列，空 = 默认 data/sandbox）。
   界面（composer 左上角「目录 …」按钮）改的是这个会话之后每一轮；
   不同会话可以在不同项目里。中途改不感染正在跑的轮：Conversation
   每轮开始时读一次库，锁定当轮。
2. **不是模型参数**。cwd 不进 bash 的 inputSchema：模型每一跳都能改
   目录的话又变回「某一条命令」。传递用 contextvar
   （`sandbox.bind_workdir`），Conversation 在每次 `run_tool` 前后
   set/reset，bash / start_job 读 `current_workdir()`。没绑过就是默认
   sandbox（TUI、离线测试不受影响）。
3. **记忆和账本不跟目录走**。`memory.md`、jobs 账本仍固定在
   data/sandbox：那是 agent 自己的运行时数据，不该混进用户项目。
   system 提示词里把两处路径都告诉模型，避免它在项目目录里找记忆。
4. **「变更」和 @file 跟会话走**：两个端点加可选 session_id，git 根从
   会话工作目录找。不给参数保持旧行为（进程 cwd）。
5. **落库路径**：新会话的目录随第一条 `/chat`（workdir 字段）进
   Conversation——会话行本身就是那一轮才建的，PATCH 没处落脚；已建
   会话用 PATCH /sessions/{id} 立即生效。/chat 只有在字段出现时才写，
   不会把 curl / TUI 改过的目录冲回默认。
6. **校验**：必须绝对路径且已存在的目录（422 / 界面内联报错，不偷建
   目录）；空字符串 = 恢复默认，是合法值。

**验收**：normalize/bind/reset/get_workdir/set_workdir 离线全过（相对
路径、不存在路径拒绝；临时库 set/get/clear/absent）；真机：新会话设
AgentLearn 根目录 → `pwd` 返回项目根、ls 见 CLAUDE.md/app/server，
turn span 记 workdir；变更面板 root 切到 AgentLearn（19+8 文件）；
@file 补全列出项目文件；恢复默认 → 按钮回「目录 sandbox」、库列清空。
`compileall` 与 `npm run build` 通过。

## D36 TUI 界面文案对齐 WebUI（D30 补课，2026-11-07）

**背景**：D30「墨与琥珀」只把 WebUI 中文化了，TUI 换了色但界面文案仍是
英文大写（AGENTLEARN / LOCAL RUNTIME / RUN LOG / COMPOSE / ENTER RUN…）。
真终端里并排一看，同一个产品两套语言，TUI 像上个版本的遗物。另：用户看到
的第一张 TUI 截图用 Textual 自带 SVG 导出，中文双宽字符排版错位重叠，
观感进一步失真——但那只是导出 bug，tmux 真终端网格干净。

**决定**：

1. **词汇表单一来源**：新增 `chat/labels.py`（运行状态 / 审批档位 / 插话
   档位 / 收条标题四张中文映射表），词与 WebUI `format.ts` 完全同源：
   READY→就绪、STREAMING→回复中、approve→需审批、queue→队列……
   screen 与 modals 都要查表，modals 被 screen 导入、反向共享会循环，
   所以独立成文件（不进 theme.py——那是视觉 token，不装文案）。
2. **内部枚举不动，只在渲染口翻译**：`_set_running_state("STREAMING")`
   之类调用点仍传英文枚举（它们是标识符）；`_show_command_result` 在
   mount 前过一次 `receipt_label()`，一处改动盖住全部 25 个调用点。
   新枚举值没配中文名时原样大写兜底（与 WebUI 同策略）。
3. **静态文案直接替换**：台账 17 个标签、顶栏、输入区、键位帮助、
   底部命令提示、命令面板组名与页脚、六个 modal 标题与按钮
   （DENY/ACCEPT ONCE/ACCEPT SESSION→拒绝/通过一次/本会话允许）、
   cards 抬头（AGENT / RESTORED RESPONSE→智能体 · 历史回复）、
   trace 检查器面板与字段（对齐 trace.ts：正常/错误/运行中/已取消）。
   主题 token 一律不动，只换字符串。
4. **顺手修存量崩溃**：`main.py trace` 自 traceView 抽出（b3f6c35 前后）
   就不能启动——`spans_for` 返回 `sqlite3.Row`，共用的
   `answers_for_requests` 调 `.get()` 直接 AttributeError；web 端点
   自己内联翻 dict 所以没炸。Row→dict 转换下沉为
   `traceView.span_payloads()`，web 与 TUI 共用，TUI 不再自己
   `json.loads(span["data"])`。

**验收**：tmux 120×36 真终端跑 115 条消息会话：顶栏「本地运行时 · 会话
1738cd58 · 115 条消息 · ● 就绪」，台账 17 行全中文（需审批/队列/排队中），
消息抬头「智能体 · 历史回复」，输入区「输入 · Enter 发送 / 发送 ↵」；
`/` 命令面板组名「运行/会话/任务/系统」+ 中文页脚。trace 检查器 headless
挂载 139 spans 并渲染 llm_request 详情；`GET /sessions/{id}/trace`
重构后同样 139 spans / 66 配对。恢复会话滚动到底经 headless 探针证实
（scroll_y 924/943），此前疑为 bug 实为滚动条读反。截图四张存
`docs/shots/`（TUI 主屏/命令面板、WebUI 空态/内容态）。`tsc --noEmit`、
`compileall` 通过。

## D37 UX 复核批：时长/命中率/停止原因的显示口径，孤儿 span 不再伪造结束时间（2026-11-07）

**背景**：UX 梳理攒了一批每天都会撞的小毛病：被打断的轮「用时 0ms」或
被历史清扫写成几天假时长；缓存命中「457‰」没人读得懂；停止原因直接
渲染 `end_turn`；真空中断轮补一张「本轮没有返回内容」的空卡；未送达的
插话 chip 留在原地没有出路；侧栏一排「未命名运行」；相对时间
「5 天前 / 前天 / 1 小时前」三套粒度；标题 42 字硬截断会切 emoji。

**决定**：

1. **时长统一格式化**：<1 秒按 ms、<1 分钟按秒（一位小数）、<1 小时给
   「分 秒」、再往上「时 分」；Web（format.ts `formatDuration`）与 TUI
   （`interfaces/tui/formatting.py`）同一套规则。0ms 显示 `<1ms`——
   被中断的轮至少看得出发生过。
2. **缓存命中一律百分数**：API 的 `cache_hit_ratio` 保持千分比不变，
   两个 UI 落屏除以 10 给一位小数（457 → 45.7%）；台账、trace 面板
   同口径。
3. **stop_reason 中文化**：与档位标签同一策略——内部传英文枚举，
   渲染口映射（end_turn→自然结束、cancelled→已中断、
   steering/injected→已并入插话…），没命中原样兜底。
4. **孤儿 span 只改状态，不编造结束时间**：D34 的清扫曾把重启时刻写成
   `end_time`，duration 于是变成「从几天前跑到重启」（实测出现 6 天
   假时长，前端把它当成一轮耗时）。现在只写 `status=cancelled` +
   `orphan` 标记，`end_time` 保持 null；`running_spans()` 与清扫 SQL
   都加 `status='running'` 条件，取消的孤儿不再被算成在跑/重复清扫。
   历史假 end_time 已就地清空（本机 10 条）。
5. **安静收场**：被中断/并入插话的轮不再补「本轮没有返回内容」这句；
   未送达的插话 chip 变成可点击的「未送达 · 点此重发」（摘掉残影后走
   正常提交流程；带图消息无法还原附件，提示手动重发）。
6. **会话标题三层兜底**：服务端启动时从 JSONL 回填历史空标题
   （set_title_if_empty，实测补了 22 条）；本地自动名（未命名运行/
   新会话）有正文时用首条用户消息重命名；真空会话显示「空会话 ·
   MM-DD HH:mm」。顺带修了根因：WebUI 启动时根本不同步 `/sessions`
   （以前只有打开恢复面板才合并），现在启动就对一次账。
7. **相对时间单一口径**：<1 分钟给秒、<1 小时给分钟、当天给小时；
   昨天 →「昨天 HH:mm」，更早直接绝对时间（「5 天前」没人算得出是哪天）。
   标题截断按码点（Array.from），不再切开 emoji 代理对。

**验收**：真机：侧栏 24 条全部真标题（回填 22 条，剩 1 条本地空草稿归
「新会话」）；台账「用时 18.1 秒 / 缓存命中 0.0% / 停止原因 自然结束」；
消息 footer「结束 · 自然结束 18.1 秒」；trace 面板「耗时 18.1 秒、
命中 0.0%」，孤儿 span 显示「—」且 `/health` 的 running_spans 从 10
回到 0、重启不再重复报清扫。TUI headless：台账 cancelled →
「已中断」、12.345s → 「12.3 秒」、457‰ → 「45.7%」；trace 孤儿行
「● turn 已取消 —」，详情「TURN / 已取消 / —」。`npm run build`、
`compileall`、两处 import 环（trace ↔ chat）复查通过。

## D38 TUI 布局细节对齐 WebUI：D30 三批细节落地终端（2026-11-07）

**背景**：墨与琥珀改版（D30）和后续三批细节（CHANGELOG 十八/十九/二十）
只动了 WebUI，TUI 的布局还是改版前的骨架——同一个信息在两边长得
完全不一样。D36 补了文案，这一条补布局，一次把结构性的差异收齐。

1. **台账一行两列**：WebUI 的 run-metrics 是「标签上、值下」的 dt/dd，
   终端 36 行放不下 13 行指标的翻倍行数，压成一行两列（标签宽 11、
   值右对齐），指标集合与行序照抄 index.html（会话/轮次/工具调用/用时/
   输入输出/缓存读写/命中/成本/思考强度/执行审批/消息插话），TUI 特有
   的模型/排队中排在后面，底脚「JSONL · SQLite · 仅本地」对应
   run-rail-footer。侧栏换成 VerticalScroll：内容是 22 行起的固定清单，
   矮终端靠滚轮看全而不是硬裁。
2. **key-help 挪窝**：14 行键位表从侧栏底部挪到输入区——常用三键
   （Enter/Shift+Enter/Esc）进 composer-status，其余压成一行提示。
   WebUI 的右栏本来就不放键位表（它有 hover），键盘提示属于输入区。
3. **顶栏挂标题**：原来显示「会话 152acf13 · N 条消息」，N 是启动快照
   在轮次进行中会说谎；换成会话标题（store 里查不到退回短 id），
   每轮收线刷新——第一条用户消息落库后标题自动就位。
4. **抬头不重复**：同 WebUI showAuthorFor 规则——一条用户消息后的
   第一条助手消息带「智能体」抬头，轮内后续（工具轮后的第二段正文等）
   不带。实现是 AssistantMessage 加 show_kicker 参数 + 屏幕侧
   `_expect_kicker` 标志（用户消息挂载置真，助手正文消费置假）。
5. **span-tick 进终端**：WebUI 的签名元素（每轮耗时按全会话最长轮归一
   的比例条）在终端是 12 格「█·」条 + 人话时长。恢复会话从 turn span
   水合：按用户文本消息切轮（tool_result 载体不开新轮，WebUI 同规则，
   否则和 turn span 错位配错轮），>24h 的时长按脏数据丢弃。基准（最长轮）
   变化时整批重画。
6. **输入区两行化**：composer-heading = 键位提示（图片计数前置）+
   「目录 短名」位（最后两段，同 WebUI workdirLabel）；点击目录位弹
   用法（终端没有 hover title）。
7. **/workdir 命令**：与 WebUI 目录按钮同一套语义——`normalize_workdir`
   校验（绝对路径、已存在）、写 `sessions.workdir`、reset 恢复默认；
   正在跑的 Conversation 每轮 `_load_workdir` 重读，下一轮生效。
8. **顺带修掉 /trace 弹层的潜伏雷**：`_command_trace` 一直喂
   `store.spans_for()` 的 sqlite3.Row，而 SpanDetail._detail 只认 dict
   （`span["data"].get`）——真点开就 AttributeError。统一走
   `span_payloads`（web 端点同款），panels 的 `_detail` 也改成吃解析好
   的 dict。独立 trace 检查器（trace.py）同批中文化补齐：首字时长过
   `format_duration`（3513ms → 3.5 秒），span/diff 状态词表放
   `interfaces/tui/formatting.py`（零依赖层，chat 和独立检查器共用，
   放 chat/labels.py 会经 `chat/__init__` 反向 import trace 成环）。

**验收**：Textual headless 17 项断言全过（台账行序、底脚在 36 行内、
顶栏标题、抬头数=有正文的轮数、tick 12 格+人话时长、/workdir 四态
往返并复原现场、/trace 弹层 dict 不炸且全中文）；多轮会话实机 2 抬头 +
2 tick（14.7 秒满条、1.6 秒一格）；120×36 tmux 实机检查台账全行可见、
/trace 树「● turn 已取消 10.4 秒」、/diff「已跟踪 28 · 未跟踪 14
（已计入）」、面板 /workdir 条目；`compileall` 通过。

---

## D39 desktop 组合根回归 main.py：application 只认端口（2026-11-07）

**背景**：D14 写的是 `main.py` 只做装配，但 desktop 的依赖实际长在
`interfaces/web/server.py` 模块级：import 就建 SQLite 连接、迁移表、
清扫孤儿 span、回填标题。与此同时 `application` 直接 import 具体的
LLM / SQLite / MCP 实现，分层口径变成文档说法。

**决定**：

1. `interfaces.web.server` 只暴露 `WebDependencies` + `create_app()`；
   Store、Transcript、SessionManager、审批、计划、配置读写、模型工厂、
   MCP 网关全部由 `main.py desktop` 构造后注入。import web 模块不再有
   存储副作用，标题回填和孤儿清扫挪到 `create_app()`。
2. application 新增 `ports.py`：`ChatClient`、`TranscriptStore`、
   `SpanStore`、`ToolGateway` 只描述用例需要的方法。Conversation 不再
   认识 Anthropic/OpenAI/SQLite 具体类；MCP 由组合根注入，未注入时保留
   内置工具空网关。
3. token 估算是压缩决策，不是协议差异，挪到 `application/tokens.py`；
   adapter 反向复用，协议字段翻译仍留在 adapter。

**否掉的备选**：

- 继续 web 模块单例——测试/复用必须先改真实 `data/agent.db`，隐藏副作用
  比 globals 更贵。
- 引 DI 容器——当前依赖数量手写 dataclass 就能表达，不需要生命周期容器。

**验收**：结构探针确认 application 无 `from infrastructure.*`、web import
无构造/回填；临时目录 TestClient 验证 `/health`、`/settings`、`/models`、
`/capabilities`；真实 `main.py desktop` 冒烟另跑；`compileall`、
`py_compile`、前端 build 不受影响。

---

## D40 前端解环与 API 地址运行期解析（2026-11-07）

**背景**：D30 后前端按功能拆出 8 个模块，但这些模块为了拿 activeThread、
hydrate、render 又反向 import `main.ts`，模块边界仍是环。API 基址则固定在
`api.ts`，desktop 换端口时前端跟不上。

**决定**：

1. 本地线程状态、持久化、运行判断抽到 `state.ts`；这是无 DOM 的下层模块。
2. 抽屉需要 main 才能实现的 hydrate / render / switch 能力走 `shellBridge.ts`
   契约，main 启动时注册实现。功能模块不 import main，环清零。
3. API 基址解析顺序为 `?apiBase=`、`VITE_AGENTLEARN_API_BASE`、
   localStorage、默认回环地址；查询参数与 localStorage 只允许回环 origin，
   防止恶意链接把对话发到远端；只有显式构建期 env 允许 https 远端。
   查询参数成功后持久化，侧栏显示实际 host，避免界面继续谎报 8000。

**否掉的备选**：

- 继续把函数从 main 搬出去——render/hydrate 本来就是 shell 编排，搬到
  “公共模块”只是换一个更大的 god module。
- 只支持 Vite env——那是构建期配置，desktop 换端口后不能跟随。

**验收**：结构探针确认 8 个功能模块无 `from "./main"`；`npm run build` 通过；
真实 Vite 页面带 `?apiBase=http://127.0.0.1:18081`，后端日志收到
`GET /settings 200`，页面显示实际模型；380×900 截图人工检查，document/body
`scrollWidth` 均为 380。

---

## D41 运行中工具与思考可视化（2026-11-08）

**背景**：SSE 已经按事件推送 thinking / tool_use / tool_result，但 desktop
normal 档在渲染层隐藏前两者。长工具执行时用户只能看通用等待占位，既不知道
模型是否在思考，也不知道工具是否已经开始。

**决定**：

1. normal 档显示简略工具卡：工具名、状态、耗时和一行参数摘要；不展开输入、
   输出或图片。等待审批仍用完整工具卡，verbose 档保持完整明细，审批能力
   不因简略展示降级。
2. normal 档显示可折叠思考块。流式阶段 summary 显示进行中与字数，展开后只看
   尾部 280 字并带闪烁 caret；用户展开状态用 `message.id + part index` 记录，
   SSE 每次重绘后恢复。thinking 档自动展开，verbose 档保持完整思考。
3. 只要已有 thinking 或非完成工具，就不再显示「正在组织回复」通用占位，
   避免同一时刻出现两层等待提示。

**否掉的备选**：

- normal 直接展开完整工具输入/输出——工具输出常是日志或长文本，会淹没正文；
   完整细节已有 verbose 档。
- 只在右侧 RUN LOG 展示活动——运行中正文区域仍空白，用户视线仍会干等。

**验收**：mock SSE 发送两段 thinking、bash tool_use、tool_result、正文与 done，
normal 档出现 compact 工具卡与思考折叠块；手动展开后跨 SSE 重绘保持展开；
380×900 populated 截图人工检查，`documentElement.scrollWidth/clientWidth`
均为 380；`npm run build` 通过。

---

## D42 TUI 会话管理五件套、台账实时活动与挂图管理（2026-11-08）

**背景**：布局对齐（D38）后 TUI 与 desktop 还有三块真差距：/resume 只能选不能管
（重命名/pin/归档/分支/删除都要去 web 端做）、侧栏没有活动流（工具跑到哪一步
只能看正文区）、`/attach` 挂错了图没有反悔入口（终端没有缩略图可点 ×）。

**决定**：

1. `/resume` 五件套直接在弹层里做：列表下加一排动作按钮（重命名 R / 置顶 P /
   归档 A / 分支 F / 删除 D），字母键与按钮同源。重命名/分支用 InputModal，
   删除用 ConfirmModal（Enter 落在「取消」上——危险动作默认路径是反悔）；
   删除/归档正在使用的会话被拦，提示先切走。V 切换含已归档视图。
2. fork 语义抽到 `application/sessionFork.py`（TUI 与 web 端点共用，越界抛
   ValueError 由两端各自翻译成 422 / 错误收条）；分支标题加「（分支）」后缀，
   spans 不复制。
3. 侧栏拆成 `#rail`：指标段（VerticalScroll，矮屏自己滚）+ 活动停靠段
   （钉在滚动区外）。为在 36 行屏放下，指标压行：token 入/出并一行、缓存
   读/写/命中并一行、排队数以「排 N」后缀并入插话行——指标集合与 WebUI
   一致，只是呈现密度不同。
4. 活动口径照抄 WebUI run-rail activity：只看最后一轮、条目新的在上、最多
   显示 6 条但计数给全部、思考给字数、工具给「耗时 · 输出首行」、错误条目
   变红。终端适配两处：首行剥 `[exit N]` 前缀（29 列窄栏里 exit 码是工具层
   状态尾巴不是内容）、按显示宽度截断（W/F 算 2 格）保证一条一行不折行。
   恢复会话从最后一轮水合（历史无时长，只给首行，同 WebUI elapsed 缺省）。
5. `/attach` 无参给「序号 + 路径」列表（ImageBlock 里只有 base64，路径存
   平行的 `_pending_image_paths`）；`/detach <n|all|路径后缀>` 移除，多匹配
   要求改用序号，计数与状态行同步，发送时两份表一起清。
6. 过滤框里 ↓/↑ 把焦点带进列表：Input 会把字母吃成过滤词，动作绑定只在
   列表/按钮拿到焦点后生效，不带焦点过去页脚键位说明就是空头支票。

**否掉的备选**：

- 活动放指标段末尾靠侧栏滚动看——「正在发生」的信息不该靠滚轮找；
  这也是把 rail 拆成两段的直接原因。
- 活动条目照搬 WebUI 48 字符不截断——29 列窄栏里必折行，一条占三行很快
  把停靠段挤爆。
- 删当前会话先自动切走再删——静默改变当前会话比拒绝更危险。

**验收**：临时库 headless 探针 30 项断言全过（五件套 14：重命名落库 / pin
开关 / 归档往返 / 分支标题+条数+越界 / 删当前被拦 / 删除后索引+正文文件消失；
活动 10：水合条目与计数 / 新轮清空 / 完成序 / 6 条上限 / 最旧挤出；挂图 6：
挂两张 / 列表 / 序号+路径+all 移除 / 状态行同步）；120×36 tmux 实机截图三张
（台账+水合活动、/resume 按钮排、分支 InputModal）人工检查。
