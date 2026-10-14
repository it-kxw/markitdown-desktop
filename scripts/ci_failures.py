# SPDX-License-Identifier: MIT
"""把 pytest 的 junitxml 失败项打成 GitHub Actions 的 `::error::` 注解。

为什么需要：CI 红了以后，Actions 的**日志 zip** 存在
`pipelines.actions.githubusercontent.com`（国内不可达：实测 A 记录 13.107.42.16 连不通），
但 **annotation API 是通的**（`GET /repos/{owner}/{repo}/check-runs/{id}/annotations`）。
所以让失败文本先变成注解，远端就能读到原因，不必再靠人工贴日志。

不解析 pytest 的 stdout：`-q` 的汇总行会被管道吞掉，junitxml 才是机器可读的那份。
`<failure>` 与 `<error>` 都算失败（前者是断言失败，后者是测试体本身抛异常）。

用法::

    python scripts/ci_failures.py pytest-report.xml

@author 康小汪【kxw】
@date 2026-10-14
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

#: 单条注解的消息上限（GitHub 注解有长度限制，超出会被截断）
MAX_MESSAGE = 600

#: junitxml 里算作失败的节点名
FAILURE_KINDS = ("failure", "error")


def _first_line(text: str | None) -> str:
    """取一段文本的首个非空行，并按注解长度上限截断。

    Args:
        text: junitxml 节点的 `message` 属性或文本正文。

    Returns:
        str: 首个非空行（最多 `MAX_MESSAGE` 字符）；没有内容时返回占位文案。
    """
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:MAX_MESSAGE]
    return "(无输出)"


def collect_failures(report_path: Path) -> list[tuple[str, str]]:
    """从 junitxml 里收集失败与出错的用例。

    Args:
        report_path: junitxml 文件路径。

    Returns:
        list[tuple[str, str]]: `(用例名, 首行信息)`，按文件中的出现顺序。
    """
    root = ET.parse(report_path).getroot()
    found: list[tuple[str, str]] = []
    for case in root.iter("testcase"):
        for kind in FAILURE_KINDS:
            node = case.find(kind)
            if node is None:
                continue
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            found.append((name, _first_line(node.get("message") or node.text)))
    return found


def _emit(report_path: Path) -> None:
    """打印注解；文件缺失或解析失败时降级为 notice，不改变退出码。"""
    if not report_path.is_file():
        print(f"::notice::找不到 {report_path}，无法汇总失败项")
        return
    try:
        failures = collect_failures(report_path)
    except ET.ParseError as exc:
        print(f"::notice::junitxml 解析失败，无法汇总失败项：{exc}")
        return
    if not failures:
        print("::notice::junitxml 里没有失败用例")
        return
    for name, message in failures:
        # 用例名里带 `::`，不能放进 title（会破坏注解语法），一律塞进消息正文
        print(f"::error::{name} | {message}")
    print(f"::notice::失败用例合计 {len(failures)} 条")


def main(argv: list[str] | None = None) -> int:
    """脚本入口：永远返回 0（这是汇总步骤，不能改变已经失败的结论）。

    Args:
        argv: 命令行参数，缺省取 `sys.argv[1:]`。

    Returns:
        int: 0 表示汇总步骤本身正常结束。
    """
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("::error::用法：ci_failures.py <junitxml>")
        return 0
    _emit(Path(args[0]))
    return 0


if __name__ == "__main__":
    from markitdown_desktop.console import make_output_safe

    make_output_safe()
    raise SystemExit(main())
