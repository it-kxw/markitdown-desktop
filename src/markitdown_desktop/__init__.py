# SPDX-License-Identifier: MIT
"""MarkItDown Desktop：MarkItDown 的 Windows 桌面外壳。

@author 康小汪【kxw】
@date 2026-02（创建）、2026-10-13（T11 随发布版升到 0.1.0，顺手补署名）、2026-10-15（随 v0.1.1 升到 0.1.1；随 v0.1.2 升到 0.1.2 —— UI 现代化版本）
"""

# 版本号在此处唯一维护：pyproject.toml 里还有一份，二者由
# tests/test_installer.py::test_version_is_synced 强制一致（安装包版本也取这一份）。
__version__ = "0.1.2"

# 必须最先执行：PySide6 与 pandas/six 存在导入顺序冲突，详见 _compat 模块文档
from ._compat import preload_heavy_deps

preload_heavy_deps()

__all__ = ["__version__"]
