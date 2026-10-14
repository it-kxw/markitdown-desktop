# SPDX-License-Identifier: MIT
"""把 pytest 的 junitxml / 冻结包自测 JSON 的失败项打成 GitHub Actions 注解。

为什么需要：CI 红了以后，Actions 的**日志 zip** 存在
`pipelines.actions.githubusercontent.com`（国内不可达：实测 A 记录 13.107.42.16 连不通），
但 **annotation API 是通的**（`GET /repos/{owner}/{repo}/check-runs/{id}/annotations`）。
所以让失败文本先变成注解，远端就能读到原因，不必再靠人工贴日志。

两种模式::

    python scripts/ci_failures.py pytest-report.xml        # 单元测试失败
    python scripts/ci_failures.py --selftest ci-selftest.json  # 冻结包自测失败

不解析 pytest 的 stdout：`-q` 的汇总行会被管道吞掉，junitxml 才是机器可读的那份。
`<failure>` 与 `<error>` 都算失败（前者是断言失败，后者是测试体本身抛异常）。

@author 康小汪【kxw】
@date 2026-10-14
"""

from __future__ import annotations

import argparse
import json
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
        text: junitxml 节点的 `message` 属性、或自测 JSON 里的 `error`/`warning`。

    Returns:
        str: 首个非空行（最多 `MAX_MESSAGE` 字符）；没有内容时返回占位文案。
    """
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:MAX_MESSAGE]
    return "(无输出)"


def _emit(name: str, message: str) -> None:
    """打一条 `::error::`；用例名里可能带 `::`，所以只放进消息正文（title 会破坏语法）。"""
    print(f"::error::{name} | {message}")


# ---------------------------------------------------------------- 单元测试模式


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


def _emit_junit(report_path: Path) -> None:
    """打印单元测试失败注解；文件缺失或解析失败时降级为 notice。"""
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
        _emit(name, message)
    print(f"::notice::失败用例合计 {len(failures)} 条")


# ---------------------------------------------------------------- 冻结包自测模式


def collect_selftest_failures(payload: dict) -> list[tuple[str, str]]:
    """从 `--selftest --json` 的报告里收集失败夹具。

    Args:
        payload: 已解析的自测报告（顶层 `results` 为逐文件结果）。

    Returns:
        list[tuple[str, str]]: `(文件路径, 首行原因)`；`ok` 为真的行不进结果。
    """
    found: list[tuple[str, str]] = []
    for row in payload.get("results") or []:
        if row.get("ok"):
            continue
        name = str(row.get("file", "?"))
        found.append((name, _first_line(row.get("error") or row.get("warning"))))
    return found


def _emit_selftest(report_path: Path) -> None:
    """打印冻结包自测的失败注解；报告缺失或损坏时降级为 notice。"""
    if not report_path.is_file():
        print(f"::notice::找不到 {report_path}（自测步骤可能被跳过）")
        return
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"::notice::自测报告解析失败：{exc}")
        return
    rows = payload.get("results") or []
    failures = collect_selftest_failures(payload)
    for name, message in failures:
        _emit(name, message)
    print(
        f"::notice::自测 {len(rows)} 个夹具：{len(failures)} 个失败，"
        f"顶层 ok={payload.get('ok')}，总耗时 {payload.get('total_s')} s"
    )


# ---------------------------------------------------------------- 入口


def _build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器：默认 junitxml 模式，`--selftest` 切到自测报告模式。"""
    parser = argparse.ArgumentParser(description="把失败项打成 GitHub Actions 注解")
    parser.add_argument("report", help="junitxml 或自测 JSON 的路径")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="把参数当成 `--selftest --json` 的报告（默认当成 junitxml）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """脚本入口：永远返回 0（这是汇总步骤，不能改变已经失败的结论）。

    Args:
        argv: 命令行参数，缺省取 `sys.argv[1:]`。

    Returns:
        int: 0 表示汇总步骤本身正常结束。
    """
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    report_path = Path(args.report)
    if args.selftest:
        _emit_selftest(report_path)
    else:
        _emit_junit(report_path)
    return 0


if __name__ == "__main__":
    from markitdown_desktop.console import make_output_safe

    make_output_safe()
    raise SystemExit(main())
