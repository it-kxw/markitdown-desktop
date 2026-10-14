"""生成第三方软件通告（`THIRD-PARTY-NOTICES.md`）与许可证正文目录（`licenses/`）。

为什么这样设计：
- 依赖范围从 `pyproject.toml` 的运行时依赖出发，沿 `Requires-Dist` 求闭包：
  `pytest` / `pyinstaller` 这类只在开发期存在的包不会被写进通告，依赖变化也不用改本脚本。
- 许可证正文优先拷上游 wheel 自带的（`*.dist-info` 内，含 `licenses/` 下的嵌套目录）；
  上游确实没带时才用 `packaging/licenses/` 下人工补充的正文（来源见该目录的 `SOURCES.md`）；
  两边都没有就记入 `missing_license` 并在通告里标「缺少正文」，绝不静默放过。
- 输出对同一输入逐字节稳定（不含生成日期），这样测试才能校验「已提交的通告是否过期」。

@author 康小汪【kxw】
@date 2026-10-13
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sys
import sysconfig
import tomllib
from importlib.metadata import Distribution, distributions
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = ROOT / "packaging" / "licenses"
NOTICE_PATH = ROOT / "THIRD-PARTY-NOTICES.md"
LICENSES_DIR = ROOT / "licenses"

# 上游 wheel 未附带许可证正文的包 → 用 packaging/licenses 下的哪个文件补（key 为规范化包名）
_SUPPLEMENTS: dict[str, list[str]] = {
    "pyside6-essentials": ["LGPL-3.0.txt", "GPL-3.0.txt"],
    "shiboken6": ["LGPL-3.0.txt", "GPL-3.0.txt"],
    "markitdown": ["markitdown.txt"],
    "magika": ["magika.txt"],
    "onnxruntime": ["onnxruntime.txt"],
    "flatbuffers": ["flatbuffers.txt"],
    "openpyxl": ["openpyxl.txt"],
    "et-xmlfile": ["et-xmlfile.txt", "et-xmlfile-python.txt"],
    "cobble": ["cobble.txt"],
}

# 随包分发的非 Python 组件（手工维护；「来源」一列里的 {} 会替换成闭包中该包的实际版本，
# 这样升级依赖后不需要回来改本表）
_NATIVE_COMPONENTS: tuple[tuple[str, str, str, str], ...] = (
    (
        "Qt 6 / PySide6",
        "随 PySide6-Essentials {pyside6-essentials} 分发",
        "LGPL-3.0-only（另可选 GPL-2.0/3.0 或商业许可）",
        "`_internal/PySide6/Qt6*.dll` 等；合规说明见第 3 节",
    ),
    (
        "shiboken6",
        "随 shiboken6 {shiboken6} 分发",
        "LGPL-3.0-only",
        "Qt 的 Python 绑定运行时，`_internal/shiboken6/`",
    ),
    (
        "Python 3.12",
        "CPython 3.12（随打包运行时）",
        "PSF-2.0",
        "`python312.dll`、`python3.dll`",
    ),
    (
        "Microsoft Visual C++ 运行库 / UCRT",
        "随 Python 3.12 与 numpy 分发",
        "Microsoft 可再分发条款",
        "`VCRUNTIME140.dll`、`VCRUNTIME140_1.dll`、`msvcp140-*.dll`、`ucrtbase.dll`、`api-ms-win-*`",
    ),
    (
        "OpenSSL",
        "随 cryptography {cryptography} 分发",
        "Apache-2.0",
        "`libcrypto-3-x64.dll`、`libssl-3-x64.dll`",
    ),
    (
        "libffi",
        "随 cffi {cffi} 分发",
        "MIT",
        "`libffi-8.dll`",
    ),
    (
        "PDFium",
        "随 pypdfium2 {pypdfium2} 分发",
        "BSD-3-Clause",
        "`pypdfium2_raw/pdfium.dll`；其内置第三方正文见 `licenses/pypdfium2-{pypdfium2}/`",
    ),
    (
        "OpenBLAS（scipy-openblas64）",
        "随 numpy {numpy} 分发",
        "BSD-3-Clause",
        "`numpy.libs/libscipy_openblas64_*.dll`",
    ),
    (
        "ONNX Runtime（C++ 部分）",
        "随 onnxruntime {onnxruntime} 分发",
        "MIT",
        "`onnxruntime/capi/onnxruntime.dll`",
    ),
    (
        "Mesa（软件 OpenGL 回退）",
        "随 PySide6-Essentials {pyside6-essentials} 分发",
        "MIT",
        "`_internal/PySide6/opengl32sw.dll`；`MDD_TRIM=aggressive` 裁剪时会被移除",
    ),
    (
        "PyInstaller 引导程序（bootloader）",
        "随 PyInstaller 6.22.3 分发",
        "GPL-2.0-or-later 附特别例外",
        "已编入 `markitdown-desktop.exe`；例外条款明确允许分发用它打包的程序",
    ),
)

_HEADER = """# 第三方软件通告

