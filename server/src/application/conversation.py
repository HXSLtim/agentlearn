"""一次对话的用例编排。这一层叫 application。

cli 和 web 两个入口共用的循环收在这里：入口只剩输入输出，
「一轮对话怎么走」只写这一份。工具循环（收到 tool_use 就执行并再请求）
是第二层加在这上面的。

历史维护方式统一为「每轮从 JSONL 整体重读」：文件是唯一事实来源，
崩溃或重启后天然一致，本地小文件的读取开销可以忽略。

trace 的埋点也在这里：每轮一个 turn span（根），每次模型请求一个
llm_request span 挂在它下面（见 store.py 的 spans 表）。第二层的工具调用
会是第三种 span（tool_call），同样挂在 turn 下面。
"""
from domain.message import Message, TextBlock, ThinkingBlock
from infrastructure.llm import AnthropicChat, OpenAIChat, merge_fragment
from infrastructure.store import Store
from infrastructure.transcript import Transcript


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
        self.stop_reason: str | None = None

    def turn(self, question: str):
        """跑一轮对话，逐个产出 (kind, text) 片段。

        片段只交出来，怎么展示是入口的事：终端打印或 SSE 推送。
        一轮的轨迹是两个 span：turn 包住整轮，llm_request 包住这一次
        模型调用，开始/结束时间和状态由 Store 落进 spans 表
        （`main.py trace` 子命令可以把这棵树打出来回放）。
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

        # llm_request span：挂在 turn 下面。开始时就知道的信息（模型名、
        # 发了多少条消息）现在记，结束才知道的（stop_reason）由 end_span 补。
        llm_span = self.store.start_span(
            self.session_id,
            "llm_request",
            parent_id=turn_span,
            data={"model": self.chat.model, "messages": len(history)},
        )

        # 片段边交出去边并成完整的块，规则在 llm.merge_fragment 里。
        blocks: list[TextBlock | ThinkingBlock] = []
        try:
            for kind, text in self.chat.stream(history):
                yield kind, text
                merge_fragment(blocks, kind, text)
        except Exception as e:
            # 出错：两个 span 都标 error，错误消息记在子 span 上（离错误最近）。
            self.store.end_span(llm_span, status="error", data={"message": str(e)})
            self.store.end_span(turn_span, status="error")
            raise
        # 注意：客户端中途断开（web 端 SSE 被掐掉）时，生成器收到的是
        # GeneratorExit，它不是 Exception 的子类，不走上面的 except——
        # 两个 span 就停在 running、end_time 为空，如实反映「这轮没跑完」。
        # trace 里一眼就能认出这种半截轮次。

        # signature 拼不出来（不在流式片段里），并出的思考块是空字符串，
        # 见 merge_fragment 的说明和 TODO「已知欠账」。
        self.transcript.append(self.session_id, Message("assistant", blocks))
        self.stop_reason = self.chat.stop_reason
        self.store.end_span(llm_span, status="ok", data={"stop_reason": self.stop_reason})
        self.store.end_span(turn_span, status="ok")
