# -*- coding: utf-8 -*-
"""主题：语义色 token、尺寸标尺与 QSS 生成（T29 / B1）。

**为什么单独一个模块**：`ui.py` 已经 983 行，把配色、圆角、字号、字体注册全塞进去
会很难维护；而主题是"纯数据 + 纯字符串生成"，与 Qt 控件树解耦，可以离线测试
（不需要起窗口），所以单独立文件。

设计依据来自 `ui-ux-pro-max` 技能的实测输出（见 `docs/t29-ui-redesign-plan.md` §1）：

- 风格 **Swiss Modernism 2.0**：网格、数学间距、最少装饰、**单一强调色**；
- 字体 **Inter**（工具 typography 输出的 Heading/Body 都是 Inter）；
- 主色 `#2563EB` 与 `packaging/icon/app-icon.svg` 的品牌蓝**完全一致**；
- 强调色工具给 `#D97706`，但实测在白底只有 3.19:1（不达 4.5:1），**压深为 `#B45309`**（5.02:1）；
- 工具的 Anti-pattern 明确列出「Playful design」「AI purple/pink gradients」
  → 因此本模块**不产生任何渐变**（`tests/test_theme.py` 会守这一点）。

**三个实测过的 Qt 行为**（探针结论，写在代码里免得后人重踩）：

1. `QFontDatabase.addApplicationFont()` 成功返回 `>= 0` 的 id，**失败返回 `-1`**
   —— 非字体文件、不存在的路径都是 `-1`；
2. `QGuiApplication.styleHints().colorScheme()` 在**真窗口**下能读到 `Dark`/`Light`，
   但在 **`QT_QPA_PLATFORM=offscreen`（测试环境）下返回 `Unknown`**
   → 所以 `auto` 必须有兜底，否则测试与真机行为不一致；
3. QSS 里的 `font-family` 只作用于**控件**，不改 `QApplication.font()`
   —— 想统一字体得同时 `app.setFont()`。

@author 康小汪【kxw】
@date 2026-10-15
"""

from __future__ import annotations

import sys
from pathlib import Path
from collections.abc import Callable
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import QApplication

# --------------------------------------------------------------------------- 主题名

THEME_AUTO: Final = "auto"
THEME_LIGHT: Final = "light"
THEME_DARK: Final = "dark"
THEME_CHOICES: Final = (THEME_AUTO, THEME_LIGHT, THEME_DARK)

# --------------------------------------------------------------------------- 色板

#: 浅色语义色。键名即用途，别用 `color1` 这种名字。
LIGHT_PALETTE: Final[dict[str, str]] = {
    "primary": "#2563EB",          # 品牌蓝，与 app-icon.svg 一致
    "primary_hover": "#1D4ED8",
    "primary_active": "#1E40AF",
    "on_primary": "#FFFFFF",
    "accent": "#B45309",           # 工具给 #D97706 但只有 3.19:1，压深后才达标
    "ok": "#15803D",
    "warn": "#B45309",
    "error": "#DC2626",
    "bg": "#F8FAFC",
    "surface": "#FFFFFF",
    "surface_2": "#F1F5F9",
    "surface_3": "#E9EEF6",
    "fg": "#0F172A",
    "fg_2": "#475569",
    "fg_3": "#64748B",
    "fg_4": "#94A3B8",
    "border": "#E4ECFC",
    "border_2": "#E2E8F0",
    "border_strong": "#CBD5E1",
    "code_dim": "#64748B",
}

