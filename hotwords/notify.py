"""告警通知：把解析失效/漂移推到可见的地方（默认写日志 + 可选 Agent Mail）。

设计原则：日志没人看，所以告警必须"落到一个会被看到的地方"。
V1 默认只打日志 + 落 alert 表；需要外发时注入 notifier。
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def log_notifier(kind: str, source: str, detail: str) -> None:
    logger.warning("【告警·%s】%s | %s", kind, source, detail)


def make_mail_notifier(to_addr: str, subject_prefix: str = "[热词采集]"):
    """返回一个调用 agent-mail CLI 的 notifier（可选）。

    注意：具体 CLI 命令需按你的 agent-mail 环境调整；
    这里给出的是"留一个外发口子"的形态，不是硬编码依赖。
    """

    def _notify(kind: str, source: str, detail: str) -> None:
        subject = f"{subject_prefix}{kind} · {source}"
        # 示例：用 agent-mail 发信（需本机已配置）
        try:
            subprocess.run(
                ["agent-mail", "send", "--to", to_addr, "--subject", subject, "--body", detail],
                check=False,
                timeout=20,
            )
        except FileNotFoundError:
            logger.warning("未找到 agent-mail CLI，告警降级为日志")
            log_notifier(kind, source, detail)

    return _notify


def build_notifier(kind: str = "log", **kwargs):
    if kind == "mail":
        return make_mail_notifier(kwargs.get("to_addr", ""))
    return log_notifier
