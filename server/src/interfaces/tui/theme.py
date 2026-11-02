"""TUI 的黑绿控制台配色。

与 desktop 共享“近黑画布、酸性绿信号、硬边框控制台”的视觉方向；
具体色值分别维护，Textual 的语义色、光标、选区和滚动条都从这里落。
"""
from textual.app import App
from textual.theme import Theme

INK = "#050705"
PANEL = "#0b0f0b"
PANEL_RAISED = "#101710"
PAPER = "#e2eadb"
MUTED = "#87947f"
DIM = "#68765f"
SIGNAL = "#b8ff4a"
TOOL = "#8ee85a"
WARN = "#ffc857"
ERROR = "#ff604d"
LINE = "#34472d"


AGENTLEARN_THEME = Theme(
    name="agentlearn",
    primary=SIGNAL,
    secondary=TOOL,
    warning=WARN,
    error=ERROR,
    success=TOOL,
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
        "scrollbar-background": PANEL,
        "scrollbar-background-active": PANEL,
        "scrollbar-background-hover": PANEL,
        "scrollbar-corner-color": PANEL,
        "text-primary": SIGNAL,
        "text-secondary": TOOL,
        "text-muted": MUTED,
        "text-disabled": DIM,
        "text-success": TOOL,
        "text-warning": WARN,
        "text-error": ERROR,
    },
)


def apply_agentlearn_theme(app: App) -> None:
    """注册并启用 TUI 的语义主题，压掉 Textual 默认蓝色状态。"""
    app.register_theme(AGENTLEARN_THEME)
    app.theme = AGENTLEARN_THEME.name