#: 深色语义色。深色**不是把浅色反过来**（工具规则 color-dark-mode）：
#: 另立一套明度，保证各自独立达标。
#:
#: 2026-10-15 改为**纯黑系（近黑 #0A0A0A）**，用户选定方案 B。三个候选都实测过：
#:   A 真纯黑 `#000000`：OLED 最省电，但 surface/bg 只有 1.06:1、surface_3/surface 1.07:1,
#:     选中行与卡片几乎分不出，层级全靠边框撑；
#:   B 近黑 `#0A0A0A`（采用）：探针实测各级灰在 #000000 与 #0A0A0A 上的对比度差异
#:     **小于 0.2:1**，即人眼分辨不出，但层级比 A 可辨（1.12:1 vs 1.07:1）；
#:   C 黑+微蓝 `#08090B`：保留一丝冷调，是"最像原品牌"的一档。
#:
#: `fg_4` 从 `#5F6E86` 提到 **`#858585`**：探针实测原值在近黑底上只有 3.2:1，
#: **不达 4.5:1**；#858585 在该 surface 上是 4.99:1。
DARK_PALETTE: Final[dict[str, str]] = {
    "primary": "#5B9BFF",
    "primary_hover": "#7FB2FF",
    "primary_active": "#3B82F6",
    "on_primary": "#0A0A0A",   # 亮蓝底上放黑字（不是白字）：7.4:1，且更有"纯黑系"气质
    "accent": "#FBBF24",
    "ok": "#4ADE80",
    "warn": "#FBBF24",
    "error": "#F87171",
    "bg": "#0A0A0A",
    "surface": "#141414",
    "surface_2": "#0F0F0F",
    "surface_3": "#1F1F1F",
    "fg": "#F5F5F5",
    "fg_2": "#B8B8B8",
    "fg_3": "#8A8A8A",
    "fg_4": "#858585",
    "border": "#2A2A2A",
    "border_2": "#333333",
    "border_strong": "#404040",
    "code_dim": "#8A8A8A",
}

# --------------------------------------------------------------------------- 标尺

#: 圆角标尺（D4 已定：v5 定稿值，不再调整）。越大的面用越大的角。
RADIUS: Final[dict[str, int]] = {
    "hairline": 2,  # 进度条这种 4px 高的细条，圆角只能是它的一半
    "bar": 5,       # 滚动条滑块（宽 10px，圆角取一半=圆头）
    "xs": 4,        # kbd、小按钮
    "sm": 6,        # 常规按钮、菜单项
    "md": 8,        # 队列条目
    "lg": 10,       # 面板内块
    "xl": 12,       # 窗口、空状态卡、拖拽遮罩
}

#: 间距基准（Swiss 的 8px 单位，其余都是它的倍数或半份）
SPACE_UNIT: Final = 8

#: 字号标尺（工具规则 font-scale：一致的比例，不要随机字号）
FONT_SIZE: Final[dict[str, int]] = {
    "micro": 10,     # 大写小标签
    "tiny": 11,      # 副行
    "small": 12,     # 按钮、正文
    "body": 13,      # 文件名
    "title": 16,     # 面板标题
}

#: 字体栈：即使打包了 Inter，也保留系统回落，避免字体加载失败时变成方块
FONT_STACK: Final = 'Inter, "Segoe UI", "Microsoft YaHei UI", system-ui, sans-serif'
#: 等宽栈：预览区与数字列
MONO_STACK: Final = ('"Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace')

def _font_dir() -> Path:
    """字体目录：冻结包取 `_MEIPASS/fonts`，源码运行取仓库 `packaging/fonts`。

    为什么要分两条路：spec 把字体收进 `_internal/fonts/`，而冻结后
    `__file__` 位于临时解包目录，`parents[2]` 指不到仓库 —— 不区分的话
    冻结包里 `load_fonts()` 永远返回空表、Inter 静默失效（回落到 Segoe UI）。
    与 `_compat.icon_path()` 同一套口径，不另立规矩。

    Returns:
        Path: 字体目录（可能不存在，调用方负责判断）。

    @author 康小汪【kxw】
    @date 2026-10-15
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "fonts"
    return Path(__file__).resolve().parents[2] / "packaging" / "fonts"


#: 打包的字体文件目录（冻结包与源码运行取到的是不同位置，见 `_font_dir`）
FONT_DIR: Final = _font_dir()


def resolve_theme(theme: str, fallback_dark: bool = False) -> str:
    """把主题名解析成具体的 `light` 或 `dark`。

    `auto` 的判定顺序（探针实测后定）：
    1. `QGuiApplication.styleHints().colorScheme()` —— 真窗口下可靠；
    2. 若为 `Unknown`（**离屏模式必然如此**）→ 回退注册表 `AppsUseLightTheme`；
    3. 仍读不到 → 用 `fallback_dark`（默认浅色）。

    Args:
        theme: `auto` / `light` / `dark` 之一；非法值按 `auto` 处理。
        fallback_dark: 前两条都读不到时的兜底，True 表示深色。

    Returns:
        str: `light` 或 `dark`。

    @author 康小汪【kxw】
    @date 2026-10-15
    """
    if theme == THEME_LIGHT:
        return THEME_LIGHT
    if theme == THEME_DARK:
        return THEME_DARK
    hints = QGuiApplication.styleHints()
    if hints is not None:
        scheme = hints.colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return THEME_DARK
        if scheme == Qt.ColorScheme.Light:
            return THEME_LIGHT
    from_registry = _dark_from_registry()
    if from_registry is not None:
        return THEME_DARK if from_registry else THEME_LIGHT
    return THEME_DARK if fallback_dark else THEME_LIGHT


def _dark_from_registry() -> bool | None:
    """从注册表读取 Windows 的深色偏好。

    Returns:
        bool | None: True 深色 / False 浅色；非 Windows 或读不到时返回 None。

    @author 康小汪【kxw】
    @date 2026-10-15
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg  # noqa: PLC0415 - 只有 Windows 才需要，延迟导入
    except ImportError:
        return None
    path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
    except OSError:
        return None
    return value == 0


