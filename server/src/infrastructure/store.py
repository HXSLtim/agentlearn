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
        self._migrate()

    def _migrate(self) -> None:
        """加列式迁移：已存在的库补上后来加的列。

        create table if not exists 不会给旧表加新列，所以每个新列一条
        ALTER + 捞重复列的 OperationalError。没有版本表：新列就这么几个、
        幂等无害，学习项目不配升版本号机制（表多到需要迁移记录再重评，
        和 WAL 那条欠账同一个边界）。
        """
        migrations = (
            # P1 会话管理（2026-11-03）：软删除标记。0 = 活跃，1 = 已归档
            # （默认列表不出现，数据全在，可恢复）。
            "alter table sessions add column archived integer not null default 0",
            # 会话库 pin（P1）：置顶标记，列表排序 pinned 优先于最近活动。
            "alter table sessions add column pinned integer not null default 0",
        )
        for statement in migrations:
            try:
                self.conn.execute(statement)
                self.conn.commit()
            except sqlite3.OperationalError as e:
                # duplicate column name：这列已经有了，正是想要的状态。
                if "duplicate column" not in str(e):
                    raise

    @_locked
    def _create_tables(self) -> None:
        """建表。if not exists 保证重复调用不会报错，也不会覆盖已有数据。"""
        # executescript 一次执行多条语句，execute 只能执行一条。
        self.conn.executescript(
            """
            create table if not exists sessions (
                id         text primary key,   -- 会话 id，也是 JSONL 的文件名
                title      text,               -- 会话标题：第一条用户消息截断（set_title_if_empty）
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
    def list_sessions(
        self, limit: int = 50, include_archived: bool = False
    ) -> list[sqlite3.Row]:
        """最近有活动的会话，最新在前（按最近活动排序，不是创建时间）。

        默认不含已归档（软删除）的会话；要归档视图就 include_archived=True
        （归档列表同样按最近活动排，方便找回刚归档的）。

        updated_at / turns / stop_reason 不在 sessions 表里，用子查询从
        spans 推导：那是已有信息的派生值，落库就要维护两处（哪个 span
        落库时忘了更新，索引就开始说谎）。量级是学习项目的几十个会话，
        三个子查询毫无压力；真到了慢的那天再谈反范式。
        - updated_at = 该会话最后一个 span 的开始时间（没跑过模型的新会话
          退回 created_at）；
        - turns = turn span 的个数（一次 turn() 一个）；
        - stop_reason = 最后一次带 stop_reason 的 llm_request（压缩请求
          没有这个键，json_extract 出 NULL 自然跳过）。
        ISO 字符串同格式，字典序就是时间序，直接 order by。
        """
        return list(
            self.conn.execute(
                """
                select s.id, s.title, s.model, s.created_at, s.jsonl_path,
                       s.archived, s.pinned,
                       coalesce(
                           (select max(p.start_time) from spans p
                            where p.session_id = s.id), s.created_at
                       ) as updated_at,
                       (select count(*) from spans p
                        where p.session_id = s.id and p.name = 'turn') as turns,
                       (select json_extract(p.data, '$.stop_reason') from spans p
                        where p.session_id = s.id and p.name = 'llm_request'
                          and p.data is not null
                          and json_extract(p.data, '$.stop_reason') is not null
                        order by p.id desc limit 1) as stop_reason
                from sessions s
                where s.archived = 0 or ?
                order by s.pinned desc, updated_at desc
                limit ?
                """,
                (1 if include_archived else 0, limit),
            )
        )

    @_locked
    def rename_session(self, session_id: str, title: str) -> bool:
        """改会话标题（用户起正式名字）。返回是否真的改了（会话不存在 = False）。

        和 set_title_if_empty 是两个动作：那个只补空位、这个无条件覆盖，
        名字分开免得调用处一个参数切语义。
        """
        cursor = self.conn.execute(
            "update sessions set title = ? where id = ?", (title, session_id)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    @_locked
    def set_archived(self, session_id: str, archived: bool) -> bool:
        """归档 / 取消归档（软删除标记）。返回会话是否存在。"""
        cursor = self.conn.execute(
            "update sessions set archived = ? where id = ?",
            (1 if archived else 0, session_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    @_locked
    def set_pinned(self, session_id: str, pinned: bool) -> bool:
        """置顶 / 取消置顶（会话库 pin）。返回会话是否存在。"""
        cursor = self.conn.execute(
            "update sessions set pinned = ? where id = ?",
            (1 if pinned else 0, session_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    @_locked
    def delete_session(self, session_id: str) -> None:
        """永久删除：sessions 行和 spans 一起清（正文文件由调用方删）。

        spans 留着会变成「指向不存在会话的孤儿行」——trace、索引的
        派生子查询都会碰它。SQLite 没建外键（建表时就没建，学习项目
        没有删库级联的需求），一致性由这里显式保证。
        """
        self.conn.execute("delete from spans where session_id = ?", (session_id,))
        self.conn.execute("delete from sessions where id = ?", (session_id,))
        self.conn.commit()

    @_locked
    def set_title_if_empty(self, session_id: str, title: str) -> None:
        """给还没有标题的会话补标题（第一条用户消息的截断）。

        只在空的时候写：用户或前端后来起的正式标题不被第一条消息覆盖。
        title 列建表时就有（当时注了「预留」），现在是兑现。
        """
        self.conn.execute(
            "update sessions set title = ? where id = ? and (title is null or title = '')",
            (title, session_id),
        )
        self.conn.commit()

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
