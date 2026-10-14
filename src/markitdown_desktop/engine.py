# SPDX-License-Identifier: MIT
"""转换引擎：对 MarkItDown 的薄封装。

设计要点（对应可行性评估 §3.1）：
- 本地文件一律走 convert_local()，避免 convert() 的宽口径与 convert_stream() 的全量入内存；
- 失败信息保留 FileConversionException.attempts 明细，便于 UI 呈现"哪个转换器因何失败"；
- 返回结构化结果，供 GUI 与 --selftest 复用。

@author 康小汪【kxw】
@date 2026-10-13
"""

from __future__ import annotations

import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from markitdown import (
    FileConversionException,
    MarkItDown,
    StreamInfo,
    UnsupportedFormatException,
)

# 常见编码提示：magika/charset 猜错时用于重试（见 troubleshooting：UnicodeDecodeError）
_CHARSET_HINTS = ("utf-8", "gb18030", "shift_jis")

# 图片后缀：无 exiftool / LLM 时这些格式会"成功但 0 字符"，需要专门提示
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"})


#: 空结果警告的文案：键是 `_warning_kind()` 给出的码。
#: T17 起界面按码查 i18n 表，这里的原文案保持不动——`ConversionOutcome.warning`
#: 是对外契约（--selftest --json 与既有测试都在读它），只增不改。
_WARNING_TEXTS = {
    "no_text_image": "已转换但未提取到文本：图片需要 exiftool 或 LLM 描述才能生成内容",
    "no_text": "已转换但未提取到文本内容（可能是空文档或纯图片文档）",
}


def _warning_kind(markdown: str, source: Path) -> str:
    """判断成功但无正文属于哪一类：给界面查 i18n 用的稳定码。

    Args:
        markdown: 转换出的 Markdown 文本。
        source: 源文件路径，用于判断是否为图片。

    Returns:
        str: 有正文时返回空串，否则 `no_text_image` / `no_text`。

    @author 康小汪【kxw】
    @date 2026-10-14
    """
    # 用 isspace() 而非 strip()：前者不产生字符串副本，大文档（实测 RSS 有 200 KB+）无额外内存开销
    if markdown and not markdown.isspace():
        return ""
    return "no_text_image" if source.suffix.lower() in _IMAGE_SUFFIXES else "no_text"