def load_fonts(font_dir: Path | None = None) -> list[str]:
    """注册随包发布的字体文件，返回成功注册的字体族名。

    Args:
        font_dir: 字体目录；默认 `packaging/fonts`。

    Returns:
        list[str]: 注册成功的族名（可能为空——那就走系统回落，不算失败）。

    @author 康小汪【kxw】
    @date 2026-10-15
    """
    directory = font_dir or FONT_DIR
    if not directory.is_dir():
        return []
    families: list[str] = []
    for font_file in sorted(directory.glob("*.ttf")) + sorted(directory.glob("*.otf")):
        font_id = QFontDatabase.addApplicationFont(str(font_file))
        if font_id < 0:  # 探针实测：失败返回 -1
            continue
        families.extend(QFontDatabase.applicationFontFamilies(font_id))
    return families


def apply_theme(app: QApplication, theme: str = THEME_AUTO) -> str:
    """把主题应用到整个应用。

    做三件事：注册字体 → 设 Fusion 风格 → 设 QSS。
    故意**不重建任何窗口**：切换主题时窗口内容、几何、队列都不该丢。

    Args:
        app: Qt 应用实例。
        theme: `auto` / `light` / `dark`。

    Returns:
        str: 实际生效的主题（`light` 或 `dark`）。

    @author 康小汪【kxw】
    @date 2026-10-15
    """
    load_fonts()
    app.setStyle("Fusion")  # 探针确认本机有 Fusion；跨 Windows 版本外观才一致
    resolved = resolve_theme(theme)
    app.setFont(_build_font())
    app.setStyleSheet(build_stylesheet(resolved))
    return resolved


def watch_system_theme(on_change: Callable[[], None]) -> bool:
    """监听系统深/浅色切换；`auto` 模式下用它实时跟随。

    探针实测（2026-10-15）：
    - `QStyleHints.colorSchemeChanged` 存在且能 connect；
    - **离屏平台（测试环境）下这个信号不会自发触发**，所以测试里要直接调回调，
      不能指望改系统设置；
    - 切换主题后控件配色会自动更新（`QLabel` 实测从 `#0f172a` 变 `#e8edf5`），
      但**手动 `setForeground` 过的列表项不会变** —— 那部分得由调用方重刷。

    Args:
        on_change: 系统配色变化时调用的函数（无参）。

    Returns:
        bool: 是否成功挂上监听；无信号时返回 False（调用方不必报错，只是不跟随）。

    @author 康小汪【kxw】
    @date 2026-10-15
    """
    hints = QGuiApplication.styleHints()
    if hints is None or not hasattr(hints, "colorSchemeChanged"):
        return False
    hints.colorSchemeChanged.connect(lambda *_: on_change())
    return True


def _build_font() -> QFont:
    """构造应用默认字体（QSS 的 font-family 只管控件，不管 app.font()）。"""
    font = QFont("Inter")
    font.setPointSizeF(FONT_SIZE["body"] - 1.5)  # 13px 视觉 ≈ 8.5pt，越小越贴近原型
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def build_stylesheet(theme: str) -> str:
    """按主题生成完整 QSS。

    只做字符串拼接，不碰任何控件 —— 所以可以完全离线测试。

    Args:
        theme: `light` 或 `dark`（其他值按 `light`）。

    Returns:
        str: 完整 QSS 文本。

    @author 康小汪【kxw】
    @date 2026-10-15
    """
    palette = DARK_PALETTE if theme == THEME_DARK else LIGHT_PALETTE
    dark_shadow = (
        "0 1px 2px rgba(0,0,0,.45), 0 14px 36px -14px rgba(0,0,0,.65)"
        if theme == THEME_DARK
        else "0 1px 2px rgba(15,23,42,.04), 0 12px 32px -12px rgba(15,23,42,.14)"
    )
    parts = [
        _qss_window(palette, dark_shadow),
        _qss_labels(palette),
        _qss_buttons(palette),
        _qss_inputs(palette),
        _qss_lists(palette),
        _qss_misc(palette),
    ]
    return "\n".join(parts)


