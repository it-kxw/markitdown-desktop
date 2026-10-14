# SPDX-License-Identifier: MIT
"""主窗口：拖拽/选择文件 → 进程池转换 → 预览 → 自动或手动写出 Markdown。

架构边界：本模块只负责**显示与交互**。转换在 worker.py 的子进程里执行，
输出路径推导与写出走 engine.py（`plan_outputs` / `write_markdown`）——
UI 不自己拼输出文件名，那正是 P0 静默覆盖 bug 的根源。

@author 康小汪【kxw】
@date 2026-02（创建）、2026-10-13（T7 接入进程池、取消、警告与关闭确认；
    P2 T13 增「导出日志…」；P2 T17 界面文案全部改走 i18n 表）
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from pathlib import Path

from PySide6.QtCore import (
    QByteArray,
    QLibraryInfo,
    QLocale,
    Qt,
    QTimer,
    QTranslator,
    QUrl,
)
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .engine import ConversionEngine, ConversionOutcome
from .i18n import FALLBACK_LANGUAGE, LANGUAGE_AUTO, normalize_language, set_language, tr
from .logging_setup import export_logs, log_dir, open_log_dir, tail_log
from .settings import AppSettings
from .settings_dialog import SettingsDialog
from .update_check import STATUS_AVAILABLE, STATUS_LATEST, UpdateResult, check_for_update
from .worker import ConversionPool, JobSpec, is_retryable

_logger = logging.getLogger(__name__)

# 队列条目的状态文案走 i18n 表（键见 i18n.py）：一眼能分清"没转 / 在转 / 转成功但没内容 / 失败"。
# 不再用模块级常量：语言能在运行时切换，常量会在切完语言后留下旧文案。

_ELAPSED_TICK_MS = 500  # 进度文字里"已用时"的刷新间隔
_ABOUT_LOG_LINES = 30  # 「关于」对话框里附带多少行日志
_UPDATE_POLL_MS = 250  # 更新检查结果：后台线程写队列，界面线程按这个间隔来取


def _first_line(text: str) -> str:
    """取多行文本的首行（错误信息首行才是给人看的摘要）。"""
    return text.splitlines()[0] if text else ""


def _resolve_language(code: str) -> str:
    """把设置里的语言码解析成具体语言：`auto` 跟随系统。

    读 QLocale 这件事只能放在界面层——i18n.py 保持零 Qt 依赖，
    这样它才敢被任何模块（含子进程）导入。

    Args:
        code: 设置里的语言码（`auto` / `zh_CN` / `en`）。

    Returns:
        str: 具体语言码。

    @author 康小汪【kxw】
    @date 2026-10-14
    """
    if code != LANGUAGE_AUTO:
        return normalize_language(code)
    return "zh_CN" if QLocale.system().name().startswith("zh") else FALLBACK_LANGUAGE


#: 已装载的 Qt 标准对话框翻译（按语言缓存，避免重复 install 越堆越多）
_qt_translators: list[QTranslator] = []
_qt_language = ""


def install_qt_translations(language: str) -> None:
    """给标准对话框（打开/保存/是 否）装载 Qt 自带翻译。

    只装 `qtbase`（QtWidgets/QtGui）与 `qt`（QtCore）：`.qm` 由打包脚本按
    语言白名单保留（见 spec 的 TRANSLATIONS_KEEP），漏一个就是静默退回英文。
    所以找不到 `.qm` 不抛异常——翻译缺失只是按钮显示英文，不该让应用起不来。

    Args:
        language: 具体语言码。

    @author 康小汪【kxw】
    @date 2026-10-14
    """
    global _qt_language
    if language == _qt_language:
        return
    app = QGuiApplication.instance()
    if app is None:
        return
    for installed in _qt_translators:
        app.removeTranslator(installed)
    _qt_translators.clear()
    directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    for prefix in ("qtbase", "qt"):
        translator = QTranslator(app)
        if translator.load(f"{prefix}_{language}", directory):
            app.installTranslator(translator)
            _qt_translators.append(translator)
    _qt_language = language


def apply_language(code: str) -> str:
    """按设置切换界面语言并装载 Qt 翻译。

    Args:
        code: 设置里的语言码。

    Returns:
        str: 实际生效的语言码。

    @author 康小汪【kxw】
    @date 2026-10-14
    """
    language = set_language(_resolve_language(code))
    install_qt_translations(language)
    return language


class MainWindow(QMainWindow):
    """主窗口：队列、进度、预览与设置持久化。

    Args:
        settings: 设置对象；测试注入 ini 版，生产走注册表默认位置。
        parent: Qt 父对象。

    @author 康小汪【kxw】
    @date 2026-02（创建）、2026-10-13（T7 接入进程池与设置）
    """

    def __init__(self, settings: AppSettings | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"MarkItDown Desktop {__version__}")
        self.resize(980, 620)
        self.setAcceptDrops(True)

        self._settings = settings or AppSettings()
        apply_language(self._settings.language)  # 建控件之前定好语言，避免中英混排
        # 父进程不再构造 MarkItDown 实例（转换在子进程里做），
        # 省下 magika/onnxruntime 模型加载的时间与内存
        self._outcomes: dict[int, ConversionOutcome] = {}
        self._planned: dict[int, Path] = {}  # 行号 → 计划输出路径（整批一次性规划）
        self._queued: set[str] = set()  # 已入队文件的归一化路径，用于去重
        self._pool: ConversionPool | None = None  # 懒创建：不转换就不起线程
        self._started_at = time.perf_counter()
        self._batch_total = 0
        self._written = 0
        self._current = ""
        self._last_note = ""

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(_ELAPSED_TICK_MS)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)

        self._update_running = False  # 同一时刻只允许一次更新检查在跑
        self._update_results: queue.Queue[tuple[UpdateResult, bool]] = queue.Queue()
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(_UPDATE_POLL_MS)
        self._update_timer.timeout.connect(self._poll_update_result)
        self._build_ui()
        self._build_menus()
        self._restore_state()
        if self._settings.check_updates_on_start:
            self._check_updates(manual=False)  # 后台跑，绝不阻塞启动

    # ------------------------------------------------------------------ 界面搭建

    def _build_ui(self) -> None:
        """搭建左侧队列、右侧预览与底部进度条。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._list.currentRowChanged.connect(self._show_preview)

        pick_btn = QPushButton(tr("ui.button.add"))
        pick_btn.clicked.connect(self._pick_files)
        self._convert_btn = QPushButton(tr("ui.button.start"))
        self._convert_btn.clicked.connect(self._start)
        self._cancel_btn = QPushButton(tr("ui.button.cancel"))
        self._cancel_btn.clicked.connect(self._cancel)
        self._cancel_btn.setEnabled(False)
        self._retry_btn = QPushButton(tr("ui.button.retry"))
        self._retry_btn.setToolTip(tr("ui.button.retry_tip"))
        self._retry_btn.setEnabled(False)  # 没有失败项就没得重试
        self._retry_btn.clicked.connect(self._retry_failed)

        save_btn = QPushButton(tr("ui.button.save_as"))
        save_btn.setToolTip(tr("ui.button.save_as_tip"))
        save_btn.clicked.connect(self._save_selected)
        self._copy_btn = QPushButton(tr("ui.button.copy_error"))
        self._copy_btn.clicked.connect(self._copy_error)
        clear_btn = QPushButton(tr("ui.button.clear"))
        clear_btn.clicked.connect(self._clear)

        first_row = QHBoxLayout()
        for btn in (pick_btn, self._convert_btn, self._cancel_btn, self._retry_btn):
            first_row.addWidget(btn)
        second_row = QHBoxLayout()
        for btn in (save_btn, self._copy_btn, clear_btn):
            second_row.addWidget(btn)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel(tr("ui.list.hint")))
        left_layout.addWidget(self._list, 1)
        left_layout.addLayout(first_row)
        left_layout.addLayout(second_row)

        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setPlaceholderText(tr("ui.preview.placeholder"))

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(left)
        self._splitter.addWidget(self._preview)
        self._splitter.setSizes([380, 600])

        self._progress = QProgressBar()
        self._progress.setValue(0)
        self._status = QLabel(tr("ui.status.ready"))

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self._splitter, 1)
        layout.addWidget(self._progress)
        layout.addWidget(self._status)
        self.setCentralWidget(central)

    def _build_menus(self) -> None:
        """菜单栏：文件（添加/清空/打开日志目录/退出）、帮助（关于）。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        file_menu = self.menuBar().addMenu(tr("ui.menu.file"))
        add_action = QAction(tr("ui.button.add"), self)
        add_action.triggered.connect(self._pick_files)
        clear_action = QAction(tr("ui.menu.clear"), self)
        clear_action.triggered.connect(self._clear)
        log_action = QAction(tr("ui.menu.open_log"), self)
        log_action.triggered.connect(open_log_dir)
        export_action = QAction(tr("ui.menu.export_log"), self)
        export_action.triggered.connect(self._export_logs)
        settings_action = QAction(tr("ui.menu.settings"), self)
        settings_action.triggered.connect(self._open_settings)
        quit_action = QAction(tr("ui.menu.quit"), self)
        quit_action.triggered.connect(self.close)
        for action in (add_action, clear_action, log_action, export_action):
            file_menu.addAction(action)
        file_menu.addSeparator()
        file_menu.addAction(settings_action)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu(tr("ui.menu.help"))
        update_action = QAction(tr("ui.menu.check_update"), self)
        update_action.triggered.connect(lambda: self._check_updates(manual=True))
        about_action = QAction(tr("ui.menu.about"), self)
        about_action.triggered.connect(self._about)
        help_menu.addAction(update_action)
        help_menu.addSeparator()
        help_menu.addAction(about_action)

    def _restore_state(self) -> None:
        """恢复上次的窗口几何与分割条位置；坏数据直接忽略，不影响启动。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        if geometry := self._settings.window_geometry:
            self.restoreGeometry(QByteArray(geometry))
        if state := self._settings.splitter_state:
            self._splitter.restoreState(QByteArray(state))

    # ------------------------------------------------------------------ 拖拽

    def dragEnterEvent(self, event) -> None:  # noqa: ANN001 - Qt 回调
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: ANN001 - Qt 回调
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        self.queue_paths(paths)
        event.acceptProposedAction()

    # ------------------------------------------------------------------ 队列

    def queue_paths(self, paths: list[str]) -> int:
        """把文件/文件夹加入队列，返回新增条目数。

        文件夹递归展开；同一文件（按归一化绝对路径比较，匹配 Windows 大小写
        不敏感的文件系统）只入队一次。

        Args:
            paths: 文件或文件夹路径列表。

        Returns:
            int: 实际新增的条目数。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        added = 0
        for raw in paths:
            for candidate in self._expand(raw):
                key = os.path.normcase(str(candidate.resolve()))
                if key in self._queued:
                    continue
                self._queued.add(key)
                item = QListWidgetItem(f"{tr('ui.item.pending')}  {candidate.name}")
                item.setData(Qt.ItemDataRole.UserRole, str(candidate))
                item.setToolTip(str(candidate))
                self._list.addItem(item)
                added += 1
        if added:
            self._status.setText(tr("ui.status.queue", count=self._list.count()))
        return added

    def queue_count(self) -> int:
        """队列条目数（供自测与测试断言）。"""
        return self._list.count()

    def status_text(self) -> str:
        """状态栏文字（供自测与测试断言）。"""
        return self._status.text()

    def _expand(self, raw: str) -> list[Path]:
        """把一次拖入的路径展开成待转换文件列表。

        目录递归展开；目录读不动（权限不足）时记日志并跳过，不让整个拖拽失败。

        Args:
            raw: 用户拖入/选择的原始路径。

        Returns:
            list[Path]: 文件列表（目录展开后的结果）。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        source = Path(raw)
        if not source.is_dir():
            return [source] if source.is_file() else []
        try:
            return [path for path in sorted(source.rglob("*")) if path.is_file()]
        except OSError as exc:
            _logger.warning("展开目录失败，已跳过：%s", source, exc_info=True)
            self._last_note = tr("ui.status.dir_failed", error=exc)
            self._status.setText(self._last_note)
            return []

    def _source_at(self, row: int) -> str | None:
        """取某行的源文件路径。

        路径存在 `UserRole` 里，**不解析条目文字**——条目文字是给人看的，
        一旦把显示文字当数据用，改文案就会连带改坏逻辑。

        Args:
            row: 队列行号。

        Returns:
            str | None: 源文件路径；行不存在或无数据时为 None。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        item = self._list.item(row)
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value else None

    def _set_item_text(self, row: int, status: str, detail: str = "") -> None:
        """更新队列条目的状态文字。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        item = self._list.item(row)
        source = self._source_at(row)
        if item is None or source is None:
            return
        item.setText(f"{status}  {Path(source).name}{detail}")

    def _pick_files(self) -> None:
        """选择文件加入队列，起始目录沿用上次的位置。"""
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("ui.dialog.pick_files"), self._settings.last_open_dir or ""
        )
        if paths:
            self._settings.last_open_dir = str(Path(paths[0]).parent)
        self.queue_paths(paths)

    def _clear(self) -> None:
        """清空队列与结果；正在转换时先取消，避免结果落到已清空的行上。"""
        if self._pool is not None and self._pool.is_busy():
            self._pool.cancel()
        self._list.clear()
        self._outcomes.clear()
        self._planned.clear()
        self._queued.clear()
        self._preview.clear()
        self._progress.setValue(0)
        self._elapsed_timer.stop()
        self._current = ""
        self._last_note = ""
        self._convert_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._retry_btn.setEnabled(False)
        self._status.setText(tr("ui.status.cleared"))

    # ------------------------------------------------------------------ 转换

    def _start(self) -> None:
        """规划输出路径，并把整批任务提交给进程池。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        if self._pool is not None and self._pool.is_busy():
            self._status.setText(tr("ui.warn.busy"))
            return

        # 行号即任务编号：结果回来时按行号回填，队列与结果永远对得上
        jobs = [
            (row, source)
            for row in range(self._list.count())
            if (source := self._source_at(row)) is not None
        ]
        if not jobs:
            QMessageBox.information(self, tr("ui.title.notice"), tr("ui.warn.no_files"))
            return

        self._outcomes.clear()
        self._planned.clear()
        self._written = 0
        self._last_note = ""
        self._submit(jobs)

    def _retry_failed(self) -> None:
        """只重跑失败项：成功项的结果与预览都留着，取消过的不复活。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        if self._pool is not None and self._pool.is_busy():
            self._status.setText(tr("ui.warn.busy"))
            return
        jobs = [
            (row, source)
            for row in range(self._list.count())
            if (source := self._source_at(row)) is not None
            and is_retryable(self._outcomes.get(row))
        ]
        if not jobs:
            self._status.setText(tr("ui.warn.nothing_to_retry"))
            return
        for row, _ in jobs:
            self._outcomes.pop(row, None)  # 旧失败结论作废，重新计入本轮进度
        self._last_note = ""
        self._submit(jobs)

    def _submit(self, jobs: list[tuple[int, str]]) -> None:
        """规划输出路径并整批提交（首次转换与重试共用）。

        进度分母取「已有结果的行数 + 本次提交数」，而不是只看本次提交数：
        重试时若把分母砍成 2，进度条会从满格退回 0/2，用户会以为前面的都白干了。
        分母分子同口径，进度条只会从已完成处继续往前。

        Args:
            jobs: (队列行号, 源文件路径) 列表，按行号升序。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        # 整批一次性规划：plan_outputs 同时避开批内重名与磁盘上已存在的文件
        planned = ConversionEngine.plan_outputs(
            [source for _, source in jobs],
            output_dir=self._settings.output_dir,
            policy=self._settings.naming_policy,
        )
        for (row, _), target in zip(jobs, planned):
            self._planned[row] = target
            self._set_item_text(row, tr("ui.item.pending"))

        self._batch_total = len(self._outcomes) + len(jobs)
        self._progress.setMaximum(self._batch_total)
        self._progress.setValue(len(self._outcomes))
        self._started_at = time.perf_counter()
        self._current = ""
        self._convert_btn.setEnabled(False)
        self._retry_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._elapsed_timer.start()
        self._tick_elapsed()

        if self._pool is None:
            self._pool = ConversionPool(workers=self._settings.concurrency, parent=self)
            self._pool.result_ready.connect(self._on_one_finished)
            self._pool.job_started.connect(self._on_job_started)
            self._pool.job_timeout.connect(self._on_job_timeout)
            self._pool.all_done.connect(self._on_all_finished)
            self._pool.pool_error.connect(self._on_pool_error)
        self._pool.submit([JobSpec(index=row, source=source) for row, source in jobs])

    def _cancel(self) -> None:
        """取消当前批次：硬杀正在跑的 worker，未开始的直接作废。"""
        if self._pool is None or not self._pool.is_busy():
            return
        self._pool.cancel()
        self._last_note = tr("ui.status.cancelled")
        self._status.setText(self._last_note)

    def _on_job_started(self, index: int, source: str) -> None:
        """标记该行正在转换，并记下当前文件名供进度文字显示。"""
        self._current = Path(source).name
        self._set_item_text(index, tr("ui.item.running"))
        self._tick_elapsed()

    @staticmethod
    def _translated(code: str, prefix: str, fallback: str) -> str:
        """按结果码查文案；码为空或表里没这个码，就回退到 engine 的原文。

        Args:
            code: 结果码，如 `timeout`。
            prefix: 文案键前缀，`err` 或 `msg`。
            fallback: 查不到时用的原文（`ConversionOutcome.error` / `warning`）。

        Returns:
            str: 当前语言下的文案。

        @author 康小汪【kxw】
        @date 2026-10-14
        """
        if not code:
            return fallback
        key = f"{prefix}.{code}"
        text = tr(key)
        return fallback if text == key else text  # 键原样返回 = 表里没有这个码

    @classmethod
    def _failure_text(cls, outcome: ConversionOutcome) -> str:
        """失败行的主文案：优先用错误码翻译，无码时回退原文。"""
        return cls._translated(outcome.error_code, "err", outcome.error)

    @classmethod
    def _warning_text(cls, outcome: ConversionOutcome) -> str:
        """无正文警告的文案：语义同 `_failure_text`。"""
        return cls._translated(outcome.warning_code, "msg", outcome.warning)

    def _on_one_finished(self, index: int, outcome: ConversionOutcome) -> None:
        """回填单个结果：条目状态、进度、警告提示、自动写出与预览。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        self._outcomes[index] = outcome
        if outcome.ok:
            self._set_item_text(
                index,
                tr("ui.item.warn") if outcome.warning else tr("ui.item.ok"),
                f"  ({outcome.elapsed_s:.2f}s)",
            )
        else:
            detail = _first_line(self._failure_text(outcome))[:60]
            self._set_item_text(index, tr("ui.item.fail"), f"  — {detail}")

        # 一出现可重试的失败就把按钮点亮；转换中的禁用由 _submit/_on_all_finished 管
        if is_retryable(outcome):
            self._retry_btn.setEnabled(True)

        # 警告与失败必须立刻可见：空文件看起来"成功"，失败更不能只躺在日志里
        note = self._warning_text(outcome) if outcome.ok else _first_line(self._failure_text(outcome))
        if note:
            self._last_note = note
            self._status.setText(note)

        self._progress.setValue(len(self._outcomes))
        self._write_result(index, outcome)
        self._show_preview(index)

    def _on_job_timeout(self, index: int, seconds: float) -> None:
        """超时提示：进程已被硬杀，最终结果仍会经 result_ready 回来。"""
        source = self._source_at(index) or ""
        self._last_note = tr("ui.status.timeout", name=Path(source).name, seconds=seconds)
        self._status.setText(self._last_note)

    def _on_pool_error(self, message: str) -> None:
        """池自身故障（如子进程起不来）：明确提示，不静默。"""
        _logger.error("进程池故障：%s", message)
        self._last_note = tr("ui.error.pool", message=message)
        self._status.setText(self._last_note)

    def _on_all_finished(self) -> None:
        """批次结束：停表、复位按钮、汇总成功/无内容/失败/写出数量。

        重试按钮按「还有没有可重试项」重算，而不是一律禁用：重试批次跑完
        可能又出新失败，按钮得能再点。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        self._elapsed_timer.stop()
        self._convert_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._retry_btn.setEnabled(any(is_retryable(o) for o in self._outcomes.values()))
        self._current = ""

        ok = sum(1 for outcome in self._outcomes.values() if outcome.ok)
        warned = sum(1 for o in self._outcomes.values() if o.ok and o.warning)
        parts = [tr("ui.summary.done", ok=ok, total=self._batch_total)]
        if warned:
            parts.append(tr("ui.summary.warned", count=warned))
        if len(self._outcomes) - ok:
            parts.append(tr("ui.summary.failed", count=len(self._outcomes) - ok))
        if self._settings.auto_save:
            parts.append(tr("ui.summary.written", count=self._written))
        self._status.setText(" · ".join(parts))

    def _tick_elapsed(self) -> None:
        """刷新「已完成 n/m · 当前文件 · 剩余 k 个 · 已用时」；备注始终附在末尾。

        只报"还剩几个文件"，不算"还剩多久"：单文件耗时从几十毫秒到几十秒都有，
        估出来的剩余时间是假的，误导比没有更糟。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        done = len(self._outcomes)
        parts = [tr("ui.progress.done", done=done, total=self._batch_total)]
        if self._current:
            parts.append(tr("ui.progress.current", name=self._current))
        parts.append(tr("ui.progress.left", count=max(self._batch_total - done, 0)))
        parts.append(tr("ui.progress.elapsed", seconds=time.perf_counter() - self._started_at))
        if self._last_note:
            parts.append(self._last_note)
        self._status.setText(" · ".join(parts))

    def _write_result(self, index: int, outcome: ConversionOutcome) -> None:
        """按设置自动写出 .md。

        写出失败（权限不足、磁盘满、文件被占用）必须落到条目与状态栏上，
        不能让用户以为转换结果已保存。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        target = self._planned.get(index)
        if not outcome.ok or target is None or not self._settings.auto_save:
            return
        try:
            ConversionEngine.write_markdown(outcome, target)
        except OSError as exc:
            _logger.warning("写出失败：%s", target, exc_info=True)
            self._set_item_text(index, tr("ui.item.fail"), tr("ui.item.write_failed", error=exc))
            self._last_note = tr("ui.status.write_failed", error=exc)
            self._status.setText(self._last_note)
            return
        self._written += 1
        _logger.info("已写出 %s", target)

    # ------------------------------------------------------------------ 预览与保存

    def _show_preview(self, row: int) -> None:
        """显示某行的转换结果；失败时给出各转换器的尝试明细。"""
        outcome = self._outcomes.get(row)
        if outcome is None:
            return
        if outcome.ok:
            self._preview.setPlainText(outcome.markdown or self._warning_text(outcome))
            return
        detail = "\n".join(outcome.attempts) if outcome.attempts else outcome.error
        self._preview.setPlainText(
            tr("ui.preview.failed", error=self._failure_text(outcome), detail=detail)
        )

    def _selected_rows(self) -> list[int]:
        """取列表中选中行（升序）；一个都没选中时退回当前行。

        @author 康小汪【kxw】
        @date 2026-10-14
        """
        rows = sorted(self._list.row(item) for item in self._list.selectedItems())
        if rows:
            return rows
        current = self._list.currentRow()
        return [current] if current >= 0 else []

    def _save_selected(self) -> None:
        """把选中的条目另存为 .md：1 个走「保存文件」，多个走「选文件夹 + 自动命名」。

        多选时文件名按**源文件名**派生（`plan_outputs` 负责换后缀与避重名），
        不逐个弹保存框——6 个文件弹 6 次对话框没人受得了。

        @author 康小汪【kxw】
        @date 2026-10-14
        """
        selected = self._selected_rows()
        rows = [row for row in selected if (o := self._outcomes.get(row)) is not None and o.ok]
        if not rows:
            QMessageBox.information(self, tr("ui.title.notice"), tr("ui.warn.select_success"))
            return
        skipped = len(selected) - len(rows)
        # 分支看「选中个数」而不是「可保存个数」：选了 2 个但只有 1 个成功时，
        # 用户的心智是"批量另存"，不该因为他运气不好只剩一个就弹单个文件框。
        if len(selected) == 1:
            self._save_one(rows[0])
            return
        target_dir = QFileDialog.getExistingDirectory(
            self, tr("ui.dialog.save_folder"), str(self._settings.last_open_dir or Path.home())
        )
        if not target_dir:
            return
        self._save_many(rows, Path(target_dir), skipped)

    def _save_one(self, row: int) -> None:
        """单个条目另存：建议路径由 `plan_outputs` 推导（自动避让重名）。

        @author 康小汪【kxw】
        @date 2026-10-14
        """
        outcome = self._outcomes[row]
        suggested = ConversionEngine.plan_outputs(
            [outcome.source],
            output_dir=self._settings.output_dir,
            policy=self._settings.naming_policy,
        )[0]
        target, _ = QFileDialog.getSaveFileName(
            self, tr("ui.dialog.save_md"), str(suggested), "Markdown (*.md)"
        )
        if not target:
            return
        try:
            ConversionEngine.write_markdown(outcome, target)
        except OSError as exc:
            QMessageBox.warning(self, tr("ui.title.save_failed"), str(exc))
            return
        self._settings.last_open_dir = str(Path(target).parent)
        self._status.setText(tr("ui.status.saved", path=target))

    def _save_many(self, rows: list[int], target_dir: Path, skipped: int = 0) -> None:
        """批量另存到同一目录：逐个写出，单个失败不中断整批，最后如实汇总。

        @author 康小汪【kxw】
        @date 2026-10-14
        """
        outcomes = [self._outcomes[row] for row in rows]
        targets = ConversionEngine.plan_outputs(
            [outcome.source for outcome in outcomes],
            output_dir=target_dir,
            policy=self._settings.naming_policy,
        )
        written = 0
        first_error = ""
        for outcome, target in zip(outcomes, targets):
            try:
                ConversionEngine.write_markdown(outcome, target)
            except OSError as exc:
                first_error = first_error or f"{Path(target).name}: {exc}"
                _logger.warning("另存为失败：%s", target, exc_info=True)
            else:
                written += 1
        self._settings.last_open_dir = str(target_dir)
        note = tr("ui.status.saved_many", count=written, target=target_dir)
        note += self._skip_note(skipped)
        if written < len(targets):
            note += " · " + tr(
                "ui.status.save_partial", count=len(targets) - written, error=first_error
            )
        self._status.setText(note)

    @staticmethod
    def _skip_note(skipped: int) -> str:
        """跳过未成功条目的说明；0 个时返回空串（不写"跳过 0 个"这种废话）。"""
        return tr("ui.skip.note", count=skipped) if skipped else ""

    def _copy_error(self) -> None:
        """把当前条目的失败明细复制到剪贴板（便于贴给别人排查）。"""
        row = self._list.currentRow()
        outcome = self._outcomes.get(row)
        if outcome is None:
            self._status.setText(tr("ui.warn.select_converted"))
            return
        detail = "\n".join(outcome.attempts) if outcome.attempts else outcome.error
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(detail or outcome.markdown)
        self._status.setText(tr("ui.status.copied"))

    def _export_logs(self) -> None:
        """把日志打包成 zip 交给用户：报障时这是唯一能拿到的现场。

        取消保存对话框视为放弃（不报错）；没有日志时给出人话提示，
        不能静默成功——否则用户拿着一份空 zip 白跑一轮沟通。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        default_dir = Path(self._settings.last_open_dir or Path.home())
        target, _ = QFileDialog.getSaveFileName(
            self,
            tr("ui.dialog.export_log"),
            str(default_dir / "markitdown-logs.zip"),
            tr("ui.filter.zip"),
        )
        if not target:
            return
        try:
            path = export_logs(Path(target))
        except FileNotFoundError:
            self._status.setText(tr("ui.status.no_logs", path=log_dir()))
        except OSError as exc:
            self._status.setText(tr("ui.status.export_failed", error=exc))
        else:
            self._status.setText(tr("ui.status.exported", path=path))

    def _open_settings(self) -> None:
        """打开设置对话框；点确定才落盘，并回收空闲进程池。

        并发度只在建池时被读取（见 `_submit`），不回收池的话用户改完并发度看不到
        任何变化，等于设置没生效；正在转换时不能回收——那会把在跑的任务一起干掉。

        @author 康小汪【kxw】
        @date 2026-10-14
        """
        if SettingsDialog(self._settings, self).exec() != QDialog.DialogCode.Accepted:
            return
        self._settings.sync()
        if self._pool is not None and not self._pool.is_busy():
            self._pool.shutdown()
            self._pool = None
        self._status.setText(tr("ui.status.settings_saved"))

    def _about(self) -> None:
        """「关于」：版本、日志目录，日志末尾放进"详细信息"里按需展开。"""
        box = QMessageBox(self)
        box.setWindowTitle(tr("ui.about.title"))
        box.setText(f"MarkItDown Desktop {__version__}")
        box.setInformativeText(tr("ui.about.log_dir", path=log_dir()))
        box.setDetailedText(tail_log(_ABOUT_LOG_LINES) or tr("ui.about.no_logs"))
        box.exec()

    # ------------------------------------------------------------------ 更新检查

    def _check_updates(self, manual: bool) -> None:
        """发起一次后台更新检查；已有检查在跑就直接复用，不叠加请求。

        网络实测要 5~15 秒，绝不能放在界面线程里（会把窗口冻住）。

        **线程模型是实测选出来的**（见 `_t20_probe4.py`）：

        * QThread 无论有没有父对象，只要进程退出时还在跑，就会让进程 abort（exit 9）；
        * daemon 线程 + **跳线程 emit Qt 信号**同样不行：跟同进程里的
          multiprocessing spawn 转换一起跑时，Windows 直接 access violation
          （tests/test_ui_queue.py 第 12 条必崩，见 tests/test_update_check.py 的守卫）。

        所以最终方案是：线程只把结果放进 `queue.Queue`（纯 Python，与 Qt 无关），
        界面线程用 QTimer 定时取。线程是 daemon，进程退出时不会卡住。

        Args:
            manual: 是否用户手动触发（决定要不要弹对话框）。

        @author 康小汪【kxw】
        @date 2026-10-14
        """
        if self._update_running:
            return
        self._update_running = True
        self._update_timer.start()
        threading.Thread(
            target=self._run_update_check, args=(manual,), daemon=True, name="update-check"
        ).start()

    def _run_update_check(self, manual: bool) -> None:
        """线程体：查一次并把结果放进队列。**这里一行 Qt 代码都不许有**。"""
        self._update_results.put((check_for_update(), manual))

    def _poll_update_result(self) -> None:
        """界面线程定时取一次结果；队列为空就继续等。"""
        try:
            result, manual = self._update_results.get_nowait()
        except queue.Empty:
            return
        self._update_timer.stop()
        self._update_running = False
        self._status.setText(self._update_status_text(result))
        if manual:
            self._show_update_dialog(result)

    @staticmethod
    def _update_status_text(result: UpdateResult) -> str:
        """状态栏文案：三态各一句，自动检查时不打扰用户。"""
        if result.status == STATUS_AVAILABLE:
            return tr("ui.status.update_available", version=result.latest or "")
        if result.status == STATUS_LATEST:
            return tr("ui.status.update_latest", version=result.current)
        return tr("ui.status.update_unknown")

    def _show_update_dialog(self, result: UpdateResult) -> None:
        """手动检查的结果对话框；有新版时额外给一个「打开发布页」按钮。"""
        box = QMessageBox(self)
        box.setWindowTitle(tr("ui.update.title"))
        if result.status == STATUS_AVAILABLE:
            box.setText(
                tr("ui.update.available", current=result.current, latest=result.latest or "")
            )
            open_btn = box.addButton(tr("ui.update.open"), QMessageBox.ButtonRole.ActionRole)
            open_btn.clicked.connect(lambda: self._open_release_page(result.url, box))
        elif result.status == STATUS_LATEST:
            box.setText(tr("ui.update.latest", current=result.current))
        else:
            box.setText(tr("ui.update.unknown"))
        box.addButton(tr("ui.update.close"), QMessageBox.ButtonRole.AcceptRole)
        box.exec()

    @staticmethod
    def _open_release_page(url: str | None, box: QMessageBox) -> None:
        """在浏览器里打开发布页并关掉对话框；地址为空时只关对话框。"""
        if url:
            QDesktopServices.openUrl(QUrl(url))
        box.accept()

    # ------------------------------------------------------------------ 关闭

    def closeEvent(self, event) -> None:  # noqa: ANN001 - Qt 回调
        """关闭前确认仍在转换的任务，回收进程池，并保存窗口状态。

        无论是否忙碌都必须回收进程池：池是长驻线程 + 长驻子进程，
        窗口析构时线程若仍在运行，Qt 会直接 qFatal（"QThread: Destroyed
        while thread is still running"），子进程也会变成孤儿。

        @author 康小汪【kxw】
        @date 2026-10-13
        """
        if self._pool is not None:
            if self._pool.is_busy():
                answer = QMessageBox.question(
                    self, tr("ui.confirm.busy_title"), tr("ui.confirm.busy_text")
                )
                if answer != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
            self._pool.shutdown()
        # 关窗就停掉更新检查的轮询：窗口可能还活着（GC 未到），不该继续每 250 ms 醒一次。
        # 后台那个 daemon 线程不用管：它只往队列里写一个结果，进程退出时自生自灭。
        self._update_timer.stop()
        self._settings.window_geometry = bytes(self.saveGeometry())
        self._settings.splitter_state = bytes(self._splitter.saveState())
        self._settings.sync()
        event.accept()
