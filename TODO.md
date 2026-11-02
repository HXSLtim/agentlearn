# TODO

学习路线总览见 docs/roadmap.md，选型理由见 docs/decisions.md。
这里的条目完成后移进 CHANGELOG，不删历史。

## 下一步（已排期）

第 4 层（上下文管理：缓存断点、截断、压缩）。第三层已完成：
now_time + bash 双工具、并行 id 配对、失败换路子。

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

## 排队中：agent 自盘点的五个缺口（2026-11-03）

来源：让 agent 用 bash 盘点自己的能力，它报了五个缺失（眼睛、耳朵、耐心、
记忆的另一半、主动性）。下面按学习性价比排，不按它渴望的程度：

- [x] 跨会话记忆注入 ✅（2026-11-03）：data/sandbox/memory.md 每轮 turn 读一次，
      拼成 system 提示词随每次请求重发；不落 JSONL（派生状态不是历史），
      轮内多圈共用同一份（前缀稳定，第四层缓化的入口）；协议差异收在 adapter
      （Anthropic 顶层 system / OpenAI 首条 role=system）。system prompt 这笔
      基建随此落地。验收：全新会话直接答出只写在 memory.md 里的暗号，
      llm_request span 记 system_chars=94
- [ ] 眼睛：图像进 tool_result。Anthropic 的 tool_result 能装 image 块；
      OpenAI 的 tool 消息只能文本，图像得作为下一条 user 消息补发——协议差异
      全收在 adapter。截图先缩图（sips 就有）：全屏一张 ≈ 5k token，图像一进
      上下文，缓存/截断立刻成刚需，和第四层连着做
- [ ] 耐心：异步任务，10 秒超时装不完依赖等不了构建。两种设计选一：轮询式
      （job_id + job_status 工具，简单但烧 MAX_TOOL_ROUNDS）或事件注入式
      （turn 暂停/唤醒）——后者就是上面 cooperative steering 的机制，
      和 steering 是同一块基建，只设计一次
- [ ] 耳朵：语音输入。模态问题不是架构问题，排最后
- [ ] 主动性：工具与 agent 的分水岭，未立项，前提先于本体——
      - [ ] 主动性触发器：定时 / 文件变化 / 任务完成事件来扣扳机，
            触发后合成消息喂给现有 turn()，循环本体不用改
      - [ ] 常驻进程：从「请求来了才存在」变成 daemon + 事件循环
      - [ ] 权限门：无人值守下做事先问。没人盯 + 纸糊沙箱 = D17 攻击链在
            无人值守下运行，容器化因此从「对外前的前置」提前为主动性的前置
      前置选型：要「更聪明的回话者」还是「不在场也延续的存在」——答案决定
      这一层动不动，定了记 decisions.md

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

- [ ] 思考块 signature 现在存空字符串：流式片段里没有它，只在最终消息里。
      **Anthropic 路径已从最终消息回填（第二层顺带解决）**；OpenAI 路径
      的 reasoning 不回传历史，signature 仍是空串，换会校验的接口前确认它不校验
- [ ] token 用量与缓存比例已记进 llm_request span（2026-11-02）：
      input/output/cache_read/cache_write/cache_hit_ratio（千分比），
      两家在 adapter 里翻成同一套键。实测中转透传，且没放 cache_control 也有命中
      （第二圈 747‰）。还没做的是主动放断点把命中率稳住，那是第四层的事
- [ ] SQLite 并发写：现在靠默认 busy_timeout（5 秒）扛——库级写锁，第二个写者
      等锁释放，超时抛 database is locked。四五个会话的写入量碰不到；
      并发真上来时连接加 `PRAGMA journal_mode=WAL`（读写互不挡），
      同时做上面 SessionManager 那条的连接复用。两件事独立，都不急。
      不上连接池：SQLite 连接是打开本地文件，开销微秒级且写锁是库级的，
      池化不增加写并发；也不上 ORM：全部 SQL 就七条，裸 SQL 是故意的
      （参数化、锁、WAL 都要看得见），等表多到需要迁移再重新评估
- [ ] bash 工具没有安全边界，是接受的取舍（证据与理由见 D17）：cwd（data/sandbox）
      只是起跑点，绝对路径和 ../ 都能绕过（实测 cat ../../config.ini 可读）。已做的
      最小收紧：子进程环境只留 PATH（实测 LLM_API_KEY 不可见）——密钥不会再经工具
      输出进模型上下文。2026-11-03 自审计实测补证：curl 直连出网 200、say 可用、
      完整攻击链（注入→读文件→出网）成立——config.ini 只有 ${LLM_API_KEY} 引用，
      但 ~/.zshrc 里的 export 对模型可读。模型跑在本机自己账号下，
      与自开终端无更多权限，学习期接受；对外服务前必须容器化或独立用户。
      超时只 kill shell 进程，后台子进程可能残留
- [ ] 跨会话的工具冲突不归循环管：now_time 纯函数、bash 有副作用但工作目录
      各命令共享（sandbox 不按 session 隔离）；工具一旦碰外部系统（第五层 MCP），
      冲突发生在工具碰的资源上，解法是工具侧的：按 session_id 隔离工作目录，
      或工具声明自己能否并行
- [ ] web 无鉴权：现在安全仅因为 uvicorn 默认只绑 127.0.0.1。
      绑 0.0.0.0 / 加 --host 之前必须先加鉴权
- [ ] Tauri 一键启动：app 启动时拉起 server 进程（现在是两边各开各的）
- [ ] API 地址可配置：`app/src/main.ts` 固定 `127.0.0.1:8000`，
      用 `main.py desktop --port` 改端口时前端也要同步改
- [ ] Tauri 仍在使用模板图标；桌面端产品化前替换为 AgentLearn 图标
- [ ] pyproject 把 domain/infrastructure/interfaces 装成顶层包名，与第三方包撞名风险低，
      出现痛点再收窄成 `agentlearn.*` 命名空间

## 以后（路线图后段，暂不展开）

- 第四层：上下文变长后的缓存、截断、压缩
- 第五层：MCP；第六层：Skills
- 意图识别模型（2026-11-03 记入 roadmap「待排期」）：主模型前加小分类模型，
  判意图再路由（闲聊直答 / 进 agent loop / 工具裁剪 / steering 档位建议）。
  前提是分类器省下的开销要大于它自己的；选型定了记 decisions.md
- 读完 LangGraph 的 StateGraph / Checkpointer 设计，对照自写的 Conversation 写对比笔记