def _qss_window(palette: dict[str, str], shadow: str) -> str:
    """窗口外壳：主窗/对话框、菜单栏与菜单、分隔条、状态栏。"""
    return f"""
QMainWindow, QDialog {{ background: {palette['bg']}; }}
QWidget {{ color: {palette['fg']}; }}
QMenuBar {{ background: {palette['surface']}; border-bottom: 1px solid {palette['border_2']}; }}
QMenuBar::item {{ padding: 4px 9px; border-radius: {RADIUS['sm']}px; }}
QMenuBar::item:selected {{ background: {palette['surface_2']}; }}
QMenu {{ background: {palette['surface']}; border: 1px solid {palette['border_strong']};
         border-radius: {RADIUS['sm']}px; padding: 4px; }}
QMenu::item {{ padding: 5px 18px 5px 10px; border-radius: {RADIUS['xs']}px; }}
QMenu::item:selected {{ background: {palette['surface_2']}; }}
QMenu::separator {{ height: 1px; background: {palette['border_2']}; margin: 4px 6px; }}
QSplitter::handle {{ background: {palette['border_2']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:hover {{ background: {palette['border_strong']}; }}
QStatusBar {{ background: {palette['surface']}; border-top: 1px solid {palette['border']};
              color: {palette['fg_3']}; }}
QPlainTextEdit[role="preview"] {{ color: {palette['fg_2']}; }}
QToolTip {{ background: {palette['surface']}; color: {palette['fg']};
            border: 1px solid {palette['border_strong']};
            border-radius: {RADIUS['xs']}px; padding: 4px 7px; }}
"""


def _qss_labels(palette: dict[str, str]) -> str:
    """标签语义色与卡片/胶囊容器。

    状态色（error/ok/warn）刻意与加粗配合：工具规则 `color-not-only` 要求
    不能只靠颜色传达信息，纯色文字在色盲与灰度环境下会丢失含义。
    """
    return f"""
QLabel {{ background: transparent; }}
QLabel#statusLabel {{ color: {palette['fg_3']}; }}
QLabel[kind="error"] {{ color: {palette['error']}; }}
QLabel[kind="ok"] {{ color: {palette['ok']}; }}
QLabel[kind="warn"] {{ color: {palette['warn']}; }}
QLabel[kind="muted"] {{ color: {palette['fg_3']}; }}
QLabel[kind="hint"] {{ color: {palette['fg_4']}; }}
QLabel[role="chip"] {{ background: {palette['surface_2']};
                       border: 1px solid {palette['border']};
                       border-radius: {RADIUS['xl']}px; padding: 2px 9px;
                       color: {palette['fg_2']}; }}
QLabel[role="empty-title"] {{ color: {palette['fg']}; font-weight: 600; font-size: 15px; }}
QLabel[kind="accent"] {{ color: {palette['accent']}; font-weight: 600; }}
/* 空状态卡：虚线边框 + 大圆角，与原型一致（实线会看起来像个错误的容器） */
QWidget#emptyState {{ background: {palette['surface']};
                      border: 1px dashed {palette['border_strong']};
                      border-radius: {RADIUS['xl']}px; }}
/* 预览区头部条：底色与预览区区分，让文件名条浮起来 */
QWidget#previewBar {{ background: {palette['surface']};
                      border-bottom: 1px solid {palette['border_2']}; }}
QLabel#previewName {{ color: {palette['fg']}; font-weight: 500; }}
QFrame#card {{ background: {palette['surface']}; border: 1px solid {palette['border']};
               border-radius: {RADIUS['xl']}px; }}
"""


