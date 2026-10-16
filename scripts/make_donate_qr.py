# SPDX-License-Identifier: MIT
"""把作者的收款码原图压缩成随包分发的小图。

## 为什么需要压缩

原始收款码是 1080×1620 / 1283×1748 的照片级 JPEG（共 295 KB）。界面里只按 220px 宽显示，
所以原图纯属浪费 —— 而 one-folder 体积已经贴着闸门（212.12 MB / 213 MB，**只剩 0.88 MB**），
每一百 KB 都要省着用。

压到 **480px 宽**（显示尺寸的 2 倍多，够高 DPI 用）后约 70 KB，**省 76%**，且不影响扫码
（二维码本身容错率高，480px 缩到 220px 显示仍有足够像素）。

## 用法

    python scripts/make_donate_qr.py           # 从原图生成（覆盖产物）
    python scripts/make_donate_qr.py --check   # 只校验产物是否符合规格，不改文件

原图路径通过参数给，**不写死在脚本里** —— 原图在作者桌面上，不属于仓库内容：

    python scripts/make_donate_qr.py --zfb D:\\path\\zfb.jpg --wx D:\\path\\wx.jpg

## 为什么产物入库

与 `packaging/icon/app-icon.ico`、`packaging/fonts/*.ttf` 同一惯例：**生成物入库 +
`--check` 守漂移**。好处是构建机与 CI 都不需要原图；改了压缩规格忘了重新生成，
`--check` 与测试会红。

@author 康小汪【kxw】
@date 2026-10-16
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
#: 产物目录：**会被 spec 收进冻结包**（照 licensing/fonts 的做法单独建目录，
#: 免得和别的资源混在一起看不出用途）
DONATE_DIR = PROJECT_ROOT / "packaging" / "donate"

#: 两张码的文件名（与 i18n 里的标签一一对应）
QR_FILES = ("zfb.jpg", "wx.jpg")

#: 压缩规格。**改这里必须同时更新 tests/test_donate.py 的上限**。
TARGET_WIDTH = 480
JPEG_QUALITY = 85
#: 单张上限：480px + q85 实测 41–61 KB（二维码内容密度不同，体积差一倍）。
#: 定 80 KB 是留出余量，同时仍能拦住"原图直放"（141 KB）那种错误。
MAX_BYTES = 80 * 1024


def compress(source: Path, target: Path) -> str:
    """把一张原图按规格缩放并重编码成 JPEG。

    Args:
        source: 原图路径。
        target: 输出路径。

    Returns:
        str: 形如 `zfb.jpg: 1080x1620 295KB -> 480x720 34KB` 的说明。

    Raises:
        SystemExit: Pillow 缺失或原图读不了（宁可报错也不产出半成品）。

    @author 康小汪【kxw】
    @date 2026-10-16
    """
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("需要 Pillow（已在 [dev] 里）：pip install -e \".[dev]\"") from None

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as image:
            before = "%dx%d %dKB" % (image.width, image.height, source.stat().st_size // 1024)
            # 只缩不放：原图比目标还小时保持原样，避免插值放大反而糊
            if image.width > TARGET_WIDTH:
                ratio = TARGET_WIDTH / image.width
                size = (TARGET_WIDTH, max(1, round(image.height * ratio)))
                image = image.resize(size, Image.Resampling.LANCZOS)
            # RGB 转换：原图可能是 RGBA/P，直接存 JPEG 会报错
            image.convert("RGB").save(target, "JPEG", quality=JPEG_QUALITY, optimize=True)
    except OSError as exc:
        raise SystemExit("读不了原图 %s：%s" % (source, exc)) from exc

    after = "%dx%d %dKB" % (*_jpeg_size(target), target.stat().st_size // 1024)
    return "%s: %s -> %s" % (target.name, before, after)


def _jpeg_size(path: Path) -> tuple[int, int]:
    """读 JPEG 的宽高（只用标准库，避免为了一个尺寸再依赖 Pillow）。"""
    data = path.read_bytes()
    index = 2
    while index < len(data) - 9:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB):
            height = int.from_bytes(data[index + 5:index + 7], "big")
            width = int.from_bytes(data[index + 7:index + 9], "big")
            return width, height
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        index += 2 + int.from_bytes(data[index + 2:index + 4], "big")
    raise SystemExit("不是有效 JPEG：%s" % path)


def check_qr(donate_dir: Path | None = None) -> list[str]:
    """校验产物是否符合规格，返回问题列表（空 = 通过）。

    判据用「宽度」与「体积」而不是像素比对：压缩是确定性的，但 Pillow 版本间
    的编码细节可能有微小差异，硬比对字节会让升级依赖就红。

    Args:
        donate_dir: 产物目录，默认 `packaging/donate`。

    Returns:
        list[str]: 每个问题一条说明。

    @author 康小汪【kxw】
    @date 2026-10-16
    """
    directory = donate_dir or DONATE_DIR
    problems: list[str] = []
    for name in QR_FILES:
        path = directory / name
        if not path.is_file():
            problems.append("缺少 %s" % name)
            continue
        size = path.stat().st_size
        if size > MAX_BYTES:
            problems.append("%s 有 %d B（>%d），看起来没压缩过" % (name, size, MAX_BYTES))
        width, _ = _jpeg_size(path)
        if width > TARGET_WIDTH:
            problems.append("%s 宽 %dpx（>%d），看起来是原图" % (name, width, TARGET_WIDTH))
    return problems


def total_bytes(donate_dir: Path | None = None) -> int:
    """返回两张码的合计体积（供报告与测试断言用）。"""
    directory = donate_dir or DONATE_DIR
    return sum((directory / name).stat().st_size
               for name in QR_FILES if (directory / name).is_file())


def main(argv: list[str] | None = None) -> int:
    """命令行入口：`--check` 只校验，否则从原图重新生成。

    Args:
        argv: 参数列表，默认取 `sys.argv[1:]`。

    Returns:
        int: 0 通过 / 1 校验失败 / 2 参数不全。

    @author 康小汪【kxw】
    @date 2026-10-16
    """
    parser = argparse.ArgumentParser(description="压缩收款码（打赏功能用）")
    parser.add_argument("--check", action="store_true", help="只校验产物，不改文件")
    parser.add_argument("--zfb", type=Path, help="支付宝收款码原图")
    parser.add_argument("--wx", type=Path, help="微信收款码原图")
    args = parser.parse_args(argv)

    if args.check:
        problems = check_qr()
        for problem in problems:
            print("  !! %s" % problem)
        print("合计 %d B = %.1f KB" % (total_bytes(), total_bytes() / 1024))
        print("check result: %s" % ("ok" if not problems else "failed"))
        return 0 if not problems else 1

    if not args.zfb or not args.wx:
        parser.error("生成时必须同时给 --zfb 与 --wx（原图不在仓库里）")
    print("  " + compress(args.zfb, DONATE_DIR / QR_FILES[0]))
    print("  " + compress(args.wx, DONATE_DIR / QR_FILES[1]))
    print("合计 %d B = %.1f KB" % (total_bytes(), total_bytes() / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
