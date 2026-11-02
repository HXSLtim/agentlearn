"""一次对话的用例编排。这一层叫 application。

TUI 和 desktop/web 两个入口共用的循环收在这里：入口只剩输入输出，
「一轮对话怎么走」只写这一份。这里是 agent loop 本体：

    用户提问 -> 模型回答
       └─ stop_reason == tool_use？-> 执行工具 -> 结果交回 -> 再请求模型
          （循环，直到模型不再要工具，或达到轮数上限）

每一圈是一次独立的厂商请求：POST 全部历史 -> SSE 流回 -> 流关。
「交回结果」不是在原流里续写，是整段历史重发一遍（API 无状态）。
圈与圈之间因此存在真实空隙：工具执行时间 + 新请求的首 token 延迟
（TTFT，记进 llm_request span 的 ttft_ms）。软中断插话插的就是这个空隙。

历史维护方式统一为「每轮从 JSONL 整体重读」：文件是唯一事实来源，
崩溃或重启后天然一致，本地小文件的读取开销可以忽略。

turn() 产出的事件是 dict，两个入口消费同一份：
    {"kind": "thinking" | "text", "text": "..."}        流式片段
    {"kind": "tool_use", "id", "name", "input"}         模型要调工具，执行前就发
    {"kind": "tool_result", "id", "name", "output",
     "is_error", "elapsed_ms"}                          工具跑完，带结果和耗时
    {"kind": "steering_injected", "text"}               软中断的消息并入当前轮（圈界处）
id 是这一次调用的标识，和落盘的 ToolUseBlock.id 是同一个值：
一次回复里并行多个工具时，前端靠它把 tool_result 填回对应的 tool_use，
不能靠事件顺序猜。TUI 把事件排进时间线，desktop/web 原样塞进 SSE。

trace 的埋点也在这里：turn span 是一棵树的根，树上是循环走过的每一层——
每次模型请求一个 llm_request span，每次工具执行一个 tool_call span
（见 store.py 的 spans 表）。`main.py trace` 在 TUI 里把这棵树展开。

跨会话记忆（2026-11-03）：每轮 turn 从 data/sandbox/memory.md 读一次，
拼成 system 提示词随每次请求重发——agent 醒来就带着行李，不用「恰好记得
去读某个文件」。system 不进 JSONL（派生状态不是历史），协议差异在 adapter
（Anthropic 顶层字段 / OpenAI 首条消息）。没写这个文件 = 不带 system。
"""
import time
from pathlib import Path

from domain.message import Message, TextBlock, ToolResultBlock
from infrastructure.llm import AnthropicChat, OpenAIChat
from infrastructure.store import Store
from infrastructure.transcript import Transcript

from application.tools import SANDBOX, all_tools, run_tool

# 一轮对话里最多让模型连续调几轮工具。没有这个上限，模型抽风反复调用时
# 会一直烧 token 停不下来——agent 循环永远要有退出条件。
MAX_TOOL_ROUNDS = 8


class TurnCancelled(Exception):
    """这一轮被抢占（steering 的 preemptive 档）打断。

    半截回答不落盘——模型对被打断的回答失忆，这是抢占档的语义。
    落盘侧是自洽的：已写入的都是完整消息（问题、tool_use、tool_result），
    缺的只是那段没生完的 assistant 回答。入口捕获它后向自己的 SSE 推
    steering_cancelled 事件；turn span 记 status=cancelled（区别于 error）。
    """