> 本文件由 `scripts/gen_third_party_notices.py` 自动生成，**请勿手工修改**。
> 重新生成：`.venv\\Scripts\\python.exe scripts\\gen_third_party_notices.py`
> 改动依赖后必须重新生成，否则 `tests/test_notices.py` 会失败。
>
> 许可证正文在本文件同级的 `licenses/` 目录下，每个包一个子目录（保上游目录结构）。
> 其中 9 个包的 wheel 未附带正文（含 Qt/PySide6 的 LGPL），使用的是按上游发布物补充的正文，
> 来源、获取日期与 SHA-256 登记在仓库 `packaging/licenses/SOURCES.md`。

"""

_QT_SECTION = """## 3. Qt / PySide6 的 LGPL-3.0 合规说明

本程序用 PySide6（Qt for Python）构建界面，按 **LGPL-3.0-only** 使用
（PySide6 另提供 GPL-2.0/3.0 与商业许可，本程序不采用）。为满足 LGPL-3.0 的要求：

1. **许可证全文随包分发**：`licenses/pyside6-essentials-*/` 与 `licenses/shiboken6-*/` 内含
   LGPL-3.0 与 GPL-3.0 全文（LGPL-3.0 正文本身引用了 GPL-3.0，两者必须同时提供）。
2. **动态链接、可替换**：Qt 与 shiboken6 以独立 DLL 形式存放在安装目录的
   `_internal\\PySide6\\`、`_internal\\shiboken6\\` 下，未与本程序静态链接。你可以自行编译
   Qt/PySide6，并用你的版本替换这些 DLL 后继续使用本程序。
3. **源码获取**：Qt 源码见 <https://code.qt.io/>，PySide6 源码见
   <https://code.qt.io/cgit/pyside/pyside-setup.git/>。
4. 本程序未修改 PySide6/Qt 源码。若你自行修改了 Qt 库，需自行承担相应的开源义务。

"""

_SELF_SECTION = """## 4. 本程序自身

MarkItDown Desktop 采用 MIT 许可证，全文见仓库根目录的 `LICENSE`
（Copyright (c) 2026 KXW）。上游项目 markitdown 由 Microsoft 开发，同样采用 MIT。

