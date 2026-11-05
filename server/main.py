"""启动入口。

这是进程的组合根：只负责解析模式、装配依赖、把控制权交给对应界面。

    uv run python main.py tui
    uv run python main.py tui <session_id>   续接一个已有会话
    uv run python main.py trace <session_id>
    uv run python main.py desktop            桌面端 HTTP/SSE API
    uv run python main.py web                desktop 的兼容别名
"""
import argparse
import os
import uuid
from pathlib import Path

# server/ 的绝对路径。config.ini 和 data/ 这些相对路径全靠它锚定。
ROOT = Path(__file__).resolve().parent


def run() -> None:
    # 把工作目录钉在 server/。各个实现里的相对路径
    # （Path("data")、Path("config.ini")）由此获得确定的位置，
    # 从仓库根、从别的目录启动都等价。main.py 是进程唯一入口，chdir 是安全的。
    os.chdir(ROOT)

    parser = argparse.ArgumentParser(description="AgentLearn")
    # 主模式只有两类界面：终端 TUI 和桌面端 API。trace 是 TUI 的只读模式。
    sub = parser.add_subparsers(dest="mode", required=True)

    tui = sub.add_parser("tui", help="在终端工作台里对话")
    tui.add_argument("session_id", nargs="?", help="续接已有会话的 id")

    desktop = sub.add_parser(
        "desktop",
        aliases=["web"],
        help="启动给 Tauri 桌面端用的 HTTP/SSE 服务（web 为兼容别名）",
    )
    desktop.add_argument("--port", type=int, default=8000, help="监听端口，默认 8000")

    trace = sub.add_parser("trace", help="在终端检查一个会话的执行轨迹")
    trace.add_argument("session_id", help="要回放的会话 id")

    args = parser.parse_args()
    if args.mode == "tui":
        _run_tui(args.session_id)
    elif args.mode == "trace":
        _run_trace(args.session_id)
    else:
        _run_desktop(args.port)


def _run_tui(session_id: str | None) -> None:
    """装配会话依赖，然后把输入输出全部交给 Textual。"""
    from application.approval import ApprovalBroker
    from application.conversation import Conversation
    from application.capabilities import capability_snapshot
    from application.plan import PlanBroker
    from infrastructure.config import (
        SettingsValues,
        load_config,
        load_settings,
        save_settings,
    )
    from infrastructure.llm import build_chat, list_models
    from infrastructure.store import Store
    from infrastructure.transcript import Transcript
    from interfaces.tui import SessionSummary, run_chat

    transcript = Transcript(Path("data") / "sessions")
    store = Store(Path("data") / "agent.db")
    approval = ApprovalBroker()
    plans = PlanBroker()

    def load_sessions() -> list[SessionSummary]:
        """把持久化索引映射成 ResumeScreen 需要的轻量列表。"""
        summaries: list[SessionSummary] = []
        for row in store.list_sessions():
            try:
                messages = transcript.read(row["id"])
            except Exception:
                messages = []
                title = "(无法读取的会话正文)"
            else:
                first_user_text = next(
                    (
                        block.text
                        for message in messages
                        if message.role == "user"
                        for block in message.content
                        if block.type == "text" and block.text.strip()
                    ),
                    "",
                )
                title = row["title"] or " ".join(first_user_text.split())
                title = title[:64] + ("…" if len(title) > 64 else "")
                title = title or "(未命名会话)"
            turns = sum(
                1
                for message in messages
                if message.role == "user"
                and any(
                    block.type == "text" and block.text.strip()
                    for block in message.content
                )
            )
            summaries.append(
                SessionSummary(
                    session_id=row["id"],
                    title=title,
                    model=row["model"] or "",
                    created_at=row["created_at"] or "",
                    turns=turns,
                )
            )
        return summaries

    session_id = session_id or str(uuid.uuid4())
    while True:
        def load_capabilities() -> dict:
            config = load_config()
            return capability_snapshot(
                config.provider, config.model_id, config.effort or ""
            )

        try:
            conversation = Conversation(
                build_chat(load_config()),
                transcript,
                store,
                session_id,
                approval=approval,
                plan_broker=plans,
            )
        except ValueError as exc:
            raise SystemExit(str(exc))

        def save_runtime_settings(values: SettingsValues):
            snapshot = save_settings(values)
            conversation.chat = build_chat(load_config())
            return snapshot

        def load_runtime_models() -> list[str]:
            return list_models(load_config())

        next_session_id = run_chat(
            conversation,
            transcript.read(session_id),
            session_id,
            settings_loader=load_settings,
            settings_saver=save_runtime_settings,
            session_loader=load_sessions,
            approval=approval,
            plan_broker=plans,
            capability_loader=load_capabilities,
            model_loader=load_runtime_models,
        )
        if next_session_id is None:
            return
        session_id = next_session_id


def _run_trace(session_id: str) -> None:
    from interfaces.tui import run_trace

    run_trace(session_id)


def _run_desktop(port: int) -> None:
    from interfaces.web.server import main as desktop_main

    desktop_main(port)


if __name__ == "__main__":
    run()
