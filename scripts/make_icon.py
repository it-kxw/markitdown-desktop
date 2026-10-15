# SPDX-License-Identifier: MIT
"""从 `packaging/icon/app-icon.svg` 生成 `packaging/icon/app-icon.ico`。

## 设计取舍（改之前先读）

1. **图标本身**：需求是「别再是占位图标」。三个方向里选了**抽象几何** ——
   圆角方块 + 135° 双色渐变 + 白色描边符号，小尺寸下细节最少、最不易糊穿。
   配色经真机对比定为**蓝青** `#2563EB → #06B6D4`，符号用 `M`。
2. **为什么 SVG 是唯一源**：矢量改色改形不用重画位图；ICO 一律由本脚本生成，
   禁止手改二进制。
3. **为什么手写 ICO 容器**（而不是交给 Pillow / Qt 存 ICO）：它们只接受一张图再
   降采样，16/24/32 档会糊；手写容器能让**每一档都从 SVG 重新渲染**，小图标才清晰。
4. **零新依赖**：渲染用已装好的 PySide6，容器与校验用标准库。

用法：

    python scripts/make_icon.py           # 生成（覆盖）app-icon.ico
    python scripts/make_icon.py --check   # 只校验现有 ICO 与 SVG 是否一致，不改文件

@author 康小汪【kxw】
@date 2026-10-15
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
# 采样校验的容差：不同 Qt 版本抗锯齿会有微小差异，像素级完全相等不是合适的判据
_SAMPLE_TOLERANCE = 12
_SAMPLE_STEP = 8

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SVG_PATH = PROJECT_ROOT / "packaging" / "icon" / "app-icon.svg"
ICO_PATH = PROJECT_ROOT / "packaging" / "icon" / "app-icon.ico"


def _qt():
    """按需导入 Qt，并保证在无声环境（CI）也能跑。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    if QGuiApplication.instance() is None:
        QGuiApplication([sys.argv[0]])
    return QByteArray, QImage, QPainter, QSvgRenderer


def render(svg_text: str, size: int):
    """把 SVG 渲染成指定边长的 QImage（每档独立渲染，不做缩放）。"""
    byte_array, image_type, painter_type, renderer_type = _qt()
    from PySide6.QtCore import Qt

    image = image_type(size, size, image_type.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = painter_type(image)
    painter.setRenderHint(painter_type.Antialiasing, True)
    painter.setRenderHint(painter_type.SmoothPixmapTransform, True)
    renderer_type(byte_array(svg_text.encode("utf-8"))).render(painter)
    painter.end()
    return image


def _png_bytes(image) -> bytes:
    """把 QImage 压成 PNG 字节。"""
    from PySide6.QtCore import QBuffer, QIODevice

    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def build_ico(svg_text: str) -> bytes:
    """组装 ICO 容器：每个尺寸一帧 PNG，偏移表按需计算。"""
    frames = [_png_bytes(render(svg_text, size)) for size in ICO_SIZES]
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries = b""
    for size, frame in zip(ICO_SIZES, frames):
        side = 0 if size >= 256 else size  # ICO 约定：256 用 0 表示
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(frame), offset)
        offset += len(frame)
    return header + entries + b"".join(frames)


def parse_ico(payload: bytes) -> list[tuple[int, int, bytes]]:
    """解析 ICO，返回 [(宽, 高, PNG 字节)]；格式不对直接抛 ValueError。"""
    if len(payload) < 6 or payload[:4] != b"\x00\x00\x01\x00":
        raise ValueError("不是合法的 ICO：文件头不对")
    count = struct.unpack("<H", payload[4:6])[0]
    frames: list[tuple[int, int, bytes]] = []
    for index in range(count):
        entry = payload[6 + 16 * index: 22 + 16 * index]
        width = entry[0] or 256
        height = entry[1] or 256
        length, offset = struct.unpack("<II", entry[8:16])
        frames.append((width, height, payload[offset: offset + length]))
    return frames


def _sample(image) -> list[tuple[int, int, int, int]]:
    """按固定步长采样像素，用于跨版本比对（比逐像素相等稳健）。"""
    step = max(image.width() // _SAMPLE_STEP, 1)
    values = []
    for y in range(0, image.height(), step):
        for x in range(0, image.width(), step):
            values.append(image.pixelColor(x, y).getRgb())
    return values


def check(svg_text: str, ico_path: Path) -> list[str]:
    """校验现有 ICO 与当前 SVG 是否一致，返回问题清单（空 = 通过）。"""
    if not ico_path.is_file():
        return ["ICO 不存在：%s" % ico_path]
    frames = parse_ico(ico_path.read_bytes())
    sizes = [width for width, _, _ in frames]
    if sizes != list(ICO_SIZES):
        return ["尺寸不匹配：ICO 是 %s，脚本要求 %s" % (sizes, list(ICO_SIZES))]
    from PySide6.QtGui import QImage

    problems: list[str] = []
    for (width, _, blob), size in zip(frames, ICO_SIZES):
        if not blob.startswith(b"\x89PNG\r\n\x1a\n"):
            problems.append("%dpx 帧不是 PNG" % size)
            continue
        committed = QImage.fromData(blob, "PNG")
        fresh = render(svg_text, size)
        if committed.width() != width or committed.width() != size:
            problems.append("%dpx 帧尺寸自相矛盾" % size)
            continue
        deltas = [
            max(abs(a - b) for a, b in zip(left, right))
            for left, right in zip(_sample(committed), _sample(fresh))
        ]
        worst = max(deltas) if deltas else 0
        if worst > _SAMPLE_TOLERANCE:
            problems.append("%dpx 帧与 SVG 渲染结果差异过大（最大通道差 %d）" % (size, worst))
    return problems


def main(argv: list[str] | None = None) -> int:
    """命令行入口：`--check` 只校验，否则重新生成 ICO。"""
    parser = argparse.ArgumentParser(description="由 SVG 生成多尺寸 ICO")
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args(argv)
    svg_text = SVG_PATH.read_text(encoding="utf-8")
    if args.check:
        problems = check(svg_text, ICO_PATH)
        for problem in problems:
            print("[FAIL] %s" % problem)
        print("check result: %s" % ("ok" if not problems else "failed"))
        return 0 if not problems else 1
    payload = build_ico(svg_text)
    ICO_PATH.write_bytes(payload)
    print("wrote %s (%d bytes, sizes=%s)" % (ICO_PATH.name, len(payload), list(ICO_SIZES)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