def _warning_for(markdown: str, source: Path) -> str:
    """转换成功但未提取到文本时，给出可操作的提示。

    Args:
        markdown: 转换出的 Markdown 文本。
        source: 源文件路径，用于判断是否为图片。

    Returns:
        str: 提示文案；有正文时返回空串。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    return _WARNING_TEXTS.get(_warning_kind(markdown, source), "")


@dataclass
class ConversionOutcome:
    """单次转换的结果与耗时。

    Attributes:
        source: 源文件路径。
        ok: 是否转换成功。
        markdown: 转换出的 Markdown 文本（失败时为空串）。
        elapsed_s: 转换耗时（秒）。
        error: 失败原因；成功时为空串。
        warning: 成功但未提取到文本时的提示；无警告时为空串。
        attempts: 转换器尝试明细，仅在 FileConversionException 分支填充。
        error_code: 失败原因码（如 `unsupported_format`），供界面查 i18n 文案；
            `error` 原文保持不变，无码时界面回退显示原文。
        warning_code: 无正文警告码（`no_text` / `no_text_image`），语义同上。

    @author 康小汪【kxw】
    @date 2026-02（创建）、2026-10-14（T17 追加 error_code / warning_code）
    """

    source: str
    ok: bool
    markdown: str = ""
    elapsed_s: float = 0.0
    error: str = ""
    warning: str = ""
    attempts: list[str] = field(default_factory=list)
    error_code: str = ""
    warning_code: str = ""


class NamingPolicy(str, Enum):
    """批量输出时的重名处理策略。

    Attributes:
        SUFFIX: 追加序号：`a.docx` + `a.pdf` → `a.md`、`a (1).md`（文件名干净）。
        KEEP_EXT: 把 `.md` 追加到完整文件名后：`a.docx` → `a.docx.md`（便于回溯来源）。

    @author 康小汪【kxw】
    @date 2026-10-13
    """

    SUFFIX = "suffix"
    KEEP_EXT = "keep_ext"


def _is_taken(candidate: Path, taken: set[str]) -> bool:
    """判断候选输出路径是否已被本批次占用，或磁盘上已存在。

    用 os.path.normcase 归一化后再比较，以匹配 Windows 大小写不敏感的文件系统
    （`a.md` 与 `A.MD` 必须视为同一个文件，否则会静默覆盖）。

    Args:
        candidate: 待检查的输出路径。
        taken: 本批次已占用的路径键集合。

    Returns:
        bool: True 表示该路径不可用。

    @author 康小汪【kxw】
    @date 2026-10-13
    """
    return os.path.normcase(str(candidate.resolve())) in taken or candidate.exists()


class ConversionEngine:
    """持有 MarkItDown 实例并执行转换。

    MarkItDown 实例不是线程安全的，因此每个工作线程应各自持有一个引擎实例。

    @author 康小汪【kxw】
    @date 2026-02
    """

    def __init__(self, enable_plugins: bool = False) -> None:
        self._md = MarkItDown(enable_plugins=enable_plugins)

    def convert_file(self, path: str | Path) -> ConversionOutcome:
        """转换单个本地文件。

        Args:
            path: 待转换文件路径。

        Returns:
            ConversionOutcome: 含 Markdown 文本、耗时与错误明细。

        @author 康小汪【kxw】
        @date 2026-02（创建）、2026-10-13（T3 补充空结果警告）
        """
        path = Path(path)
        start = time.perf_counter()
        try:
            result = self._md.convert_local(str(path))
            return ConversionOutcome(
                source=str(path),
                ok=True,
                markdown=result.markdown,
                elapsed_s=time.perf_counter() - start,
                warning=_warning_for(result.markdown, path),
                warning_code=_warning_kind(result.markdown, path),
            )
        except UnsupportedFormatException as exc:
            return ConversionOutcome(
                source=str(path),
                ok=False,
                error=f"格式不支持：{exc}",
                elapsed_s=time.perf_counter() - start,
                error_code="unsupported_format",
            )
        except FileConversionException as exc:
            attempts = []
            for attempt in exc.attempts or []:
                name = type(attempt.converter).__name__
                if attempt.exc_info is None:
                    attempts.append(f"{name}: 无执行信息")
                else:
                    attempts.append(f"{name}: {attempt.exc_info[0].__name__} - {attempt.exc_info[1]}")
            # UnicodeDecodeError 时按常见编码重试一次，规避字符集猜测错误
            if any("UnicodeDecodeError" in a for a in attempts):
                retry = self._retry_with_charset(path, start)
                if retry is not None:
                    return retry
            return ConversionOutcome(
                source=str(path),
                ok=False,
                error=str(exc),
                attempts=attempts,
                elapsed_s=time.perf_counter() - start,
            )
        except Exception as exc:  # noqa: BLE001 - GUI 需要兜住所有异常
            return ConversionOutcome(
                source=str(path),
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                elapsed_s=time.perf_counter() - start,
            )

    def _retry_with_charset(self, path: Path, start: float) -> ConversionOutcome | None:
        """编码猜测失败时，按候选编码依次重试。

        Args:
            path: 文件路径。
            start: 首次转换的起始时间戳，用于累计耗时。

        Returns:
            成功时返回结果，全部失败返回 None（交由调用方报告原始错误）。

        @author 康小汪【kxw】
        @date 2026-02（创建）、2026-10-13（T3 补充空结果警告）
        """
        extension = path.suffix
        for charset in _CHARSET_HINTS:
            try:
                result = self._md.convert_local(
                    str(path),
                    stream_info=StreamInfo(extension=extension, charset=charset),
                )
                return ConversionOutcome(
                    source=str(path),
                    ok=True,
                    markdown=result.markdown,
                    elapsed_s=time.perf_counter() - start,
                    warning=_warning_for(result.markdown, path),
                )
            except Exception:  # noqa: BLE001 - 重试失败继续下一个编码
                continue
        return None

    @staticmethod
    def plan_outputs(
        sources: Sequence[str | Path],
        output_dir: str | Path | None = None,
        policy: NamingPolicy = NamingPolicy.SUFFIX,
        reserved: Iterable[str | Path] = (),
    ) -> list[Path]:
        """为一批源文件规划输出路径，保证批内与磁盘上都不覆盖。

        批量转换的安全入口：逐个推导 `.md` 路径，一旦与本批次其他文件或磁盘上
        已有文件冲突，就按 `name (1).md`、`name (2).md` 依次向后寻找可用名。

        Args:
            sources: 源文件路径序列。
            output_dir: 统一输出目录；为 None 时各自输出到源文件所在目录。
            policy: 重名处理策略，见 NamingPolicy。
            reserved: 额外需要避开的路径（如用户已指定的其他输出目标）。

        Returns:
            list[Path]: 与 sources 一一对应的输出路径，调用方可直接写出。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        taken = {os.path.normcase(str(Path(path).resolve())) for path in reserved}
        planned: list[Path] = []
        for source in sources:
            source = Path(source)
            target_dir = Path(output_dir) if output_dir else source.parent
            # KEEP_EXT 用完整文件名做基名，SUFFIX 只用主干名
            stem = source.name if policy is NamingPolicy.KEEP_EXT else source.stem
            candidate = target_dir / f"{stem}.md"
            index = 1
            while _is_taken(candidate, taken):
                candidate = target_dir / f"{stem} ({index}).md"
                index += 1
            taken.add(os.path.normcase(str(candidate.resolve())))
            planned.append(candidate)
        return planned

    @staticmethod
    def write_markdown(outcome: ConversionOutcome, target: str | Path) -> Path:
        """以 UTF-8 写出 Markdown（规避 stdout 编码替换问题）。"""
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(outcome.markdown, encoding="utf-8")
        return target

    @staticmethod
    @contextmanager
    def temp_snapshot(path: str | Path) -> Iterator[Path]:
        """上下文管理器：把文件复制到临时目录再转换，规避 Office 占用导致的读取失败。

        用法::

            with ConversionEngine.temp_snapshot(p) as tmp:
                engine.convert_file(tmp)

        退出时自动删除临时文件，避免残留。
        """
        path = Path(path)
        handle, tmp_name = tempfile.mkstemp(suffix=path.suffix, prefix="mdd_")
        os.close(handle)
        tmp_path = Path(tmp_name)
        try:
            tmp_path.write_bytes(path.read_bytes())
            yield tmp_path
        finally:
            tmp_path.unlink(missing_ok=True)
