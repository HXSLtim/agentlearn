"""启动入口。

选择从终端还是从 web 服务跑。用法：

    uv run python main.py cli
    uv run python main.py cli <session_id>   续接一个已有会话
    uv run python main.py web                启动 HTTP 服务，默认 8000 端口
"""
import argparse

from interfaces.cli import main as cli_main
from interfaces.web.server import main as web_main


def run() -> None:
    parser = argparse.ArgumentParser(description="AgentLearn")
    # 两个子命令：cli 和 web。
    sub = parser.add_subparsers(dest="mode", required=True)

    cli = sub.add_parser("cli", help="在终端里对话")
    # session_id 可选。不传就新开一个会话。
    cli.add_argument("session_id", nargs="?", help="续接已有会话的 id")

    web = sub.add_parser("web", help="启动给前端用的 HTTP 服务")
    web.add_argument("--port", type=int, default=8000, help="监听端口，默认 8000")

    args = parser.parse_args()
    if args.mode == "cli":
        cli_main(args.session_id)
    else:
        web_main(args.port)


if __name__ == "__main__":
    run()
