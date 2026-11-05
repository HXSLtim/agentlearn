"""TUI 的「墨与琥珀」语义配色（与 desktop 同一套设计语言，D30）。

两个界面各画各的，但词汇表一致：墨黑带暖调的画布、纸色文本、
琥珀强调（批注笔）、红/赭/青/绿各归其位。终端里没有字体和毛玻璃，
能带走的是色彩体系——Textual 的语义色、光标、选区和滚动条都从这里落。
常量名沿用拆分前的一套（PAPER=前景、INK=画布……），改的是值不是
词汇表，chat/ 包里的 CSS 引用零改动。
"""
from textual.app import App
from textual.theme import Theme

# 墨与琥珀深色（终端默认深底）：
#   画布 151412（墨黑带暖）、浮层 26231E（抬升一档）
#   文本 ECE7DD（纸色）、分隔线 35312A
#   强调 EDA03C（琥珀批注）、红 FF6A58 / 赭 EDA03C / 青 7CCFE0 / 绿 4CC38A
INK = "#151412"
PANEL = "#1c1a17"
PANEL_RAISED = "#26231e"
PAPER = "#ece7dd"
MUTED = "#948d7f"
DIM = "#6f695e"
SIGNAL = "#eda03c"
TOOL = "#7ccfe0"
WARN = "#f0942e"
ERROR = "#ff6a58"
LINE = "#35312a"
SUCCESS = "#4cc38a"


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
