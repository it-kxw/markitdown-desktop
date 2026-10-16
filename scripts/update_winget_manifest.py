#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 packaging/winget/ 的 4 个清单更新到 pyproject.toml 里的新版本。

为什么需要它：发一个版本要改 **9 处**（`PackageVersion` ×4、`DisplayVersion`、
`InstallerUrl`、`InstallerSha256`、`ReleaseNotesUrl` ×2），其中 `InstallerSha256` 是
64 位十六进制 —— 手抄错一位，winget 那边要么装不上要么校验红灯。这个脚本把
「下载真实产物 → 算哈希 → 逐行替换 → 先在临时目录过官方校验 → 再落盘」固定下来。

四条设计要点（每条都有对应测试）：

1. **逐行替换，不重新生成 YAML**：注释、决策说明、字段顺序全部保留。这正是本仓
   不用 `wingetcreate` 的原因 —— 它会把清单重新生成一遍，丢掉我们的注释；
2. **只有真下载到安装包才算哈希**：Release 还没发、URL 404、下载 0 字节，都直接失败，
   绝不凭空写一个值，也绝不先写占位符；
3. **默认不动文件**：不加 `--write` 只打印将要改的行；`--check` 只体检不联网；
4. **落盘前必须过官方校验**：改好的 4 个文件先写进临时目录跑
   `winget validate --manifest <临时目录>`，通过才覆盖真文件。

用法（在仓库根目录）：

    .venv\\Scripts\\python.exe scripts\\update_winget_manifest.py             # 预览
    .venv\\Scripts\\python.exe scripts\\update_winget_manifest.py --write     # 落盘
    .venv\\Scripts\\python.exe scripts\\update_winget_manifest.py --check     # 只体检
    .venv\\Scripts\\python.exe scripts\\update_winget_manifest.py --file dist\\installer\\某安装包.exe

@author 康小汪【kxw】
@date 2026-10-15
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
WINGET_DIR = REPO_ROOT / "packaging" / "winget"
INSTALLER_ISS = REPO_ROOT / "packaging" / "installer.iss"
PYPROJECT = REPO_ROOT / "pyproject.toml"

PACKAGE_ID = "KXW.MarkItDownDesktop"
REPO_URL = "https://github.com/it-kxw/markitdown-desktop"
RELEASE_API = "https://api.github.com/repos/it-kxw/markitdown-desktop/releases/tags/v{version}"
USER_AGENT = "kxw-update-winget-manifest"

# 文件名 → 这个文件里需要更新的字段。实测清点：1 + 4 + 2 + 2 = 9 处。
MANIFEST_FIELDS: dict[str, tuple[str, ...]] = {
    f"{PACKAGE_ID}.yaml": ("PackageVersion",),
    f"{PACKAGE_ID}.installer.yaml": (
        "PackageVersion",
        "DisplayVersion",
        "InstallerUrl",
        "InstallerSha256",
    ),
    f"{PACKAGE_ID}.locale.en-US.yaml": ("PackageVersion", "ReleaseNotesUrl"),
    f"{PACKAGE_ID}.locale.zh-CN.yaml": ("PackageVersion", "ReleaseNotesUrl"),
}

SHA256_RE = re.compile(r"^[0-9A-Fa-f]{64}$")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class ManifestError(RuntimeError):
    """清单更新过程中可以预期的失败（消息直接给用户看）。"""


class ManifestPreview(NamedTuple):
    """一次「准备落盘」的全部结果（打印与写盘共用同一份）。

    Attributes:
        version: 目标版本号。
        asset_name: 安装包文件名（由 installer.iss 推出）。
        sha256: 大写的 SHA-256。
        size: 安装包字节数。
        old_texts: 文件名 → 旧全文。
        new_texts: 文件名 → 新全文。
        changed: 人类可读的改动说明。
    """

    version: str
    asset_name: str
    sha256: str
    size: int
    old_texts: dict[str, str]
    new_texts: dict[str, str]
    changed: list[str]


