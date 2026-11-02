"""会话索引和执行轨迹，存在一个 SQLite 文件里。

这里不存消息正文。正文在 transcript.py 的 JSONL 里，这个库只记「发生了什么」。

- sessions 表：有哪些会话。一行一个会话，给会话列表用。
- spans 表：执行轨迹（trace）。一行一个 span——一段有起止时间的操作，
  现在有两种：turn（一轮对话）、llm_request（一次模型请求），
  第二层会加 tool_call（一次工具调用）。
  每行有 parent_id 指向父 span，一个会话的 span 摊开是一棵树；
  回答「每步做了多久、按什么顺序、哪里出错」靠它，评测回放的数据源也是它。

「按会话组织、记在本地」是刻意的部署形态（docs/decisions.md D10）：
外部系统只需要拿 session_id，工具执行和轨迹全留在 agent 所在的机器上。

spans 表取代了原来的 logs 表（一行一个点事件）：点事件算不出耗时，
也没有父子关系，撑不起回放。选型记录是 D11。
"""
import functools
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _locked(method):
    """实例方法包一层锁。

    Store 在 web 进程里是全进程一个实例（SessionManager 收归复用），
    FastAPI 的同步端点跑在线程池里，多个请求线程会共用 self.conn。
    SQLite 的写锁本来就是库级的，这里串行化防的是另一件事：同一条
    sqlite3 连接上两个线程交错 execute/commit，事务会互相吞并。
    本地低频写入，锁开销可忽略。
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class Store:
    """agent.db 的读写器。建表、登记会话、记 span 都从这里走。"""

    def __init__(self, db_path: Path) -> None:
        # 数据库文件的父目录可能还不存在，先建好。
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # 连接到数据库文件。文件不存在时 sqlite3 会自动创建。
        # check_same_thread=False：web 端的 SSE 同步生成器会被 FastAPI 的线程池
        # 派发到不同线程执行，而 SQLite 默认只允许创建连接的线程用它。
        # 本地单用户写入频率低，没有锁竞争问题。
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        # 默认一行结果是元组，只能靠下标取值。换成 Row 后能用列名：row["id"]。
        self.conn.row_factory = sqlite3.Row
        # 见 _locked 的说明：多线程共用这条连接的串行化。必须在
        # _create_tables 之前就位（它也要过锁）。
        self._lock = threading.RLock()
        # 每次启动都确保表存在。
        self._create_tables()

    @_locked
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
            create table if not exists spans (
                id         integer primary key,  -- 自增，插入顺序就是开始顺序
                span_id    text unique,          -- 这条 span 自己的 id，结束时靠它找回来
                session_id text,                 -- 属于哪个会话
                parent_id  text,                 -- 父 span 的 span_id。turn 是根，为 NULL
                name       text,                 -- turn / llm_request / tool_call（第二层）
                start_time text,                 -- 开始时间
                end_time   text,                 -- 结束时回填。NULL = 没跑完进程就没了
                status     text,                 -- running / ok / error
                data       text                  -- JSON：模型名、stop_reason、错误消息等
            );
            """
        )

    @_locked
    def create_session(self, session_id: str, model: str, jsonl_path: str, title: str = "") -> None:
        """登记一个新会话。id 是主键，同一个 id 再登记一次会抛 IntegrityError。"""
        # 问号是参数占位符。用参数而不是拼字符串，避免值里出现引号时语句出错。
        self.conn.execute(
            "insert into sessions (id, title, model, created_at, jsonl_path) values (?, ?, ?, ?, ?)",
            (session_id, title, model, _now(), jsonl_path),
        )
        # execute 只是改了内存里的事务，commit 才真正写进文件。
        self.conn.commit()

    @_locked
    def has_session(self, session_id: str) -> bool:
        """这个会话是否已登记过。Conversation 靠它决定要不要 create_session。"""
        row = self.conn.execute("select 1 from sessions where id = ?", (session_id,)).fetchone()
        return row is not None

    @_locked
    def list_sessions(self, limit: int = 50) -> list[sqlite3.Row]:
        """最近创建的会话，最新的排在前面。

        TUI 的 /resume 用它生成选择器；限制条数避免历史很长时一次读太多正文。
        """
        return list(
            self.conn.execute(
                "select * from sessions order by created_at desc limit ?",
                (limit,),
            )
        )

    @_locked
    def start_span(
        self,
        session_id: str,
        name: str,
        parent_id: str | None = None,
        data: dict | None = None,
    ) -> str:
        """记一段操作开始，返回它的 span_id（end_span 时要拿回来）。

        parent_id 不传就是根 span。data 是开始时就知道的信息（比如模型名），
        结束时知道的（比如 stop_reason）由 end_span 补上。
        """
        span_id = uuid.uuid4().hex
        self.conn.execute(
            "insert into spans (span_id, session_id, parent_id, name, start_time, status, data)"
            " values (?, ?, ?, ?, ?, ?, ?)",
            (span_id, session_id, parent_id, name, _now(), "running", _json(data)),
        )
        self.conn.commit()
        return span_id

    @_locked
    def end_span(self, span_id: str, status: str = "ok", data: dict | None = None) -> None:
        """回填一段操作的结束：结束时间、状态、补充信息。

        data 和开始时的合并而不是覆盖——开始记了模型名，结束补 stop_reason，
        两边都留得住。span 不见了（理论上不会）就不做任何事。
        """
        row = self.conn.execute("select data from spans where span_id = ?", (span_id,)).fetchone()
        if row is None:
            return
        merged = json.loads(row["data"] or "{}")
        merged.update(data or {})
        self.conn.execute(
            "update spans set end_time = ?, status = ?, data = ? where span_id = ?",
            (_now(), status, _json(merged), span_id),
        )
        self.conn.commit()

    @_locked
    def span_duration_ms(self, span_id: str) -> int | None:
        """一个 span 的耗时，单位毫秒。还没结束（end_time 为空）返回 None。

        事件流和 trace 回放都用它，保证两边看到的是同一个数：
        耗时只在这一处算，不让每个调用方各写一遍时间相减。
        """
        row = self.conn.execute(
            "select start_time, end_time from spans where span_id = ?", (span_id,)
        ).fetchone()
        if row is None or row["end_time"] is None:
            return None
        seconds = (
            datetime.fromisoformat(row["end_time"]) - datetime.fromisoformat(row["start_time"])
        ).total_seconds()
        return round(seconds * 1000)

    @_locked
    def spans_for(self, session_id: str) -> list[sqlite3.Row]:
        """一个会话的全部 span，按开始顺序排列。id 自增，所以按 id 排就是按开始排。

        trace 子命令用它画树；评测回放读它拼「每步耗时 + 顺序」。
        """
        return list(
            self.conn.execute("select * from spans where session_id = ? order by id", (session_id,))
        )


def _json(data: dict | None) -> str:
    """dict 转成 JSON 字符串存库。ensure_ascii=False 让中文直接可读。"""
    return json.dumps(data or {}, ensure_ascii=False)


def _now() -> str:
    """当前时间的 ISO 字符串，例如 2026-10-30T01:23:45.678901+00:00。

    统一用 UTC，避免同一条数据在不同时区的机器上读出来差几个小时。
    带微秒：span 的耗时是两个时间相减算出来的，粒度到不了微秒就全显示 0。
    """
    return datetime.now(timezone.utc).isoformat()
