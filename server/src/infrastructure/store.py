"""会话索引和日志，存在一个 SQLite 文件里。

这里不存消息正文。正文在 transcript.py 的 JSONL 里。
SQLite 只管两件需要查询的事：

- sessions 表：有哪些会话。用来列出历史会话，一行一个会话。
- logs 表：会话里发生过什么。一行一个事件，比如一次请求、一次报错。

两张表放同一个库，没必要拆成两个数据库文件。
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class Store:
    """agent.db 的读写器。建表、登记会话、记日志都从这里走。"""

    def __init__(self, db_path: Path) -> None:
        # 数据库文件的父目录可能还不存在，先建好。
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # 连接到数据库文件。文件不存在时 sqlite3 会自动创建。
        self.conn = sqlite3.connect(db_path)
        # 默认一行结果是元组，只能靠下标取值。换成 Row 后能用列名：row["id"]。
        self.conn.row_factory = sqlite3.Row
        # 每次启动都确保表存在。
        self._create_tables()

    def _create_tables(self) -> None:
        """建表。if not exists 保证重复调用不会报错，也不会覆盖已有数据。"""
        # executescript 一次执行多条语句，execute 只能执行一条。
        self.conn.executescript(
            """
            create table if not exists sessions (
                id         text primary key,   -- 会话 id，也是 JSONL 的文件名
                title      text,               -- 预留，现在还没有起标题的地方
                model      text,               -- 这个会话用的模型
                created_at text,               -- 创建时间，ISO 格式的字符串
                jsonl_path text                -- 正文文件的路径
            );
            create table if not exists logs (
                id         integer primary key,  -- 自增，也就是事件的先后顺序
                session_id text,                 -- 属于哪个会话
                time       text,                 -- 事件发生的时间
                event      text,                 -- 事件种类：request、error 等
                data       text                  -- 一段 JSON，事件的具体内容
            );
            """
        )

    def create_session(self, session_id: str, model: str, jsonl_path: str, title: str = "") -> None:
        """登记一个新会话。id 是主键，同一个 id 再登记一次会抛 IntegrityError。"""
        # 问号是参数占位符。用参数而不是拼字符串，避免值里出现引号时语句出错。
        self.conn.execute(
            "insert into sessions (id, title, model, created_at, jsonl_path) values (?, ?, ?, ?, ?)",
            (session_id, title, model, _now(), jsonl_path),
        )
        # execute 只是改了内存里的事务，commit 才真正写进文件。
        self.conn.commit()

    def list_sessions(self) -> list[sqlite3.Row]:
        """全部会话，最近创建的排在前面。"""
        return list(self.conn.execute("select * from sessions order by created_at desc"))

    def log(self, session_id: str, event: str, data: str) -> None:
        """记一条事件。data 由调用方准备好，是一段 JSON 字符串。"""
        self.conn.execute(
            "insert into logs (session_id, time, event, data) values (?, ?, ?, ?)",
            (session_id, _now(), event, data),
        )
        self.conn.commit()

    def logs_for(self, session_id: str) -> list[sqlite3.Row]:
        """一个会话的全部事件，按发生顺序排列。id 自增，所以按 id 排就是按时间排。"""
        return list(self.conn.execute("select * from logs where session_id = ? order by id", (session_id,)))


def _now() -> str:
    """当前时间的 ISO 字符串，例如 2026-10-30T01:23:45+00:00。

    统一用 UTC，避免同一条数据在不同时区的机器上读出来差几个小时。
    """
    return datetime.now(timezone.utc).isoformat()
