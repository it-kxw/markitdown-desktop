# SPDX-License-Identifier: MIT
"""兼容性补丁：规避 shiboken6 与 six.moves 的导入顺序冲突。

## 现象

PySide6-Essentials 6.12.0 + pandas 3.0.6 + six 1.17.0，只要 PySide6 先于 pandas 导入：

    python -c "from PySide6.QtWidgets import QApplication; import pandas"
    AttributeError: '_SixMetaPathImporter' object has no attribute '_path'

最小复现只需 dateutil：

    python -c "from PySide6.QtWidgets import QApplication; import dateutil.tz"

## 调用链

1. 导入 PySide6 后，shiboken6 会安装导入钩子
   `shibokensupport.feature.feature_imported`（源码编译进 shiboken6.abi3.dll）；
2. 该钩子对之后导入的每个模块调用 `inspect.getsource()`，判断模块是否使用
   `__feature__` 语法；
3. pandas 在 `pandas._libs.tslibs.timezones` 阶段导入 `dateutil.tz`，
   后者执行 `from six.moves import _thread`；
4. `six.moves._thread` 由 six 自己的 meta path importer（`_SixMetaPathImporter`）提供，
   该对象的 `__spec__` 里 `origin is None`、`loader` 没有 `_path`；
5. `inspect.getsource()` 失败时 CPython 需要格式化异常信息，调用 `repr(module)` →
   `importlib._bootstrap._module_repr_from_spec()` →
   `list(spec.loader._path)` → AttributeError，异常逃逸并中断 pandas 导入。

## 规避方式（两道保险）

1. **补属性**：给 `six._importer` 补上缺失的 `_path`，使 `repr()` 正常返回，
   `inspect.getsource()` 退化为普通的 `TypeError: ... is a built-in module`，
   而该 TypeError 会被 shiboken 钩子正常处理。此方式与导入顺序无关，
   因此对 PyInstaller 冻结包（运行时 hook 会提前导入 PySide6）同样有效。
2. **调顺序**：随后才导入 pandas / markitdown，使 `dateutil`、`six.moves.*`
   在 shiboken 钩子安装之前就进入 `sys.modules`，从源头减少触发机会。

本模块由包 `__init__` 在任何子模块之前调用，因此 `import markitdown_desktop`、
`python -m markitdown_desktop`、PyInstaller 入口脚本三条路径都已覆盖。
"""

from __future__ import annotations

import sys
from pathlib import Path

_APPLIED = False
# 应用图标文件名：冻结包内位于 _MEIPASS 根（PyInstaller 把 datas 的 "." 目标放进
# _internal），源码运行时位于 packaging/icon/ 下。见 icon_path()。
_ICON_FILENAME = "app-icon.ico"


def _patch_six_importer_repr() -> bool:
    """给 six 的 meta path importer 补 `_path`，返回是否打补丁成功。"""
    try:
        import six
    except ImportError:
        return False

    importer = getattr(six, "_importer", None)
    if importer is None or hasattr(importer, "_path"):
        return False

    # 仅用于 _module_repr_from_spec() 中 list(spec.loader._path)，空元组即可
    importer._path = ()
    return True


def preload_heavy_deps() -> None:
    """应用补丁并预导入 pandas / markitdown。幂等。"""
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True

    patched = _patch_six_importer_repr()

    # 顺序不可调整：pandas 必须先于 PySide6 进入 sys.modules
    import pandas  # noqa: F401
    import markitdown  # noqa: F401

    assert "pandas" in sys.modules, "pandas 预导入失败"
    if patched:
        # 正常路径（six 已安装且补丁生效）
        assert hasattr(sys.modules["six"]._importer, "_path")


def donate_dir() -> Path:
    """返回打赏收款码所在目录：冻结包取 `_MEIPASS/donate`，源码运行取 `packaging/donate`。

    为什么走 `_compat`：这是**冻结包路径**问题，与 `icon_path()` 同一类。
    直接用 `__file__` 的相对路径在冻结后会指到临时解包目录外面（字体当初就踩过这个坑，
    见 `theme._font_dir` 的注释）。

    @author 康小汪【kxw】
    @date 2026-10-16

    Returns:
        Path: 目录路径。调用方仍需自行判断文件是否存在：精简环境或源码直跑时
        可能没有这两张图，此时应降级而不是让界面起不来。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "donate"
    return Path(__file__).resolve().parents[2] / "packaging" / "donate"


def icon_path() -> Path:
    """返回应用图标 ICO 的路径：冻结包取 _MEIPASS，源码运行取仓库 packaging/icon。

    @author 康小汪【kxw】
    @date 2026-10-15

    Returns:
        Path: 图标路径。注意调用方仍需自行判断 `is_file()`：源码直跑或精简环境下
        文件可能不存在，此时应静默跳过而不是让界面起不来。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / _ICON_FILENAME
    return Path(__file__).resolve().parents[2] / "packaging" / "icon" / _ICON_FILENAME
