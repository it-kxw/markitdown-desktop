# SPDX-License-Identifier: MIT
"""入口：GUI 模式与自测模式（进程内 / 子进程池）。

自测模式用于打包验证（对应可行性评估 §7 P0、P1 的 T8/T9）：
    markitdown-desktop --selftest file1 file2 ... --json result.json --mode pool
会依次测量 Qt 初始化、窗口构造、各文件转换耗时，并输出 JSON 报告。

@author 康小汪【kxw】
@date 2026-02（创建）、2026-10-13（T8：进程池自测、freeze_support 前置、日志与异常钩子）
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import platform
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

    from markitdown_desktop.engine import ConversionOutcome
    from markitdown_desktop.ui import MainWindow

from markitdown_desktop._compat import icon_path

T_PROCESS_START = time.perf_counter()

# 自测模式可选值：inproc = 进程内转换（P0 基准），pool = 子进程池（发布版实际路径）
_MODE_CHOICES = ("inproc", "pool")
# 主线程抽事件间隔：进程池在子进程里跑，主线程必须持续 processEvents 才能收到队列信号
_POLL_INTERVAL_S = 0.02
# 等结果的额外宽限：子进程要冷启动并导入 markitdown/pandas
_RESULT_WAIT_EXTRA_S = 60.0


class _PoolRun(NamedTuple):
    """进程池自测结果：报告行 + 三个计时点（秒）。

    dispatch_s: 提交到首个任务被派发；first_result_s: 提交到首个结果回来；
    ready_s: 从进程启动到池已派发首批任务（与 inproc 的 time_to_ready 同一含义）。

    @author 康小汪【kxw】
    @date 2026-10-13
    """

    rows: list[dict[str, object]]
    dispatch_s: float
    first_result_s: float
    ready_s: float


class _PoolCollector:
    """收集进程池结果，并记录三个计时点（秒）。

    计时口径：dispatch_s/first_result_s 相对**提交时刻**，ready_s 相对**进程启动**
    （与 inproc 的 time_to_ready 同义）。

    @author 康小汪【kxw】
    @date 2026-10-13
    """

    def __init__(self) -> None:
        self.outcomes: dict[int, ConversionOutcome] = {}
        self.dispatch_s = 0.0
        self.first_result_s = 0.0
        self.ready_s = 0.0
        self._submitted_at = time.perf_counter()

    def on_started(self, _index: int, _source: str) -> None:
        """首个任务被派发：派发耗时与“启动到就绪”都定在这一刻。"""
        if not self.dispatch_s:
            self.dispatch_s = time.perf_counter() - self._submitted_at
            self.ready_s = time.perf_counter() - T_PROCESS_START

    def on_result(self, index: int, outcome: ConversionOutcome) -> None:
        """记录结果；首个结果回来时定下“提交到首个结果”耗时。"""
        if not self.outcomes:
            self.first_result_s = time.perf_counter() - self._submitted_at
        self.outcomes[index] = outcome


def _safe_print(text: str) -> None:
    """窗口化（noconsole）冻结包中 sys.stdout 可能为 None，打印需容错。

    中文在 cp1252 控制台（en-US 的 Windows）上会抛 UnicodeEncodeError，
    所以先给标准流加 `errors="replace"` 兜底（只改错误策略、不改编码）再打印；
    打印本身失败也不能影响自测结果。

    @author 康小汪【kxw】
    @date 2026-10-14
    """
    from markitdown_desktop.console import make_output_safe

    make_output_safe()
    if sys.stdout is not None:
        try:
            print(text)
        except Exception:  # noqa: BLE001 - 打印失败不应影响自测
            pass


def _start_logging() -> None:
    """初始化文件日志与全局异常钩子。

    日志目录不可写（磁盘满、无权限）时必须继续启动：宁可不记日志，
    也不能因为日志把程序挡在门外。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    from markitdown_desktop.logging_setup import init_logging, install_excepthooks

    try:
        init_logging()
    except OSError as exc:
        _safe_print(f"日志初始化失败，继续以无日志模式运行：{exc}")
        return
    install_excepthooks()


def _result_row(source: str, outcome: ConversionOutcome) -> dict[str, object]:
    """把一次转换压成报告行。

    两种模式共用同一份整形逻辑，避免字段各写一套之后悄悄漂移。

    Args:
        source: 源文件路径。
        outcome: 转换结果。

    Returns:
        dict: 报告行（file/ok/elapsed_s/chars/error/warning）。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    return {
        "file": os.path.basename(source),
        "ok": outcome.ok,
        "elapsed_s": round(outcome.elapsed_s, 3),
        "chars": len(outcome.markdown),
        "error": outcome.error.splitlines()[0] if outcome.error else "",
        "warning": outcome.warning,
    }


def _print_row(source: str, outcome: ConversionOutcome) -> None:
    """打印单行结果。

    warning 也要露出来：空结果在日志里看着和成功一模一样，容易漏掉。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    _safe_print(
        f"[{'OK ' if outcome.ok else 'FAIL'}] {os.path.basename(source):45s} "
        f"{outcome.elapsed_s:6.2f}s  {len(outcome.markdown):8d} chars  "
        f"{outcome.error[:60]}{outcome.warning[:40]}"
    )