def manifest_paths(winget_dir: Path | None = None) -> dict[str, Path]:
    """列出 4 个清单文件。

    目录里**只允许**这 4 个文件：官方 `winget validate --manifest <目录>` 会把目录里
    每个文件都当清单解析，多一个 `README.md` 就会报
    `[YAML:Scanner] mapping values are not allowed in this context`（本仓真踩过）。

    Args:
        winget_dir: 清单目录，默认 `packaging/winget`。

    Returns:
        dict[str, Path]: 文件名 → 路径。

    Raises:
        ManifestError: 目录里文件集合不是那 4 个清单。
    """
    directory = winget_dir or WINGET_DIR
    found = sorted(item.name for item in directory.iterdir() if item.is_file())
    expected = sorted(MANIFEST_FIELDS)
    if found != expected:
        raise ManifestError("%s 里只允许放这 4 个清单文件；实际=%s" % (directory, found))
    return {name: directory / name for name in expected}


def read_project_version(pyproject_path: Path | None = None) -> str:
    """读 `pyproject.toml` 的 `[project] version`（版本号唯一真源）。

    Args:
        pyproject_path: pyproject 路径，默认仓库根的 `pyproject.toml`。

    Returns:
        str: 形如 `0.1.1` 的版本号。

    Raises:
        ManifestError: 文件读不了、没有 version、或不是 `X.Y.Z` 三段数字。
    """
    target = pyproject_path or PYPROJECT
    try:
        parsed = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError("读不了 %s：%s" % (target, exc)) from exc
    version = str(parsed.get("project", {}).get("version", "")).strip()
    if not VERSION_RE.match(version):
        raise ManifestError("%s 里的版本号不是 X.Y.Z 形式：%r" % (target, version))
    return version


def compare_versions(left: str, right: str) -> int:
    """比较两个 `X.Y.Z` 版本号。

    Args:
        left: 左版本号。
        right: 右版本号。

    Returns:
        int: left < right 返回 -1，相等 0，left > right 返回 1。

    Raises:
        ManifestError: 任一版本号不是 `X.Y.Z`。
    """
    parts = []
    for value in (left, right):
        matched = VERSION_RE.match(value.strip())
        if matched is None:
            raise ManifestError("版本号不是 X.Y.Z 形式：%r" % value)
        parts.append(tuple(int(group) for group in matched.groups()))
    return (parts[0] > parts[1]) - (parts[0] < parts[1])


def read_installer_asset_name(version: str, iss_path: Path | None = None) -> str:
    """从 `installer.iss` 的 `OutputBaseFilename` 推出安装包文件名。

    不手写 `MarkItDownDesktop-<版本>-setup.exe` 这种常量：命名一旦在 iss 里改了，
    常量就会悄悄漂移，而清单里的 URL 也就跟着错。

    Args:
        version: 目标版本号。
        iss_path: `installer.iss` 路径，默认 `packaging/installer.iss`。

    Returns:
        str: 形如 `MarkItDownDesktop-0.1.1-setup.exe` 的文件名（Inno 负责补 `.exe`）。

    Raises:
        ManifestError: 找不到 `OutputBaseFilename`，或替换后还残留 `{#...}` 占位符。
    """
    target = iss_path or INSTALLER_ISS
    text = target.read_text(encoding="utf-8-sig", errors="replace")
    matched = re.search(r"^[ \t]*OutputBaseFilename[ \t]*=[ \t]*([^\r\n]*)", text, re.MULTILINE)
    if matched is None:
        raise ManifestError("%s 里找不到 OutputBaseFilename" % target)
    base = matched.group(1).strip().replace("{#AppVersion}", version)
    if "{#" in base:
        raise ManifestError("OutputBaseFilename 里还有没替换的占位符：%s" % base)
    return base + ".exe"


def build_asset_url(version: str, asset_name: str) -> str:
    """拼出 Release 资产下载地址（tag 形如 `v0.1.1`）。

    Args:
        version: 目标版本号。
        asset_name: 安装包文件名。

    Returns:
        str: 完整下载 URL。
    """
    return "%s/releases/download/v%s/%s" % (REPO_URL, version, asset_name)


def build_release_url(version: str) -> str:
    """拼出 Release 页面地址。

    Args:
        version: 目标版本号。

    Returns:
        str: 形如 `https://github.com/it-kxw/markitdown-desktop/releases/tag/v0.1.1`。
    """
    return "%s/releases/tag/v%s" % (REPO_URL, version)


