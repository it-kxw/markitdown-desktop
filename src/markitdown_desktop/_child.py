# SPDX-License-Identifier: MIT
"""子进程侧实现：任务描述与转换循环（**不含任何 Qt 依赖**）。

为什么单独一个模块：multiprocessing 的 spawn 会按名字反查并导入子进程入口函数
所在的模块。若入口函数放在 `worker.py`（它必须导入 QThread），每个 worker 都会
被迫加载整个 PySide6——既白付导入成本，又让 Qt 在子进程里初始化。
本模块只依赖标准库，`engine` 在函数内延迟导入；实测导入后 sys.modules 中无 PySide6
（由 tests/test_worker_pool.py::test_child_module_does_not_import_qt 守住）。

**父进程看门狗**（P2 T14）：父进程被强杀（任务管理器"结束任务"、崩溃）时，
`multiprocessing` 的 daemon 标记救不了——那套回收逻辑挂在父进程正常退出的钩子上，
父进程被 `TerminateProcess` 时根本不执行。因此子进程自己在守护线程里盯父进程，
发现父进程已终止就 `os._exit(1)`。三条实测结论（探针数据见 p2 计划 T14）：
- `os.getppid()` 在 Windows 上**父进程死后不变**（无 reparent），不能当存活判据；
- `OpenProcess(SYNCHRONIZE)` + `WaitForSingleObject(h, 0) == 0` 是 OS 原生语义，
  父进程一终止立即为真（实测反应 < 0.8 s）；
- 退出必须用 `os._exit`：主线程可能正卡在 C 扩展里，`sys.exit` 只会结束当前线程。
备选的 Win32 Job Object（`KILL_ON_JOB_CLOSE`）不采用：它要求父进程把自己加进 job，
而"父进程已经在别的 job 里"（安装器/CI/调试器启动）时行为不同，风险更大。

**启动时机**（P2 T21）：看门狗必须在**进 `child_main` 之前**就装上。spawn 的引导
（从管道读父进程传来的准备数据）发生在 `child_main` 之前，父进程若在这个窗口内被杀，
子进程还没装上看门狗，就会永久阻塞在管道读上——实测残留进程 3.5 分钟后仍存活
（CPU 静止在 0.92 s、139.9 MB、34 线程）；对照实验：引导窗口内杀 2/2 留孤儿、
worker 就绪后杀 2/2 归零。因此冻结包入口 `packaging/run.py` 在 `freeze_support()`
之前就调 `start_spawn_child_watchdog()`，`child_main` 里那次调用保留（源码启动路径
没有别的时机），靠 `_watchdog_started` 保证只起一个线程。
非冻结的 spawn 子进程命令行是 `python -c "…spawn_main…" --multiprocessing-fork`，
不经过本包入口，所以早启动只覆盖冻结包（发布物）；源码运行的同一窗口未覆盖。

@author 康小汪【kxw】
@date 2026-10-13（P2 T14 增父进程看门狗）、2026-10-14（T21：入口早启动 + 幂等）
"""

from __future__ import annotations

import ctypes
import multiprocessing.spawn
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


_WATCHDOG_PERIOD_S = 2.0  # 检查周期：足够及时，又不至于让空闲 worker 白耗 CPU
_OPEN_FAILURE_LIMIT = 5  # 连续打不开父进程句柄多少次才判死（防误杀，见 _watch_parent）
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0

# 看门狗是否已启动（幂等用）：入口早启动与 child_main 会各调一次
_watchdog_started = False


@dataclass(frozen=True)
class JobSpec:
    """一次转换请求。

    必须可 pickle：它会经 multiprocessing.Queue 送到子进程。
    输出路径的规划与写出由父进程负责（见 engine.plan_outputs），子进程只负责转换；
    因此这里**没有** output_dir / naming —— 两处推导逻辑并存必然漂移。

    Attributes:
        index: 调用方定义的序号（界面用行号），用于把结果对回队列项。
        source: 待转换文件路径。
        timeout_s: 单文件超时秒数；超时由父进程硬杀该 worker。

    @author 康小汪【kxw】
    @date 2026-10-13
    """

    index: int
    source: str
    timeout_s: float = 300.0


