# -*- coding: utf-8 -*-
"""队列条目的类型图标：按扩展名给一个描边矢量图标（T29 / B3）。

**为什么自己画 SVG 而不是用系统图标**：
1. 本仓零新依赖（P2 全局约束），引图标库会直接违约；
2. 图标要跟着主题换色（浅色/深色两套前景色），位图做不到；
3. `scripts/make_icon.py` 已经走了「纯矢量路径 + QSvgRenderer」这条路，这里复用同一条，
   冻结包里不用额外打包插件（PNG 图标需要 imageformats 插件，SVG 不需要）。

**为什么是 5 个图标而不是 25 个**：25 个扩展名各配一张图会让工具栏变成调色板，
而且用户扫列表时辨的是「这是文档/表格/图片/压缩包」这个粒度。分类依据是实测的
支持清单（见 `docs/ui-redesign.md`：行为探针跑出来的 25 个扩展名）。

图标路径手写、不依赖字体 —— 与 app 图标同一个理由：CI 上不会因为缺字体而糊掉。

@author 康小汪【kxw】
@date 2026-10-15
"""

from __future__ import annotations

from functools import lru_cache
from typing import Final

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# --------------------------------------------------------------------------- 分类

#: 扩展名 → 图标种类。只放**实测被上游支持**的（探针结果），其余一律回落通用文档。
#: key 一律小写且带点，避免大小写与漏点造成的静默失配。
SUFFIX_KINDS: Final[dict[str, str]] = {
    # 文档
    ".pdf": "doc", ".docx": "doc", ".txt": "doc", ".md": "doc",
    ".html": "doc", ".htm": "doc", ".xml": "doc", ".epub": "doc",
    ".msg": "mail", ".ipynb": "code", ".py": "code", ".js": "code",
    # 表格
    ".xlsx": "sheet", ".xls": "sheet", ".csv": "sheet",
    # 幻灯片
    ".pptx": "slide",
    # 图片
    ".jpg": "image", ".jpeg": "image", ".png": "image",
    # 音频
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio",
    # 视频（上游只对 mp4 探针通过）
    ".mp4": "video",
    # 压缩包
    ".zip": "archive",
    # 结构数据
    ".json": "code",
}

#: 兜底种类：不认识的扩展名都用它（**不许抛异常**，否则一个怪文件名能炸掉入队流程）。
#: 刻意**不复用** `"doc"`：若兜底与真实类别同名，"未映射"就会静默伪装成"文档"，
#: 测试与排查都分不出来（这是写测试时发现的真实设计缺陷）。
FALLBACK_KIND: Final = "file"

#: 所有种类的描边路径（24x24 视口，stroke 走 currentColor 的替身：这里用固定白/深色由渲染时指定）
_ICON_PATHS: Final[dict[str, str]] = {
    # 通用文件：空文档 + 中间一道横线（不认识扩展名时用，与"文档"区分开）
    "file": 'M14 3H7a1.5 1.5 0 0 0-1.5 1.5v15A1.5 1.5 0 0 0 7 21h10a1.5 1.5 0 0 0 1.5-1.5V7.5z'
            'M14 3v4.5h4.5 M9 13.5h6',
    # 文档：带折角
    "doc": 'M14 3H7a1.5 1.5 0 0 0-1.5 1.5v15A1.5 1.5 0 0 0 7 21h10a1.5 1.5 0 0 0 1.5-1.5V7.5z'
           'M14 3v4.5h4.5',
    # 表格：网格
    "sheet": 'M4.5 4.5h15v15h-15z M4.5 10h15 M10 4.5v15 M4.5 14.5h15 M14 10v9',
    # 幻灯片：矩形 + 底部标题条
    "slide": 'M3.5 5.5h17v11h-17z M8 20h8 M8 9h8 M8 12.5h5',
    # 图片：山与太阳
    "image": 'M3.5 5h17v14h-17z M4 17l5-5 4.5 4.5L16 14l4 4',
    # 音频：音符
    "audio": 'M9 18V6l9-1.5v11.5 M9 18a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z'
             ' M18 16a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z',
    # 视频：胶片框 + 播放三角
    "video": 'M3.5 6h17v12h-17z M10 9.5l5 2.5-5 2.5z',
    # 压缩包：带拉链
    "archive": 'M14 3H7a1.5 1.5 0 0 0-1.5 1.5v15A1.5 1.5 0 0 0 7 21h10a1.5 1.5 0 0 0 1.5-1.5V7.5z'
               'M14 3v4.5h4.5 M10.5 3v3 M10.5 8.5v2',
    # 邮件：信封
    "mail": 'M3.5 6h17v12h-17z M4 6.5l8 6 8-6',
    # 代码：尖括号 + 斜杠
    "code": 'M9 8l-4 4 4 4 M15 8l4 4-4 4 M13.5 5.5l-3 13',
    # 圆圈：太阳（图片里的装饰圆会重画，所以这里单独给个实心点由调用方决定）
    "image_sun": 'M9 9.6a1.2 1.2 0 1 1-2.4 0 1.2 1.2 0 0 1 2.4 0z',
}

#: 缓存的渲染尺寸：列表行图标尺寸固定，缓存后同一图标只画一次
_ICON_PIXEL: Final = 32


def kind_for(suffix: str) -> str:
    """把文件扩展名映射成图标种类。

    Args:
        suffix: 扩展名，可带点也可不带（例如 `.pdf` / `PDF` / `pdf`）。

    Returns:
        str: 图标种类；不认识的一律 `FALLBACK_KIND`，**不抛异常**。

    @author 康小汪【kxw】
    @date 2026-10-15
    """
    if not suffix:
        return FALLBACK_KIND
    normalized = suffix.strip().lower()
    if not normalized.startswith("."):
        normalized = "." + normalized
    return SUFFIX_KINDS.get(normalized, FALLBACK_KIND)


@lru_cache(maxsize=64)
def icon_for(suffix: str, color: str = "#64748B") -> QIcon:
    """按扩展名与颜色生成图标（结果缓存）。

    为什么带 `color` 参数：浅色主题用中灰、深色主题用浅灰，同一个图标要两种色；
    `lru_cache` 按 (suffix, color) 缓存，最多 64 个条目 —— 列表里出现的种类本就很少。

    Args:
        suffix: 扩展名（大小写与点可有可无）。
        color: `#RRGGBB` 描边色。

    Returns:
        QIcon: 可直接 `setIcon()` 的图标；**永不返回空图标**（路径缺失时回落通用文档）。

    @author 康小汪【kxw】
    @date 2026-10-15
    """
    kind = kind_for(suffix)
    svg = _svg_for(kind, color)
    pixmap = _render(svg)
    return QIcon(pixmap)


def _svg_for(kind: str, color: str) -> str:
    """拼出该种类的 SVG 文本；未定义的种类回落通用文档，保证不会渲染空白。"""
    path = _ICON_PATHS.get(kind) or _ICON_PATHS[FALLBACK_KIND]
    extra = _ICON_PATHS["image_sun"] if kind == "image" else ""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'width="24" height="24" fill="none" stroke="%s" stroke-width="1.7" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="%s"/>%s</svg>'
    ) % (color, path, '<path d="%s"/>' % extra if extra else "")


def _render(svg: str) -> QPixmap:
    """把 SVG 渲染成带透明背景的位图。

    用 `QSvgRenderer` 而不是 Qt 的图片加载器：SVG 走的是 QtSvg 模块，不依赖
    `imageformats` 插件，冻结包里少一层打包风险（PNG 图标就需要那个插件）。
    """
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(QSize(_ICON_PIXEL, _ICON_PIXEL))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return pixmap
