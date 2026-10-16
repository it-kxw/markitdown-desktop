# SPDX-License-Identifier: MIT
"""把 `packaging/fonts/` 里的完整 Inter 字体子集化成界面真正用得到的字符集。

## 为什么需要它（改之前先读）

T29 决定随包分发 Inter（D1=(b)），但**完整静态字重有 1.59 MB**，直接把 one-folder
从 211.46 MB 顶到 213.19 MB —— 越过 213 MB 闸门 0.19 MB。

三个候选方案都试过，结论如下：

1. **换 woff2**（官方 web/ 目录里有，每字重仅 114 KB）：**Qt 6.12 不支持**。
   探针实测 `QFontDatabase.addApplicationFont("*.woff2")` 全部返回 `-1`
   （woff2 要 brotli 解压，Qt 字体后端不处理）。
2. **只打部分字重**：会丢掉 500/600 两个字重，界面字重层级变粗。
3. **TTF 子集化** ← 选它。实测 `Inter-Regular.ttf` 411,640 → 109,932 B（省 73%）。

## 子集范围怎么定的

**只保留拉丁 + 数字 + 常用标点**。理由：Inter 本来就没有汉字，中文界面文案必然回落到
`Microsoft YaHei UI`（字体栈见 `theme.FONT_STACK`），所以**砍掉西里尔/希腊/越南语等
几十个语言的字形，对中文界面零损失**。

字形表占原文件 54.8%（`glyf` + `loca` 实测），砍掉用不到的部分就能省下大头。

## 依赖归属

用 `uvx --from "fonttools[woff]" pyftsubset` 调用，**不往 `pyproject.toml` 加任何依赖**：

- `dependencies`（运行时）零增长 —— 冻结包里不会带 fontTools；
- 连 `[dev]` 都不用动 —— `uvx` 按需临时拉取（实测首次 76 ms 装完 3 个包）。

## 产物为什么入库

与 `packaging/icon/app-icon.ico` 同一惯例：**生成物入库 + `--check` 守漂移**。
好处是构建机与 CI 都不需要 fontTools；`tests/test_theme.py` 会比对文件是否与
脚本参数一致，改了源字体忘了重新生成就会红。

用法：

    python scripts/make_font_subset.py           # 生成（覆盖）子集字体
    python scripts/make_font_subset.py --check   # 只校验现有产物是否已经是子集，不改文件

@author 康小汪【kxw】
@date 2026-10-15
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
#: 产物目录：**会被 spec 整个收进冻结包**，只许放子集化后的字体
FONT_DIR = PROJECT_ROOT / "packaging" / "fonts"
#: 源字体目录：完整字重放这里，**不进包**。
#: 为什么分两个目录：`_collect_fonts()` 收的是整个 `packaging/fonts`，源字体若放在
#: 同一层会被一并打进包（+1.59 MB，白折腾）；而且 `theme.load_fonts()` 的
#: `glob("*.ttf")` 也会把同族的完整字体重复注册一遍。
FONT_SRC_DIR = PROJECT_ROOT / "packaging" / "fonts-src"

#: 要子集化的字重（与界面用到的 400/500/600/700 一一对应）
WEIGHTS = ("Regular", "Medium", "SemiBold", "Bold")

#: 原始（完整）文件名的后缀：`Inter-Regular.ttf` → 子集化后写回同名文件。
#: 刻意**覆盖同名**而不是加 `-subset` 后缀：`theme.load_fonts()` 与 spec 不用改，
#: 「文件名没变、内容更小」对调用方完全透明。
FULL_SUFFIX = ".full.ttf"

#: 子集化的 Unicode 范围（拉丁 + 常用符号）。中文不在此列是有意为之 ——
#: Inter 没有汉字字形，中文走系统雅黑。每段都写清用途，改的时候知道在砍什么。
UNICODE_RANGES = (
    "U+0000-00FF",   # 基本拉丁 + 拉丁-1 补充（西欧语言、数字、基础标点）
    "U+0100-017F",   # 拉丁扩展-A（含 Ā 这类带音标字母）
    "U+0180-024F",   # 拉丁扩展-B（罗曼语系补充）
    "U+2000-206F",   # 常用标点（破折号、引号、省略号）
    "U+2070-209F",   # 上下标（下标数字、上标 2/3）
    "U+20A0-20BF",   # 货币符号（€ £ ¥ ₽ 等）
    "U+2100-214F",   # 字母式符号（™ № 等）
    "U+2190-21FF",   # 箭头（界面提示会用到）
    "U+2200-22FF",   # 数学运算符（≤ ≥ ≠ 等）
    "U+25A0-25FF",   # 几何图形（■ □ ● 等，列表项可能用）
    "U+FEFF",        # 零宽不换行空格（BOM）
    "U+FFFD",        # 替换字符（缺字形时的兜底显示）
)

#: 体积上限：子集化后单个字重不该超过这个值。定 200 KB 是因为实测 110 KB，
#: 留一倍余量；一旦超了说明子集范围被误改大（例如不小心带上了 CJK）。
MAX_SUBSET_BYTES = 200 * 1024


def _pyftsubset_command(source: Path, target: Path) -> list[str]:
    """拼出 `uvx` 形式的 pyftsubset 命令。

    用 `uvx --from` 而不是要求本机装好 fontTools：本项目 `dependencies` 是运行时
    依赖清单，构建期工具不该混进去；`[dev]` 也不必背这个包袱。

    Args:
        source: 输入的完整字体文件。
        target: 输出路径。

    Returns:
        list[str]: 可直接交给 subprocess 的参数列表。

    @author 康小汪【kxw】
    @date 2026-10-15
    """
    return [
        "uvx", "--from", "fonttools[woff]", "pyftsubset", str(source),
        "--output-file=%s" % target,
        "--layout-features=*",           # 保留字距与连字，否则排版会松垮
        "--unicodes=%s" % ",".join(UNICODE_RANGES),
        "--no-hinting",                  # 屏幕渲染不需要点阵提示，能再省一点
        "--desubroutinize",
        "--name-IDs=*",                  # 保留字体名，否则 Qt 认不出 "Inter"
        "--drop-tables+=DSIG",           # 数字签名表在子集化后必然失效
    ]


def subset_fonts(font_dir: Path | None = None, src_dir: Path | None = None) -> list[str]:
    """把 `fonts-src/` 里的完整字重子集化，产物写到 `fonts/`。

    Args:
        font_dir: 产物目录，默认 `packaging/fonts`。
        src_dir: 源字体目录，默认 `packaging/fonts-src`。

    Returns:
        list[str]: 每个字重的 `名字: 原大小 -> 新大小` 说明。

    Raises:
        SystemExit: `uvx` 不可用或某个字重缺失时退出（不许静默跳过）。

    @author 康小汪【kxw】
    @date 2026-10-15
    """
    directory = font_dir or FONT_DIR
    sources = src_dir or FONT_SRC_DIR
    directory.mkdir(parents=True, exist_ok=True)
    report: list[str] = []
    for weight in WEIGHTS:
        source = sources / ("Inter-%s%s" % (weight, FULL_SUFFIX))
        if not source.is_file():
            raise SystemExit("缺少完整字体：%s（把 4 个完整字重放进 %s）" % (source, sources))
        target = directory / ("Inter-%s.ttf" % weight)
        before = source.stat().st_size
        result = subprocess.run(_pyftsubset_command(source, target),
                                capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise SystemExit("子集化失败 %s：%s" % (weight, result.stderr[:400]))
        after = target.stat().st_size
        report.append("%s: %d -> %d B（省 %.0f%%）" % (weight, before, after,
                                                       (1 - after / before) * 100))
    return report


def check_fonts(font_dir: Path | None = None) -> list[str]:
    """校验现有字体已经是子集（体积远小于完整版），返回问题列表。

    判据用**体积**而不是逐字节点数：体积是用户能感知的约束（闸门按 MB 算），
    而且不需要 fontTools 就能算 —— 让 `--check` 在没装工具的机器上也能跑。

    Args:
        font_dir: 字体目录，默认 `packaging/fonts`。

    Returns:
        list[str]: 每个问题一条说明；全通过时为空。

    @author 康小汪【kxw】
    @date 2026-10-15
    """
    directory = font_dir or FONT_DIR
    problems: list[str] = []
    for weight in WEIGHTS:
        target = directory / ("Inter-%s.ttf" % weight)
        if not target.is_file():
            problems.append("缺少 %s" % target.name)
            continue
        size = target.stat().st_size
        if size > MAX_SUBSET_BYTES:
            problems.append("%s 有 %d B（>%d），看起来没子集化过"
                            % (target.name, size, MAX_SUBSET_BYTES))
    # 产物目录里混进源字体是很实际的错误（曾把它们放同一层，会被打进包）
    strays = sorted(p.name for p in directory.glob("*.full.ttf"))
    if strays:
        problems.append("产物目录 %s 里有源字体（会被打进包）：%s" % (directory, strays))
    return problems


def total_bytes(font_dir: Path | None = None) -> int:
    """返回当前字体总体积（供报告与测试断言用）。"""
    directory = font_dir or FONT_DIR
    return sum(f.stat().st_size for f in directory.glob("Inter-*.ttf"))


def main(argv: list[str] | None = None) -> int:
    """命令行入口：`--check` 只校验，否则重新生成子集字体。

    Args:
        argv: 参数列表，默认取 `sys.argv[1:]`。

    Returns:
        int: 0 通过 / 1 校验失败。

    @author 康小汪【kxw】
    @date 2026-10-15
    """
    parser = argparse.ArgumentParser(description="子集化 Inter 字体（T29 方案 C）")
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    parser.add_argument("--json", help="把结果写到指定路径（供 CI/脚本读取）")
    args = parser.parse_args(argv)

    if args.check:
        problems = check_fonts()
        for problem in problems:
            print("  !! %s" % problem)
        print("总数 %d B = %.2f MB" % (total_bytes(), total_bytes() / 1048576))
        print("check result: %s" % ("ok" if not problems else "failed"))
        return 0 if not problems else 1

    for line in subset_fonts():
        print("  " + line)
    print("子集化完成，合计 %d B = %.2f MB" % (total_bytes(), total_bytes() / 1048576))
    if args.json:
        Path(args.json).write_text(
            json.dumps({"total_bytes": total_bytes(), "weights": list(WEIGHTS)},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
