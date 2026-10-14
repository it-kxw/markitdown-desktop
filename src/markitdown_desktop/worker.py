# SPDX-License-Identifier: MIT
"""转换进程池：把转换放到子进程里跑，换取可取消、可超时、崩溃不拖垮界面。

设计要点：
- **每个 worker 一个专属任务队列**：父进程由此确知"哪个进程在跑哪个任务"，
  取消/超时才能精确 terminate()，而不是盲杀一个可能无辜的 worker；
- **worker 长驻**：导入成本只付一次；只有超时/取消/崩溃才换新进程；
- **共享状态一把锁**：run() 在子线程、submit()/cancel()/shutdown() 在 GUI 线程，
  不加锁必然出现 "dictionary changed size during iteration"；
- **信号一律在锁外发**：槽函数可能回调 cancel()/shutdown()，持锁发信号会自锁；
- **子进程入口在 _child.py**：本模块的 Qt 依赖不会被 spawn 带进子进程；
- **失败一律落日志**（P2 T13）：结果非 ok 时在父进程侧记一条 WARNING（路径 + 原因首行）——
  子进程崩溃时自己没有机会写，且子进程日志不会回到父进程 handler，只能在父进程记。

@author 康小汪【kxw】
@date 2026-10-13（P2 T13 增失败写日志）
"""

from __future__ import annotations

import logging
import multiprocessing
import threading
import time
from collections import deque
from queue import Empty
from typing import Any, Callable, Sequence

from PySide6.QtCore import QThread, Signal

from ._child import JobSpec, child_main
from .engine import ConversionOutcome
from .settings import MAX_CONCURRENCY, MIN_CONCURRENCY

# 冻结包必须用 spawn：fork 在 PyInstaller 引导后不可用，且 fork 会让子进程
# 继承父进程已初始化的 Qt（随后在子进程里用 Qt 是未定义行为）。
_ctx = multiprocessing.get_context("spawn")

_POLL_INTERVAL_MS = 50  # 主循环节拍：足够及时，又不至于空转吃 CPU
_JOIN_TIMEOUT_S = 1.0  # 单个子进程回收的等待上限
_THREAD_STOP_TIMEOUT_MS = 3000  # 等 run() 退出主循环的上限
_CANCELLED = "已取消"  # 用户主动取消的固定文案：既用于结果，也用于日志过滤
#: 结果码（T17 起供界面查 i18n 文案）：`ConversionOutcome.error_code` 的取值
CANCELLED_CODE = "cancelled"
_ERROR_LOG_LIMIT = 200  # 日志里错误文本的截断长度（与 UI 摘要同口径）

_logger = logging.getLogger(__name__)

__all__ = ["ConversionPool", "JobSpec"]


def _first_line(text: str, limit: int = _ERROR_LOG_LIMIT) -> str:
    """取错误文本首行并截断。

    隐私约束：异常文本可能夹带被转换文件的内容片段，而日志文件是要交给用户
    发给我们排查问题的，因此只记首行、且限长，绝不整段落盘。

    Args:
        text: 错误文本，可能为多行。
        limit: 单行最大字符数。

    Returns:
        str: 首行（已截断）；空文本返回空串。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    first = text.splitlines()[0] if text else ""
    return first[:limit]


def is_retryable(outcome: ConversionOutcome | None) -> bool:
    """该结果是否值得重试：确实失败了、且不是用户主动取消。

    "已取消"必须排除：取消是用户意图，把取消项拿去"重试"等于告诉用户取消失效了。

    Args:
        outcome: 单条转换结果；None（该行还没跑过）不算可重试。

    Returns:
        bool: True 表示可以重试。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    return outcome is not None and not outcome.ok and outcome.error != _CANCELLED


class _WorkerSlot:
    """一个 worker 槽位：专属任务队列 + 进程。

    进程被杀后槽位保留（队列可复用），下次需要时补一个新进程进来。

    @author 康小汪【kxw】
    @date 2026-10-13
    """

    __slots__ = ("task_q", "proc")

    def __init__(self, task_q: Any) -> None:
        self.task_q = task_q
        self.proc: Any = None

    def alive(self) -> bool:
        """进程是否存活；未启动或已退出均返回 False。"""
        return self.proc is not None and self.proc.is_alive()


