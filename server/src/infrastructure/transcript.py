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
import re
import time
from pathlib import Path

# 只依赖 domain，不依赖任何 SDK。
from domain.message import Message

# session_id 会直接拼进文件名。只接受 UUID，否则像 "../x" 这样的值能逃出目录，写到别的文件上。
_SESSION_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class Transcript:
    """一组 JSONL 会话文件的读写器。"""

    def __init__(self, directory: Path) -> None:
        # 所有会话文件放在同一个目录下，一个会话一个文件。
        self.directory = directory

    def path_for(self, session_id: str) -> Path:
        """一个会话对应的文件路径。文件可以尚不存在。

        session_id 只允许是 UUID。它来自请求参数，不校验的话可以被写成路径，
        读到或覆盖目录之外的文件。
        """
        if not _SESSION_ID.fullmatch(session_id):
            raise ValueError(f"session_id 必须是 UUID: {session_id}")
        # 解析成绝对路径后再确认一次没有逃出目录，防止符号链接之类的绕法。
        path = (self.directory / f"{session_id}.jsonl").resolve()
        if not path.is_relative_to(self.directory.resolve()):
            raise ValueError(f"session_id 越出了目录: {session_id}")
        return path

    def summary_path_for(self, session_id: str) -> Path:
        """一个会话的压缩摘要文件路径（第 4 层的派生层）。

        校验复用 path_for 的逻辑：session_id 同样要过 UUID 检查。
        摘要和正文放同一目录，生命周期一致——删掉正文它就没有意义了。
        """
        return self.path_for(session_id).with_suffix(".summary.json")

    def delete(self, session_id: str) -> None:
        """永久删除一个会话的正文文件和摘要派生物。不存在时静默（幂等）。

        「删掉正文摘要就没有意义了」——summary_path_for 注释里的承诺
        在这里兑现。摘要先删：万一第二步失败，孤儿的摘要文件无害
        （下次压缩会重建），反过来留的却是没有正文的假历史。
        """
        summary = self.summary_path_for(session_id)
        summary.unlink(missing_ok=True)
        self.path_for(session_id).unlink(missing_ok=True)

    def append(self, session_id: str, message: Message) -> None:
        """在会话末尾追加一条消息。不修改已有的行。"""
        # parents=True 连同父目录一起建，exist_ok=True 目录已在时不报错。
        self.directory.mkdir(parents=True, exist_ok=True)
        # ensure_ascii=False：中文原样写进去，而不是写成 \uXXXX。
        # ts 只在存储层加盖：to_dict 同时是 Anthropic 请求的 payload，
        # 不能把时间戳混进发给模型的消息形状里。
        payload = message.to_dict()
        payload.setdefault("ts", time.time())
        line = json.dumps(payload, ensure_ascii=False)
        # "a" 是追加模式，只在文件末尾写，不会覆盖已有内容。
        with self.path_for(session_id).open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read_rows(self, session_id: str) -> list[dict]:
        """按顺序读出原始字典行（含存储层加盖的 ts）。空行跳过；截断行照样抛。"""
        path = self.path_for(session_id)
        if not path.exists():
            return []
        rows = []
        # splitlines 按行切开并去掉行尾换行。
        # 空行跳过，防的是 json.loads("") 报错；真发生截断时那一行是非空的
        # 非法 JSON，照样会抛——宁可报错，不静默丢数据。
        for line in path.read_text(encoding="utf-8").splitlines():
            if line:
                rows.append(json.loads(line))
        return rows

    def read(self, session_id: str) -> list[Message]:
        """按顺序读出全部消息。文件不存在说明是新会话，返回空列表。"""
        # from_dict 只读 role/content，多出来的 ts 键会被自然忽略。
        return [Message.from_dict(row) for row in self.read_rows(session_id)]
