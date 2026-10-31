"""一次对话的用例编排。这一层叫 application。

cli 和 web 两个入口共用的循环收在这里：入口只剩输入输出，
「一轮对话怎么走」只写这一份。这里是 agent loop 本体：

    用户提问 -> 模型回答
       └─ stop_reason == tool_use？-> 执行工具 -> 结果交回 -> 再请求模型
          （循环，直到模型不再要工具，或达到轮数上限）

历史维护方式统一为「每轮从 JSONL 整体重读」：文件是唯一事实来源，
崩溃或重启后天然一致，本地小文件的读取开销可以忽略。

trace 的埋点也在这里：turn span 是一棵树的根，树上是循环走过的每一层——
每次模型请求一个 llm_request span，每次工具执行一个 tool_call span
（见 store.py 的 spans 表）。`main.py trace` 能把这棵树打出来。
"""
from domain.message import Message, TextBlock, ToolResultBlock
from infrastructure.llm import AnthropicChat, OpenAIChat
from infrastructure.store import Store
from infrastructure.transcript import Transcript

from application.tools import all_tools, run_tool

# 一轮对话里最多让模型连续调几轮工具。没有这个上限，模型抽风反复调用时
# 会一直烧 token 停不下来——agent 循环永远要有退出条件。
MAX_TOOL_ROUNDS = 8


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
    ) -> None:
        # 构造时就校验 session_id（必须是 UUID），非法在这里抛 ValueError。
        # cli 把它转成一句提示退出，web 在端点里提前拦下返回 422。
        self.session_path = transcript.path_for(session_id)
        self.chat = chat
        self.transcript = transcript
        self.store = store
        self.session_id = session_id
        # 最近一轮的结束原因，turn 完整跑完才有值。
        # 带 tools 的一轮可能经历多次请求，这里是最后一次的。
        self.stop_reason: str | None = None

    def turn(self, question: str):
        """跑一轮对话，逐个产出 (kind, text) 片段，直到给出最终回答。

        片段只交出来，怎么展示是入口的事：终端打印或 SSE 推送。
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

        user = Message("user", [TextBlock(question)])
        self.transcript.append(self.session_id, user)
        # 整体重读而不是内存累积：见本文件顶部的说明。
        history = self.transcript.read(self.session_id)

        # 工具定义在这里取一次。它随下面的每次请求重申（API 无状态，
        # 「注册」不是一次性的，是每回都带）。
        tools = all_tools()

        # agent loop：最多 MAX_TOOL_ROUNDS 次「请求 -> (要工具就执行再请求)」。
        for _ in range(MAX_TOOL_ROUNDS):
            # llm_request span：挂在 turn 下。开始时就知道的（模型名、消息条数）
            # 现在记，结束才知道的（stop_reason）由 end_span 补。
            llm_span = self.store.start_span(
                self.session_id,
                "llm_request",
                parent_id=turn_span,
                data={"model": self.chat.model, "messages": len(history)},
            )

            try:
                for kind, text in self.chat.stream(history, tools):
                    yield kind, text
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
            self.store.end_span(llm_span, status="ok", data={"stop_reason": stop})

            if stop != "tool_use":
                # 模型不再要工具，这一轮到此为止。
                self.stop_reason = stop
                self.store.end_span(turn_span, status="ok")
                return

            # stop_reason == "tool_use"：模型发起了工具调用（一条回复里可能有多个）。
            # 逐个执行，配对靠 id 不靠顺序（并行调多个时顺序不可靠）。
            results = []
            for block in blocks:
                if block.type != "tool_use":
                    continue
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
