"""会话正文的存储。

一次会话对应一个文件，路径是 <目录>/<session_id>.jsonl。
JSONL 就是「一行一个 JSON」：一行是一条 Message，文件从上到下就是这次对话。

为什么正文不用 SQLite：消息的形状接下来会变（第二层要加工具调用和工具结果）。
一行一个 JSON 没有表结构，形状变了旧文件照样能读，不用改表。
而且对话只在末尾生长，追加一行正好符合这个习惯。

存的必须是完整消息，包括思考块的 signature。
如果只存屏幕上显示出来的文字，下一轮请求拼不回原来的消息，会话就续不上。
"""
import json
from pathlib import Path

# 只依赖 domain，不依赖任何 SDK。
from domain.message import Message


class Transcript:
    """一组 JSONL 会话文件的读写器。"""

    def __init__(self, directory: Path) -> None:
        # 所有会话文件放在同一个目录下，一个会话一个文件。
        self.directory = directory

    def path_for(self, session_id: str) -> Path:
        """一个会话对应的文件路径。文件可以尚不存在。"""
        return self.directory / f"{session_id}.jsonl"

    def append(self, session_id: str, message: Message) -> None:
        """在会话末尾追加一条消息。不修改已有的行。"""
        # parents=True 连同父目录一起建，exist_ok=True 目录已在时不报错。
        self.directory.mkdir(parents=True, exist_ok=True)
        # ensure_ascii=False：中文原样写进去，而不是写成 \uXXXX。
        line = json.dumps(message.to_dict(), ensure_ascii=False)
        # "a" 是追加模式，只在文件末尾写，不会覆盖已有内容。
        with self.path_for(session_id).open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read(self, session_id: str) -> list[Message]:
        """按顺序读出全部消息。文件不存在说明是新会话，返回空列表。"""
        path = self.path_for(session_id)
        if not path.exists():
            return []
        messages = []
        # splitlines 按行切开并去掉行尾换行。空行跳过，避免读到半行。
        for line in path.read_text(encoding="utf-8").splitlines():
            if line:
                messages.append(Message.from_dict(json.loads(line)))
        return messages
