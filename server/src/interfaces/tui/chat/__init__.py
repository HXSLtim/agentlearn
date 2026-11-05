"""Textual 终端工作台（包门面）。

只重导出，不放逻辑；对外仍可 `from interfaces.tui.chat import X`。
布局：screen 装配 App，composer/palette/cards 是时间线与输入的部件，
modals 是各弹层，panels 是全屏只读检视器。
"""
from .composer import ATTACH_MEDIA_TYPES, PromptComposer, QueuedPrompt
from .palette import (
    COMMANDS,
    COMMANDS_BY_NAME,
    EFFORT_CYCLE,
    CommandPalette,
    CommandResult,
    SlashCommand,
)
from .cards import (
    AssistantMessage,
    EmptyState,
    ReasoningPanel,
    RetryErrorCard,
    ToolCard,
    UserMessage,
    _user_text,
)
from .modals import (
    ApprovalChoice,
    ApprovalScreen,
    ConversationSearchScreen,
    HistorySearchScreen,
    PlanScreen,
    ResumeScreen,
    SessionSummary,
    SettingsScreen,
    _format_session_time,
)
from .panels import TraceScreen, WorkspaceDiffScreen
from .screen import TRANSCRIPT_MODES, AgentLearnTUI, run_chat

__all__ = [
    "ATTACH_MEDIA_TYPES", "COMMANDS", "COMMANDS_BY_NAME", "EFFORT_CYCLE",
    "AgentLearnTUI", "ApprovalChoice", "ApprovalScreen", "AssistantMessage",
    "CommandPalette", "CommandResult", "ConversationSearchScreen", "EmptyState",
    "HistorySearchScreen", "PlanScreen", "PromptComposer", "QueuedPrompt",
    "ReasoningPanel", "ResumeScreen", "RetryErrorCard", "SessionSummary",
    "SettingsScreen", "SlashCommand", "ToolCard", "TraceScreen",
    "TRANSCRIPT_MODES", "UserMessage", "WorkspaceDiffScreen", "_format_session_time",
    "_user_text", "run_chat",
]
