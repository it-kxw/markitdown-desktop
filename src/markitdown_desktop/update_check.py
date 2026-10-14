# SPDX-License-Identifier: MIT
"""更新检查：只提示，不自动下载。

设计要点全部由探针实测决定（见 `docs/p2-implementation-plan.md` 的 Task 20）：

* **不走 GitHub API**：`api.github.com` 匿名配额只有 60 次/小时，实测本机已被限流
  （HTTP 403 `rate limit exceeded`）——共享出口 IP 很容易触发。改用
  `github.com/<owner>/<repo>/releases/latest` 的 302 重定向，**只读 Location 头**、
  不下载正文（实测正文 316 KB，本项目用不上），既不吃配额也不费流量。
* **一律不抛异常**：网络是外部世界，失败只返回 `unknown`，绝不打断用户。
* **不发任何用户数据**：请求只带固定的 User-Agent（含本程序版本），
  不带机器名、路径、用户名等任何信息；URL 里也没有 query。
* **不自动下载/安装**：只返回结果，由界面提示。
* **不导入 Qt**：纯标准库，便于单测，也避免和界面耦合。

@author 康小汪【kxw】
@date 2026-10-14
"""

from __future__ import annotations

import http.client
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import __version__

# 本项目自己的 Release 页。仓库还是私有状态时会 404 —— 按「无法确认」处理。
RELEASES_LATEST_URL = "https://github.com/it-kxw/markitdown-desktop/releases/latest"

# 实测单次请求 5~15 秒（网络慢是常态），所以给足超时；调用方必须放在后台线程里跑
REQUEST_TIMEOUT_S = 15.0

USER_AGENT = f"markitdown-desktop/{__version__}"

STATUS_AVAILABLE = "available"
STATUS_LATEST = "latest"
STATUS_UNKNOWN = "unknown"

_TAG_RE = re.compile(r"/releases/tag/(?P<tag>[^/?#]+)")
_VERSION_RE = re.compile(r"^(?P<numbers>\d+(?:\.\d+)*)")


@dataclass(frozen=True)
class UpdateResult:
    """一次更新检查的结果。

    Attributes:
        status: `available`（有新版）/ `latest`（已是最新）/ `unknown`（无法确认）。
        current: 当前版本号。
        latest: 远端最新版本号；无法确认时为 None。
        url: 发布页地址；无法确认时为 None。

    @author 康小汪【kxw】
    @date 2026-10-14
    """

    status: str
    current: str
    latest: str | None = None
    url: str | None = None

    @property
    def has_update(self) -> bool:
        """是否发现了新版本。"""
        return self.status == STATUS_AVAILABLE


def parse_version(text: str) -> tuple[int, ...] | None:
    """把版本号解析成可比较的整数元组，解析不出来返回 None。

    容忍 `v` 前缀与预发布后缀：`v0.2.0-rc1` → `(0, 2, 0)`。后缀一律忽略是本项目的
    **已知边界**（只发 `vX.Y.Z` 正式版），它不是一个通用版本比较器。

    Args:
        text: 形如 `v0.1.8` / `0.1` / `0.1.0-rc1` 的版本字符串。

    Returns:
        tuple[int, ...] | None: 版本元组；无法解析时为 None。

    @author 康小汪【kxw】
    @date 2026-10-14
    """
    if not text:
        return None
    match = _VERSION_RE.match(text.strip().lstrip("vV"))
    if match is None:
        return None
    return tuple(int(part) for part in match.group("numbers").split("."))


def is_newer(latest: str, current: str) -> bool:
    """判断 `latest` 是否比 `current` 新；任一解析失败都返回 False。

    Args:
        latest: 远端版本号。
        current: 当前版本号。

    Returns:
        bool: 远端更新时为 True。

    @author 康小汪【kxw】
    @date 2026-10-14
    """
    remote, local = parse_version(latest), parse_version(current)
    if remote is None or local is None:
        return False
    return remote > local


class _StopAtRedirect(urllib.request.HTTPRedirectHandler):
    """遇到 3xx 就停下，只把 Location 头交出来，不跟过去下载正文。

    这是「不吃 API 配额、也不白下 300 KB」的关键：默认的 urllib 会一路跟到
    最终页面并把正文读下来。

    @author 康小汪【kxw】
    @date 2026-10-14
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - urllib 回调
        """把重定向变成可捕获的 HTTPError，从而拿到 Location。"""
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def fetch_latest_tag(
    url: str = RELEASES_LATEST_URL, timeout: float = REQUEST_TIMEOUT_S
) -> str | None:
    """取远端最新 Release 的 tag；任何失败都返回 None（绝不抛异常）。

    Args:
        url: `releases/latest` 地址。
        timeout: 单次请求超时（秒）。

    Returns:
        str | None: 例如 `v0.2.0`；取不到时为 None。

    @author 康小汪【kxw】
    @date 2026-10-14
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(_StopAtRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            location = response.geturl()
    except urllib.error.HTTPError as error:
        location = (error.headers or {}).get("Location", "") if 300 <= error.code < 400 else ""
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
        return None
    match = _TAG_RE.search(location or "")
    return match.group("tag") if match else None


def check_for_update(
    current: str | None = None,
    url: str = RELEASES_LATEST_URL,
    timeout: float = REQUEST_TIMEOUT_S,
) -> UpdateResult:
    """检查是否有新版本；任何失败都归为 `unknown`。

    Args:
        current: 当前版本；None 时取本包 `__version__`。
        url: 检查地址（测试可注入）。
        timeout: 超时秒数。

    Returns:
        UpdateResult: 三态结果。

    @author 康小汪【kxw】
    @date 2026-10-14
    """
    current_version = current or __version__
    tag = fetch_latest_tag(url, timeout)
    if not tag:
        return UpdateResult(status=STATUS_UNKNOWN, current=current_version)
    latest = tag.lstrip("vV")
    page_url = f"{url.replace('/releases/latest', '')}/releases/tag/{tag}"
    if is_newer(latest, current_version):
        return UpdateResult(STATUS_AVAILABLE, current_version, latest, page_url)
    return UpdateResult(STATUS_LATEST, current_version, latest, page_url)


__all__ = [
    "RELEASES_LATEST_URL",
    "REQUEST_TIMEOUT_S",
    "STATUS_AVAILABLE",
    "STATUS_LATEST",
    "STATUS_UNKNOWN",
    "UpdateResult",
    "check_for_update",
    "fetch_latest_tag",
    "is_newer",
    "parse_version",
]
