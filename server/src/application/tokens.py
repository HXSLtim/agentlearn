"""Token 估算。估算只服务压缩决策，不承诺精确计数。"""

from __future__ import annotations

import re


_CJK = re.compile(r"[一-鿿]")


def estimate_tokens(text: str) -> int:
    """中文按 1 token、其它文字按 4 字符折 1 token 的保守估算。"""
    cjk = len(_CJK.findall(text))
    return cjk + (len(text) - cjk) // 4
