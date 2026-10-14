# SPDX-License-Identifier: MIT
"""应用设置持久化：输出目录、命名策略、并发度、窗口几何。

设计要点（均经 Windows 实测确认，不是推测）：
- **键名用扁平形式**（`output_dir`、`auto_save`…）而不是 `general/output_dir`：
  Qt 的 ini 解析器把 `[general]` 段当作**根段**，写成 `general/output_dir` 后
  用户手改 `[general]` 时根本读不到（键会落到根级），是静默失效。
  窗口状态才用真正的子段 `window/geometry`。
- 生产环境走 QSettings 默认后端（Windows 上即注册表
  `HKCU\\Software\\MarkItDownDesktop`）；测试一律注入 `ini_path`，不污染真实注册表。
  T11 的卸载器必须删除该键，才能满足"卸载无残留"。
- **配置文件是外部输入**：缺键、空值、手改出来的垃圾值都必须收敛成默认值，
  绝不抛异常中断启动。实测到的两个坑：
  1. 写入 `None` 会存成 `@Invalid()`，若用 `type=str` 读会得到空串——空串被当成
     路径就写到了当前工作目录，因此统一走 `_as_optional_str`；
  2. 手写段落里的值只是普通字符串，`bytes("notbytes")` 直接抛 TypeError——因此
     窗口几何走 `_as_bytes`，非二进制值一律当作"没存过"。
  布尔则直接用 `type=bool`：实测它把 `"false"`/`"0"`/`""` 都正确转成 False，
  不需要额外包装。
- QSettings 非线程安全，本类只在 GUI 线程使用（进程池的 worker 不读设置）。

@author 康小汪【kxw】
@date 2026-10-13
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QSettings

from .engine import NamingPolicy
from .i18n import LANGUAGE_AUTO, SUPPORTED_LANGUAGES

# 并发度上下限与默认值：设置对话框、进程池、设置层共用同一组常量，
# 避免两处魔法数字各自漂移
MIN_CONCURRENCY = 1
MAX_CONCURRENCY = 4
DEFAULT_CONCURRENCY = 2

#: 界面语言的可选值（`auto` = 跟随系统）；非法值一律回退 auto
LANGUAGE_CHOICES = (LANGUAGE_AUTO, *SUPPORTED_LANGUAGES)


def _as_optional_str(raw: Any) -> str | None:
    """把 QSettings 的返回值收敛成 `str | None`。

    缺键读到 None，写进去的 None 会被存成 `@Invalid()`、用 `type=str` 读回来是
    **空串**；两种都必须变成 None——否则界面会把 `''` 当路径，输出落到工作目录。

    Args:
        raw: QSettings 的原始返回值。

    Returns:
        str | None: 非空字符串原样返回，其余（None/空串/非字符串）一律 None。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    if isinstance(raw, str):
        return raw or None
    return None


def _as_bytes(raw: Any) -> bytes | None:
    """把窗口几何/分割条状态收敛成 `bytes | None`。

    手改过的垃圾值（普通字符串）直接当作"没存过"：`bytes("notbytes")`
    会抛 `TypeError: string argument without an encoding`，启动即崩。

    Args:
        raw: QSettings 的原始返回值。

    Returns:
        bytes | None: QByteArray/bytes 原样转换，其余一律 None。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    if isinstance(raw, QByteArray):
        return bytes(raw)
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    return None


def _clamp_concurrency(value: Any) -> int:
    """把并发度收敛到 [MIN_CONCURRENCY, MAX_CONCURRENCY]；非法值回到默认值。

    自己解析而不用 `type=int`：越界值必须被钳制，非法值必须回退，
    不能把异常抛给界面。

    Args:
        value: 原始值，可能来自界面输入或手改的配置文件。

    Returns:
        int: 合法范围内的并发度。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CONCURRENCY
    return max(MIN_CONCURRENCY, min(MAX_CONCURRENCY, number))


