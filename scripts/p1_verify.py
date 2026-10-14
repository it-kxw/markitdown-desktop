# SPDX-License-Identifier: MIT
"""P1 端到端验证（T9）：进程池、超时硬杀、失败隔离、日志落盘、无残留进程。

冻结包存在就用冻结包跑，否则退回源码模式（`python -m markitdown_desktop`），
这样没打包时也能先验证行为。任何一项失败即非零退出。

用法（Windows cmd）：
    .venv\\Scripts\\python.exe scripts\\p1_verify.py
产物：
    p1-verify.json（机器可读报告，供 T12 文档引用）

注意：本脚本断言“无残留 markitdown-desktop.exe 进程”，运行前请先关掉
正在使用的 GUI 实例，否则会误报。

@author 康小汪【kxw】
@date 2026-10-13
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "dist" / "markitdown-desktop" / "markitdown-desktop.exe"
FIXTURES = ROOT / "tests" / "fixtures"
REPORT = ROOT / "p1-verify.json"
IMAGE_NAME = "markitdown-desktop.exe"

#: 故意的失败用例（随机字节，无任何转换器认领）；README.md 是夹具说明，不是夹具
_SKIP_FIXTURES = {"random.bin", "README.md"}


def _entry() -> list[str]:
    """优先用冻结包，没有就退回源码模式。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    if EXE.is_file():
        return [str(EXE)]
    return [sys.executable, "-m", "markitdown_desktop"]


def _run(args: list[str], timeout: float = 900.0) -> subprocess.CompletedProcess:
    """跑一次入口命令（冻结包是窗口化程序，不会往 stdout 写东西）。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    return subprocess.run(
        [*_entry(), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        errors="replace",
    )


def _fixture_files() -> list[str]:
    """除故意失败用例与说明文件以外的全部夹具。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    return [
        str(path)
        for path in sorted(FIXTURES.glob("*"))
        if path.is_file() and path.name not in _SKIP_FIXTURES
    ]


def _read_report(path: Path) -> dict:
    """读回自测报告；文件不在说明入口提前退出了，报错要说清楚。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    assert path.is_file(), f"自测报告未生成：{path}（入口可能提前退出）"
    return json.loads(path.read_text(encoding="utf-8"))


def check_selftest_pool(tmp: Path) -> dict:
    """全部夹具在进程池模式下成功（冻结包能拉起子进程 = spawn 反查通过）。

    断言「运行模式与入口一致」而非「必须在冻结包里」：没打包时本脚本退回源码模式
    （见 `_entry()`），此时 `frozen` 为 False 属正常，硬断言 True 会让源码模式必然失败。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    files = _fixture_files()
    out = tmp / "pool.json"
    proc = _run(["--selftest", *files, "--offscreen", "--mode", "pool", "--json", str(out)])
    assert proc.returncode == 0, f"进程池自测失败：{proc.stderr[-2000:]}"
    payload = _read_report(out)
    failed = [row["file"] for row in payload["results"] if not row["ok"]]
    assert not failed, f"以下夹具转换失败：{failed}"
    assert payload["frozen"] is EXE.is_file(), (
        f"运行模式与入口不一致：报告 frozen={payload['frozen']}，冻结包存在={EXE.is_file()}"
    )
    return {
        "cases": len(payload["results"]),
        "total_s": payload["total_s"],
        "time_to_ready_s": payload["timings_s"]["time_to_ready"],
        "child_import_s": round(
            payload["pool_first_result_s"] - payload["results"][0]["elapsed_s"], 3
        ),
    }


def check_timeout_and_recovery(tmp: Path) -> dict:
    """超时被硬杀并如实报告，且杀掉 worker 后池能继续处理后续文件。

    单 worker + 150 MB 文本 + 2.5 s 超时：文本必然超时，紧跟其后的 test.json
    必然成功——一次运行同时证明“杀 → 报 → 继续”。

    阀值依据（冻结包实测）：68 MB 文本要 3.755 s（含子进程导入 0.9 s），
    重建的 worker 跑 test.json 约 1.0 s。取 150 MB + 2.5 s，两侧各留约 3 倍余量。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    big = tmp / "big.txt"
    big.write_text("line\n" * 30_000_000, encoding="utf-8")
    out = tmp / "timeout.json"
    started = time.perf_counter()
    proc = _run(
        [
            "--selftest", str(big), str(FIXTURES / "test.json"),
            "--offscreen", "--mode", "pool", "--concurrency", "1",
            "--timeout", "2.5", "--json", str(out),
        ],
        timeout=300,
    )
    wall_s = round(time.perf_counter() - started, 2)
    rows = _read_report(out)["results"]
    assert "超时" in rows[0]["error"], f"未报告超时：{rows[0]}"
    assert rows[1]["ok"] is True, f"杀掉 worker 后未能继续：{rows[1]}"
    assert proc.returncode == 1, "有失败项时退出码应为 1（0 会让 CI 误判成功）"
    return {"timeout_error": rows[0]["error"], "recovered_next_file": True, "wall_s": wall_s}


def check_failure_isolation(tmp: Path) -> dict:
    """某个文件转换失败（无转换器认领）不得影响同批后续文件。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    out = tmp / "isolate.json"
    proc = _run(
        [
            "--selftest", str(FIXTURES / "random.bin"), str(FIXTURES / "test.json"),
            "--offscreen", "--mode", "pool", "--json", str(out),
        ]
    )
    rows = _read_report(out)["results"]
    assert rows[0]["ok"] is False, "random.bin 本应失败"
    assert rows[1]["ok"] is True, "前一个文件失败后未能继续处理后续文件"
    assert proc.returncode == 1, "有失败项时退出码应为 1"
    return {"failed_file": rows[0]["file"], "error": rows[0]["error"][:60], "isolated": True}


def check_log_fresh(started_at: float) -> dict:
    """日志必须落盘、非空，且**本次运行**确实写过（防止拿旧文件冒充）。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    log_dir = Path(os.environ["LOCALAPPDATA"]) / "MarkItDownDesktop" / "logs"
    log = log_dir / "app.log"
    assert log.is_file(), f"日志未生成：{log}"
    size = log.stat().st_size
    assert size > 0, "日志为空"
    assert log.stat().st_mtime >= started_at - 1, "日志不是本次运行写的（时间戳过旧）"
    return {"log": str(log), "size_bytes": size}


def check_no_orphans() -> dict:
    """正常退出后不得残留 worker 进程（T7 实测过父进程被强杀会留孤儿）。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    proc = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {IMAGE_NAME}", "/NH"],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
    )
    lines = [ln for ln in proc.stdout.splitlines() if IMAGE_NAME.lower() in ln.lower()]
    assert not lines, f"残留进程：{lines}"
    return {"leftover_processes": 0}


def main() -> int:
    """依次跑全部检查项，写出报告；任一项失败即非零退出。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    started_at = time.time()
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        summary = {
            "entry": " ".join(_entry()),
            "frozen": EXE.is_file(),
            "selftest_pool": check_selftest_pool(tmp),
            "timeout_and_recovery": check_timeout_and_recovery(tmp),
            "failure_isolation": check_failure_isolation(tmp),
            "log": check_log_fresh(started_at),
            "orphans": check_no_orphans(),
        }
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    REPORT.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
