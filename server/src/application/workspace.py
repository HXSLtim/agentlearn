"""只读读取 Git 工作区状态。"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from pathlib import Path

MAX_PATCH_BYTES = 1_000_000
# 一次面板最多展开这么多补丁。新会话会把整个仓库的未跟踪文件算进来
# （例如 node_modules）；不设上限的话每个文件一次 git diff，界面会一直停在加载。
MAX_TOTAL_PATCH_BYTES = 1_000_000
MAX_PATH_COMPLETIONS = 100
_COMMAND_TIMEOUT_SECONDS = 8
_TOTAL_TIMEOUT_SECONDS = 20
_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)

class WorkspaceError(RuntimeError):
    """Git 工作区不可读，或 Git 查询失败。"""


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    # status 默认可顺手刷新 index；只读面板连这个可选写入也关掉。
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    env.pop("GIT_EXTERNAL_DIFF", None)
    env.pop("GIT_DIFF_OPTS", None)
    return env


def _kill_git(proc: subprocess.Popen[bytes]) -> None:
    """超时后连同孙进程一起杀掉。

    只杀直接子进程不够：git 拉起的钩子或 fsmonitor 若继承了管道，
    communicate 会一直等 EOF，调用方就停在「正在读取」。
    """
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def _run_git(root: Path, args: list[str], timeout: float = _COMMAND_TIMEOUT_SECONDS) -> bytes:
    # stdin 必须关掉。继承终端的话，git 或其钩子会把 Esc 读走，
    # 面板就关不掉。新会话另起进程，这是第一次碰 git，最容易踩中。
    try:
        proc = subprocess.Popen(
            ["git", "-C", str(root), "--no-pager", *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_env(),
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise WorkspaceError("没有找到 git 命令。") from exc
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_git(proc)
        raise WorkspaceError("读取 Git 工作区超时。") from exc
    # `git diff --no-index` 用 1 表示“有差异”，不是查询失败。
    allowed = (0, 1) if "--no-index" in args else (0,)
    if proc.returncode not in allowed:
        detail = (stderr or b"").decode("utf-8", "replace").strip()
        raise WorkspaceError(detail or f"git 退出码 {proc.returncode}")
    return stdout or b""


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


def _patch(root: Path, path: str, untracked: bool) -> bytes:
    if untracked:
        return _run_git(
            root,
            [
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--unified=3",
                "--no-index",
                "--",
                "/dev/null",
                path,
            ],
        )
    return _run_git(
        root,
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--unified=3",
            "HEAD",
            "--",
            path,
        ],
    )


def _parse_patch(patch: bytes) -> dict:
    truncated = len(patch) > MAX_PATCH_BYTES
    text = patch[:MAX_PATCH_BYTES].decode("utf-8", "replace")
    binary = any(
        line.startswith("Binary files ") or line.startswith("GIT binary patch")
        for line in text.splitlines()
    )
    hunks: list[dict] = []
    additions = 0
    deletions = 0
    current: dict | None = None
    old_line = 0
    new_line = 0
    for raw_line in text.splitlines():
        match = _HUNK_RE.match(raw_line)
        if match:
            old_line = int(match.group("old_start"))
            new_line = int(match.group("new_start"))
            current = {
                "header": raw_line,
                "old_start": old_line,
                "old_count": int(match.group("old_count") or 1),
                "new_start": new_line,
                "new_count": int(match.group("new_count") or 1),
                "lines": [],
            }
            hunks.append(current)
            continue
        if current is None:
            continue
        marker = raw_line[:1]
        content = raw_line[1:]
        line: dict | None = None
        if marker == " ":
            line = {
                "kind": "context",
                "old_line": old_line,
                "new_line": new_line,
                "text": content,
            }
            old_line += 1
            new_line += 1
        elif marker == "-":
            line = {
                "kind": "delete",
                "old_line": old_line,
                "new_line": None,
                "text": content,
            }
            old_line += 1
            deletions += 1
        elif marker == "+":
            line = {
                "kind": "add",
                "old_line": None,
                "new_line": new_line,
                "text": content,
            }
            new_line += 1
            additions += 1
        elif marker == "\\":
            line = {
                "kind": "meta",
                "old_line": None,
                "new_line": None,
                "text": raw_line,
            }
        if line is not None:
            current["lines"].append(line)
    return {
        "binary": binary,
        "truncated": truncated,
        "hunks": hunks,
        "additions": additions,
        "deletions": deletions,
    }


def changed_files(
    include_untracked: bool = False, start: Path | None = None
) -> dict:
    """列出工作区变更文件；不 checkout、不 add、不改索引。

    start 是从哪个目录找 Git 根（会话的工作目录）；不给就是进程 cwd。
    """
    root = find_repository_root(start)
    raw = _run_git(
        root,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
    )
    fields = raw.split(b"\0")
    entries: list[dict] = []
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
        entries.append(
            {"path": path, "old_path": old_path, "status": status}
        )
    # 已跟踪的改动优先展开。未跟踪目录（node_modules）否则会占满预算，
    # 把用户真正改过的文件挤掉。输出仍按 git status 的原顺序。
    deadline = time.monotonic() + _TOTAL_TIMEOUT_SECONDS
    used = 0
    stop = False
    partial = False
    parsed_by_id: dict[int, dict] = {}
    ordered = sorted(entries, key=lambda entry: entry["status"] == "untracked")
    for entry in ordered:
        timed_out = time.monotonic() >= deadline
        if stop or timed_out or used >= MAX_TOTAL_PATCH_BYTES:
            stop = True
            partial = partial or timed_out
            parsed_by_id[id(entry)] = _omitted_file(entry)
            continue
        try:
            patch = _patch(root, entry["path"], entry["status"] == "untracked")
        except WorkspaceError:
            stop = True
            partial = True
            parsed_by_id[id(entry)] = _omitted_file(entry)
            continue
        used += len(patch)
        parsed_by_id[id(entry)] = {
            **entry,
            **_cap_diff_lines(_parse_patch(patch)),
            "omitted": False,
        }
    files = [parsed_by_id[id(entry)] for entry in entries]
    return {
        "root": str(root),
        "files": files,
        "untracked_count": untracked_count,
        "include_untracked": include_untracked,
        "partial": partial,
        "omitted_count": sum(1 for file in files if file["omitted"]),
    }


_MAX_DIFF_LINES = 400


def _cap_diff_lines(parsed: dict) -> dict:
    """单个文件只把前 400 行交给界面。再多就会在主线程上撑死布局。"""
    kept: list[dict] = []
    count = 0
    for hunk in parsed["hunks"]:
        if count >= _MAX_DIFF_LINES:
            parsed["truncated"] = True
            break
        lines = hunk["lines"]
        room = _MAX_DIFF_LINES - count
        if len(lines) > room:
            kept.append({**hunk, "lines": lines[:room]})
            count += room
            parsed["truncated"] = True
            break
        kept.append(hunk)
        count += len(lines)
    parsed["hunks"] = kept
    return parsed


def _omitted_file(entry: dict) -> dict:
    """预算或超时时只留路径，不再为这个文件启动 git diff。"""
    return {
        **entry,
        "binary": False,
        "truncated": False,
        "hunks": [],
        "additions": 0,
        "deletions": 0,
        "omitted": True,
    }


def complete_paths(
    prefix: str, limit: int = 50, start: Path | None = None
) -> dict:
    """按前缀补全 Git 工作区路径；只取路径名，不读文件内容。"""
    root = find_repository_root(start)
    raw = _run_git(
        root,
        [
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
    )
    paths = []
    for raw_path in raw.split(b"\0"):
        path = raw_path.decode("utf-8", "surrogateescape")
        if path and path.startswith(prefix):
            paths.append(path)
    paths.sort()
    bounded_limit = max(1, min(limit, MAX_PATH_COMPLETIONS))
    return {
        "root": str(root),
        "prefix": prefix,
        "paths": paths[:bounded_limit],
        "total": len(paths),
        "limited": len(paths) > bounded_limit,
    }
