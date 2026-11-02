"""启动入口。

选择从终端还是从 web 服务跑。用法：

    uv run python main.py cli
    uv run python main.py cli <session_id>   续接一个已有会话
    uv run python main.py cli --plain        强制纯文本模式
    uv run python main.py web                启动 HTTP 服务，默认 8000 端口
    uv run python main.py trace <session_id> 回放一个会话的执行轨迹
"""
import argparse
import os
from pathlib import Path

from interfaces.cli import main as cli_main
from interfaces.cli import show_trace as trace_main
from interfaces.web.server import main as web_main

# server/ 的绝对路径。config.ini 和 data/ 这些相对路径全靠它锚定。
ROOT = Path(__file__).resolve().parent


def run() -> None:
    # 把工作目录钉在 server/。cli.py / server.py / config.py 里的相对路径
    # （Path("data")、Path("config.ini")）由此获得确定的位置，
    # 从仓库根、从别的目录启动都等价。main.py 是进程唯一入口，chdir 是安全的。
    os.chdir(ROOT)

    parser = argparse.ArgumentParser(description="AgentLearn")
    # 两个子命令：cli 和 web。
    sub = parser.add_subparsers(dest="mode", required=True)

    cli = sub.add_parser("cli", help="在终端工作台里对话")
    # session_id 可选。不传就新开一个会话。
    cli.add_argument("session_id", nargs="?", help="续接已有会话的 id")
    cli.add_argument("--plain", action="store_true", help="强制使用逐行纯文本模式")

    web = sub.add_parser("web", help="启动给前端用的 HTTP 服务")
    web.add_argument("--port", type=int, default=8000, help="监听端口，默认 8000")

    trace = sub.add_parser("trace", help="回放一个会话的执行轨迹")
    trace.add_argument("session_id", help="要回放的会话 id")

    args = parser.parse_args()
    if args.mode == "cli":
        cli_main(args.session_id, plain=args.plain)
    elif args.mode == "trace":
        trace_main(args.session_id)
    else:
        web_main(args.port)


if __name__ == "__main__":
    run()
