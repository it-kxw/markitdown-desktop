# SPDX-License-Identifier: MIT
"""控制台输出的编码兜底：中文在任何代码页下都不该把程序弄崩。

为什么需要（CI 实测，不是猜测）：GitHub 的 `windows-latest` runner 是 en-US，
`sys.stdout` 的编码是 **cp1252**，脚本里 `print("已生成 9 个夹具")` 直接抛
`UnicodeEncodeError: 'charmap' codec can't encode characters in position 0-2`，
脚本以退出码 1 收场（2026-10-14 CI 真事，日志见 `docs/p2-verification.md`）。

这里只给标准流加 `errors="replace"`，**不动 encoding**：
GBK 控制台照旧显示中文，cp1252 控制台上装不下的字变成 `?`，
但不会再因为一句日志印不出来就让整个流程失败。

@author 康小汪【kxw】
@date 2026-10-14
"""

from __future__ import annotations

import sys
from typing import TextIO

__all__ = ["make_output_safe"]


def _patch_stream(stream: TextIO | None) -> None:
    """给单个标准流加编码兜底（不支持或已被关闭时静默跳过）。

    Args:
        stream: `sys.stdout` / `sys.stderr`；窗口化冻结包里可能是 None。
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(errors="replace")
    except (OSError, ValueError):
        # 流可能已被替换或关闭（例如测试里的捕获流），不能因此中断主流程
        pass


def make_output_safe() -> None:
    """让标准输出/标准错误遇到装不下的字符时替换而不是抛异常。

    幂等：重复调用没有副作用。只改错误策略，不改编码。
    """
    _patch_stream(sys.stdout)
    _patch_stream(sys.stderr)
