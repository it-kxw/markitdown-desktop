# SPDX-License-Identifier: MIT
"""设置对话框：让用户改输出目录、命名策略、并发度、自动写出与界面语言。

为什么单独一个模块：主窗口已经 700+ 行，设置控件再塞进去只会更难读。
本模块只依赖 `AppSettings`、`i18n` 与 Qt，不碰进程池、不碰转换逻辑。

设计要点（都有测试兜着，不是推测）：
- **只在「确定」时写回**：取消、按 Esc、「恢复默认」都不许改动 `AppSettings`
  的一个字段——「恢复默认」只把控件拉回默认，用户还得点确定才算数。
- **上下限与默认值直接复用 settings 的常量**（`MIN_CONCURRENCY` /
  `MAX_CONCURRENCY` / `DEFAULT_CONCURRENCY`），绝不复制一份数字：
  复制出来的第二份迟早会和第一份漂移。
- **输出目录是外部输入**：确定时校验目录真实存在，不存在就提示并**留在对话框**里
  等用户改。写进去只会让之后每一次转换都失败，比不写更糟。
- 并发度只在**建池**时被读取，所以主窗口在确定后要回收空闲池（见 `ui.py`）。
- **界面语言是 T17 新增项**：`auto` 交给主窗口解析成具体语言（要读 QLocale，
  对话框不参与），本对话框只负责把选项写回设置。语言选项的显示名也走 i18n 表——
  这样英文界面上不会出现任何中文，漏翻会被 `test_ui_texts_are_english_in_english`
  直接抓住。

@author 康小汪【kxw】
@date 2026-10-14
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .engine import NamingPolicy
from .i18n import LANGUAGE_AUTO, tr
from .settings import (
    DEFAULT_CONCURRENCY,
    LANGUAGE_CHOICES,
    MAX_CONCURRENCY,
    MIN_CONCURRENCY,
    AppSettings,
)


class SettingsDialog(QDialog):
    """设置对话框：输出目录 / 命名策略 / 并发度 / 自动写出 / 界面语言。

    Args:
        settings: 被编辑的设置对象；**只在点「确定」时写回**。
        parent: Qt 父对象。

    @author 康小汪【kxw】
    @date 2026-10-14
    """

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle(tr("dlg.title"))
        self._build_form()
        self._build_buttons()
        self._load(settings)

    def _build_form(self) -> None:
        """搭建设置控件，装进表单布局。

        @author 康小汪【kxw】
        @date 2026-10-14
        """
        self._dir_edit = QLineEdit()
        self._dir_edit.setPlaceholderText(tr("dlg.output_dir_placeholder"))
        self._browse_btn = QPushButton(tr("dlg.browse"))
        self._browse_btn.clicked.connect(self._browse_output_dir)
        self._same_dir = QCheckBox(tr("dlg.same_dir"))
        self._same_dir.toggled.connect(self._on_same_dir_toggled)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self._dir_edit)
        dir_row.addWidget(self._browse_btn)

        # 命名策略用 itemData 直接存 `NamingPolicy.value`，避免"界面文案 → 枚举"的
        # 映射表再维护一份。注意不能存枚举本身：`NamingPolicy` 是 str 子类，
        # PySide6 存进 QVariant 会退化成普通 str（实测 currentData() 返回 'suffix'），
        # 取回时必须重新包成枚举。
        self._policy_box = QComboBox()
        self._policy_box.addItem(tr("dlg.policy_suffix"), NamingPolicy.SUFFIX.value)
        self._policy_box.addItem(tr("dlg.policy_keep_ext"), NamingPolicy.KEEP_EXT.value)

        self._spin = QSpinBox()
        self._spin.setRange(MIN_CONCURRENCY, MAX_CONCURRENCY)
        self._spin.setToolTip(tr("dlg.concurrency_tip"))

        self._auto_save = QCheckBox(tr("dlg.auto_save"))

        # itemData 存的是普通字符串（语言码），不存在 str 子类退化问题
        self._lang_box = QComboBox()
        for code in LANGUAGE_CHOICES:
            self._lang_box.addItem(tr(f"dlg.language_{code}"), code)

        form = QFormLayout()
        form.addRow(tr("dlg.output_dir_label"), dir_row)
        form.addRow("", self._same_dir)
        form.addRow(tr("dlg.policy_label"), self._policy_box)
        form.addRow(tr("dlg.concurrency_label"), self._spin)
        form.addRow("", self._auto_save)
        form.addRow(tr("dlg.language_label"), self._lang_box)
        layout = QVBoxLayout(self)
        layout.addLayout(form)

    def _build_buttons(self) -> None:
        """底部按钮：恢复默认 / 确定 / 取消。

        @author 康小汪【kxw】
        @date 2026-10-14
        """
        self._restore_btn = QPushButton(tr("dlg.restore_defaults"))
        self._restore_btn.clicked.connect(self._restore_defaults)
        self._ok_btn = QPushButton(tr("dlg.ok"))
        self._ok_btn.clicked.connect(self.accept)
        self._cancel_btn = QPushButton(tr("dlg.cancel"))
        self._cancel_btn.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self._restore_btn)
        row.addStretch(1)
        row.addWidget(self._ok_btn)
        row.addWidget(self._cancel_btn)
        self.layout().addLayout(row)

    def _load(self, settings: AppSettings) -> None:
        """把当前设置填进控件；未设输出目录时勾选「与源文件同目录」。

        Args:
            settings: 读取来源。

        @author 康小汪【kxw】
        @date 2026-10-14
        """
        self._dir_edit.setText(settings.output_dir or "")
        self._same_dir.setChecked(not settings.output_dir)
        index = self._policy_box.findData(settings.naming_policy.value)
        self._policy_box.setCurrentIndex(max(index, 0))  # findData 失配时退回第一项
        self._spin.setValue(settings.concurrency)
        self._auto_save.setChecked(settings.auto_save)
        self._lang_box.setCurrentIndex(max(self._lang_box.findData(settings.language), 0))

    def _on_same_dir_toggled(self, checked: bool) -> None:
        """勾上「与源文件同目录」就禁用路径框与浏览按钮（不清空已填内容）。

        Args:
            checked: 勾选状态。

        @author 康小汪【kxw】
        @date 2026-10-14
        """
        self._dir_edit.setEnabled(not checked)
        self._browse_btn.setEnabled(not checked)

    def _browse_output_dir(self) -> None:
        """选一个已存在的目录填进路径框；用户取消时保持原值。

        @author 康小汪【kxw】
        @date 2026-10-14
        """
        start = self._dir_edit.text().strip() or str(Path.home())
        picked = QFileDialog.getExistingDirectory(self, tr("dlg.choose_output_dir"), start)
        if not picked:
            return  # 取消：保持原值，绝不写空串
        self._dir_edit.setText(picked)
        self._same_dir.setChecked(False)  # 选了具体目录就不再是"同目录"

    def _restore_defaults(self) -> None:
        """把控件拉回出厂默认；此时**不落盘**，要用户再点确定。

        @author 康小汪【kxw】
        @date 2026-10-14
        """
        self._same_dir.setChecked(True)
        self._dir_edit.clear()
        self._policy_box.setCurrentIndex(self._policy_box.findData(NamingPolicy.SUFFIX.value))
        self._spin.setValue(DEFAULT_CONCURRENCY)
        self._auto_save.setChecked(True)
        self._lang_box.setCurrentIndex(self._lang_box.findData(LANGUAGE_AUTO))

    def _policy_value(self) -> NamingPolicy:
        """把下拉框当前项转回枚举（存进去的是 `value`，是普通字符串）。

        Returns:
            NamingPolicy: 当前选中的命名策略。

        @author 康小汪【kxw】
        @date 2026-10-14
        """
        return NamingPolicy(self._policy_box.currentData())

    def _language_value(self) -> str:
        """把语言下拉框当前项转成语言码。

        Returns:
            str: `auto` / `zh_CN` / `en` 之一；控件为空时兜底 `auto`。

        @author 康小汪【kxw】
        @date 2026-10-14
        """
        return str(self._lang_box.currentData() or LANGUAGE_AUTO)

    def _output_dir_value(self) -> str | None:
        """把控件状态收敛成 output_dir 的值：勾了同目录或留空都算「未设」。

        Returns:
            str | None: 目录路径；None 表示与源文件同目录。

        @author 康小汪【kxw】
        @date 2026-10-14
        """
        if self._same_dir.isChecked():
            return None
        return self._dir_edit.text().strip() or None

    def accept(self) -> None:
        """校验后一次写回五个字段；输出目录无效则提示并留在对话框里。

        字段一起写：用户点的是「确定」，不该出现"改了一半"的中间状态。

        @author 康小汪【kxw】
        @date 2026-10-14
        """
        target = self._output_dir_value()
        if target is not None and not Path(target).is_dir():
            QMessageBox.warning(
                self,
                tr("dlg.invalid_output_dir"),
                tr("dlg.invalid_output_dir_text", path=target),
            )
            return
        self._settings.output_dir = target
        self._settings.naming_policy = self._policy_value()
        self._settings.concurrency = self._spin.value()
        self._settings.auto_save = self._auto_save.isChecked()
        self._settings.language = self._language_value()
        super().accept()
