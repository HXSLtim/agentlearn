"""一次对话的用例编排。这一层叫 application。

cli 和 web 两个入口共用的循环收在这里：入口只剩输入输出，
「一轮对话怎么走」只写这一份。工具循环（收到 tool_use 就执行并再请求）
是下一层要加在这上面的，届时 trace 埋点也加在这里。

历史维护方式统一为「每轮从 JSONL 整体重读」：文件是唯一事实来源，
崩溃或重启后天然一致，本地小文件的读取开销可以忽略。
"""
import json

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
        流中途出错时：记一条 error 日志（共同动作，记在这里），
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

        user = Message("user", [TextBlock(question)])
        self.transcript.append(self.session_id, user)
        # 整体重读而不是内存累积：见本文件顶部的说明。
        history = self.transcript.read(self.session_id)

        # 片段边交出去边并成完整的块，规则在 llm.merge_fragment 里。
        blocks: list[TextBlock | ThinkingBlock] = []
        try:
            for kind, text in self.chat.stream(history):
                yield kind, text
                merge_fragment(blocks, kind, text)
        except Exception as e:
            self.store.log(self.session_id, "error", json.dumps({"message": str(e)}))
            raise

        # signature 拼不出来（不在流式片段里），并出的思考块是空字符串，
        # 见 merge_fragment 的说明和 TODO「已知欠账」。
        self.transcript.append(self.session_id, Message("assistant", blocks))
        self.stop_reason = self.chat.stop_reason
        self.store.log(
            self.session_id, "request", json.dumps({"stop_reason": self.stop_reason})
        )