class ConversionPool(QThread):
    """在子进程中执行转换的进程池。

    Signals:
        result_ready(int, object): 单个任务结束（含失败、取消、超时），载荷为 ConversionOutcome。
        job_started(int, str): 任务已派发给某个 worker，载荷为 (index, 源路径)。
        job_timeout(int, float): 任务超时且进程已被硬杀，载荷为 (index, 已用秒数)。
        all_done(): 批次结束（pending 与 running 同时清空时**只发一次**）。
        pool_error(str): 池自身故障（如无法启动子进程），界面据此提示而非静默。

    @author 康小汪【kxw】
    @date 2026-10-13
    """

    result_ready = Signal(int, object)
    job_started = Signal(int, str)
    job_timeout = Signal(int, float)
    all_done = Signal()
    pool_error = Signal(str)

    def __init__(
        self,
        workers: int = 2,
        child_target: Callable[..., None] | None = None,
        parent: Any = None,
    ) -> None:
        """初始化进程池并启动轮询线程。

        Args:
            workers: 并发 worker 数，钳制到 1–4。上限 4 是因为每个 worker 都持有一份
                pandas/markitdown（内存随进程数线性上涨），再多收益递减。
            child_target: 子进程入口，仅测试注入替身；生产用 _child.child_main。
            parent: Qt 父对象。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        super().__init__(parent)
        self._workers = max(MIN_CONCURRENCY, min(MAX_CONCURRENCY, int(workers)))
        self._child_target = child_target or child_main
        self._result_q = _ctx.Queue()  # 结果队列全 worker 共用，靠 index 区分归属
        self._slots: list[_WorkerSlot] = []
        self._pending: deque[JobSpec] = deque()
        self._running: dict[int, tuple[int, JobSpec, float]] = {}  # index -> (槽位号, 任务, 起始时刻)
        self._errors: list[str] = []  # 锁内收集、锁外上报的池故障
        self._lock = threading.Lock()
        self._stopping = False
        self._was_busy = False
        # 必须最后调用：run() 依赖上面全部字段
        self.start()

    # ------------------------------------------------------------------ 对外接口

    def submit(self, specs: Sequence[JobSpec]) -> None:
        """提交一批任务；取消后再次提交视为新批次。

        Args:
            specs: 任务序列。index 需在本批次内唯一，重复的会被忽略（否则同一行
                会收到两次结果，或先到的结果被后到的覆盖）。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        if not specs:
            return
        with self._lock:
            if self._stopping:
                return
            known = {spec.index for spec in self._pending} | set(self._running)
            for spec in specs:
                if spec.index in known:
                    continue
                known.add(spec.index)
                self._pending.append(spec)

    def cancel(self) -> None:
        """取消当前批次：清空待办、硬杀全部 worker，未完成项统一报"已取消"。

        这里主动杀掉**空闲** worker 也一样：池会在下一次 submit 时按需重新拉起，
        这样"取消"之后确实一个子进程都不剩（测试断言依赖此行为）。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        with self._lock:
            pending = list(self._pending)
            running = list(self._running.values())
            self._pending.clear()
            self._running.clear()
            procs = []
            for slot in self._slots:
                if slot.proc is not None:
                    procs.append(slot.proc)
                    slot.proc = None
        # 硬杀：转换器可能卡在 C 扩展里，join 是等不到的，只能 terminate
        for proc in procs:
            self._kill(proc)
        # 锁外发信号：槽函数可能回调本对象，持锁发信号会自锁
        for spec in pending:
            self.result_ready.emit(spec.index, self._cancelled_outcome(spec))
        for _, spec, _ in running:
            self.result_ready.emit(spec.index, self._cancelled_outcome(spec))
        # 不在这里发 all_done：交给主循环的 busy→idle 跃迁统一发，避免重复

    def shutdown(self) -> None:
        """停止轮询线程并回收所有子进程；幂等，供 closeEvent 调用。

        退出路径不逐条上报"已取消"：窗口都要关了，没人再关心队列状态。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        with self._lock:
            if self._stopping:
                return
            self._stopping = True
            self._pending.clear()
            self._running.clear()
            slots = list(self._slots)
        # 先放锁再等待：run() 每轮都要拿这把锁，持锁 wait() 必然死锁
        if self.isRunning():
            self.wait(_THREAD_STOP_TIMEOUT_MS)
        for slot in slots:
            proc, slot.proc = slot.proc, None
            if proc is None:
                continue
            if proc.is_alive():
                # 毒丸：每个 worker 有专属队列，一条 None 就能让它干净退出
                try:
                    slot.task_q.put(None)
                except Exception:  # noqa: BLE001 - 队列已关闭时直接硬杀
                    pass
                proc.join(_JOIN_TIMEOUT_S)
            self._kill(proc)

    def worker_pids(self) -> list[int]:
        """存活 worker 的 pid 列表（测试用于断言进程被复用而非重建）。"""
        with self._lock:
            return [slot.proc.pid for slot in self._slots if slot.alive()]

    def is_busy(self) -> bool:
        """是否还有待办或进行中的任务；界面关闭前据此确认。"""
        with self._lock:
            return bool(self._pending or self._running)

    # ------------------------------------------------------------------ 主循环

    def run(self) -> None:
        """主循环：收结果 → 看门狗 → 补位 → 派发，每 50 ms 一拍。

        顺序不可调换（每一步都为下一步扫清障碍）：
        1. `_drain()` 先收：进程可能"写完结果才死"，先收就不会误报崩溃；
        2. `_watchdog()` 再判：必须在补位之前——否则崩溃的槽位会被立刻补成"活的"，
           崩溃检测永远看不到进程死过，任务永远收不到结果（实测踩到）；
        3. `_fill()` 后补：此时已无待判的死进程，补位才是安全的；
        4. `_dispatch()` 最后派发：要用补好的活进程。

        信号一律在锁外发射，见模块文档。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        while not self._stopping:
            with self._lock:
                if self._stopping:
                    break
                done = self._drain()
                done += self._watchdog()
                self._fill()
                started = self._dispatch()
                errors, self._errors = self._errors, []
                busy = bool(self._pending or self._running)
                if busy or started or done:
                    # 先置位再判空闲：万一整批在同一拍内跑完，也不会漏发 all_done
                    self._was_busy = True
                idle_after_batch = not busy and self._was_busy
                if idle_after_batch:
                    self._was_busy = False
            for message in errors:
                self.pool_error.emit(message)
            for index, source in started:
                self.job_started.emit(index, source)
            for index, outcome, timeout_elapsed in done:
                if timeout_elapsed is not None:
                    self.job_timeout.emit(index, timeout_elapsed)
                self._log_failure(outcome)
                self.result_ready.emit(index, outcome)
            if idle_after_batch:
                self.all_done.emit()
            self.msleep(_POLL_INTERVAL_MS)

    # ------------------------------------------------------------------ 内部实现

    def _fill(self) -> None:
        """按需补位：只在有任务时拉起 worker，空闲时不白占进程。

        被杀掉的 worker 不在这里立即补：等下一次 submit 再拉起，
        这样 cancel() 之后池里确实一个子进程都不剩。
        """
        wanted = min(self._workers, len(self._pending) + len(self._running))
        while len(self._slots) < wanted:
            self._slots.append(_WorkerSlot(_ctx.Queue()))
        for slot in self._slots[:wanted]:
            if not slot.alive():
                self._spawn(slot)

    def _spawn(self, slot: _WorkerSlot) -> None:
        """在槽位里拉起一个 worker 进程；启动失败只上报，不让主循环崩掉。"""
        try:
            proc = _ctx.Process(
                target=self._child_target,
                args=(slot.task_q, self._result_q),
                daemon=True,  # 父进程退出时一并回收，不留孤儿进程
            )
            proc.start()
        except Exception as exc:  # noqa: BLE001 - 句柄耗尽等系统级失败
            slot.proc = None
            self._errors.append(f"无法启动转换进程：{type(exc).__name__}: {exc}")
            return
        slot.proc = proc

    def _dispatch(self) -> list[tuple[int, str]]:
        """把待办任务派给空闲 worker，返回已启动的 (index, 源路径)。

        归属明确：每个槽位一个队列，所以写进哪个队列就等于交给哪个进程。
        """
        started: list[tuple[int, str]] = []
        busy_slots = {slot_index for slot_index, _, _ in self._running.values()}
        for slot_index, slot in enumerate(self._slots):
            if not self._pending:
                break
            if slot_index in busy_slots or not slot.alive():
                continue
            spec = self._pending.popleft()
            slot.task_q.put(spec)
            self._running[spec.index] = (slot_index, spec, time.monotonic())
            started.append((spec.index, spec.source))
        return started

    def _drain(self) -> list[tuple[int, ConversionOutcome, float | None]]:
        """收结果，返回 (index, 结果, 超时秒数或 None)。

        已超时/已取消任务迟到的结果直接丢弃，避免同一行收到两次结果。
        """
        done: list[tuple[int, ConversionOutcome, float | None]] = []
        while True:
            try:
                index, outcome = self._result_q.get_nowait()
            except Empty:
                return done
            if index in self._running:
                self._running.pop(index)
                done.append((index, outcome, None))

    def _watchdog(self) -> list[tuple[int, ConversionOutcome, float | None]]:
        """超时硬杀 + 崩溃检测（转换器把子进程干掉的兜底）。

        先判崩溃再判超时：进程已死就没必要再等超时。
        """
        done: list[tuple[int, ConversionOutcome, float | None]] = []
        now = time.monotonic()
        for index, (slot_index, spec, started_at) in list(self._running.items()):
            slot = self._slots[slot_index]
            elapsed = now - started_at
            if not slot.alive():
                # 进程死了却没回结果：不能让界面永远停在"转换中"
                self._running.pop(index)
                done.append((
                    index,
                    ConversionOutcome(
                        source=spec.source,
                        ok=False,
                        error=f"子进程异常退出（转换器崩溃），{elapsed:.1f} 秒后终止",
                        error_code="crash",
                    ),
                    None,
                ))
                continue
            if elapsed > spec.timeout_s:
                self._kill(slot.proc)
                slot.proc = None
                self._running.pop(index)
                done.append((
                    index,
                    ConversionOutcome(
                        source=spec.source,
                        ok=False,
                        error=f"转换超时（超过 {spec.timeout_s:g} 秒），已终止",
                        error_code="timeout",
                    ),
                    elapsed,
                ))
        return done

    @staticmethod
    def _cancelled_outcome(spec: JobSpec) -> ConversionOutcome:
        """构造"已取消"结果：让界面能把每一行都收敛到终态。"""
        return ConversionOutcome(source=spec.source, ok=False, error=_CANCELLED, error_code=CANCELLED_CODE)

    @staticmethod
    def _log_failure(outcome: ConversionOutcome) -> None:
        """把失败结果写进日志；成功与用户取消不记，避免日志被正常流程刷满。

        在父进程侧记录而不是子进程侧：子进程的日志不会回到父进程的 handler，
        而且子进程崩溃时根本没有机会写。日志只记路径与原因首行（见 `_first_line`）。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        if outcome.ok or outcome.error == _CANCELLED:
            return
        _logger.warning("转换失败：%s（%s）", outcome.source, _first_line(outcome.error))

    @staticmethod
    def _kill(proc: Any) -> None:
        """硬杀子进程并回收，避免留下僵尸进程。"""
        if proc is None:
            return
        try:
            if proc.is_alive():
                proc.terminate()
            proc.join(_JOIN_TIMEOUT_S)
        except (AssertionError, ValueError):
            # 未启动或已被回收的进程：无需处理
            pass
