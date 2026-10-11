"""平台徽标的枚举 <-> 中文名唯一出处。

为什么要有这个模块：徽标枚举既要在解析层（数字 -> 中文展示名）用，
也要在配置/日志层（把 sources.yaml 里的排除项渲染成可读串）用。
放在任一侧都会造成另一侧反向 import，所以单列出来当公共词汇表。

============================================================
抖音 label：不是位掩码，是【枚举】
============================================================
这里曾经猜过是位掩码（因为出现值恰好是 1/3/5/8/9/11/16/17 这种
"看着像 2 的幂组合"的稀疏集合）。**猜错了**。

真正的证据来自数据本身：每条带 label 的条目都附了一个徽标图 URL
    https://lf3-static.bytednsdoc.com/obj/.../hotspot_detail_page/<label>.png
把它下载放大后肉眼一看，语义无可争议（8 张图，见 README「坑 21」）：

    label 1  -> 新      label 9  -> 挑战
    label 3  -> 热      label 11 -> 剧集
    label 5  -> 首发    label 16 -> 辟谣
    label 8  -> 独家    label 17 -> 热议

那些"缺失"的 2/4/6/7/10/12… 只是没用到的枚举 id，不是位组合。
教训：“数值长得像位掩码”不是证据，去把图下下来看才是。
============================================================
"""
from __future__ import annotations

DOUYIN_BADGES: dict[int, str] = {
    1: "新",
    3: "热",
    5: "首发",
    8: "独家",
    9: "挑战",
    11: "剧集",
    16: "辟谣",
    17: "热议",
}

# 「官方话题」类徽标：这些条目是平台活动话题，会连续数周驻留在榜上。
# 详见 sources.yaml 里 douyin_hot.track_exclude_labels 的说明。
DOUYIN_TOPIC_BADGES: frozenset[int] = frozenset({9})


def douyin_badge(raw: object) -> str | None:
    """数字 label -> 中文徽标。未知值返回 None —— 绝不猜。"""
    try:
        return DOUYIN_BADGES.get(int(raw))
    except (TypeError, ValueError):
        return None


def describe_douyin_label(label_id: int) -> str:
    """把 label_id 渲染成 `9=挑战`，供日志与 CLI 回显排除项。"""
    name = DOUYIN_BADGES.get(int(label_id))
    return f"{int(label_id)}={name}" if name else str(label_id)
