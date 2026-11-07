"""终端显示用的数字格式化：时长、缓存命中率。

放在 tui 包根层而不是 chat/ 里：trace.py（同级模块）也要用，
而 import chat.* 会执行 chat/__init__.py 把整包拉起来，
反过来又 import trace —— 直接成环。这里零依赖，谁都能安全引用。
WebUI 侧对应的是 format.ts 的 formatDuration / cacheHitPercent，规则一致。
"""


def format_duration(ms) -> str:
    """毫秒是内部单位，落屏要人话：<1 秒按毫秒，<1 分钟按秒，再往上给分/时。

    None（字段缺失）和负数统一「—」，调用点不用各写一遍判空。
    """
    if ms is None or ms < 0:
        return "—"
    if ms < 1:
        return "<1ms"
    if ms < 1000:
        return f"{round(ms)}ms"
    if ms < 60_000:
        return f"{ms / 1000:.1f} 秒"
    if ms < 3_600_000:
        minutes = int(ms // 60_000)
        seconds = round((ms % 60_000) / 1000)
        return f"{minutes} 分 {seconds:02d} 秒"
    hours = int(ms // 3_600_000)
    minutes = round((ms % 3_600_000) / 60_000)
    return f"{hours} 时 {minutes:02d} 分"


def cache_hit_percent(per_mille) -> str:
    """服务端 cache_hit_ratio 是千分比；落屏统一百分数（457 → 45.7%）。"""
    if not isinstance(per_mille, (int, float)):
        return "—"
    return f"{per_mille / 10:.1f}%"


# 下面两张表对齐 WebUI format.ts 的 TRACE_STATUS_LABELS / DIFF_STATUS_LABELS。
# 放这一层（而不是 chat/labels.py）是因为独立的 trace 检查器（trace.py，
# 与 chat 包同级）也要用——import chat.* 会把整包 __init__ 拉起来反向
# 依赖 trace，成环；formatting 零依赖，谁都能安全引用。

SPAN_STATUS_LABELS = {
    "ok": "正常",
    "error": "错误",
    "running": "运行中",
    "cancelled": "已取消",
}

DIFF_STATUS_LABELS = {
    "modified": "已修改",
    "added": "新增",
    "deleted": "删除",
    "renamed": "重命名",
    "copied": "复制",
    "untracked": "未跟踪",
    "typechange": "类型变更",
    "conflicted": "冲突",
}


def span_status_label(status: str) -> str:
    return SPAN_STATUS_LABELS.get(status, status)


def diff_status_label(status: str) -> str:
    return DIFF_STATUS_LABELS.get(status, status.upper())
