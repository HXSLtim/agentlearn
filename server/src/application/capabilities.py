"""运行时能力快照：TUI /status 与 desktop 能力面板共用。"""

from __future__ import annotations

import sys
from pathlib import Path

from application.tools import (
    REGISTRY,
    SANDBOX,
    _load_skills,
    _mcp_manager,
    all_tools,
)


def capability_snapshot(provider: str, model_id: str, effort: str = "") -> dict:
    """只读收集工具、MCP、环境与版本事实；不主动启动 MCP 进程。"""
    builtin = {tool.name for tool, _ in REGISTRY.values()}
    mcp_tools = _mcp_manager().health()
    skills = [
        {"name": name, "description": description}
        for name, description, _ in _load_skills()
    ]
    return {
        "tools": [
            {
                "name": tool.name,
                "source": (
                    "builtin"
                    if tool.name in builtin
                    else "mcp"
                    if "__" in tool.name
                    else "skill"
                ),
                "description": tool.description.split("\n")[0][:120],
            }
            for tool in all_tools()
        ],
        "mcp": mcp_tools,
        "skills": skills,
        "environment": {
            "cwd": str(Path.cwd()),
            "sandbox": str(SANDBOX),
            "sandbox_boundary": (
                "工作目录起跑点，不是安全边界：绝对路径和 ../ 都能绕过"
                "（D17 已接受的取舍，容器化前不放宽）"
            ),
            "memory_file": str(SANDBOX / "memory.md"),
        },
        "runtime": {
            "python": sys.version.split()[0],
            "provider": provider,
            "model": model_id,
            "effort": effort,
            "triggers": _trigger_count(),
        },
    }


def _trigger_count() -> int:
    from infrastructure.config import load_triggers

    return len(load_triggers())
