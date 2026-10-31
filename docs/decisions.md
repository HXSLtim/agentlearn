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
