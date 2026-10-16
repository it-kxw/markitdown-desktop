# SPDX-License-Identifier: MIT
"""界面文案表（中文 / 英文）与查表函数。

为什么自研而不用 Qt 的 `.ts`/`.qm`：本应用只有约 90 条文案、两种语言，
不值得为它引入 lupdate/lrelease 构建链（P2 Global Constraint：依赖零增长）。
PySide6 的 `.qm` 仍然用于**标准对话框**（打开/保存/退出确认的按钮文字），
那部分由 `ui.install_qt_translations()` 单独加载。

三条硬规则（都有测试守着）：
1. `zh_CN` 与 `en` 的键集合必须完全一致、没有空值——漏翻一半的界面比全中文更难用；
2. 查不到的键返回**键本身**（界面上会露出 `ui.foo.bar`）：一眼看得见漏翻，
   比静默回退成空串或中文更容易被发现；
3. 不支持的语言码一律退回英文：英文是"看不懂中文的用户"的兜底。

本模块**不导入 Qt**，也不被 `engine.py` / `worker.py` 导入——那两个模块只产出
错误码（`error_code` / `warning_code`），文案在界面层查表，所以子进程零负担。

@author 康小汪【kxw】
@date 2026-10-14
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

#: 设置里的"跟随系统"取值；解析成具体语言由界面层负责（需要 QLocale，见 ui.py）
LANGUAGE_AUTO = "auto"

#: 界面支持的语言（顺序即设置里下拉框的展示顺序）
SUPPORTED_LANGUAGES = ("zh_CN", "en")

#: 兜底语言：不支持的语言码都用它
FALLBACK_LANGUAGE = "en"

#: 文案表：语言码 → {键: 文案}。占位符用 str.format 语法（如 {name}）
_STRINGS: dict[str, dict[str, str]] = {
    "zh_CN": {
        # ---- 队列条目状态 ----
        "ui.item.pending": "待转换",
        "ui.item.running": "转换中…",
        "ui.item.ok": "✅ 成功",
        "ui.item.warn": "⚠️ 无内容",
        "ui.item.fail": "❌ 失败",
        # ---- 按钮与提示 ----
        "ui.button.add": "添加文件…",
        "ui.button.start": "开始转换",
        "ui.button.cancel": "取消",
        "ui.button.retry": "重试失败项",
        "ui.button.retry_tip": "只重跑失败的文件；已成功的不会重转",
        "ui.button.save_as": "另存为 .md",
        "ui.button.save_as_tip": "选中多个时：选一个文件夹，按原文件名生成 .md",
        "ui.button.copy_error": "复制错误",
        "ui.button.copy": "复制",
        "ui.button.clear": "清空",
        "ui.list.hint": "把文件或文件夹拖到这里，或点击「添加文件」：",
        # 计数胶囊：{count} 由界面层填入，改文案时别丢占位符
        "ui.count.zero": "0 项",
        "ui.count.items": "{count} 项",
        # 空状态卡（队列为空时显示）；快捷键用 Ctrl+O / Ctrl+Enter，与菜单一致
        "ui.empty.kicker": "开始",
        "ui.empty.title": "把文件拖进来，一次转换整批",
        "ui.empty.desc": "支持 PDF、Word、Excel、PowerPoint、HTML、CSV、EPub、"
                         "Outlook 邮件与 ZIP。全部在本机完成，文件不出机器。",
        "ui.empty.key_add": "添加文件",
        "ui.empty.key_run": "开始转换",
        # 预览区头部
        "ui.preview.head": "预览",
        "ui.preview.kind": "Markdown",
        "ui.preview.copy_tip": "复制全部 Markdown 到剪贴板",
        "ui.preview.empty": "在左侧选中一个文件查看转换结果",
        "ui.preview.placeholder": "转换结果预览",
        "ui.status.ready": "就绪",
        # ---- 菜单 ----
        "ui.menu.file": "文件",
        "ui.menu.clear": "清空队列",
        "ui.menu.open_log": "打开日志目录",
        "ui.menu.export_log": "导出日志…",
        "ui.menu.settings": "设置…",
        "ui.menu.quit": "退出",
        "ui.menu.help": "帮助",
        "ui.menu.about": "关于",
        "ui.menu.donate": "请作者喝杯咖啡",
        "ui.donate.title": "请作者喝杯咖啡",
        "ui.donate.intro": "MarkItDown Desktop 是免费开源的，如果它帮到了你，"
                           "可以请作者喝杯咖啡。",
        "ui.donate.optional": "完全自愿，不影响任何功能。",
        "ui.donate.zfb": "支付宝",
        "ui.donate.wx": "微信",
        "ui.donate.hint": "扫码或保存图片后识别都可以。",
        "ui.donate.missing": "收款码图片不可用（精简环境或源码运行时可能出现）。",
        "ui.menu.check_update": "检查更新…",
        # ---- 队列与通用状态 ----
        "ui.status.queue": "队列 {count} 个文件",
        "ui.dialog.pick_files": "选择要转换的文件",
        "ui.status.cleared": "已清空",
        "ui.status.dir_failed": "目录读取失败：{error}",
        "ui.warn.busy": "上一批还在转换中，请先取消或等待完成",
        "ui.title.notice": "提示",
        "ui.warn.no_files": "请先添加文件",
        "ui.warn.nothing_to_retry": "没有可重试的失败项",
        "ui.status.cancelled": "已取消",
        "ui.status.timeout": "{name} 超时（{seconds:.0f} s），已终止",
        "ui.error.pool": "进程池故障：{message}",
        # ---- 批次汇总与进度 ----
        "ui.summary.done": "完成：{ok}/{total} 成功",
        "ui.summary.warned": "{count} 个未提取到文本",
        "ui.summary.failed": "{count} 个失败",
        "ui.summary.written": "已写出 {count} 个文件",
        "ui.progress.done": "已完成 {done}/{total}",
        "ui.progress.current": "当前 {name}",
        "ui.progress.left": "剩余 {count} 个",
        "ui.progress.elapsed": "已用时 {seconds:.1f} s",
        "ui.item.write_failed": "  — 写出失败：{error}",
        "ui.status.write_failed": "写出失败：{error}",
        "ui.status.written_one": "已写出 {path}",
        "ui.preview.failed": "转换失败：\n{error}\n\n各转换器尝试明细：\n{detail}",
        # ---- 另存为 ----
        "ui.warn.select_converted": "请先选中一个已转换的文件",
        "ui.warn.select_success": "请先成功转换一个文件",
        "ui.dialog.save_folder": "选择保存到的文件夹",
        "ui.dialog.save_md": "保存 Markdown",
        "ui.title.save_failed": "保存失败",
        "ui.status.saved": "已保存：{path}",
        "ui.error.save_many": "另存为失败：{error}",
        "ui.status.saved_many": "已保存 {count} 个文件到 {target}",
        "ui.status.save_partial": "{count} 个失败（{error}）",
        "ui.skip.note": "（跳过 {count} 个未成功）",
        "ui.status.copied": "已复制到剪贴板",
        # ---- 导出日志 ----
        "ui.dialog.export_log": "导出日志",
        "ui.filter.zip": "Zip 压缩包 (*.zip)",
        "ui.status.no_logs": "没有可导出的日志：{path}",
        "ui.status.export_failed": "导出失败：{error}",
        "ui.status.exported": "已导出日志：{path}",
        # ---- 设置与关于 ----
        "ui.status.settings_saved": "设置已保存",
        "ui.status.language_restart": "界面语言将在重启应用后生效",
        "ui.confirm.lang_title": "需要重启应用",
        "ui.confirm.lang_text": "界面语言已改为「{language}」，"
                               "需要重启应用才能让所有文字切换。\n\n现在重启吗？",
        "ui.confirm.lang_restart": "立即重启",
        "ui.confirm.lang_later": "稍后",
        "ui.status.update_available": "发现新版本 {version}（帮助 → 检查更新）",
        "ui.status.update_latest": "已是最新版本（{version}）",
        "ui.status.update_unknown": "无法检查更新（网络不通，或尚未发布 Release）",
        "ui.about.title": "关于 MarkItDown Desktop",
        "ui.about.log_dir": "日志目录：{path}",
        "ui.about.no_logs": "（暂无日志）",
        "ui.update.title": "检查更新",
        "ui.update.available": "发现新版本：{latest}（当前 {current}）",
        "ui.update.latest": "已是最新版本（{current}）。",
        "ui.update.unknown": "无法确认是否有新版本。\n\n可能是网络不通，或本项目尚未发布 Release。",
        "ui.update.open": "打开发布页",
        "ui.update.close": "关闭",
        "ui.confirm.busy_title": "仍在转换",
        "ui.confirm.busy_text": "有文件正在转换，确定要退出吗？",
        # ---- 设置对话框 ----
        "dlg.title": "设置",
        "dlg.output_dir_label": "输出目录：",
        "dlg.output_dir_placeholder": "留空表示写到源文件旁边",
        "dlg.browse": "浏览…",
        "dlg.same_dir": "与源文件同目录",
        "dlg.policy_label": "重名处理：",
        "dlg.policy_suffix": "追加序号（a.md、a (1).md）",
        "dlg.policy_keep_ext": "保留扩展名（a.docx.md）",
        "dlg.concurrency_label": "并发数：",
        "dlg.concurrency_tip": "同时转换的文件数：越大越快，内存占用也越高",
        "dlg.auto_save": "转换完成后自动写出 .md",
        "dlg.check_updates": "启动时检查更新",
        "dlg.check_updates_tip": "只读取 GitHub 的发布页，不发送任何本机信息",
        "dlg.language_label": "界面语言：",
        "dlg.language_auto": "跟随系统",
        "dlg.language_zh_CN": "简体中文",
        "dlg.language_en": "English",
        "dlg.theme_label": "外观：",
        "dlg.theme_auto": "跟随系统",
        "dlg.theme_light": "浅色",
        "dlg.theme_dark": "深色",
        "dlg.restore_defaults": "恢复默认",
        "dlg.ok": "确定",
        "dlg.cancel": "取消",
        "dlg.choose_output_dir": "选择输出目录",
        "dlg.invalid_output_dir": "输出目录无效",
        "dlg.invalid_output_dir_text": "目录不存在：{path}\n\n请重新选择，或勾选「与源文件同目录」。",
        # ---- 结果码：失败原因（engine / worker 只给码） ----
        "err.unsupported_format": "格式不支持",
        "err.timeout": "转换超时，已终止",
        "err.crash": "子进程异常退出（转换器崩溃），已终止",
        "err.cancelled": "已取消",
        # ---- 结果码：成功但无正文 ----
        "msg.no_text": "已转换但未提取到文本内容（可能是空文档或纯图片文档）",
        "msg.no_text_image": "已转换但未提取到文本：图片需要 exiftool 或 LLM 描述才能生成内容",
    },
    "en": {
        # ---- queue item status ----
        "ui.item.pending": "Pending",
        "ui.item.running": "Converting…",
        "ui.item.ok": "✅ Done",
        "ui.item.warn": "⚠️ No text",
        "ui.item.fail": "❌ Failed",
        # ---- buttons and hints ----
        "ui.button.add": "Add files…",
        "ui.button.start": "Convert",
        "ui.button.cancel": "Cancel",
        "ui.button.retry": "Retry failed",
        "ui.button.retry_tip": "Only failed files are converted again; successful ones are kept",
        "ui.button.save_as": "Save as .md",
        "ui.button.save_as_tip": "With several rows selected: pick one folder, names come from the source files",
        "ui.button.copy_error": "Copy error",
        "ui.button.copy": "Copy",
        "ui.button.clear": "Clear",
        "ui.list.hint": "Drop files or folders here, or click “Add files”:",
        "ui.count.zero": "0 items",
        "ui.count.items": "{count} items",
        "ui.empty.kicker": "Start",
        "ui.empty.title": "Drop files in, convert the whole batch",
        "ui.empty.desc": "Supports PDF, Word, Excel, PowerPoint, HTML, CSV, EPub, "
                         "Outlook messages and ZIP. Everything runs locally — "
                         "your files never leave this machine.",
        "ui.empty.key_add": "Add files",
        "ui.empty.key_run": "Start converting",
        "ui.preview.head": "Preview",
        "ui.preview.kind": "Markdown",
        "ui.preview.copy_tip": "Copy all Markdown to clipboard",
        "ui.preview.empty": "Select a file on the left to see its result",
        "ui.preview.placeholder": "Conversion result",
        "ui.status.ready": "Ready",
        # ---- menus ----
        "ui.menu.file": "File",
        "ui.menu.clear": "Clear queue",
        "ui.menu.open_log": "Open log folder",
        "ui.menu.export_log": "Export logs…",
        "ui.menu.settings": "Settings…",
        "ui.menu.quit": "Exit",
        "ui.menu.help": "Help",
        "ui.menu.about": "About",
        "ui.menu.donate": "Buy the author a coffee",
        "ui.donate.title": "Buy the author a coffee",
        "ui.donate.intro": "MarkItDown Desktop is free and open source. "
                           "If it helped you, you can buy the author a coffee.",
        "ui.donate.optional": "Entirely optional — it does not affect any feature.",
        "ui.donate.zfb": "Alipay",
        "ui.donate.wx": "WeChat",
        "ui.donate.hint": "Scan the code, or save the image and scan from your gallery.",
        "ui.donate.missing": "The payment QR images are not available (this can happen in a trimmed build or when running from source).",
        "ui.menu.check_update": "Check for updates…",
        # ---- queue and general status ----
        "ui.status.queue": "{count} file(s) in queue",
        "ui.dialog.pick_files": "Choose files to convert",
        "ui.status.cleared": "Cleared",
        "ui.status.dir_failed": "Failed to read the folder: {error}",
        "ui.warn.busy": "The previous batch is still running — cancel it or wait for it to finish",
        "ui.title.notice": "Notice",
        "ui.warn.no_files": "Add some files first",
        "ui.warn.nothing_to_retry": "Nothing to retry",
        "ui.status.cancelled": "Cancelled",
        "ui.status.timeout": "{name} timed out (after {seconds:.0f} s) and was terminated",
        "ui.error.pool": "Process pool failure: {message}",
        # ---- batch summary and progress ----
        "ui.summary.done": "Done: {ok}/{total} succeeded",
        "ui.summary.warned": "{count} with no text extracted",
        "ui.summary.failed": "{count} failed",
        "ui.summary.written": "{count} file(s) written",
        "ui.progress.done": "{done}/{total} done",
        "ui.progress.current": "current {name}",
        "ui.progress.left": "{count} left",
        "ui.progress.elapsed": "{seconds:.1f} s elapsed",
        "ui.item.write_failed": "  — write failed: {error}",
        "ui.status.write_failed": "Write failed: {error}",
        "ui.status.written_one": "Written {path}",
        "ui.preview.failed": "Conversion failed:\n{error}\n\nConverter attempts:\n{detail}",
        # ---- save as ----
        "ui.warn.select_converted": "Select a converted file first",
        "ui.warn.select_success": "Convert a file successfully first",
        "ui.dialog.save_folder": "Choose a folder to save into",
        "ui.dialog.save_md": "Save Markdown",
        "ui.title.save_failed": "Save failed",
        "ui.status.saved": "Saved: {path}",
        "ui.error.save_many": "Save failed: {error}",
        "ui.status.saved_many": "Saved {count} file(s) to {target}",
        "ui.status.save_partial": "{count} failed ({error})",
        "ui.skip.note": " ({count} not successful, skipped)",
        "ui.status.copied": "Copied to clipboard",
        # ---- export logs ----
        "ui.dialog.export_log": "Export logs",
        "ui.filter.zip": "Zip archive (*.zip)",
        "ui.status.no_logs": "No logs to export: {path}",
        "ui.status.export_failed": "Export failed: {error}",
        "ui.status.exported": "Logs exported: {path}",
        # ---- settings and about ----
        "ui.status.settings_saved": "Settings saved",
        "ui.status.language_restart": "Language will take effect after restarting the app",
        "ui.confirm.lang_title": "Restart required",
        "ui.confirm.lang_text": "The interface language was changed to \"{language}\". "
                               "A restart is needed for every label to switch.\n\nRestart now?",
        "ui.confirm.lang_restart": "Restart now",
        "ui.confirm.lang_later": "Later",
        "ui.status.update_available": "New version {version} available (Help → Check for updates)",
        "ui.status.update_latest": "Up to date ({version})",
        "ui.status.update_unknown": "Could not check for updates (no network, or no release published yet)",
        "ui.about.title": "About MarkItDown Desktop",
        "ui.about.log_dir": "Log folder: {path}",
        "ui.about.no_logs": "(no logs yet)",
        "ui.update.title": "Check for updates",
        "ui.update.available": "New version available: {latest} (you have {current})",
        "ui.update.latest": "You are up to date ({current}).",
        "ui.update.unknown": "Could not determine whether a newer version exists.\n\nThe network may be unavailable, or this project has not published a release yet.",
        "ui.update.open": "Open release page",
        "ui.update.close": "Close",
        "ui.confirm.busy_title": "Still converting",
        "ui.confirm.busy_text": "Files are still converting. Quit anyway?",
        # ---- settings dialog ----
        "dlg.title": "Settings",
        "dlg.output_dir_label": "Output folder:",
        "dlg.output_dir_placeholder": "Leave empty to write next to the source file",
        "dlg.browse": "Browse…",
        "dlg.same_dir": "Same folder as the source file",
        "dlg.policy_label": "Name conflicts:",
        "dlg.policy_suffix": "Append a number (a.md, a (1).md)",
        "dlg.policy_keep_ext": "Keep the extension (a.docx.md)",
        "dlg.concurrency_label": "Parallel jobs:",
        "dlg.concurrency_tip": "How many files convert at the same time: faster, but uses more memory",
        "dlg.auto_save": "Write the .md automatically after conversion",
        "dlg.check_updates": "Check for updates on startup",
        "dlg.check_updates_tip": "Only reads the GitHub release page; sends no information about your machine",
        "dlg.language_label": "Language:",
        "dlg.language_auto": "Follow system",
        "dlg.language_zh_CN": "Chinese (Simplified)",
        "dlg.language_en": "English",
        "dlg.theme_label": "Appearance:",
        "dlg.theme_auto": "Follow system",
        "dlg.theme_light": "Light",
        "dlg.theme_dark": "Dark",
        "dlg.restore_defaults": "Restore defaults",
        "dlg.ok": "OK",
        "dlg.cancel": "Cancel",
        "dlg.choose_output_dir": "Choose output folder",
        "dlg.invalid_output_dir": "Invalid output folder",
        "dlg.invalid_output_dir_text": "Folder not found: {path}\n\nPick another folder, or tick “Same folder as the source file”.",
        # ---- result codes: failure reasons ----
        "err.unsupported_format": "Unsupported format",
        "err.timeout": "Conversion timed out and was terminated",
        "err.crash": "The converter process crashed and was terminated",
        "err.cancelled": "Cancelled",
        # ---- result codes: converted but no text ----
        "msg.no_text": "Converted, but no text was extracted (empty or image-only document)",
        "msg.no_text_image": "Converted, but no text was extracted: images need exiftool or an LLM description",
    },
}

#: 当前生效的语言；模块级状态，由 `set_language` 改写
_current_language = FALLBACK_LANGUAGE


def normalize_language(code: object) -> str:
    """把外部来的语言码收敛成受支持的语言。

    大小写不敏感；`auto`、None、空串、以及任何未知值（手改的配置文件就可能带来）
    一律落到英文，绝不抛出异常——启动期不允许被一个语言码中断。

    Args:
        code: 语言码，可能来自设置文件或命令行的任意值。

    Returns:
        str: `SUPPORTED_LANGUAGES` 中的某一项。

    @author 康小汪【kxw】
    @date 2026-10-14
    """
    if isinstance(code, str):
        wanted = code.strip().lower()
        for supported in SUPPORTED_LANGUAGES:
            if wanted == supported.lower():
                return supported
    return FALLBACK_LANGUAGE


def set_language(code: object) -> str:
    """设置当前语言（切换后已建好的界面不会自动刷新，需重新打开窗口）。

    Args:
        code: 语言码；不支持的值退回英文，见 `normalize_language`。

    Returns:
        str: 实际生效的语言码。

    @author 康小汪【kxw】
    @date 2026-10-14
    """
    global _current_language
    _current_language = normalize_language(code)
    return _current_language


def current_language() -> str:
    """返回当前生效的语言码。

    Returns:
        str: 语言码。

    @author 康小汪【kxw】
    @date 2026-10-14
    """
    return _current_language


def tr(key: str, **params: object) -> str:
    """查表取文案；当前语言缺键时退回英文，仍缺则返回键本身。

    格式参数缺失时返回**未格式化**的原文而不是抛异常：这段代码跑在 Qt 槽函数里，
    抛出去的 KeyError 会变成一条未捕获异常对话框，比少一个数字严重得多。

    Args:
        key: 文案键，形如 `ui.button.start`。
        **params: 文案里的占位符（`str.format` 语法）。

    Returns:
        str: 文案；查不到时返回 key 本身（界面上会露出 `ui.foo.bar`，便于发现漏翻）。

    @author 康小汪【kxw】
    @date 2026-10-14
    """
    text = _STRINGS[_current_language].get(key) or _STRINGS[FALLBACK_LANGUAGE].get(key) or key
    if not params:
        return text
    try:
        return text.format(**params)
    except (KeyError, IndexError, ValueError):
        _logger.warning("文案占位符不匹配：%s", key)
        return text