class AppSettings:
    """应用设置的读写封装（QSettings）。

    写入是惰性的：改完属性后由调用方在合适的时机 `sync()` 落盘。

    Args:
        ini_path: 仅测试注入；为 None 时走 QSettings 默认后端（Windows 注册表）。

    @author 康小汪【kxw】
    @date 2026-10-13
    """

    def __init__(self, ini_path: Path | None = None) -> None:
        if ini_path is not None:
            self._s = QSettings(str(ini_path), QSettings.Format.IniFormat)
        else:
            self._s = QSettings("MarkItDownDesktop", "MarkItDownDesktop")

    @property
    def output_dir(self) -> str | None:
        """统一输出目录；None 表示输出到源文件所在目录。"""
        return _as_optional_str(self._s.value("output_dir", None))

    @output_dir.setter
    def output_dir(self, value: str | None) -> None:
        self._s.setValue("output_dir", value)

    @property
    def naming_policy(self) -> NamingPolicy:
        """批量输出的重名处理策略；存了非法值就回退默认，不抛异常。"""
        raw = self._s.value("naming_policy", NamingPolicy.SUFFIX.value)
        try:
            return NamingPolicy(raw)
        except (ValueError, TypeError):
            return NamingPolicy.SUFFIX

    @naming_policy.setter
    def naming_policy(self, value: NamingPolicy) -> None:
        self._s.setValue("naming_policy", value.value)

    @property
    def concurrency(self) -> int:
        """并发转换数，钳制在 [MIN_CONCURRENCY, MAX_CONCURRENCY]。"""
        return _clamp_concurrency(self._s.value("concurrency", DEFAULT_CONCURRENCY))

    @concurrency.setter
    def concurrency(self, value: int) -> None:
        self._s.setValue("concurrency", _clamp_concurrency(value))

    @property
    def auto_save(self) -> bool:
        """转换完成后是否自动写出 .md。"""
        return self._s.value("auto_save", True, type=bool)

    @auto_save.setter
    def auto_save(self, value: bool) -> None:
        self._s.setValue("auto_save", bool(value))

    @property
    def language(self) -> str:
        """界面语言：`auto`（跟随系统）/ `zh_CN` / `en`；非法值回退 auto。

        与 `naming_policy` 同口径：配置文件是外部输入，垃圾值不能中断启动。

        @author 康小汪【kxw】
        @date 2026-10-14
        """
        raw = self._s.value("language", LANGUAGE_AUTO)
        return raw if raw in LANGUAGE_CHOICES else LANGUAGE_AUTO

    @language.setter
    def language(self, value: str) -> None:
        self._s.setValue("language", value if value in LANGUAGE_CHOICES else LANGUAGE_AUTO)

    @property
    def window_geometry(self) -> bytes | None:
        """主窗口几何（QMainWindow.saveGeometry 的结果）。"""
        return _as_bytes(self._s.value("window/geometry", None))

    @window_geometry.setter
    def window_geometry(self, value: bytes | None) -> None:
        self._s.setValue("window/geometry", QByteArray(value) if value is not None else None)

    @property
    def splitter_state(self) -> bytes | None:
        """列表/预览分割条状态（QSplitter.saveState 的结果）。"""
        return _as_bytes(self._s.value("window/splitter", None))

    @splitter_state.setter
    def splitter_state(self, value: bytes | None) -> None:
        self._s.setValue("window/splitter", QByteArray(value) if value is not None else None)

    @property
    def last_open_dir(self) -> str | None:
        """上次打开文件的位置，供文件对话框定位。"""
        return _as_optional_str(self._s.value("last_open_dir", None))

    @last_open_dir.setter
    def last_open_dir(self, value: str | None) -> None:
        self._s.setValue("last_open_dir", value)

    def sync(self) -> None:
        """立即落盘（关闭窗口前调用）。"""
        self._s.sync()
