# SPDX-License-Identifier: MIT
"""日志与未捕获异常钩子。

设计要点（对应可行性评估 §3.4 与 P0 的 Landmine #2）：
- 冻结包 `console=False` 时未捕获异常会弹**阻塞式对话框**，用户拿不到任何可用信息。
  这里把完整 traceback 落盘，UI 只收一行摘要，配合"打开日志目录"按钮定位问题；
- 日志按 2 MB × 6 轮转，长期使用不会吃满用户磁盘；
- 用户可一键把日志打包发出来（`export_logs`）：报障时这是唯一能拿到的现场；
- 日志目录可用环境变量 `MDD_LOG_DIR` 覆盖（测试隔离与便携版自定义用）。

@author 康小汪【kxw】
@date 2026-10-13
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import traceback
import zipfile
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable

# 用户数据目录。刻意与安装目录 %LOCALAPPDATA%\Programs\... 分开：卸载时应保留用户日志，
# 且安装目录可能只读。
_APP_DIR = "MarkItDownDesktop"
_LOG_NAME = "app.log"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 5
# UI 摘要长度上限：单行足够放下 "TypeError: xxx" 这类常见信息，且不会撑爆状态栏
_SUMMARY_LIMIT = 200

_log_path: Path | None = None
_handler: RotatingFileHandler | None = None
_on_error: Callable[[str], None] | None = None
# 原始钩子只链一次，避免重复安装时套娃（一次异常被记多遍）
_chained_excepthook: Callable[..., Any] | None = None
_chained_threading_hook: Callable[..., Any] | None = None


def log_dir() -> Path:
    """返回日志目录，不存在时创建。

    环境变量 `MDD_LOG_DIR` 优先，其次是 `%LOCALAPPDATA%\\MarkItDownDesktop\\logs`。

    Returns:
        Path: 日志目录。

    Raises:
        OSError: 目录无法创建（磁盘满/权限不足）时抛出，由调用方决定是否降级。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    override = os.environ.get("MDD_LOG_DIR")
    base = (
        Path(override)
        if override
        else Path(os.environ.get("LOCALAPPDATA", Path.home())) / _APP_DIR / "logs"
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


def init_logging(level: int = logging.INFO) -> Path:
    """初始化文件日志。

    按**目标路径**判断幂等：同一路径重复调用直接复用，路径变化时自动摘掉旧 handler
    再挂新的（否则每行日志会被写两遍，轮转也会提前触发）。

    Args:
        level: 根 logger 级别，默认 INFO。

    Returns:
        Path: 当前日志文件路径。

    Raises:
        OSError: 日志目录不可创建或文件不可写时抛出。调用方（程序入口）必须兜住——
            日志不可用不应阻断应用启动。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    global _log_path, _handler
    target = log_dir() / _LOG_NAME
    if _handler is not None and _log_path == target:
        return target

    root = logging.getLogger()
    if _handler is not None:
        root.removeHandler(_handler)
        _handler.close()

    _handler = RotatingFileHandler(
        target, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    _handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(process)d] %(name)s: %(message)s")
    )
    root.setLevel(level)
    root.addHandler(_handler)
    # PySide6 在 DEBUG 级别会刷屏，压到 WARNING 保持日志可读
    logging.getLogger("PySide6").setLevel(logging.WARNING)
    _log_path = target
    logging.getLogger(__name__).info("日志初始化：%s", target)
    return target


def _summary_of(exc_type: type[BaseException], exc: BaseException) -> str:
    """生成给 UI 展示的单行摘要。

    Args:
        exc_type: 异常类型。
        exc: 异常实例。

    Returns:
        str: `类型: 消息` 形式的单行文本，已折叠换行并截断到 _SUMMARY_LIMIT。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    text = f"{exc_type.__name__}: {exc}".replace("\n", " ").replace("\r", " ")
    return text.strip()[:_SUMMARY_LIMIT]


def _report_unhandled(
    exc_type: type[BaseException], exc: BaseException, tb: Any
) -> None:
    """未捕获异常的统一处理：完整 traceback 落盘，一行摘要通知 UI。

    Args:
        exc_type: 异常类型。
        exc: 异常实例。
        tb: traceback 对象，可能为 None。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    logger = logging.getLogger("markitdown_desktop.unhandled")
    logger.critical("未捕获异常：\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
    if _on_error is None:
        return
    try:
        _on_error(_summary_of(exc_type, exc))
    except Exception:  # noqa: BLE001 - 通知失败不能掩盖原异常
        logger.exception("异常通知回调失败")


def _hook(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
    """`sys.excepthook` 的替换实现（主线程）。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    _report_unhandled(exc_type, exc, tb)
    if _chained_excepthook is not None:
        _chained_excepthook(exc_type, exc, tb)


def _thread_hook(args: threading.ExceptHookArgs) -> None:
    """`threading.excepthook` 的替换实现（子线程，转换工作线程是主要来源）。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    _report_unhandled(args.exc_type, args.exc_value, args.exc_traceback)
    if _chained_threading_hook is not None:
        _chained_threading_hook(args)


def install_excepthooks(on_error: Callable[[str], None] | None = None) -> None:
    """接管主线程与子线程的未捕获异常。

    可重复调用：只替换 `on_error` 回调，不会把钩子层层套娃；原始的
    `sys.excepthook` / `threading.excepthook` 只链一次，以免遮蔽调试器或测试框架的钩子。

    Args:
        on_error: 可选回调，收到 `异常类型: 消息` 形式的**单行摘要**（已截断）。
            完整 traceback 只写日志文件，不上屏。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    global _on_error, _chained_excepthook, _chained_threading_hook
    _on_error = on_error
    if _chained_excepthook is None:
        _chained_excepthook = sys.excepthook
        _chained_threading_hook = threading.excepthook
    sys.excepthook = _hook
    threading.excepthook = _thread_hook


def tail_log(lines: int = 200) -> str:
    """读取日志末尾若干行，供 UI 展示。

    Args:
        lines: 返回的最大行数，默认 200。

    Returns:
        str: 日志尾部文本；文件不存在时返回空串。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    path = _log_path or (log_dir() / _LOG_NAME)
    if not path.exists():
        return ""
    # deque(maxlen=N) 只保留尾部 N 行，避免把 2 MB 日志整体读进内存；
    # errors="replace" 保证日志里混入非法字节时也不会让 UI 崩掉
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return "".join(deque(handle, maxlen=lines))


def export_logs(target: Path) -> Path:
    """把日志目录下的 `app.log*` 打包成 zip，供用户一键发出来排查问题。

    两处刻意设计：
    - **只记 `app.log*`**：当前日志与全部轮转文件一份不漏；
    - **没有日志就报错**：产出一个空 zip 会让用户以为导出成功，白丢一轮沟通。

    刻意**不做** flush：`RotatingFileHandler` 继承 `StreamHandler`，每条记录写完
    立即 flush，导出时磁盘上的内容已经是最新的（实测确认，勿再加无用同步）。

    Args:
        target: 目标 zip 路径；未以 `.zip` 结尾时自动补上。

    Returns:
        Path: 实际写入的 zip 路径。

    Raises:
        FileNotFoundError: 日志目录里没有任何 `app.log*` 文件。
        OSError: 目录不可创建或 zip 不可写时抛出，由调用方提示用户。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    directory = log_dir()
    target = Path(target)
    if target.suffix.lower() != ".zip":
        target = target.with_suffix(".zip")
    files = sorted(path for path in directory.glob(f"{_LOG_NAME}*") if path.is_file())
    if not files:
        raise FileNotFoundError(f"日志目录里没有 {_LOG_NAME}*：{directory}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, arcname=path.name)
    logging.getLogger(__name__).info("已导出日志：%s（%d 个文件）", target, len(files))
    return target


def open_log_dir() -> None:
    """在文件管理器中打开日志目录（仅 Windows 有实现）。

    非 Windows 平台只记一条警告：不能为不支持的平台引入额外依赖（YAGNI），
    但也不能静默吞掉——否则用户点了按钮毫无反应又查不到原因。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    directory = log_dir()
    if os.name != "nt":
        logging.getLogger(__name__).warning(
            "open_log_dir 仅支持 Windows，当前平台：%s", os.name
        )
        return
    os.startfile(directory)  # type: ignore[attr-defined]  # noqa: S606 - 固定目录，无注入风险
