"""TUI 的 Apple 深色语义配色。

与 desktop 共享「Apple 设计语言」的视觉方向：深灰画布、系统蓝强调、
语义状态色（红/橙/青/绿各归其位）。终端里没有 SF 字体和毛玻璃，
能带走的是色彩体系——具体色值分别维护，Textual 的语义色、光标、
选区和滚动条都从这里落。常量名沿用拆分前的一套（PAPER=前景、
INK=画布……），改的是值不是词汇表，chat/ 包里的 CSS 引用零改动。
"""
from textual.app import App
from textual.theme import Theme

# Apple 深色模式语义色（macOS HIG）：
#   画布 1C1C1E（systemBackground）、浮层 2C2C2E（tertiarySystemBackground）
#   文本 F5F5F7（label）、分隔线 3A3A3C（separator）
#   强调 0A84FF（systemBlue）、红 FF453A / 橙 FF9F0A / 青 64D2FF / 绿 30D158
INK = "#1c1c1e"
PANEL = "#242426"
PANEL_RAISED = "#2c2c2e"
PAPER = "#f5f5f7"
MUTED = "#98989d"
DIM = "#6e6e73"
SIGNAL = "#0a84ff"
TOOL = "#64d2ff"
WARN = "#ff9f0a"
ERROR = "#ff453a"
LINE = "#3a3a3c"
SUCCESS = "#30d158"


AGENTLEARN_THEME = Theme(
    name="agentlearn",
    primary=SIGNAL,
    secondary=TOOL,
    warning=WARN,
    error=ERROR,
    success=SUCCESS,
    accent=SIGNAL,
    foreground=PAPER,
    background=INK,
    surface=PANEL,
    panel=PANEL_RAISED,
    dark=True,
    variables={
        "border": LINE,
        "border-blurred": LINE,
        "block-cursor-background": SIGNAL,
        "block-cursor-foreground": INK,
        "block-cursor-blurred-background": LINE,
        "block-cursor-blurred-foreground": PAPER,
        "input-cursor-background": SIGNAL,
        "input-cursor-foreground": INK,
        "input-selection-background": SIGNAL,
        "input-selection-foreground": INK,
        "scrollbar": LINE,
        "scrollbar-active": SIGNAL,
        "scrollbar-hover": SIGNAL,
        "scrollbar-background": INK,
        "scrollbar-background-active": PANEL,
        "scrollbar-background-hover": PANEL,
        "scrollbar-corner-color": PANEL,
        "text-primary": SIGNAL,
        "text-secondary": MUTED,
        "text-muted": MUTED,
        "text-disabled": DIM,
        "text-success": SUCCESS,
        "text-warning": WARN,
        "text-error": ERROR,
    },
)


def apply_agentlearn_theme(app: App) -> None:
    """注册并启用 TUI 的语义主题，压掉 Textual 默认蓝色状态。"""
    app.register_theme(AGENTLEARN_THEME)
    app.theme = AGENTLEARN_THEME.name