def _qss_buttons(palette: dict[str, str]) -> str:
    """按钮：一个主 CTA（primary）+ 常规 + 图标按钮。"""
    return f"""
QPushButton {{ background: {palette['surface']}; color: {palette['fg']};
               border: 1px solid {palette['border_strong']};
               border-radius: {RADIUS['sm']}px; padding: 5px 11px; }}
QPushButton:hover {{ background: {palette['surface_2']}; border-color: {palette['fg_4']}; }}
QPushButton:pressed {{ background: {palette['surface_3']}; }}
QPushButton:disabled {{ color: {palette['fg_4']}; border-color: {palette['border_2']};
                        background: {palette['surface_2']}; }}
QPushButton:focus {{ outline: none; border-color: {palette['primary']}; }}
QPushButton[primary="true"] {{ background: {palette['primary']}; color: {palette['on_primary']};
                               border-color: {palette['primary']}; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {palette['primary_hover']};
                                     border-color: {palette['primary_hover']}; }}
QPushButton[primary="true"]:pressed {{ background: {palette['primary_active']}; }}
QPushButton[primary="true"]:disabled {{ background: {palette['border_strong']};
                                        border-color: {palette['border_strong']};
                                        color: {palette['surface']}; }}
QPushButton[flat="true"] {{ background: transparent; border-color: transparent;
                            color: {palette['fg_2']}; }}
QPushButton[flat="true"]:hover {{ background: {palette['surface_2']}; color: {palette['fg']}; }}
"""


def _qss_inputs(palette: dict[str, str]) -> str:
    """文本区、下拉框、单/多选框、数字框（预览区与设置对话框用得到）。"""
    return f"""
QPlainTextEdit, QTextEdit {{ background: {palette['surface_2']}; color: {palette['fg']};
                             border: 1px solid {palette['border_2']};
                             border-radius: {RADIUS['md']}px; padding: 10px 12px;
                             selection-background-color: {palette['primary']};
                             selection-color: {palette['on_primary']}; }}
QComboBox {{ background: {palette['surface']}; color: {palette['fg']};
             border: 1px solid {palette['border_strong']};
             border-radius: {RADIUS['sm']}px; padding: 4px 9px; }}
QComboBox:hover {{ border-color: {palette['fg_4']}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {palette['surface']}; color: {palette['fg']};
                               border: 1px solid {palette['border_strong']};
                               selection-background-color: {palette['surface_2']};
                               selection-color: {palette['fg']}; }}
QSpinBox {{ background: {palette['surface']}; color: {palette['fg']};
            border: 1px solid {palette['border_strong']};
            border-radius: {RADIUS['sm']}px; padding: 3px 7px; }}
QCheckBox, QRadioButton {{ color: {palette['fg']}; spacing: 7px; }}
QLineEdit {{ background: {palette['surface']}; color: {palette['fg']};
             border: 1px solid {palette['border_strong']};
             border-radius: {RADIUS['sm']}px; padding: 4px 8px; }}
"""


def _qss_lists(palette: dict[str, str]) -> str:
    """队列列表：条目圆角、悬停、选中、进度条。"""
    return f"""
QListWidget {{ background: {palette['surface']}; color: {palette['fg']};
               border: 1px solid {palette['border']};
               border-radius: {RADIUS['md']}px; padding: 4px; outline: none; }}
QListWidget::item {{ border-radius: {RADIUS['md']}px; padding: 6px 8px;
                     border: 1px solid transparent; }}
QListWidget::item:hover {{ background: {palette['surface_2']}; }}
QListWidget::item:selected {{ background: {palette['surface_2']};
                              border-color: {palette['border_strong']};
                              color: {palette['fg']}; }}
QProgressBar {{ background: {palette['surface_3']}; border: none;
                border-radius: {RADIUS['hairline']}px; height: 4px; text-align: center;
                color: {palette['fg_3']}; }}
QProgressBar::chunk {{ background: {palette['primary']};
                       border-radius: {RADIUS['hairline']}px; }}
"""


def _qss_misc(palette: dict[str, str]) -> str:
    """滚动条、复选框指示器、分组框（圆角与配色统一到标尺）。"""
    return f"""
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {palette['border_strong']};
                               border-radius: {RADIUS['bar']}px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {palette['fg_4']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {palette['border_strong']};
                                 border-radius: {RADIUS['bar']}px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QGroupBox {{ border: 1px solid {palette['border_2']};
             border-radius: {RADIUS['md']}px; margin-top: 9px; padding-top: 9px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 9px; padding: 0 4px;
                    color: {palette['fg_3']}; }}
"""
