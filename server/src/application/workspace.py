"""只读读取 Git 工作区状态。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


class WorkspaceError(RuntimeError):
    """Git 工作区不可读，或 Git 查询失败。"""


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    # status 默认可顺手刷新 index；只读面板连这个可选写入也关掉。
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run_git(root: Path, args: list[str]) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "--no-pager", *args],
            capture_output=True,
            check=False,
            env=_git_env(),
            timeout=8,
        )
    except FileNotFoundError as exc:
        raise WorkspaceError("没有找到 git 命令。") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError("读取 Git 工作区超时。") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise WorkspaceError(detail or f"git 退出码 {completed.returncode}")
    return completed.stdout


def find_repository_root(start: Path | None = None) -> Path:
    """返回 start 所在 Git 根；不是仓库时抛 WorkspaceError。"""
    origin = (start or Path.cwd()).resolve()
    raw = _run_git(origin, ["rev-parse", "--show-toplevel"])
    text = raw.decode("utf-8", "surrogateescape").strip()
    if not text:
        raise WorkspaceError("当前目录不在 Git 工作区里。")
    return Path(text)


def _status_label(xy: str) -> str:
    if "R" in xy:
        return "renamed"
    if "C" in xy:
        return "copied"
    if "D" in xy:
        return "deleted"
    if "A" in xy:
        return "added"
    if "U" in xy or xy in ("AA", "DD"):
        return "conflicted"
    return "modified"


def changed_files(include_untracked: bool = False) -> dict:
    """列出工作区变更文件；不 checkout、不 add、不改索引。"""
    root = find_repository_root()
    raw = _run_git(
        root,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
    )
    fields = raw.split(b"\0")
    files: list[dict] = []
    untracked_count = 0
    index = 0
    while index < len(fields):
        token = fields[index]
        index += 1
        if not token or len(token) < 4 or token[2:3] != b" ":
            continue
        xy = token[:2].decode("ascii", "replace")
        path = token[3:].decode("utf-8", "surrogateescape")
        old_path = None
        if "R" in xy or "C" in xy:
            if index >= len(fields):
                break
            old_path = fields[index].decode("utf-8", "surrogateescape")
            index += 1
        if xy == "??":
            untracked_count += 1
            if not include_untracked:
                continue
            status = "untracked"
        else:
            status = _status_label(xy)
        files.append({"path": path, "old_path": old_path, "status": status})
    return {
        "root": str(root),
        "files": files,
        "untracked_count": untracked_count,
        "include_untracked": include_untracked,
    }