class Conversation:
    """一个会话的编排器。

    持有四个依赖：模型客户端、正文存储、索引存储、会话 id。
    生命周期内可以连续 turn 多轮，历史由它负责追加和落盘。
    """

    def __init__(
        self,
        chat: AnthropicChat | OpenAIChat,
        transcript: Transcript,
        store: Store,
        session_id: str,
        memory_path: Path | None = None,
        steering=None,
    ) -> None:
        # 构造时就校验 session_id（必须是 UUID），非法在这里抛 ValueError。
        # TUI 把它转成一句提示退出，web 端点在推流前拦下返回 422。
        self.session_path = transcript.path_for(session_id)
        self.chat = chat
        self.transcript = transcript
        self.store = store
        self.session_id = session_id
        # 跨会话记忆的来源文件。sandbox 本来就跨会话留存（agent 的便签都住在
        # 那里），memory.md 只是给「醒来要带的行李」一个固定位置——agent 在
        # 自己的工作目录里 echo >> memory.md 就能写下记忆。参数可注入，
        # 测试换临时文件，不碰真 sandbox。
        self.memory_path = memory_path if memory_path is not None else SANDBOX / "memory.md"
        # steering 是同会话并发的钩子（见 session_manager.py），鸭子类型：
        #   cancelled() -> bool      被抢占了吗（preemptive 档设的标志）
        #   drain() -> list[str]     取走等待插入的新消息（cooperative 档的 inbox）
        # TUI 单连接用不上，None = 没有并发，所有检查点都变空操作。
        self.steering = steering
        # 当前轮的 turn span id。抢占检查点要用它把 span 收成 cancelled，
        # 检查点散落在循环各处、拿不到局部变量，挂在自己身上。
        # TUI 串行复用同一个 Conversation，每次 turn() 开头覆盖，安全。
        self._turn_span: str | None = None
        # 最近一轮的结束原因，turn 完整跑完才有值。
        # 带 tools 的一轮可能经历多次请求，这里是最后一次的。
        self.stop_reason: str | None = None

    def _checkpoint(self) -> None:
        """抢占检查点：在把事件交给消费者之后、开新的厂商请求之前调用。

        生成器在消费者要下一个事件时才恢复执行，恢复的这一刻是我们能感知
        取消的最快时机。真·零等待抢不动一个阻塞中的网络读，抢占档的等待
        上限是「下一个事件到达或下一圈开始」，这点如实记在 D18。
        被抢占时把 turn span 收成 cancelled（半截回答不落盘），再抛
        TurnCancelled 给入口——怎么向用户表现打断是入口的事。
        """
        if self.steering is not None and self.steering.cancelled():
            if self._turn_span:
                self.store.end_span(
                    self._turn_span, status="cancelled",
                    data={"note": "被抢占打断，半截回答未落盘"},
                )
            raise TurnCancelled

    def turn(self, question: str):
        """跑一轮对话，逐个产出事件（dict），直到给出最终回答。

        事件的形状见本文件顶部。两个入口拿到的是同一份：
        TUI 排进时间线，web 原样推进 SSE，谁都不需要再拼装工具的入参出参。
        一轮的轨迹是 turn span 一棵树：循环里每次模型请求挂一个
        llm_request，每次工具执行挂一个 tool_call（`main.py trace` 回放）。
        流中途出错时：给出错的 span 标上 error（共同动作，记在这里），
        然后把异常抛回给入口（怎么表现错误是入口的事）。
        半截回复不落盘——存一半的对话和用户看到的不一致，宁可不要；
        已落盘的 user 消息留着，下一轮模型会看到这次没答上的问题。
        """
        # 第一次真正有消息时才登记会话：启动后立刻退出的会话
        # 不再在 sessions 表里留下指向不存在文件的行。续接时表里已有，跳过。
        if not self.store.has_session(self.session_id):
            self.store.create_session(
                self.session_id, self.chat.model, str(self.session_path)
            )

        # turn span：整轮的根，不挂父。它下面的所有 span 都算这轮的开销。
        turn_span = self.store.start_span(self.session_id, "turn")
        self._turn_span = turn_span

        user = Message("user", [TextBlock(question)])
        self.transcript.append(self.session_id, user)
        # 整体重读而不是内存累积：见本文件顶部的说明。
        history = self.transcript.read(self.session_id)

        # 工具定义在这里取一次。它随下面的每次请求重申（API 无状态，
        # 「注册」不是一次性的，是每回都带）。
        tools = all_tools()

        # 跨会话记忆：每轮 turn 开始读一次文件，拼成 system 提示词随每次
        # 请求重发。三个刻意的决定——
        # 1. 不落 JSONL：system 是「随请求重发的派生状态」，不是对话历史。
        #    存历史里反而坏事：下一轮它就成了普通文本，改记忆文件也不会更新。
        # 2. 每轮读一次而不是每次请求读：轮内多圈请求共用同一个 system，
        #    前缀稳定，prompt caching 才有得救（第四层的入口就埋在这）。
        #    轮内用 bash 写的新记忆，下一轮才生效。
        # 3. 文件不存在或为空 = 没有 system，请求不带这个字段。
        memory = ""
        if self.memory_path.exists():
            memory = self.memory_path.read_text(encoding="utf-8").strip()
        system = ""
        if memory:
            system = (
                "以下是你的跨会话记忆（data/sandbox/memory.md，由你自己维护，"
                "可以用 bash 更新它：追加、改写、删去过期的）：\n\n" + memory
            )

        # agent loop：最多 MAX_TOOL_ROUNDS 次「请求 -> (要工具就执行再请求)」。
        for _ in range(MAX_TOOL_ROUNDS):
            # 圈界 = 请求间隙 = 软中断（cooperative steering）的插入点。
            # 排干 inbox：等在这里的新消息先落盘、发一条事件，再整体重读
            # 历史——下一圈请求里模型就「看得见」它们。此时上一个流和上一个
            # 工具都已经跑完，这正是 cooperative 的语义：等待单位是一次请求。
            # 落盘后历史尾部会出现 user(tool_result) + user(新消息) 的连续
            # user，Anthropic 方向由 adapter.history_to_anthropic 合并。
            if self.steering is not None:
                self._checkpoint()
                injected = self.steering.drain()
                for text in injected:
                    self.transcript.append(
                        self.session_id, Message("user", [TextBlock(text)])
                    )
                    # 事件发在当前流上：这轮的 SSE 消费者看得见插入了什么。
                    yield {"kind": "steering_injected", "text": text}
                    self._checkpoint()
                if injected:
                    history = self.transcript.read(self.session_id)
            # llm_request span：挂在 turn 下。开始时就知道的（模型名、消息条数）
            # 现在记，结束才知道的（stop_reason）由 end_span 补。
            llm_span = self.store.start_span(
                self.session_id,
                "llm_request",
                parent_id=turn_span,
                data={
                    "model": self.chat.model,
                    "messages": len(history),
                    # 记忆有没有带上、带了多少，记长度不记内容——spans 只记
                    # 控制流（D11），内容以文件为准，trace 里不复制一遍。
                    "system_chars": len(system),
                },
            )

            # TTFT（首 token 延迟）：从发起请求到第一个片段回来。
            # stream 是生成器，第一次迭代才真正发出网络请求，所以 t0 放 for 前面。
            # 这段等待 = 网络往返 + 厂商排队 + prefill（重读全部历史）+ 出第一个字；
            # 工具循环每圈都重付一次，历史越长 prefill 越久。
            t0 = time.monotonic()
            ttft: float | None = None

            try:
                for kind, text in self.chat.stream(history, tools, system=system):
                    if ttft is None:
                        ttft = time.monotonic() - t0
                    # 模型的 tool_use 片段只有名字，入参还在流里分片、这里拿不到，
                    # 跳过它：完整的 tool_use 事件等这轮流结束、从 blocks 里发。
                    if kind == "tool_use":
                        continue
                    yield {"kind": kind, "text": text}
                    # 让出后恢复的第一件事：看有没有被抢占。
                    self._checkpoint()
            except TurnCancelled:
                # 被抢占不是出错：llm span 收成 cancelled（这次请求是被掐的），
                # turn span 已由 _checkpoint 收尾，这里只结束自己这一层再往外抛。
                self.store.end_span(llm_span, status="cancelled")
                raise
            except Exception as e:
                # 出错：两个 span 都标 error，错误消息记在子 span 上（离错误最近）。
                self.store.end_span(llm_span, status="error", data={"message": str(e)})
                self.store.end_span(turn_span, status="error")
                raise
            # 注意：客户端中途断开（web 端 SSE 被掐掉）时，生成器收到的是
            # GeneratorExit，它不是 Exception 的子类，不走上面的 except——
            # span 就停在 running、end_time 为空，如实反映「这轮没跑完」。

            # 完整内容块从客户端拿（Anthropic：最终消息，含 signature 和解析好的
            # 入参；OpenAI：流里拼好）。落盘用完整块，不用展示片段。
            blocks = self.chat.blocks
            self.transcript.append(self.session_id, Message("assistant", blocks))
            stop = self.chat.stop_reason
            # ttft 和用量都拆开记：总耗时里藏着 ttft，token 和缓存比例藏在 usage 里。
            # effort 只在配置了才记。被夹档时 note 是 max→high，否则就是档位本身。
            data = {"stop_reason": stop, "ttft_ms": round(ttft * 1000) if ttft is not None else None,
                    **self.chat.usage}
            if self.chat.effort:
                data["effort"] = self.chat.effort_note
            self.store.end_span(llm_span, status="ok", data=data)

            if stop != "tool_use":
                # 模型不再要工具，这一轮到此为止。
                self.stop_reason = stop
                self.store.end_span(turn_span, status="ok")
                return

            # stop_reason == "tool_use"：模型发起了工具调用（一条回复里可能有多个）。
            # 逐个执行，配对靠 id 不靠顺序（并行调多个时顺序不可靠）。
            # 这一整段没有抢占检查点，是刻意的：assistant(tool_use) 已经落盘，
            # 对应的 user(tool_result) 不跟上，这个会话就再也续不上
            # （Anthropic 要求配对）。「配对不变式」优先于抢占的及时性——
            # 工具批次一旦开跑就完整落盘，取消在下一圈圈界生效（D18）。
            results = []
            for block in blocks:
                if block.type != "tool_use":
                    continue
                # 执行前先报一次：前端可以立刻渲染「正在调用 calculator(12*11)」，
                # 不用干等工具跑完。id 带上，并行多个调用时结果靠它配对。
                yield {"kind": "tool_use", "id": block.id, "name": block.name, "input": block.input}
                tool_span = self.store.start_span(
                    self.session_id,
                    "tool_call",
                    parent_id=turn_span,
                    data={"tool": block.name, "input": block.input},
                )
                # run_tool 永不抛异常：出错也变成文本结果交回，模型自己调整。
                content, is_error = run_tool(block.name, block.input)
                self.store.end_span(
                    tool_span,
                    status="error" if is_error else "ok",
                    data={"output": content},
                )
                # 耗时用 span 的起止时间，和 trace 回放是同一个算法（store.span_duration_ms）：
                # 前端看到的 elapsed_ms 跟 `main.py trace` 打出来的毫秒数一致。
                elapsed_ms = self.store.span_duration_ms(tool_span)
                # 跑完再报一次：出参、是否出错、耗时。id 和上面的 tool_use 相同。
                yield {
                    "kind": "tool_result",
                    "id": block.id,
                    "name": block.name,
                    "output": content,
                    "is_error": is_error,
                    "elapsed_ms": elapsed_ms,
                }
                results.append(ToolResultBlock(
                    tool_use_id=block.id, content=content, is_error=is_error
                ))

            # 工具结果作为下一条 user 消息交回（Anthropic 协议如此；
            # OpenAI 方向由 adapter 拆成 role="tool" 的独立消息）。
            self.transcript.append(self.session_id, Message("user", results))
            # 带着工具结果整体重读历史，进下一轮请求——模型这时「看得见」结果了。
            history = self.transcript.read(self.session_id)
        else:
            # for 循环自然走完（没从 return 出去）= 轮数用尽模型还在要工具。
            # 强行收住并在轨迹里留痕；stop_reason 保持最后一次的 "tool_use"，
            # 如实反映「是我不想再跑了」，不是模型说完了。
            self.stop_reason = self.chat.stop_reason
            self.store.end_span(turn_span, status="ok", data={"note": "达到工具轮数上限"})