def _parent_terminated(parent_pid: int) -> bool | None:
    """父进程是否已终止。

    为什么不用 `multiprocessing.parent_process().is_alive()`：它在 spawn 子进程里
    依赖框架自己重建的句柄，属于实现细节；`OpenProcess` + 零超时等待是 OS 契约。

    Args:
        parent_pid: 父进程 pid。

    Returns:
        bool | None: True=已终止，False=还活着，None=句柄打不开（**不确定**，
            必须由调用方累积判断，不能单次据此判死）。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # 显式声明类型：句柄是 64 位指针，ctypes 默认 restype=c_int 会截断它
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, int(parent_pid))
    if not handle:
        return None
    try:
        return kernel32.WaitForSingleObject(handle, 0) == _WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


def _watch_parent(
    parent_pid: int,
    period_s: float = _WATCHDOG_PERIOD_S,
    exit_process: Callable[[int], None] = os._exit,
) -> None:
    """守护循环：父进程一旦已终止就硬退出本进程。

    句柄打不开（None）时**不立刻退出**，而是连续累计到 `_OPEN_FAILURE_LIMIT`
    次才判死——"权限受限但父进程还活着"时立刻退出会误杀正常 worker；反过来，
    父进程连进程对象都销毁了（pid 已回收）时会持续打不开，累计够次数即判死。

    Args:
        parent_pid: 父进程 pid。
        period_s: 检查周期，测试注入 0 以快速跑完。
        exit_process: 退出钩子，默认 `os._exit`（`sys.exit` 在阻塞线程里退不干净）。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    open_failures = 0
    while True:
        verdict = _parent_terminated(parent_pid)
        if verdict is False:  # 父进程还活着：一次打不开只是偶发，计数清零
            open_failures = 0
        else:
            open_failures += 1
            if verdict is True or open_failures >= _OPEN_FAILURE_LIMIT:
                exit_process(1)
                return
        time.sleep(period_s)


def _start_parent_watchdog(parent_pid: int) -> None:
    """起守护线程盯父进程（非 Windows 平台本应用不发版，直接不启动；幂等）。

    幂等的原因：冻结包入口在 `freeze_support()` 之前先调一次（覆盖 spawn 引导窗口），
    `child_main` 里再调一次（源码启动路径）——重复调用只允许起一个线程。

    Args:
        parent_pid: 父进程 pid，由 `os.getppid()` 采集。

    @author 康小汪【kxw】
    @date 2026-10-13（创建）、2026-10-14（T21：幂等，支持入口早启动）
    """
    global _watchdog_started
    if sys.platform != "win32" or _watchdog_started:
        return
    _watchdog_started = True
    threading.Thread(
        target=_watch_parent,
        args=(parent_pid,),
        name="parent-watchdog",
        daemon=True,
    ).start()


def start_spawn_child_watchdog(argv: list[str]) -> bool:
    """spawn 出来的子进程：在 `freeze_support()` 之前就把看门狗装上。

    为什么需要（P2 T21 实测）：父进程若在 spawn 引导窗口内被强杀，子进程进程已存在、
    却还没进 `child_main`，看门狗根本没装上，于是永久阻塞在引导的管道读上。这个窗口
    只有 1~2 s，等 `child_main` 再装就晚了。

    判据用标准库 `multiprocessing.spawn.is_forking`，不自己解析命令行：真命令行是
    stdlib 拼的，手写判据会跟着版本漂移。

    Args:
        argv: 进程命令行，调用方传 `sys.argv`。

    Returns:
        bool: True 表示本进程是 spawn 子进程且看门狗已启动。

    @author 康小汪【kxw】
    @date 2026-10-14
    """
    if not multiprocessing.spawn.is_forking(argv):
        return False
    _start_parent_watchdog(os.getppid())
    return True


def child_main(task_q: Any, result_q: Any) -> None:
    """子进程主循环：长驻并复用同一个 ConversionEngine，直到收到毒丸（None）。

    长驻的意义：pandas + markitdown 的导入成本（实测约 1 s）只付一次。
    每个进程独占一个引擎实例——MarkItDown 实例并非线程/进程安全的共享对象。

    Args:
        task_q: 本 worker 的专属任务队列；读到 None 表示退出。
        result_q: 全部 worker 共用的结果队列，元素为 (index, ConversionOutcome)。

    Returns:
        None: 函数返回即进程结束（由父进程 join 回收）。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    # 最先启动：父进程可能在 worker 还在导入 pandas 时就被杀，看门狗必须已经就位
    _start_parent_watchdog(os.getppid())
    from .engine import ConversionEngine, ConversionOutcome

    engine = ConversionEngine()
    while True:
        spec = task_q.get()
        if spec is None:
            return
        try:
            result_q.put((spec.index, engine.convert_file(spec.source)))
        except BaseException as exc:  # noqa: BLE001 - 子进程绝不能静默退出
            # engine 已兜住常规异常，能走到这里的是更深层的故障；仍要回一条结果，
            # 否则父进程只能靠"进程死了"来推断，错误信息会丢。
            result_q.put((
                spec.index,
                ConversionOutcome(
                    source=spec.source,
                    ok=False,
                    error=f"子进程转换异常：{type(exc).__name__}: {exc}",
                ),
            ))
