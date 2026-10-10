"""热度值归一化。

为什么单独成模块：各源热度格式不统一——
    微博: "1.2亿" / "8900万" / 数字
    百度: 纯数字（如 100909）
    抖音/B站: 数字或空
必须：
  1) 保留原始字符串（存 hot_value）
  2) 另存归一化数值（存 hot_value_num），且规则版本化（norm_version）
    —— 规则一改，历史数据不可比，所以要留版本号
"""
from __future__ import annotations

import re

NORM_VERSION = "v1"

_UNIT = {
    "亿": 100_000_000,
    "万": 10_000,
    "w": 10_000,
    "W": 10_000,
    "k": 1_000,
    "K": 1_000,
}

_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([亿万千wWkK]?)")


def normalize_hot_value(raw: str | int | float | None) -> int | None:
    """把各种热度写法归一为整数。无法解析时返回 None（不抛异常）。

    注意：dict/list 等复合类型一律返回 None —— 解包是 Parser 层的职责。
    若不拒绝，str({'value':'1'}) 里的数字会被正则误匹配，静默产出错误数值。

    >>> normalize_hot_value("1.2亿")
    120000000
    >>> normalize_hot_value("8900万")
    89000000
    >>> normalize_hot_value("100909")
    100909
    >>> normalize_hot_value("")
    >>> normalize_hot_value(None)
    >>> normalize_hot_value({"value": "1"}) is None
    True
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        try:
            return int(raw)
        except (TypeError, ValueError, OverflowError):
            return None
    # 复合类型：拒绝，交由 Parser 解包
    if not isinstance(raw, str):
        return None

    s = raw.strip().replace(",", "").replace(" ", "")
    if not s:
        return None

    m = _NUM_RE.fullmatch(s)   # fullmatch：整串必须是一个数值+单位，避免误匹配
    if not m:
        return None

    num = float(m.group(1))
    unit = m.group(2)
    factor = _UNIT.get(unit, 1)
    try:
        return int(num * factor)
    except (TypeError, ValueError, OverflowError):
        return None


def normalize_word(word: str) -> str:
    """词条归一化，仅用于生成去重 hash（不改变展示原文）。

    处理：去首尾空白、全角转半角、去中间空白、统一大小写。
    """
    if not word:
        return ""
    # 全角 -> 半角
    out = []
    for ch in word:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    s = "".join(out)
    s = re.sub(r"\s+", "", s)
    return s.strip().casefold()
