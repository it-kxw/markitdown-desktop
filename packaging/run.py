# SPDX-License-Identifier: MIT
"""PyInstaller 入口脚本。

`freeze_support()` 必须早于任何 GUI/主流程：冻结包里的 worker 是用
`--multiprocessing-fork` 重新拉起本 exe 的，若不早返回，每个 worker 都会再开一个窗口
（P0 踩过）。但它**不能是第一件事**：spawn 引导窗口内父进程被杀时，子进程还没进
`child_main`，看门狗必须已经装上（P2 T21 实测：晚装会残留孤儿进程不退）；
而且引导失败后必须当场 `os._exit`，否则解释器收尾会把进程挂死。
所以顺序是「装看门狗 → `freeze_support()`（失败即 `os._exit`）→ `main()`」。

包 __init__ 会先执行 `_compat.preload_heavy_deps()`，
因此 pandas 一定早于 PySide6 导入（规避 shiboken6 的 import hook 崩溃）。
"""

import multiprocessing
import os
import sys

if __name__ == "__main__":
    from markitdown_desktop._child import start_spawn_child_watchdog

    # 必须早于 freeze_support()：引导窗口只有 1~2 s，晚一步装看门狗就补不上
    start_spawn_child_watchdog(sys.argv)
    try:
        multiprocessing.freeze_support()
    except BaseException:  # noqa: BLE001 - 引导失败 = 父进程已死，必须当场退
        # 为什么不能让它自然退出：异常之后解释器进入收尾（finalize），此时主线程
        # 握着 GIL 不放，看门狗线程连一行 Python 都跑不了（实测栈：看门狗卡在
        # `_trace` 的 `open()`，主线程 `<no Python frame>`），进程会永久挂着。
        # os._exit 直接从 C 层终止，不进收尾。
        os._exit(1)

    from markitdown_desktop.__main__ import main

    sys.exit(main())