def _pump_until(app: QApplication, done: Callable[[], bool], limit_s: float) -> None:
    """抽 Qt 事件直到条件成立或超时。

    超时不是错误：没收到的结果由 `_collect_rows()` 标成失败，
    这样池挂住时报告仍会完整落盘。

    Args:
        app: 当前 QApplication。
        done: 完成条件。
        limit_s: 最长等待秒数，防止池挂住时自测永不返回。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    deadline = time.monotonic() + limit_s
    while not done() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(_POLL_INTERVAL_S)


def _run_inproc(files: list[str], timings: dict[str, float]) -> list[dict[str, object]]:
    """建引擎并在进程内顺序转换：P0 的基准路径，也是 pool 模式的对照实现。

    Args:
        files: 源文件列表。
        timings: 计时字典，写入 `engine_init` 与 `time_to_ready`。

    Returns:
        list: 报告行列表。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    from markitdown_desktop.engine import ConversionEngine

    t = time.perf_counter()
    engine = ConversionEngine()
    timings["engine_init"] = round(time.perf_counter() - t, 3)
    timings["time_to_ready"] = round(time.perf_counter() - T_PROCESS_START, 3)
    rows: list[dict[str, object]] = []
    for path in files:
        outcome = engine.convert_file(path)
        _print_row(path, outcome)
        rows.append(_result_row(path, outcome))
    return rows


def _collect_rows(
    files: list[str], outcomes: dict[int, ConversionOutcome]
) -> list[dict[str, object]]:
    """按源文件顺序收齐结果行；缺结果的（池提前结束）标成失败而不是丢掉。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    from markitdown_desktop.engine import ConversionOutcome

    rows: list[dict[str, object]] = []
    for index, path in enumerate(files):
        outcome = outcomes.get(index) or ConversionOutcome(
            source=path, ok=False, error="未收到结果（进程池提前结束）"
        )
        _print_row(path, outcome)
        rows.append(_result_row(path, outcome))
    return rows


def _convert_with_pool(
    app: QApplication, files: list[str], workers: int, timeout_s: float
) -> _PoolRun:
    """用子进程池转换全部文件。

    首个结果耗时减去该文件自身的转换耗时，就是子进程导入 markitdown/pandas 的开销。

    Args:
        app: 当前 QApplication，用于抽事件收结果。
        files: 源文件列表，下标即任务编号。
        workers: worker 数量。
        timeout_s: 单文件超时秒数，透传给 JobSpec。

    Returns:
        _PoolRun: 报告行与三个计时点。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    from markitdown_desktop.worker import ConversionPool, JobSpec

    collector = _PoolCollector()
    pool = ConversionPool(workers=workers)
    pool.job_started.connect(collector.on_started)
    pool.result_ready.connect(collector.on_result)
    pool.submit(
        [
            JobSpec(index=index, source=path, timeout_s=timeout_s)
            for index, path in enumerate(files)
        ]
    )
    limit_s = timeout_s * max(len(files), 1) + _RESULT_WAIT_EXTRA_S
    _pump_until(app, lambda: len(collector.outcomes) >= len(files), limit_s)
    pool.shutdown()
    return _PoolRun(
        rows=_collect_rows(files, collector.outcomes),
        dispatch_s=round(collector.dispatch_s, 3),
        first_result_s=round(collector.first_result_s, 3),
        ready_s=round(collector.ready_s, 3),
    )


def _build_report(mode: str, concurrency: int, timeout_s: float) -> dict[str, object]:
    """建自测报告骨架：环境信息 + 本次参数（T12 复现时要对得上）。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "frozen": getattr(sys, "frozen", False),
        "executable": sys.executable,
        "mode": mode,
        "concurrency": concurrency,
        "timeout_s": timeout_s,
        "timings_s": {},
        "results": [],
    }


def _open_window(app: QApplication, timings: dict[str, float]) -> MainWindow:
    """建主窗口并 show()，记录 window_show。

    走真实 GUI 路径：既能保住 P0 的启动指标可比性，也能提前暴露冻结包缺 Qt 插件。

    Args:
        app: 当前 QApplication。
        timings: 计时字典，就地写入。

    Returns:
        MainWindow: 建好的窗口（调用方负责 `close()`）。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    from markitdown_desktop.settings import AppSettings
    from markitdown_desktop.ui import MainWindow

    # 注入临时 ini：自测最后会 close() 窗口，走默认后端会把窗口状态写进注册表
    selftest_ini = Path(tempfile.mkdtemp()) / "selftest.ini"
    t = time.perf_counter()
    window = MainWindow(settings=AppSettings(ini_path=selftest_ini))
    window.show()
    app.processEvents()
    timings["window_show"] = round(time.perf_counter() - t, 3)
    return window