"""


def _project_requirements(project_file: Path) -> list[Requirement]:
    """读 pyproject.toml 的运行时依赖（`dependencies`，不含开发用的 extra）。"""
    data = tomllib.loads(project_file.read_text(encoding="utf-8"))
    return [Requirement(item) for item in data["project"]["dependencies"]]


def _walk_requirements(
    dist: Distribution, extras: set[str], environment: dict[str, str]
) -> list[tuple[str, set[str]]]:
    """展开一个包的 Requires-Dist：基础依赖 + 已启用 extra 的依赖。

    参数：
        dist: 待展开的已安装包。
        extras: 本次需要展开的 extra 名集合（重访时只传新增的那些，避免重复展开）。
        environment: marker 求值环境（平台信息）。
    返回：
        (依赖包名, 该依赖要求启用的 extra) 列表；marker 为假的依赖直接跳过。
    """
    found: list[tuple[str, set[str]]] = []
    for raw in dist.requires or []:
        requirement = Requirement(raw)
        for extra in ["", *sorted(extras)]:
            if requirement.marker is None or requirement.marker.evaluate({**environment, "extra": extra}):
                found.append((requirement.name, set(requirement.extras)))
                break
    return found


def runtime_closure(
    roots: list[Requirement], by_name: dict[str, Distribution]
) -> dict[str, Distribution]:
    """沿 Requires-Dist 求运行时依赖闭包。

    同一个包可能先以无 extra 的形式被依赖、之后又以 `pkg[extra]` 的形式出现，
    因此用「新出现的 extra」判断是否需要重新展开，而不是简单的 visited 集合。
    """
    environment = default_environment()
    seen: dict[str, set[str]] = {}
    queue = [(item.name, set(item.extras)) for item in roots]
    while queue:
        name, extras = queue.pop()
        key = canonicalize_name(name)
        fresh = extras - seen.get(key, set())
        if not fresh and key in seen:
            continue
        seen.setdefault(key, set()).update(extras)
        dist = by_name.get(key)
        if dist is None:
            print(f"警告：依赖 {name} 未安装，通告中不会有它", file=sys.stderr)
            continue
        queue += _walk_requirements(dist, fresh, environment)
    return {key: by_name[key] for key in sorted(seen) if key in by_name}


def _license_id(dist: Distribution) -> str:
    """取许可证标识：PEP 639 的 License-Expression > Classifier > License 字段。"""
    metadata = dist.metadata
    expression = (metadata.get("License-Expression") or "").strip()
    if expression:
        return expression
    for classifier in metadata.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            return classifier.rsplit("::", 1)[-1].strip()
    fallback = " ".join((metadata.get("License") or "").split())
    return fallback[:60] if fallback else "未标注"


def _upstream_texts(dist: Distribution) -> list[Path]:
    """列出上游 wheel 自带的许可证正文（`licenses/` 子目录递归，其余只取 dist-info 根下）。"""
    # 私有属性 _path 指向 dist-info 目录：locate_file("") 返回的是 site-packages，拿不到它
    info_dir = Path(dist._path)
    found = [
        item
        for pattern in ("LICENSE*", "COPYING*", "NOTICE*")
        for item in sorted(info_dir.glob(pattern))
        if item.is_file()
    ]
    licenses = info_dir / "licenses"
    if licenses.is_dir():
        found += [item for item in sorted(licenses.rglob("*")) if item.is_file()]
    return found


def _strip_licenses_prefix(relative: Path) -> Path:
    """去掉 PEP 639 约定的 `licenses/` 前缀，其余目录结构保留（便于对照上游）。"""
    parts = relative.parts
    return Path(*parts[1:]) if parts and parts[0] == "licenses" else relative


def _copy_upstream(dist: Distribution, target: Path) -> list[Path]:
    """拷上游 wheel 自带的许可证正文，保留相对目录结构。"""
    info_dir = Path(dist._path)
    written = []
    for source in _upstream_texts(dist):
        destination = target / _strip_licenses_prefix(source.relative_to(info_dir))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        written.append(destination)
    return written


def _copy_supplements(
    dist: Distribution, target: Path, supplements: dict[str, list[str]], vendor_dir: Path
) -> list[Path]:
    """拷 `packaging/licenses` 下登记的人工补充正文（平铺进 target）。"""
    written = []
    for name in supplements.get(canonicalize_name(dist.metadata["Name"]), []):
        source = vendor_dir / name
        if not source.is_file():
            print(f"警告：补充正文 {source} 不存在", file=sys.stderr)
            continue
        target.mkdir(parents=True, exist_ok=True)
        destination = target / name
        shutil.copyfile(source, destination)
        written.append(destination)
    return written


def _stage_texts(
    dist: Distribution, target: Path, supplements: dict[str, list[str]], vendor_dir: Path
) -> tuple[list[Path], bool]:
    """把上游自带正文与人工补充正文都拷进 target 目录。

    为什么两者是叠加关系而不是「上游没有时才用补充」：PySide6/shiboken6 的 wheel 只带
    `LicenseRef-Qt-Commercial.txt`（商业许可引用），不带 LGPL 正文；若把补充当兜底，
    LGPL 全文就永远发不出去。

    返回：
        (拷贝出的文件列表, 是否用了补充正文)。列表为空表示这个包拿不到任何正文。
    """
    upstream = _copy_upstream(dist, target)
    vendored = _copy_supplements(dist, target, supplements, vendor_dir)
    return upstream + vendored, bool(vendored)


def _reset_dir(path: Path) -> None:
    """清空并重建目录，避免上次生成留下的旧包目录一直堆在 licenses/ 里。"""
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _stage_all(
    closure: dict[str, Distribution],
    licenses_dir: Path,
    supplements: dict[str, list[str]],
    vendor_dir: Path,
) -> tuple[list[tuple[str, str, str, str, bool]], list[str], list[str]]:
    """逐包落盘许可证正文，返回 (表格行, 缺正文的包, 用了补充正文的包)。"""
    rows: list[tuple[str, str, str, str, bool]] = []
    missing: list[str] = []
    vendored: list[str] = []
    for key, dist in closure.items():
        display = dist.metadata["Name"] or key
        target = licenses_dir / f"{key}-{dist.version}"
        written, used_vendor = _stage_texts(dist, target, supplements, vendor_dir)
        if not written:
            missing.append(key)
        if used_vendor:
            vendored.append(key)
        rows.append((display, dist.version, _license_id(dist), target.name, bool(written)))
    return rows, missing, vendored


def _package_table(rows: list[tuple[str, str, str, str, bool]]) -> str:
    """拼「Python 包」表格；拿不到正文的包在表格里明确标出。"""
    lines = ["| 包 | 版本 | 许可证 | 许可证正文 |", "| --- | --- | --- | --- |"]
    for display, version, license_id, dir_name, has_text in rows:
        where = f"`licenses/{dir_name}/`" if has_text else "**缺少正文**"
        lines.append(f"| {display} | {version} | {license_id} | {where} |")
    return "\n".join(lines) + "\n\n"


class _Versions(dict):
    """版本占位符字典：闭包里没有的包不会抛 KeyError，而是给出可读提示。"""

    def __missing__(self, key: str) -> str:
        return "（未安装）"


def _native_table(versions: dict[str, str]) -> str:
    """拼「非 Python 组件」表格，版本占位符取自当前闭包，避免写死后过期。"""
    lines = ["| 组件 | 版本 | 许可证 | 说明 |", "| --- | --- | --- | --- |"]
    for name, version, license_id, note in _NATIVE_COMPONENTS:
        lines.append(
            f"| {name} | {version.format_map(versions)} | {license_id} | {note.format_map(versions)} |"
        )
    return "\n".join(lines) + "\n\n"


def _render(
    rows: list[tuple[str, str, str, str, bool]], missing: list[str], versions: dict[str, str]
) -> str:
    """拼出完整通告文本；顺序由已排序的 rows 决定，保证输出稳定。"""
    body = _HEADER + f"## 1. Python 包（{len(rows)} 个）\n\n" + _package_table(rows)
    body += "## 2. 非 Python 组件（随包分发的原生库与运行时）\n\n" + _native_table(versions)
    body += _QT_SECTION + _SELF_SECTION
    if missing:
        body += "## 5. 需要人工补充正文的包\n\n"
        body += "下列包既没有自带许可证正文，也没有在 `packaging/licenses/` 里登记补充正文：\n\n"
        body += "".join(f"- `{name}`\n" for name in missing)
    return body


def generate(
    site_packages: Path,
    out_md: Path,
    licenses_dir: Path,
    project_file: Path | None = None,
    supplements: dict[str, list[str]] | None = None,
    vendor_dir: Path | None = None,
) -> dict:
    """生成通告与许可证目录。

    参数：
        site_packages: 扫描范围（通常是当前环境的 `purelib`）。
        out_md: 通告输出路径。
        licenses_dir: 许可证目录（会先清空重建）。
        project_file: 提供运行时依赖的 pyproject.toml，默认仓库根的那份。
        supplements: 包名 → 补充正文文件名，默认 `_SUPPLEMENTS`。
        vendor_dir: 补充正文所在目录，默认 `packaging/licenses`。
    返回：
        `{"packages": 包数, "missing_license": [缺正文的包], "vendored": [用了补充正文的包]}`。
    """
    project_file = project_file or ROOT / "pyproject.toml"
    vendor_dir = vendor_dir or VENDOR_DIR
    supplements = _SUPPLEMENTS if supplements is None else supplements
    by_name = {canonicalize_name(item.metadata["Name"]): item for item in distributions(path=[str(site_packages)])}
    closure = runtime_closure(_project_requirements(project_file), by_name)
    _reset_dir(licenses_dir)
    rows, missing, vendored = _stage_all(closure, licenses_dir, supplements, vendor_dir)
    versions = _Versions({key: dist.version for key, dist in closure.items()})
    out_md.write_text(_render(rows, missing, versions), encoding="utf-8")
    return {"packages": len(closure), "missing_license": missing, "vendored": vendored}


def _recorded_hashes(text: str) -> dict[str, str]:
    """从 SOURCES.md 的表格里取出「文件名 → SHA-256」。"""
    found: dict[str, str] = {}
    for line in text.splitlines():
        cells = [cell.strip().strip("`") for cell in line.split("|")]
        if len(cells) >= 7 and re.fullmatch(r"[0-9a-f]{64}", cells[-2]):
            found[cells[1]] = cells[-2]
    return found


def verify_vendor(vendor_dir: Path) -> list[str]:
    """核对补充正文与 SOURCES.md 记录的 SHA-256，返回不一致清单（空表示全部一致）。

    目的：许可证正文一旦被无声改动，通告就与实际分发的库不符；这里让改动必然被发现。
    """
    sources = vendor_dir / "SOURCES.md"
    if not sources.is_file():
        return [f"缺少 {sources}"]
    recorded = _recorded_hashes(sources.read_text(encoding="utf-8"))
    problems = []
    for item in sorted(vendor_dir.glob("*.txt")):
        expected = recorded.get(item.name)
        if expected is None:
            problems.append(f"{item.name} 未登记在 SOURCES.md")
        elif hashlib.sha256(item.read_bytes()).hexdigest() != expected:
            problems.append(f"{item.name} 的 SHA-256 与 SOURCES.md 不一致")
    problems += [f"{name} 已登记但文件不存在" for name in recorded if not (vendor_dir / name).is_file()]
    return problems


def main() -> int:
    """命令行入口：生成仓库根的通告与 licenses/，有包缺正文时返回非零。"""
    problems = verify_vendor(VENDOR_DIR)
    for problem in problems:
        print(f"警告：{problem}", file=sys.stderr)
    report = generate(Path(sysconfig.get_paths()["purelib"]), NOTICE_PATH, LICENSES_DIR)
    print(
        f"已生成 {NOTICE_PATH.name}：{report['packages']} 个包，"
        f"其中 {len(report['vendored'])} 个用了补充正文，"
        f"缺正文 {len(report['missing_license'])} 个"
    )
    return 1 if report["missing_license"] else 0


if __name__ == "__main__":
    # 只在脚本入口导入：模块被测试 import 时不拉重依赖，也不动 pytest 捕获的流
    from markitdown_desktop.console import make_output_safe

    make_output_safe()
    sys.exit(main())
