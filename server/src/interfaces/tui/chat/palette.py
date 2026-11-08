"""斜杠命令：命令表、档位循环表与不抢焦点的命令索引。"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

from textual.widgets import (
    Static,
)

from ..theme import (
    DIM,
    ERROR,
    INK,
    MUTED,
    PAPER,
    SIGNAL,
    TOOL,
    WARN,
)


@dataclass(frozen=True)
class SlashCommand:
    name: str
    usage: str
    description: str
    group: str
    aliases: tuple[str, ...] = ()
    takes_argument: bool = False


COMMANDS = (
    SlashCommand(
        "resume",
        "/resume [id]",
        "选择并续接历史会话",
        "会话",
        takes_argument=True,
    ),
    SlashCommand(
        "plan",
        "/plan <任务>",
        "只读探索后产出计划，确认后才执行",
        "运行",
        takes_argument=True,
    ),
    SlashCommand("stop", "/stop", "在下一个检查点停止当前轮", "运行"),
    SlashCommand("retry", "/retry", "重跑上一条失败请求，不重复用户卡片", "运行"),
    SlashCommand(
        "attach",
        "/attach [path]",
        "挂一张图片；无参列出已挂列表",
        "运行",
        takes_argument=True,
    ),
    SlashCommand(
        "detach",
        "/detach [n|all|path]",
        "移除已挂图片：按序号、路径或全部",
        "运行",
        takes_argument=True,
    ),
    SlashCommand(
        "steering",
        "/steering <mode>",
        "切换 queue / cooperative / preemptive",
        "运行",
        takes_argument=True,
    ),
    SlashCommand(
        "effort",
        "/effort [level]",
        "切换思考等级；不带参数循环",
        "运行",
        takes_argument=True,
    ),
    SlashCommand(
        "approval",
        "/approval [mode]",
        "切换 approve / auto / yolo",
        "运行",
        takes_argument=True,
    ),
    SlashCommand(
        "rounds",
        "/rounds [N]",
        "查看/调整本会话工具轮数上限",
        "运行",
        takes_argument=True,
    ),
    SlashCommand("clear", "/clear", "清空当前上下文并开新会话", "会话"),
    SlashCommand("new", "/new", "新开一个空会话", "会话"),
    SlashCommand(
        "memory",
        "/memory [内容]",
        "往跨会话记忆记一条；不带参数查看",
        "会话",
        takes_argument=True,
    ),
    SlashCommand(
        "find",
        "/find [关键词]",
        "定位当前会话里的一条已渲染消息",
        "会话",
        takes_argument=True,
    ),
    SlashCommand(
        "transcript",
        "/transcript [mode]",
        "切换 normal / thinking / verbose 三档正文视图",
        "运行",
        takes_argument=True,
    ),
    SlashCommand("status", "/status", "查看当前会话与运行状态", "运行"),
    SlashCommand(
        "workdir",
        "/workdir [路径]",
        "设置会话工作目录；reset 恢复 sandbox",
        "运行",
        takes_argument=True,
    ),
    SlashCommand(
        "context",
        "/context",
        "查看上下文构成：估算 / 实数 / 预算",
        "运行",
    ),
    SlashCommand(
        "compact",
        "/compact",
        "手动压缩上下文（花一次模型请求）",
        "运行",
    ),
    SlashCommand("trace", "/trace", "查看当前会话的 span 轨迹", "运行"),
    SlashCommand("diff", "/diff", "查看当前 Git 工作区差异", "运行"),
    SlashCommand("copy", "/copy", "复制最近一条完整回答", "系统"),
    SlashCommand(
        "export",
        "/export [path]",
        "把当前会话导出为 Markdown",
        "系统",
        takes_argument=True,
    ),
    SlashCommand(
        "model",
        "/model <id>",
        "切换运行模型；不带参数打开设置",
        "运行",
        takes_argument=True,
    ),
    SlashCommand(
        "config",
        "/config",
        "打开 config.ini 模型设置",
        "运行",
        ("settings",),
    ),
    SlashCommand("now", "/now", "触发 now_time 示例", "任务"),
    SlashCommand("files", "/files", "查看 sandbox 目录", "任务"),
    SlashCommand(
        "boundary",
        "/boundary",
        "触发 reason / act / report 示例",
        "任务",
    ),
    SlashCommand("help", "/help", "显示全部命令", "系统"),
    SlashCommand("quit", "/quit", "退出终端工作台", "系统"),
)


COMMANDS_BY_NAME = {
    alias: command
    for command in COMMANDS
    for alias in (command.name, *command.aliases)
}


# Alt+T 只在真正会发往模型的四档之间循环；默认档是“不发送”，
# 和 off 在 adapter 里语义相同，所以不把两个看起来不同的空档串进来。
EFFORT_CYCLE = {
    "": "low",
    "off": "low",
    "low": "medium",
    "medium": "high",
    "high": "max",
    "max": "",
}


class CommandPalette(Static):
    """输入 `/` 时出现的轻量命令索引，不抢输入焦点。"""

    def __init__(self) -> None:
        super().__init__(classes="command-palette")
        self._matches = list(COMMANDS)
        self._selected = 0
        self.display = False

    @property
    def selected(self) -> SlashCommand:
        return self._matches[self._selected]

    def filter(self, query: str) -> bool:
        text = query.strip()
        if not text.startswith("/") or any(char.isspace() for char in text):
            self.display = False
            return False
        prefix = text[1:].casefold()
        matches = [
            command
            for command in COMMANDS
            if any(
                candidate.startswith(prefix)
                for candidate in (command.name, *command.aliases)
            )
        ]
        if not matches:
            self.display = False
            return False
        self._matches = matches
        self._selected = min(self._selected, len(matches) - 1)
        self.update(self._paint())
        self.display = True
        return True

    def move(self, delta: int) -> None:
        if not self.display:
            return
        self._selected = (self._selected + delta) % len(self._matches)
        self.update(self._paint())

    def _paint(self) -> Text:
        text = Text.assemble(
            ("命令索引", f"bold {SIGNAL}"),
            ("  /  会话命令在前\n", DIM),
        )
        group = ""
        for index, command in enumerate(self._matches):
            if command.group != group:
                group = command.group
                text.append(f"{group}\n", f"bold {DIM}")
            selected = index == self._selected
            row_style = f"bold {INK} on {SIGNAL}" if selected else PAPER
            detail_style = f"{INK} on {SIGNAL}" if selected else MUTED
            text.append("  ▶ " if selected else "    ", style=SIGNAL)
            text.append(f"{command.usage:<22}", style=row_style)
            text.append(command.description + "\n", style=detail_style)
        text.append("↑↓ 选择 · Tab 补全 · Enter 执行 · Esc 关闭", style=DIM)
        return text


class CommandResult(Static):
    """命令的本地结果；不会写进 JSONL，也不会发给模型。"""

    def __init__(
        self,
        title: str,
        body: str,
        *,
        error: bool = False,
        warning: bool = False,
    ) -> None:
        tone = ERROR if error else WARN if warning else TOOL
        text = Text.assemble(
            ("提醒  ·  " if warning else "命令  ·  ", f"bold {tone}"),
            (title, f"bold {PAPER}"),
            ("\n", DIM),
            (body, ERROR if error else WARN if warning else MUTED),
        )
        super().__init__(text, classes="command-result")