def normalize_sha256(value: str) -> str:
    """校验并规范化 SHA-256（winget 清单里用大写）。

    Args:
        value: 待校验的十六进制字符串。

    Returns:
        str: 64 位大写的 SHA-256。

    Raises:
        ManifestError: 不是 64 位十六进制。
    """
    trimmed = value.strip()
    if not SHA256_RE.match(trimmed):
        raise ManifestError("不是 64 位十六进制 SHA-256：%r" % value)
    return trimmed.upper()


def sha256_of_file(path: Path, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    """算文件的 SHA-256 与字节数。

    Args:
        path: 文件路径。
        chunk_size: 分块大小。

    Returns:
        tuple[str, int]: 大写的 SHA-256、字节数。

    Raises:
        ManifestError: 文件不存在或读不了。
    """
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise ManifestError("算哈希失败：%s（%s）" % (path, exc)) from exc
    if size == 0:
        raise ManifestError("文件是 0 字节，不肯拿它当安装包：%s" % path)
    return digest.hexdigest().upper(), size


def download_asset(url: str, dest: Path, timeout: float = 600.0) -> int:
    """流式下载安装包。

    Args:
        url: 下载地址。
        dest: 落盘路径。
        timeout: 单次 socket 超时（秒）。

    Returns:
        int: 写入的字节数。

    Raises:
        ManifestError: 网络失败、HTTP 错误或下载到 0 字节。
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    written = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, dest.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
    except (urllib.error.URLError, OSError) as exc:
        raise ManifestError("下载失败：%s（%s）" % (url, exc)) from exc
    if written == 0:
        raise ManifestError("下载到 0 字节：%s" % url)
    return written


def fetch_expected_digest(version: str, asset_name: str, timeout: float = 30.0) -> str | None:
    """查 GitHub Releases API 的 `assets[].digest` 作为交叉核对。

    这个值是 GitHub 自己算的，和本地下载算出来的哈希独立；两边一致才说明
    「我们下载到的东西 = 挂在那条 Release 上的东西」。

    Args:
        version: 目标版本号。
        asset_name: 安装包文件名。
        timeout: 请求超时（秒）。

    Returns:
        str | None: 规范化后的 SHA-256；API 不可达、没有该资产或没有 digest 时返回 None。
    """
    request = urllib.request.Request(
        RELEASE_API.format(version=version),
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print("  提示：读不到 Releases API（%s），只能用本地下载算的哈希。" % exc)
        return None
    for asset in payload.get("assets", []):
        if asset.get("name") != asset_name:
            continue
        digest = str(asset.get("digest") or "")
        if digest.startswith("sha256:"):
            return normalize_sha256(digest.split(":", 1)[1])
    return None


def _field_pattern(field: str) -> re.Pattern[str]:
    """给出「捕获整行里的字段与值」的正则（保留缩进与行尾 CR）。"""
    return re.compile(r"^([ \t]*%s:[ \t]*)([^\r\n]*)" % re.escape(field), re.MULTILINE)


def current_field_value(text: str, field: str) -> str:
    """读一个字段的当前值。

    Args:
        text: 清单全文。
        field: 字段名。

    Returns:
        str: 去掉首尾空白的值。

    Raises:
        ManifestError: 该字段在文件里出现次数不是 1。
    """
    matched = _field_pattern(field).findall(text)
    if len(matched) != 1:
        raise ManifestError("字段 %s 应恰好出现 1 次，实际 %d 次" % (field, len(matched)))
    return matched[0][1].strip()


def substitute_field(text: str, field: str, new_value: str) -> str:
    """把某个字段整行替换成新值（其余字符一个不动）。

    Args:
        text: 清单全文。
        field: 字段名。
        new_value: 新值。

    Returns:
        str: 替换后的全文。

    Raises:
        ManifestError: 该字段出现次数不是 1。
    """
    pattern = _field_pattern(field)
    if len(pattern.findall(text)) != 1:
        raise ManifestError("字段 %s 应恰好出现 1 次，无法安全替换" % field)
    return pattern.sub(lambda matched: matched.group(1) + new_value, text, count=1)


def _assert_layout_unchanged(old_text: str, new_text: str) -> None:
    """确保替换没有动到换行风格与行数（防止把 CRLF 文件写坏）。"""
    if old_text.count("\r\n") != new_text.count("\r\n"):
        raise ManifestError("换行风格被改动了，拒绝写入")
    if old_text.count("\n") != new_text.count("\n"):
        raise ManifestError("行数被改动了，拒绝写入")


def render_manifests(
    version: str,
    asset_url: str,
    release_url: str,
    sha256: str,
    texts: dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    """按字段逐行替换，生成新的清单全文。

    Args:
        version: 新版本号。
        asset_url: 新的 InstallerUrl。
        release_url: 新的 ReleaseNotesUrl。
        sha256: 新的大写 InstallerSha256。
        texts: 文件名 → 旧全文（必须正好是那 4 个文件）。

    Returns:
        tuple[dict[str, str], list[str]]: 文件名 → 新全文；人类可读的改动说明列表。

    Raises:
        ManifestError: 缺文件、字段缺失/重复，或换行风格与行数被破坏。
    """
    values = {
        "PackageVersion": version,
        "DisplayVersion": version,
        "InstallerUrl": asset_url,
        "InstallerSha256": sha256,
        "ReleaseNotesUrl": release_url,
    }
    rendered: dict[str, str] = {}
    changed: list[str] = []
    for name, fields in MANIFEST_FIELDS.items():
        if name not in texts:
            raise ManifestError("少了清单文件：%s" % name)
        text = texts[name]
        for field in fields:
            before = current_field_value(text, field)
            text = substitute_field(text, field, values[field])
            if before != values[field]:
                changed.append("%s: %s %s → %s" % (name, field, before, values[field]))
        _assert_layout_unchanged(texts[name], text)
        rendered[name] = text
    return rendered, changed


def format_diff(old_texts: dict[str, str], new_texts: dict[str, str]) -> str:
    """把新旧全文的逐行差异拼成一段可读文本。

    Args:
        old_texts: 文件名 → 旧全文。
        new_texts: 文件名 → 新全文。

    Returns:
        str: 各文件去掉 `---/+++` 头之后的 unified diff。
    """
    lines: list[str] = []
    for name in sorted(new_texts):
        diff = difflib.unified_diff(
            old_texts[name].splitlines(),
            new_texts[name].splitlines(),
            fromfile=name,
            tofile=name,
            lineterm="",
            n=1,
        )
        lines.extend(list(diff)[2:])
    return "\n".join(lines)


def check_drift(version: str, winget_dir: Path | None = None) -> list[str]:
    """体检：4 个清单里的版本相关字段是否与目标版本自洽（纯离线）。

    Args:
        version: 目标版本号（通常来自 pyproject）。
        winget_dir: 清单目录，默认 `packaging/winget`。

    Returns:
        list[str]: 每一条不一致的说明；全一致时返回空列表。
    """
    problems: list[str] = []
    try:
        expected = _expected_values(version)
    except ManifestError as exc:
        return ["推不出期望值：%s" % exc]
    for name, path in sorted(manifest_paths(winget_dir).items()):
        text = path.read_text(encoding="utf-8")
        for field in MANIFEST_FIELDS[name]:
            try:
                value = current_field_value(text, field)
            except ManifestError as exc:
                problems.append("%s：%s" % (name, exc))
                continue
            problems.extend(_field_problems(name, field, value, expected))
    return problems


def _expected_values(version: str) -> dict[str, str]:
    """算出 4 个清单里每个版本相关字段「应该」是什么。

    Returns:
        dict[str, str]: 字段名 → 期望值。
    """
    return {
        "PackageVersion": version,
        "DisplayVersion": version,
        "InstallerUrl": build_asset_url(version, read_installer_asset_name(version)),
        "InstallerSha256": "",
        "ReleaseNotesUrl": build_release_url(version),
    }


def _field_problems(name: str, field: str, value: str, expected: dict[str, str]) -> list[str]:
    """判断单个字段是否与期望值一致，返回问题说明（可能为空）。"""
    if field == "InstallerSha256":
        if not SHA256_RE.match(value):
            return ["%s：%s 不是 64 位十六进制" % (name, field)]
        return []
    if value != expected[field]:
        return ["%s：%s=%s ≠ 期望的 %s" % (name, field, value, expected[field])]
    return []


def run_winget_validate(manifest_dir: Path, timeout: float = 180.0) -> tuple[bool, str]:
    """在给定目录上跑官方 `winget validate`。

    输出走临时文件而不是管道：Windows 上 `capture_output=True` 在孙进程持有管道句柄时
    会卡过 timeout（本仓实测过一次 25 分钟不返回），超时改用 `taskkill /T` 杀整棵进程树。

    Args:
        manifest_dir: 只放着那 4 个清单的目录。
        timeout: 超时秒数。

    Returns:
        tuple[bool, str]: 是否通过、合并后的输出文本。
    """
    executable = shutil.which("winget")
    if executable is None:
        return False, "未找到 winget 命令（装一下「应用安装程序」App Installer，或用 --skip-validate）"
    with tempfile.TemporaryDirectory(prefix="winget-validate-") as sandbox:
        stdout_path = Path(sandbox) / "stdout.txt"
        stderr_path = Path(sandbox) / "stderr.txt"
        with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
            process = subprocess.Popen(
                [executable, "validate", "--manifest", str(manifest_dir)],
                stdout=stdout_file,
                stderr=stderr_file,
                stdin=subprocess.DEVNULL,
            )
            try:
                exit_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                               check=False, capture_output=True)
                return False, "winget validate 超过 %.0f 秒没返回，已杀进程" % timeout
        merged = stdout_path.read_bytes() + stderr_path.read_bytes()
    return exit_code == 0, _decode_output(merged)


def _decode_output(raw: bytes) -> str:
    """把 winget 的输出解码成文本（控制台编码可能是 UTF-8 也可能是 GBK）。"""
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def load_manifest_texts(winget_dir: Path | None = None) -> dict[str, str]:
    """读取 4 个清单的全文。

    Args:
        winget_dir: 清单目录，默认 `packaging/winget`。

    Returns:
        dict[str, str]: 文件名 → 全文（按字节解码，换行风格原样保留）。
    """
    return {name: path.read_bytes().decode("utf-8") for name, path in manifest_paths(winget_dir).items()}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="把 packaging/winget 的清单更新到 pyproject 里的版本")
    parser.add_argument("--write", action="store_true", help="真的写入文件（默认只预览）")
    parser.add_argument("--check", action="store_true", help="只体检清单与目标版本是否自洽（不联网）")
    parser.add_argument("--file", type=Path, help="复用已下载的安装包，不重新下载")
    parser.add_argument("--allow-api-missing", action="store_true", help="允许 Releases API 查不到 digest")
    parser.add_argument("--skip-validate", action="store_true", help="跳过官方 winget validate（不推荐）")
    parser.add_argument("--force", action="store_true", help="允许写成同一个或更小的版本")
    return parser.parse_args(argv)


def _resolve_download(args: argparse.Namespace, version: str, asset_name: str) -> tuple[str, int, bool]:
    """取得安装包并算出哈希。

    Returns:
        tuple[str, int, bool]: 大写的 SHA-256、字节数、是否用了 --file。
    """
    if args.file is not None:
        sha256, size = sha256_of_file(args.file)
        print("复用本地文件：%s（%d 字节）" % (args.file, size))
        return sha256, size, True
    url = build_asset_url(version, asset_name)
    with tempfile.TemporaryDirectory(prefix="winget-asset-") as sandbox:
        target = Path(sandbox) / asset_name
        print("下载 %s" % url)
        written = download_asset(url, target)
        sha256, size = sha256_of_file(target)
    print("下载完成：%d 字节" % written)
    return sha256, size, False


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 参数列表，默认取 `sys.argv[1:]`。

    Returns:
        int: 0 成功；1 校验/体检未过；2 用法或前置条件不满足。
    """
    args = _parse_args(argv)
    try:
        version, paths, current = _load_context()
    except ManifestError as exc:
        print("错误：%s" % exc)
        return 2
    if args.check:
        return _report_check(version)
    if not args.force and compare_versions(version, current) <= 0:
        print("错误：pyproject 版本 %s 不大于清单里的 %s（真要写就加 --force）。" % (version, current))
        return 2
    try:
        preview = _build_preview(args, version)
    except ManifestError as exc:
        print("错误：%s" % exc)
        return 2
    _print_preview(preview)
    if not _validate_preview(preview.new_texts, args):
        return 1
    if not args.write:
        print("以上是预览：加 --write 才会落盘。")
        return 0
    _write_files(paths, preview.new_texts)
    return 0


def _load_context() -> tuple[str, dict[str, Path], str]:
    """读齐「目标版本 + 4 个清单路径 + 清单里当前版本」。

    Returns:
        tuple[str, dict[str, Path], str]: 目标版本、文件名→路径、清单里的当前版本。

    Raises:
        ManifestError: 版本号读不了，或清单目录内容不对。
    """
    version = read_project_version()
    paths = manifest_paths()
    version_text = paths[f"{PACKAGE_ID}.yaml"].read_text(encoding="utf-8")
    return version, paths, current_field_value(version_text, "PackageVersion")


def _build_preview(args: argparse.Namespace, version: str) -> ManifestPreview:
    """下载 / 哈希 / 交叉核对 / 渲染，凑齐一份「待落盘」结果。

    Returns:
        ManifestPreview: 新旧全文与改动说明。

    Raises:
        ManifestError: 任一环节不成立（这种时候绝不写文件）。
    """
    asset_name = read_installer_asset_name(version)
    sha256, size, _ = _resolve_download(args, version, asset_name)
    sha256 = _cross_check(args, version, asset_name, sha256)
    old_texts = load_manifest_texts()
    new_texts, changed = render_manifests(
        version, build_asset_url(version, asset_name), build_release_url(version), sha256, old_texts)
    return ManifestPreview(version, asset_name, sha256, size, old_texts, new_texts, changed)


def _print_preview(preview: ManifestPreview) -> None:
    """打印哈希、改动清单与逐行 diff。"""
    print("SHA-256=%s（%d 字节）" % (preview.sha256, preview.size))
    if preview.changed:
        print("将改动 %d 处：" % len(preview.changed))
    else:
        print("无需改动：清单已经是这个版本 %s。" % preview.version)
    for item in preview.changed:
        print("  - %s" % item)
    print(format_diff(preview.old_texts, preview.new_texts))


def _write_files(paths: dict[str, Path], new_texts: dict[str, str]) -> None:
    """把新全文按 UTF-8 写回（不加 BOM、不动换行字节）。"""
    for name, text in new_texts.items():
        paths[name].write_bytes(text.encode("utf-8"))
    print("已写入 %d 个文件；记得 git add packaging/winget 后提交。" % len(new_texts))


def _report_check(version: str) -> int:
    """执行 `--check` 并返回退出码。"""
    problems = check_drift(version)
    if not problems:
        print("4 个清单与目标版本 %s 自洽（9 处字段全部一致）。" % version)
        return 0
    print("发现 %d 处不一致：" % len(problems))
    for item in problems:
        print("  - %s" % item)
    return 1


def _cross_check(args: argparse.Namespace, version: str, asset_name: str, sha256: str) -> str:
    """与 Releases API 的 digest 交叉核对，返回最终采用的 SHA-256。

    即使安装包是 `--file` 复用的本地文件，也照样核对：这个哈希来自 GitHub 自己算的，
    跟本地算的那条路径完全独立。
    """
    expected = fetch_expected_digest(version, asset_name)
    if expected is None:
        if not args.allow_api_missing:
            raise ManifestError(
                "Releases API 没给出 digest，核对不了；确认网络后重试，或用 --allow-api-missing 只用本地哈希")
        print("提示：按 --allow-api-missing 只用本地哈希。")
        return sha256
    if expected != sha256:
        raise ManifestError("哈希对不上：本地=%s，Releases API=%s" % (sha256, expected))
    print("交叉核对通过：本地哈希 = Releases API digest。")
    return sha256


def _validate_preview(new_texts: dict[str, str], args: argparse.Namespace) -> bool:
    """把预览写进临时目录并跑官方校验（通过才允许落盘）。"""
    if args.skip_validate:
        print("已按 --skip-validate 跳过官方校验（清单只改了 9 个字段，仍建议自己跑一次）。")
        return True
    with tempfile.TemporaryDirectory(prefix="winget-preview-") as sandbox:
        for name, text in new_texts.items():
            (Path(sandbox) / name).write_bytes(text.encode("utf-8"))
        passed, output = run_winget_validate(Path(sandbox))
    print("官方校验：%s" % ("通过" if passed else "未通过"))
    if output.strip():
        print("  " + output.strip().replace("\r", "").replace("\n", "\n  "))
    if not passed:
        print("校验没过 → 不落盘。")
    return passed

if __name__ == "__main__":
    sys.exit(main())
