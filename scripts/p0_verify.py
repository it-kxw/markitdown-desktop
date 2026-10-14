"""P0 验证脚本：跑通「转换 → 写盘 → 失败路径」全流程，输出可核对的事实。

用法（Windows Python）：
    python scripts/p0_verify.py <测试文件目录> <输出目录>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from markitdown_desktop.engine import ConversionEngine  # noqa: E402

CASES = [
    "test.docx",
    "test.pdf",
    "test.pptx",
    "test.xlsx",
    "test.xls",
    "test_mskanji.csv",
    "test_blog.html",
    "test.json",
    "test_notebook.ipynb",
    "test_rss.xml",
    "test.epub",
    "test_outlook_msg.msg",
    "test_with_comment.docx",
    "rlink.docx",
    "pptx_notes_empty_run.pptx",
    "test.pdf",  # 重复一次，验证同引擎连续转换
]
EXPECT_FAIL = ["random.bin"]


def main(src_dir: str, out_dir: str) -> int:
    src = Path(src_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    engine = ConversionEngine()
    failures = 0

    print("=" * 100)
    print("成功路径")
    print("=" * 100)
    for name in CASES:
        path = src / name
        if not path.exists():
            print(f"[SKIP] {name} 不存在")
            continue
        outcome = engine.convert_file(path)
        if outcome.ok:
            target = ConversionEngine.write_markdown(
                outcome, out / f"{path.stem}.md"
            )
            raw = target.read_bytes()
            first = outcome.markdown.strip().splitlines()[:1]
            print(
                f"[OK  ] {name:34s} {outcome.elapsed_s:6.3f}s "
                f"{len(outcome.markdown):7d} chars -> {target.name} ({len(raw)} B utf-8)"
            )
            print(f"        首行: {first[0][:90] if first else '(空)'}")
        else:
            failures += 1
            print(f"[FAIL] {name:34s} {outcome.error[:80]}")
            for line in outcome.attempts:
                print(f"        - {line[:110]}")

    print()
    print("=" * 100)
    print("失败路径（预期失败，用于验证错误信息是否可解释）")
    print("=" * 100)
    for name in EXPECT_FAIL:
        path = src / name
        if not path.exists():
            print(f"[SKIP] {name} 不存在")
            continue
        outcome = engine.convert_file(path)
        ok_expected = not outcome.ok
        print(f"[{'符合预期' if ok_expected else '不符合预期'}] {name} -> ok={outcome.ok}")
        print(f"        error   : {outcome.error[:120]}")
        for line in outcome.attempts:
            print(f"        attempt : {line[:120]}")
        if not ok_expected:
            failures += 1

    print()
    print("=" * 100)
    print("临时快照上下文管理器（Office 占用规避）")
    print("=" * 100)
    with ConversionEngine.temp_snapshot(src / "test.docx") as tmp:
        print(f"临时副本: {tmp}  存在={tmp.exists()}")
        outcome = engine.convert_file(tmp)
        print(f"副本转换: ok={outcome.ok} chars={len(outcome.markdown)}")
    print(f"退出后已删除: {not tmp.exists()}")

    print()
    print(f"失败项合计: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
