# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""PyInstaller 打包配置（one-folder）。

关键点（对应可行性评估 §6 风险 1、2）：
- magika 的模型数据必须显式收集，否则冻结后格式嗅探失效；
- onnxruntime 的动态库由 pyinstaller-hooks-contrib 的 hook 处理；
- 复制 markitdown 的包元数据，保证 importlib.metadata 的 entry point
  （插件发现）在冻结环境中仍可用。

体积裁剪通过环境变量 `MDD_TRIM` 控制：
- `none`       : 不裁剪，作为体积基线；
- `safe`（默认）: 只裁“确定不会被本地转换路径用到”的文件（Qt 翻译、Pillow AVIF、
                 Qt 网络/TLS 插件、sqlite3、QtNetwork 等），预计省约 20 MB；
- `aggressive` : 在 safe 基础上再裁 `opengl32sw.dll`（软件 OpenGL 回退，20 MB）
                 与 `qdirect2d` 平台插件，代价是极端环境（无 GPU 驱动的 VM/RDP）
                 下的兼容性需要自行验证。

裁剪只针对 Analysis 产出的 `datas` / `binaries` 列表，不改动 PYZ 中的 Python 模块
（`sqlite3` 模块仍在，只是其扩展模块被移除，因此一旦被 import 会得到 ImportError，
而不会静默降级）。每次构建会写出 `dist/trim-report.txt` 记录裁掉了什么。
"""

import fnmatch
import os

from PyInstaller.utils.hooks import collect_all, copy_metadata

# --------------------------------------------------------------------------
# 输出编码兜底（必须在任何 print 之前执行）
# --------------------------------------------------------------------------
# spec 是**在 PyInstaller 进程里**被 exec 的，所以 src/markitdown_desktop/console.py
# 的兜底不会被自动套用；而本文件后面会 print 中文。在 en-US 机器上（stdout=cp1252、
# errors=strict）那行 print 会抛 UnicodeEncodeError，把整次构建打掉 ——
# 2026-10-14 的 release.yml 首次真跑就是这么红的（本机 PYTHONIOENCODING=cp1252
# 已逐字复现，见 docs/p2-verification.md §15）。
# 复用同一个实现，不另写一份会漂移的副本（探针确认 spec 执行期可 import，
# 因为 venv 是 `pip install -e .`，src 在 sys.path 上）。
from markitdown_desktop.console import make_output_safe

make_output_safe()

# --------------------------------------------------------------------------
# 裁剪规则
# --------------------------------------------------------------------------

#: 需要保留的 Qt 翻译（其余全部裁掉）
TRANSLATIONS_KEEP = {
    "qt_en.qm",
    "qtbase_en.qm",
    "qt_help_en.qm",
    "qt_zh_CN.qm",
    "qtbase_zh_CN.qm",
    "qt_help_zh_CN.qm",
}

#: safe 档裁剪：glob 模式 → 理由
TRIM_SAFE = [
    ("PySide6/translations/*", "Qt 自带翻译，仅保留 en / zh_CN（含 help）"),
    ("PIL/_avif*.pyd", "Pillow AVIF 插件：markitdown 不解析 AVIF 图像内容"),
    ("PySide6/plugins/tls/*", "Qt TLS 后端：本应用不使用 QtNetwork 发请求"),
    ("PySide6/plugins/networkinformation/*", "Qt 网络信息插件：未使用"),
    ("PySide6/plugins/generic/qtuiotouchplugin.dll", "触摸输入插件：面向嵌入式设备"),
    ("PySide6/plugins/imageformats/qtiff.dll", "TIFF 图像显示插件：UI 不渲染图片"),
    ("PySide6/plugins/imageformats/qicns.dll", "ICNS 图标插件：macOS 专用"),
    ("PySide6/plugins/imageformats/qtga.dll", "TGA 图像显示插件：UI 不渲染图片"),
    ("PySide6/plugins/imageformats/qwbmp.dll", "WBMP 图像显示插件：UI 不渲染图片"),
    ("PySide6/plugins/imageformats/qgif.dll", "GIF 图像显示插件：UI 不渲染图片"),
    ("PySide6/Qt6Network.dll", "Qt 网络库：转换走 requests，不使用 QtNetwork"),
    ("PySide6/QtNetwork.pyd", "PySide6 QtNetwork 绑定：未使用"),
    ("sqlite3.dll", "SQLite：本地转换路径不读写 SQL 数据库"),
    ("_sqlite3.pyd", "SQLite 扩展模块：同上"),
]

#: aggressive 档额外裁剪
TRIM_AGGRESSIVE = [
    (
        "PySide6/opengl32sw.dll",
        "Mesa 软件 OpenGL 回退（20 MB）：Qt Widgets 用光栅绘制；"
        "风险是无 GPU 驱动的 VM/RDP 环境若强行走 OpenGL 会失败",
    ),
    ("PySide6/plugins/platforms/qdirect2d.dll", "Direct2D 平台插件：默认使用 qwindows"),
]

#: 无论如何都不裁（保留说明，避免后人误删）
KEEP_ALWAYS = [
    "PySide6/plugins/platforms/qwindows.dll",
    "PySide6/plugins/platforms/qoffscreen.dll",  # --selftest --offscreen 需要
    "PySide6/plugins/styles/qmodernwindowsstyle.dll",
    "PySide6/plugins/imageformats/qsvg.dll",
    "PySide6/plugins/iconengines/qsvgicon.dll",
    "PySide6/Qt6Svg.dll",
    "magika/models/*",
]


def _should_drop(dest: str, patterns: list[tuple[str, str]]) -> str | None:
    """返回命中的裁剪理由；未命中返回 None。"""
    normalized = dest.replace("\\", "/")
    for pattern, reason in patterns:
        if not fnmatch.fnmatch(normalized, pattern):
            continue
        # 翻译例外：保留白名单
        if normalized.startswith("PySide6/translations/"):
            if os.path.basename(normalized) in TRANSLATIONS_KEEP:
                return None
        return reason
    return None


def _trim(entries: list, patterns: list[tuple[str, str]], report: list[str]) -> list:
    """按 glob 规则过滤 PyInstaller 的 datas / binaries 列表。"""
    kept = []
    for entry in entries:
        dest = entry[0]
        reason = _should_drop(dest, patterns)
        if reason is None:
            kept.append(entry)
        else:
            report.append(f"DROP {dest}\t({reason})")
    return kept


# --------------------------------------------------------------------------
# 许可文件收集
# --------------------------------------------------------------------------

PROJECT_ROOT = os.path.dirname(SPECPATH)


def _collect_legal_docs() -> list[tuple[str, str]]:
    """收集 LICENSE、第三方通告与许可证正文（T10 产出）。

    文件不存在就跳过：T9 要能在 T10 之前先跑通，不能因为缺通告文件构建失败。
    放到包内根目录（与 exe 同一层），安装包才能一并收录并供用户查阅。

    `licenses/` 整目录收录（而不是只取平铺文件）：T10 的布局是一包一目录，
    且 numpy / pypdfium2 的许可证正文还在更深的子目录里，逐个列文件会漏且会拍平结构。
    """
    docs: list[tuple[str, str]] = []
    for name in ("LICENSE", "THIRD-PARTY-NOTICES.md"):
        path = os.path.join(PROJECT_ROOT, name)
        if os.path.isfile(path):
            docs.append((path, "."))
    licenses_dir = os.path.join(PROJECT_ROOT, "licenses")
    if os.path.isdir(licenses_dir):
        docs.append((licenses_dir, "licenses"))
    return docs


# --------------------------------------------------------------------------
# 依赖收集
# --------------------------------------------------------------------------

# magika：数据（模型）+ 二进制 + 隐式导入一次性收集
magika_datas, magika_binaries, magika_hidden = collect_all("magika")

datas = list(magika_datas)
datas += copy_metadata("markitdown")
datas += copy_metadata("markitdown-desktop")
datas += _collect_legal_docs()
# 图标文件本身也要进包：exe 的图标由上面的 icon= 写进 PE 资源，但**运行时窗口图标**
# 需要能读到文件（任务栏 / Alt-Tab），所以随 data 一起打包到 _internal 根。
datas.append((os.path.join(PROJECT_ROOT, "packaging", "icon", "app-icon.ico"), "."))

binaries = list(magika_binaries)

hiddenimports = list(magika_hidden) + [
    "pandas",
    "openpyxl",
    "xlrd",
    "olefile",
    "mammoth",
    "pptx",
    "pdfplumber",
    "pdfminer",
    "markdownify",
    "bs4",
    "lxml.etree",
    "youtube_transcript_api",
    "charset_normalizer",
    "markitdown_desktop.ui",
    "markitdown_desktop.engine",
    "markitdown_desktop._compat",
    # T8/T9：入口与进程池。_child 是 spawn 的按名反查目标（子进程会重新导入它），
    # 即使 worker.py 已静态导入，也显式列出来防止优化/裁剪把它弄丢。
    "markitdown_desktop.__main__",
    "markitdown_desktop.worker",
    "markitdown_desktop._child",
    "markitdown_desktop.settings",
    "markitdown_desktop.logging_setup",
    "multiprocessing",
    "multiprocessing.spawn",
    "multiprocessing.popen_spawn_win32",
    "multiprocessing.resource_tracker",
]

excludes = [
    # 未安装或与本地转换无关的重量级模块
    "azure",
    "azure_ai_documentintelligence",
    "azure_ai_contentunderstanding",
    "azure_identity",
    "tkinter",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtWebEngineCore",
    "PySide6.Qt3DCore",
    "PySide6.QtMultimedia",
    "PySide6.QtNetworkAuth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "matplotlib",
    "pytest",
    "IPython",
]

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

# --------------------------------------------------------------------------
# 应用裁剪
# --------------------------------------------------------------------------

TRIM_LEVEL = os.environ.get("MDD_TRIM", "safe").strip().lower()
trim_report: list[str] = []

if TRIM_LEVEL in ("safe", "aggressive"):
    patterns = list(TRIM_SAFE)
    if TRIM_LEVEL == "aggressive":
        patterns += TRIM_AGGRESSIVE
    before = len(a.datas) + len(a.binaries)
    a.datas = _trim(a.datas, patterns, trim_report)
    a.binaries = _trim(a.binaries, patterns, trim_report)
    after = len(a.datas) + len(a.binaries)
    print(f"[trim] level={TRIM_LEVEL} 条目 {before} -> {after}（裁掉 {before - after} 项）")
elif TRIM_LEVEL == "none":
    print("[trim] level=none（未裁剪）")
else:
    raise SystemExit(f"MDD_TRIM 取值非法：{TRIM_LEVEL!r}（可选 none/safe/aggressive）")

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    # 应用图标（PE 资源里的那个）：由 scripts/make_icon.py 从
    # packaging/icon/app-icon.svg 生成，共 7 个尺寸。写成绝对路径，
    # 避免 spec 被从别的目录调用时解析不到。
    icon=os.path.join(PROJECT_ROOT, "packaging", "icon", "app-icon.ico"),
    name="markitdown-desktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # 正式发布应为窗口化程序
    # 窗口化 traceback 默认**关闭**（弹框会卡住无控制台的发布版，见可行性评估风险 2）；
    # 异常一律写日志 + 界面提示。排查构建/依赖问题时设 MDD_SHOW_TRACEBACK=1 打开弹框。
    disable_windowed_traceback=os.environ.get("MDD_SHOW_TRACEBACK", "0") != "1",
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="markitdown-desktop",
)

# 写出裁剪报告，便于在文档中引用确切的裁剪项
with open(os.path.join(DISTPATH, "trim-report.txt"), "w", encoding="utf-8") as fh:
    fh.write(f"trim level: {TRIM_LEVEL}\n")
    fh.write(f"dropped entries: {len(trim_report)}\n\n")
    fh.write("\n".join(sorted(trim_report)))
    fh.write("\n")