def _apply_app_icon(app: QApplication) -> None:
    """给整个应用设置窗口图标（任务栏与 Alt-Tab 显示的就是它）。

    exe 自身的图标由 PyInstaller 依据 spec 的 `icon=` 写进 PE 资源，与这里无关；
    这里解决的是「窗口图标」—— 不设的话任务栏显示 Qt 的通用图标。

    @author 康小汪【kxw】
    @date 2026-10-15

    Args:
        app: 已经创建好的 QApplication。

    Returns:
        None: 图标文件读不到时静默跳过，不影响启动。
    """
    from PySide6.QtGui import QIcon

    path = icon_path()
    if path.is_file():
        app.setWindowIcon(QIcon(str(path)))


def _run_selftest(
    files: list[str],
    json_path: str | None,
    offscreen: bool,
    mode: str = "pool",
    concurrency: int = 2,
    timeout_s: float = 300.0,
) -> int:
    """执行自测：测量启动与转换耗时，输出 JSON 报告。

    Args:
        files: 待转换文件列表。
        json_path: 报告输出路径；None 时写到当前目录的 p0-frozen-report.json。
        offscreen: 是否使用离屏渲染（无显示器的机器与 CI）。
        mode: `inproc` 进程内转换（P0 基准）或 `pool` 子进程池（发布版实际路径）。
        concurrency: pool 模式的 worker 数量。
        timeout_s: 单文件超时秒数。

    Returns:
        int: 0 表示全部成功，1 表示有文件失败。

    @author 康小汪【kxw】
    @date 2026-02（创建）、2026-10-13（T8：新增 mode/concurrency/timeout）
    """
    report = _build_report(mode, concurrency, timeout_s)
    timings: dict[str, float] = report["timings_s"]  # type: ignore[assignment]
    if offscreen:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    t = time.perf_counter()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    timings["qt_init"] = round(time.perf_counter() - t, 3)
    _apply_app_icon(app)
    window = _open_window(app, timings)

    # pool 模式不建父进程引擎（发布版路径），子进程成本另由 pool_* 两个字段量化；
    # time_to_ready 统一表示“启动到可以开始转换”：inproc 是引擎就绪，pool 是首批任务已派发
    if mode == "pool":
        run = _convert_with_pool(app, files, concurrency, timeout_s)
        report["pool_start_s"] = run.dispatch_s
        report["pool_first_result_s"] = run.first_result_s
        timings["time_to_ready"] = run.ready_s
        rows = run.rows
    else:
        rows = _run_inproc(files, timings)

    report["results"] = rows
    report["ok"] = all(row["ok"] for row in rows)
    report["total_s"] = round(time.perf_counter() - T_PROCESS_START, 3)
    _write_report(report, json_path)
    window.close()
    return 0 if report["ok"] else 1


def _write_report(report: dict[str, object], json_path: str | None) -> None:
    """把报告同时打到控制台并写入文件（窗口化冻结包里控制台可能不存在）。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    text = json.dumps(report, ensure_ascii=False, indent=2)
    _safe_print(text)
    target = json_path or os.path.join(os.getcwd(), "p0-frozen-report.json")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(text)


def _build_parser() -> argparse.ArgumentParser:
    """建命令行参数解析器。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    parser = argparse.ArgumentParser(prog="markitdown-desktop")
    parser.add_argument(
        "--selftest",
        nargs="*",
        default=None,
        metavar="FILE",
        help="自测模式：转换给定文件并输出耗时报告",
    )
    parser.add_argument("--json", default=None, help="自测报告写入路径")
    parser.add_argument("--offscreen", action="store_true", help="自测时使用离屏渲染")
    parser.add_argument(
        "--mode",
        choices=_MODE_CHOICES,
        default="pool",
        help="自测转换方式：pool=子进程池（默认，发布版路径）、inproc=进程内",
    )
    parser.add_argument("--concurrency", type=int, default=2, help="自测 pool 模式的 worker 数")
    parser.add_argument("--timeout", type=float, default=300.0, help="自测单文件超时秒数")
    parser.add_argument("--version", action="store_true", help="打印版本并退出")
    return parser


def main(argv: list[str] | None = None) -> int:
    """程序入口。

    `freeze_support()` 必须在最前面：冻结包里的 worker 是用
    `--multiprocessing-fork` 重新拉起本 exe 的，不早返回就会多开一个窗口。

    @author 康小汪【kxw】
    @date 2026-02（创建）、2026-10-13（T8：freeze_support、日志钩子、自测参数）
    """
    multiprocessing.freeze_support()
    args = _build_parser().parse_args(argv)

    if args.version:
        from markitdown_desktop import __version__

        print(f"markitdown-desktop {__version__}")
        return 0

    _start_logging()
    if args.selftest is not None:
        return _run_selftest(
            args.selftest, args.json, args.offscreen, args.mode, args.concurrency, args.timeout
        )

    from PySide6.QtWidgets import QApplication

    from markitdown_desktop.ui import MainWindow

    app = QApplication(sys.argv)
    _apply_app_icon(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
