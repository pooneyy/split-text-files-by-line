"""PyQt6 图形界面与子线程"""

from __future__ import annotations

import os
import re

from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import (
    QObject,
    QThread,
    QTimer,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QAction,
    QActionGroup,
    QKeySequence,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import config
from core import (
    SplitConfig,
    decide_padding,
    estimate_outputs,
    iter_input_lines,
    run_split,
)
from version import (
    __author__,
    __app_name__,
    __app_name_en__,
    __version__,
)

ENCODINGS = ["utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"]
NEWLINE_POLICY_CODES = ["preserve", "lf", "crlf"]
CONFLICT_POLICY_CODES = ["skip", "overwrite", "rename"]

# --------------------------------------------------------------------------- #
# 子线程
# --------------------------------------------------------------------------- #

def _count_lines_quick(path: Path, encoding: str) -> int:
    """与 core.count_lines 等价的快速行数。"""
    with open(path, "rb") as f:
        data = f.read()
    text = data.decode(encoding, errors="replace")
    if not text:
        return 0
    lines = text.splitlines()
    if text.endswith("\n") or text.endswith("\r"):
        return len(lines)
    return len(lines)

class SplitWorker(QObject):
    """在子线程中执行切分。"""

    progress = pyqtSignal(int, int)
    log = pyqtSignal(str)
    finished = pyqtSignal(int, int, int)  # written, skipped, errors
    failed = pyqtSignal(str)

    def __init__(
        self,
        paths: List[str],
        plan: List[int],
        cfg: SplitConfig,
        output_dir: str,
    ):
        super().__init__()
        self.paths = paths
        self.plan = plan
        self.cfg = cfg
        self.output_dir = output_dir
        self._cancel = False

    def cancel(self):
        self._cancel = True

    @pyqtSlot()
    def run(self):
        try:
            result = run_split(
                [Path(p) for p in self.paths],
                self.plan,
                self.cfg,
                Path(self.output_dir),
                log=lambda m: self.log.emit(m),
                progress=lambda d, t: self.progress.emit(d, t),
                should_cancel=lambda: self._cancel,
            )
            self.finished.emit(
                len(result.written_files),
                len(result.skipped),
                len(result.errors),
            )
        except Exception as e:
            self.failed.emit(str(e))

# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #

class MainWindow(QMainWindow):
    def __init__(self, base_dir: str, version: str = __version__):
        super().__init__()
        self.base_dir = base_dir
        self.app_version: str = version
        self.cfg_data = config.load_config(base_dir)
        self._split_thread: Optional[QThread] = None
        self._split_worker: Optional[SplitWorker] = None
        self._input_files: List[str] = self.cfg_data.get("input_files") or []

        self._build_ui()
        self._apply_lang(self.cfg_data.get("language", "zh"))
        self._restore_from_config()
        self._refresh_total_lines()

    # ----------------------- 绘制 UI ----------------------- #

    def _build_ui(self):
        # _build_ui 在 _apply_lang 之前调用,所有初值用 _t() 取默认语言文案,
        # 随后 _apply_lang 会按配置语言再统一刷一遍。
        self.resize(*self.cfg_data.get("window_size", [800, 600]))

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ===== 输入文件列表 =====
        self.input_box = QGroupBox()
        input_layout = QVBoxLayout(self.input_box)
        top = QHBoxLayout()
        self.btn_add_files = QPushButton(self._t("add_files"))
        self.btn_remove = QPushButton(self._t("remove"))
        self.btn_clear = QPushButton(self._t("clear"))
        self.btn_move_up = QToolButton()
        self.btn_move_down = QToolButton()
        self.btn_move_up.setText(self._t("move_up"))
        self.btn_move_down.setText(self._t("move_down"))
        top.addWidget(self.btn_add_files)
        top.addWidget(self.btn_remove)
        top.addWidget(self.btn_clear)
        top.addStretch(1)
        top.addWidget(self.btn_move_up)
        top.addWidget(self.btn_move_down)
        input_layout.addLayout(top)

        self.list_files = QListWidget()
        self.list_files.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_files.setAcceptDrops(True)
        self.list_files.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_files.customContextMenuRequested.connect(self._show_files_menu)
        input_layout.addWidget(self.list_files)

        self.lbl_total = QLabel(f"{self._t('total_lines')} -")
        input_layout.addWidget(self.lbl_total)

        # ===== 切分方案 =====
        self.plan_box = QGroupBox()
        plan_layout = QVBoxLayout(self.plan_box)
        plan_top = QHBoxLayout()
        self.lbl_plan_label = QLabel(self._t("plan_label"))
        plan_top.addWidget(self.lbl_plan_label)
        plan_top.addStretch(1)
        self.btn_plan_add = QPushButton(self._t("plan_add"))
        self.btn_plan_remove = QPushButton(self._t("plan_remove"))
        self.btn_plan_clear = QPushButton(self._t("plan_clear"))
        plan_top.addWidget(self.btn_plan_add)
        plan_top.addWidget(self.btn_plan_remove)
        plan_top.addWidget(self.btn_plan_clear)
        plan_layout.addLayout(plan_top)

        self.list_plan = QListWidget()
        self.list_plan.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        plan_layout.addWidget(self.list_plan)

        self.lbl_plan_hint = QLabel(self._t("plan_hint"))
        self.lbl_plan_hint.setStyleSheet("color: gray;")
        self.lbl_plan_hint.setWordWrap(True)
        plan_layout.addWidget(self.lbl_plan_hint)

        self.lbl_preview = QLabel(f"{self._t('estimate')} -")
        plan_layout.addWidget(self.lbl_preview)

        self.lbl_names_preview = QLabel(f"{self._t('preview')} -")
        self.lbl_names_preview.setWordWrap(True)
        plan_layout.addWidget(self.lbl_names_preview)

        # ===== 文件命名 =====
        self.name_box = QGroupBox()
        name_form = QFormLayout(self.name_box)
        self.edit_prefix = QLineEdit()
        self.edit_suffix = QLineEdit()
        self.spin_index_start = QSpinBox()
        self.spin_index_start.setRange(0, 999999)
        self.lbl_prefix = QLabel(self._t("prefix"))
        self.lbl_suffix = QLabel(self._t("suffix"))
        self.lbl_index_start = QLabel(self._t("index_start"))
        name_form.addRow(self.lbl_prefix, self.edit_prefix)
        name_form.addRow(self.lbl_suffix, self.edit_suffix)
        name_form.addRow(self.lbl_index_start, self.spin_index_start)

        # ===== 高级选项 =====
        self.adv_box = QGroupBox()
        adv_form = QFormLayout(self.adv_box)
        self.combo_encoding = QComboBox()
        self.combo_encoding.addItems(ENCODINGS)
        self.combo_newline = QComboBox()
        self.combo_conflict = QComboBox()
        self.lbl_encoding = QLabel(self._t("encoding"))
        self.lbl_newline = QLabel(self._t("newline"))
        self.lbl_conflict = QLabel(self._t("conflict"))
        adv_form.addRow(self.lbl_encoding, self.combo_encoding)
        adv_form.addRow(self.lbl_newline, self.combo_newline)
        adv_form.addRow(self.lbl_conflict, self.combo_conflict)

        # ===== 输出目录 =====
        self.out_box = QGroupBox()
        out_layout = QHBoxLayout(self.out_box)
        self.edit_output = QLineEdit()
        self.btn_choose_out = QPushButton(self._t("choose"))
        out_layout.addWidget(self.edit_output)
        out_layout.addWidget(self.btn_choose_out)

        # ===== 顶部布局 =====
        top_row = QHBoxLayout()
        top_row.addWidget(self.input_box, 2)
        top_row.addWidget(self.name_box, 1)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.plan_box, 1)
        bottom_row.addWidget(self.adv_box, 1)

        # ===== 日志 + 进度 =====
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)

        # ===== 底部按钮 =====
        btn_row = QHBoxLayout()
        self.btn_start = QPushButton(self._t("start"))
        self.btn_cancel = QPushButton(self._t("cancel"))
        self.btn_cancel.setEnabled(False)
        self.btn_open_out = QPushButton(self._t("open_out"))
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_open_out)

        root.addLayout(top_row)
        root.addLayout(bottom_row)
        root.addWidget(self.out_box)
        root.addWidget(self.progress)
        self.lbl_log = QLabel(self._t("log"))
        root.addWidget(self.lbl_log)
        root.addWidget(self.log_box, 1)
        root.addLayout(btn_row)

        # ----- 菜单栏 / 快捷键 -----
        menu = self.menuBar()
        m_lang = menu.addMenu(self._t("menu_lang"))
        lang_group = QActionGroup(self)
        lang_group.setExclusive(True)
        for code in ("zh", "en"):
            act = QAction(self._t(f"menu_lang_{code}"), self)
            act.setCheckable(True)
            act.setData(code)
            lang_group.addAction(act)
            m_lang.addAction(act)
            act.triggered.connect(lambda _checked=False, c=code: self._apply_lang(c))
        self._lang_actions = {a.data(): a for a in lang_group.actions()}
        self._lang_actions[self.cfg_data.get("language", "zh")].setChecked(True)

        m_help = menu.addMenu(self._t("menu_help"))
        self.act_about = QAction(self._t("about"), self)
        self.act_about.triggered.connect(self._on_about)
        m_help.addAction(self.act_about)

        # 快捷键
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self._on_add_files)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._on_start)

        # ----- 信号连接 -----
        self.btn_add_files.clicked.connect(self._on_add_files)
        self.btn_remove.clicked.connect(self._on_remove_selected)
        self.btn_clear.clicked.connect(self._on_clear_files)
        self.btn_move_up.clicked.connect(lambda: self._move_selected(-1))
        self.btn_move_down.clicked.connect(lambda: self._move_selected(1))
        self.btn_plan_add.clicked.connect(self._on_plan_add)
        self.btn_plan_remove.clicked.connect(self._on_plan_remove_selected)
        self.btn_plan_clear.clicked.connect(lambda: (self.list_plan.clear(), self._refresh_preview()))
        self.btn_choose_out.clicked.connect(self._on_choose_output)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_open_out.clicked.connect(self._on_open_output)
        self.list_files.model().rowsInserted.connect(lambda *_: self._refresh_total_lines())
        self.list_files.model().rowsRemoved.connect(lambda *_: self._refresh_total_lines())
        self.list_plan.model().rowsInserted.connect(lambda *_: self._refresh_preview())
        self.list_plan.model().rowsRemoved.connect(lambda *_: self._refresh_preview())
        self.combo_encoding.currentTextChanged.connect(lambda _t: self._refresh_total_lines())

        # 状态栏: 左侧临时消息，右侧永久显示版本号
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._lbl_version = QLabel(f"v{self.app_version}")
        self._lbl_version.setStyleSheet("color: gray; padding: 0 8px;")
        sb.addPermanentWidget(self._lbl_version)

    # ----------------------- 语言 ----------------------- #

    # 用户可见的所有字符串集中在这里。新增/修改文案只改这两个表。
    TEXTS_ZH = {
        # 窗口与分组标题
        "title": "按行分割文本文件",
        "input_group": "输入文件",
        "plan_group": "切分方案",
        "name_group": "文件命名",
        "adv_group": "高级",
        "output_group": "输出目录",

        # 按钮
        "add_files": "添加文件",
        "remove": "删除选中",
        "clear": "清空",
        "move_up": "▲",
        "move_down": "▼",
        "plan_add": "添加",
        "plan_remove": "删除选中",
        "plan_clear": "清空",
        "choose": "选择…",
        "start": "开始",
        "cancel": "取消",
        "open_out": "打开输出目录",

        # 表单行
        "prefix": "前缀:",
        "suffix": "后缀:",
        "index_start": "起始序号:",
        "encoding": "编码:",
        "newline": "换行:",
        "conflict": "冲突策略:",
        "plan_label": "方案 (每段行数):",
        "log": "日志:",
        "total_lines": "总行数:",
        "estimate": "预计生成:",
        "preview": "预览:",
        "plan_hint": "(列表最后一个值作为之后所有文件的每段行数)",

        # 菜单
        "menu_lang": "语言",
        "menu_help": "帮助",
        "menu_lang_zh": "中文",
        "menu_lang_en": "English",
        "about": "关于",
        "about_title": "关于",
        "about_app_name_label": "应用:",
        "about_version_label": "版本:",
        "about_author_label": "作者:",

        # 下拉框选项（按 code 取值）
        "newline_preserve": "原样",
        "newline_lf": "LF",
        "newline_crlf": "CRLF",
        "conflict_skip": "跳过",
        "conflict_overwrite": "覆盖",
        "conflict_rename": "重命名",

        # 文件对话框
        "dlg_open_files_title": "选择文本文件",
        "dlg_open_files_filter": "所有文件 (*);;文本 (*.txt)",
        "dlg_choose_output_title": "选择输出目录",

        # 右键菜单
        "ctx_remove": "删除选中",
        "ctx_clear_all": "清空全部",
        "ctx_move_up": "上移",
        "ctx_move_down": "下移",

        # 切分方案对话框
        "dlg_plan_add_title": "添加切分行数",
        "dlg_plan_add_prompt": "每段行数 (正整数):",
        "dlg_invalid": "无效",
        "dlg_invalid_positive_int": "请输入正整数。",

        # 校验/启动
        "err_no_input_files": "请添加至少一个输入文件。",
        "err_file_not_found": "文件不存在: {path}",
        "err_no_plan": "请添加至少一个切分行数。",
        "err_plan_non_positive": "切分行数必须为正整数。",
        "err_no_output_dir": "请选择输出目录。",
        "err_invalid_output_dir": "输出目录无效。",
        "err_open_output_failed": "失败",
        "dlg_cannot_start_title": "无法开始",

        # 启动 / 进度 / 完成 / 失败
        "log_start": "[开始]",
        "log_canceling": "[取消中…]",
        "log_lines_scan_failed": "行数预扫失败: {err}",
        "lbl_total_error": "错误",
        "log_done": "[完成] 写入 {written} 个, 跳过 {skipped} 个, 错误 {errors} 个",
        "status_done": "完成: 写入 {written}，跳过 {skipped}，错误 {errors}",
        "log_error_prefix": "[错误] ",
        "dlg_split_failed_title": "切分失败",
        "status_failed": "失败: {msg}",
    }
    TEXTS_EN = {
        # 窗口与分组标题
        "title": "Split Text Files by Line",
        "input_group": "Input files",
        "plan_group": "Split plan",
        "name_group": "File naming",
        "adv_group": "Advanced",
        "output_group": "Output directory",

        # 按钮
        "add_files": "Add files",
        "remove": "Remove selected",
        "clear": "Clear",
        "move_up": "▲",
        "move_down": "▼",
        "plan_add": "Add",
        "plan_remove": "Remove selected",
        "plan_clear": "Clear",
        "choose": "Choose…",
        "start": "Start",
        "cancel": "Cancel",
        "open_out": "Open output",

        # 表单行
        "prefix": "Prefix:",
        "suffix": "Suffix:",
        "index_start": "Index start:",
        "encoding": "Encoding:",
        "newline": "Newline:",
        "conflict": "On conflict:",
        "plan_label": "Plan (lines per part):",
        "log": "Log:",
        "total_lines": "Total lines:",
        "estimate": "Estimated files:",
        "preview": "Preview:",
        "plan_hint": "(The last value is reused for all subsequent parts)",

        # 菜单
        "menu_lang": "Language",
        "menu_help": "Help",
        "menu_lang_zh": "Chinese",
        "menu_lang_en": "English",
        "about": "About",
        "about_title": "About",
        "about_app_name_label": "App:",
        "about_version_label": "Version:",
        "about_author_label": "Author:",

        # 下拉框选项（按 code 取值）
        "newline_preserve": "Preserve",
        "newline_lf": "LF",
        "newline_crlf": "CRLF",
        "conflict_skip": "Skip",
        "conflict_overwrite": "Overwrite",
        "conflict_rename": "Rename",

        # 文件对话框
        "dlg_open_files_title": "Choose text files",
        "dlg_open_files_filter": "All files (*);;Text (*.txt)",
        "dlg_choose_output_title": "Choose output directory",

        # 右键菜单
        "ctx_remove": "Remove selected",
        "ctx_clear_all": "Clear all",
        "ctx_move_up": "Move up",
        "ctx_move_down": "Move down",

        # 切分方案对话框
        "dlg_plan_add_title": "Add split size",
        "dlg_plan_add_prompt": "Lines per part (positive integer):",
        "dlg_invalid": "Invalid",
        "dlg_invalid_positive_int": "Please enter a positive integer.",

        # 校验/启动
        "err_no_input_files": "Please add at least one input file.",
        "err_file_not_found": "File not found: {path}",
        "err_no_plan": "Please add at least one split size.",
        "err_plan_non_positive": "Split sizes must be positive integers.",
        "err_no_output_dir": "Please choose an output directory.",
        "err_invalid_output_dir": "Output directory is invalid.",
        "err_open_output_failed": "Failed",
        "dlg_cannot_start_title": "Cannot start",

        # 启动 / 进度 / 完成 / 失败
        "log_start": "[Start]",
        "log_canceling": "[Canceling…]",
        "log_lines_scan_failed": "Line scan failed: {err}",
        "lbl_total_error": "error",
        "log_done": "[Done] wrote {written}, skipped {skipped}, errors {errors}",
        "status_done": "Done: wrote {written}, skipped {skipped}, errors {errors}",
        "log_error_prefix": "[Error] ",
        "dlg_split_failed_title": "Split failed",
        "status_failed": "Failed: {msg}",
    }

    def _T(self):
        """返回当前语言的文案表。"""
        return self.TEXTS_ZH if getattr(self, "_lang", "zh") == "zh" else self.TEXTS_EN

    def _t(self, key, **kwargs):
        """按 key 取当前语言文案；缺键时回退到 key 本身，避免崩。"""
        text = self._T().get(key, key)
        if kwargs:
            try:
                return text.format(**kwargs)
            except (KeyError, IndexError):
                return text
        return text

    def _apply_lang(self, code: str):
        self._lang = code
        T = self.TEXTS_ZH if code == "zh" else self.TEXTS_EN
        self.setWindowTitle(f"{T['title']} v{self.app_version}")
        self._set_group_titles(T)
        self.btn_add_files.setText(T["add_files"])
        self.btn_remove.setText(T["remove"])
        self.btn_clear.setText(T["clear"])
        self.btn_move_up.setText(T["move_up"])
        self.btn_move_down.setText(T["move_down"])
        self.btn_plan_add.setText(T["plan_add"])
        self.btn_plan_remove.setText(T["plan_remove"])
        self.btn_plan_clear.setText(T["plan_clear"])
        self.btn_choose_out.setText(T["choose"])
        self.btn_start.setText(T["start"])
        self.btn_cancel.setText(T["cancel"])
        self.btn_open_out.setText(T["open_out"])
        self.lbl_total.setText(f"{T['total_lines']} -")
        self.lbl_preview.setText(f"{T['estimate']} -")
        self.lbl_names_preview.setText(f"{T['preview']} -")
        self.lbl_plan_hint.setText(T["plan_hint"])
        self.lbl_plan_label.setText(T["plan_label"])
        self.lbl_log.setText(T["log"])
        self.lbl_prefix.setText(T["prefix"])
        self.lbl_suffix.setText(T["suffix"])
        self.lbl_index_start.setText(T["index_start"])
        self.lbl_encoding.setText(T["encoding"])
        self.lbl_newline.setText(T["newline"])
        self.lbl_conflict.setText(T["conflict"])
        mb_actions = self.menuBar().actions()
        if len(mb_actions) >= 1:
            mb_actions[0].setText(T["menu_lang"])
        if len(mb_actions) >= 2:
            mb_actions[1].setText(T["menu_help"])
        if hasattr(self, "act_about"):
            self.act_about.setText(T["about"])
        # 刷新语言切换菜单项的可见文本
        for act in self._lang_actions.values():
            c = act.data()
            act.setText(T[f"menu_lang_{c}"])
        self._update_combo_labels()
        self.cfg_data["language"] = code

    def _set_group_titles(self, T):
        self.input_box.setTitle(T["input_group"])
        self.plan_box.setTitle(T["plan_group"])
        self.name_box.setTitle(T["name_group"])
        self.adv_box.setTitle(T["adv_group"])
        self.out_box.setTitle(T["output_group"])

    def _update_combo_labels(self):
        T = self._T()
        # newline
        cur = self.combo_newline.currentData()
        self.combo_newline.blockSignals(True)
        self.combo_newline.clear()
        for code in NEWLINE_POLICY_CODES:
            self.combo_newline.addItem(T[f"newline_{code}"], code)
        if cur:
            idx = self.combo_newline.findData(cur)
            if idx >= 0:
                self.combo_newline.setCurrentIndex(idx)
        self.combo_newline.blockSignals(False)

        # conflict
        cur = self.combo_conflict.currentData()
        self.combo_conflict.blockSignals(True)
        self.combo_conflict.clear()
        for code in CONFLICT_POLICY_CODES:
            self.combo_conflict.addItem(T[f"conflict_{code}"], code)
        if cur:
            idx = self.combo_conflict.findData(cur)
            if idx >= 0:
                self.combo_conflict.setCurrentIndex(idx)
        self.combo_conflict.blockSignals(False)

    def _on_about(self):
        """弹出“关于”对话框，集中显示版本与元信息。

        版本号取自 ``self.app_version``（运行期参数，默认来自
        ``version.__version__``），便于将来在多窗口/插件场景中按实例替换。
        """
        app_name = __app_name_en__ if self._lang == "en" else __app_name__
        text = (
            f"<p><b>{self._t('about_version_label')}</b> v{self.app_version}</p>"
            f"<p><b>{self._t('about_author_label')}</b> {__author__}</p>"
        )
        QMessageBox.about(self, self._t("about_title"), text)

    # ----------------------- 配置装载 / 保存 ----------------------- #

    def _restore_from_config(self):
        # 输入文件
        self.list_files.clear()
        for p in self._input_files:
            if p and os.path.isfile(p):
                self.list_files.addItem(QListWidgetItem(p))
        # 方案
        self.list_plan.clear()
        plan = self.cfg_data.get("plan") or [5000]
        for n in plan:
            self.list_plan.addItem(str(n))
        # 命名
        self.edit_prefix.setText(self.cfg_data.get("prefix", ""))
        self.edit_suffix.setText(self.cfg_data.get("suffix", ".txt"))
        self.spin_index_start.setValue(int(self.cfg_data.get("index_start", 1)))
        # 编码
        enc = self.cfg_data.get("encoding", "utf-8")
        if enc in ENCODINGS:
            self.combo_encoding.setCurrentText(enc)
        # newline + conflict
        self._update_combo_labels()
        nl = self.cfg_data.get("newline_policy", "preserve")
        idx = self.combo_newline.findData(nl)
        if idx >= 0:
            self.combo_newline.setCurrentIndex(idx)
        cp = self.cfg_data.get("conflict_policy", "rename")
        idx = self.combo_conflict.findData(cp)
        if idx >= 0:
            self.combo_conflict.setCurrentIndex(idx)
        # 输出目录
        self.edit_output.setText(self.cfg_data.get("output_dir", ""))
        self._refresh_preview()

    def _save_config(self):
        self.cfg_data["input_files"] = [
            self.list_files.item(i).text() for i in range(self.list_files.count())
        ]
        self.cfg_data["plan"] = self._plan_int_list()
        self.cfg_data["prefix"] = self.edit_prefix.text()
        self.cfg_data["suffix"] = self.edit_suffix.text()
        self.cfg_data["index_start"] = self.spin_index_start.value()
        self.cfg_data["encoding"] = self.combo_encoding.currentText()
        self.cfg_data["newline_policy"] = self.combo_newline.currentData() or "preserve"
        self.cfg_data["conflict_policy"] = self.combo_conflict.currentData() or "rename"
        self.cfg_data["output_dir"] = self.edit_output.text()
        self.cfg_data["language"] = getattr(self, "_lang", "zh")
        self.cfg_data["window_size"] = [self.width(), self.height()]
        try:
            config.save_config(self.base_dir, self.cfg_data)
        except OSError:
            pass

    def closeEvent(self, e):
        self._save_config()
        self._stop_threads()
        super().closeEvent(e)

    def _stop_threads(self):
        # 取消 split worker
        if self._split_worker is not None:
            try:
                self._split_worker.cancel()
            except Exception:
                pass
        for attr in ("_split_thread",):
            th = getattr(self, attr, None)
            if th is not None:
                try:
                    th.quit()
                    th.wait(5000)
                except Exception:
                    pass
                setattr(self, attr, None)

    # ----------------------- 输入文件交互 ----------------------- #

    def _on_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, self._t("dlg_open_files_title"), "", self._t("dlg_open_files_filter")
        )
        for f in files:
            existing = [self.list_files.item(i).text() for i in range(self.list_files.count())]
            if f not in existing:
                self.list_files.addItem(QListWidgetItem(f))

    def _on_remove_selected(self):
        for it in self.list_files.selectedItems():
            self.list_files.takeItem(self.list_files.row(it))

    def _on_clear_files(self):
        self.list_files.clear()

    def _move_selected(self, delta: int):
        rows = sorted({i.row() for i in self.list_files.selectedIndexes()})
        if not rows:
            return
        items = [self.list_files.takeItem(r) for r in rows]
        for i, it in enumerate(items):
            new_row = max(0, min(self.list_files.count(), rows[i] + delta))
            self.list_files.insertItem(new_row, it)
            it.setSelected(True)

    def _show_files_menu(self, pos):
        menu = QMenu(self)
        menu.addAction(self._t("ctx_remove"), self._on_remove_selected)
        menu.addAction(self._t("ctx_clear_all"), self._on_clear_files)
        menu.addSeparator()
        menu.addAction(self._t("ctx_move_up"), lambda: self._move_selected(-1))
        menu.addAction(self._t("ctx_move_down"), lambda: self._move_selected(1))
        menu.exec(self.list_files.mapToGlobal(pos))

    # ----------------------- 方案交互 ----------------------- #

    def _plan_int_list(self) -> List[int]:
        out = []
        for i in range(self.list_plan.count()):
            try:
                v = int(self.list_plan.item(i).text())
                if v > 0:
                    out.append(v)
            except ValueError:
                pass
        return out

    def _on_plan_add(self):
        text, ok = QInputDialog.getText(
            self, self._t("dlg_plan_add_title"), self._t("dlg_plan_add_prompt")
        )
        if not ok:
            return
        text = text.strip()
        if not re.fullmatch(r"[1-9]\d*", text):
            QMessageBox.warning(self, self._t("dlg_invalid"), self._t("dlg_invalid_positive_int"))
            return
        self.list_plan.addItem(text)

    def _on_plan_remove_selected(self):
        for it in self.list_plan.selectedItems():
            self.list_plan.takeItem(self.list_plan.row(it))

    # ----------------------- 输出目录 ----------------------- #

    def _on_choose_output(self):
        d = QFileDialog.getExistingDirectory(
            self, self._t("dlg_choose_output_title"), self.edit_output.text()
        )
        if d:
            self.edit_output.setText(d)

    def _on_open_output(self):
        d = self.edit_output.text()
        if not d or not os.path.isdir(d):
            QMessageBox.warning(self, self._t("dlg_invalid"), self._t("err_invalid_output_dir"))
            return
        # os.startfile (Windows) 或 xdg-open (跨平台)
        try:
            if os.name == "nt":
                os.startfile(d)  # type: ignore[attr-defined]
            elif os.uname().sysname == "Darwin":  # type: ignore[attr-defined]
                os.system(f'open "{d}"')
            else:
                os.system(f'xdg-open "{d}"')
        except Exception as e:
            QMessageBox.warning(self, self._t("err_open_output_failed"), str(e))

    # ----------------------- 总行数预扫 ----------------------- #

    def _refresh_total_lines(self):
        """同步预扫总行数（足够快，不开子线程）。"""
        paths = [self.list_files.item(i).text() for i in range(self.list_files.count())]
        if not paths:
            self._total_lines = 0
            self.lbl_total.setText(f"{self._t('total_lines')} -")
            self._refresh_preview()
            return
        try:
            total = sum(
                _count_lines_quick(Path(p), self.combo_encoding.currentText()) for p in paths
            )
        except Exception as e:
            self._total_lines = 0
            self.lbl_total.setText(f"{self._t('total_lines')} {self._t('lbl_total_error')}")
            self.statusBar().showMessage(self._t("log_lines_scan_failed", err=e), 5000)
            self._refresh_preview()
            return
        self._total_lines = total
        self.lbl_total.setText(f"{self._t('total_lines')} {total}")
        self._refresh_preview()

    # ----------------------- 预览 ----------------------- #

    def _refresh_preview(self):
        plan = self._plan_int_list()
        total = getattr(self, "_total_lines", 0) or 0
        if total <= 0:
            self.lbl_preview.setText(f"{self._t('estimate')} -")
            self.lbl_names_preview.setText(f"{self._t('preview')} -")
            return
        try:
            n = estimate_outputs(total, plan)
        except ValueError as e:
            self.lbl_preview.setText(str(e))
            return
        self.lbl_preview.setText(f"{self._t('estimate')} {n}")
        padding = decide_padding(n)
        start = self.spin_index_start.value()
        prefix = self.edit_prefix.text()
        suffix = self.edit_suffix.text()
        sample = [f"{prefix}{start+i:0{padding}d}{suffix}" for i in range(min(3, n))]
        self.lbl_names_preview.setText(f"{self._t('preview')} {' / '.join(sample)}")

    def _t(self, key):
        T = self.TEXTS_ZH if getattr(self, "_lang", "zh") == "zh" else self.TEXTS_EN
        return T.get(key, key)

    # ----------------------- 切分 ----------------------- #

    def _validate_inputs(self) -> Optional[str]:
        if self.list_files.count() == 0:
            return self._t("err_no_input_files")
        for i in range(self.list_files.count()):
            p = self.list_files.item(i).text()
            if not os.path.isfile(p):
                return self._t("err_file_not_found", path=p)
        plan = self._plan_int_list()
        if not plan:
            return self._t("err_no_plan")
        if any(n <= 0 for n in plan):
            return self._t("err_plan_non_positive")
        if not self.edit_output.text():
            return self._t("err_no_output_dir")
        return None

    def _on_start(self):
        if self._split_thread is not None:
            return
        err = self._validate_inputs()
        if err:
            QMessageBox.warning(self, self._t("dlg_cannot_start_title"), err)
            return
        self._save_config()
        self.log_box.clear()
        self.log_box.append(self._t("log_start"))
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setRange(0, max(1, getattr(self, "_total_lines", 0)))
        self.progress.setValue(0)

        cfg = SplitConfig(
            encoding=self.combo_encoding.currentText(),
            newline_policy=self.combo_newline.currentData() or "preserve",
            conflict_policy=self.combo_conflict.currentData() or "rename",
            prefix=self.edit_prefix.text(),
            suffix=self.edit_suffix.text(),
            index_start=self.spin_index_start.value(),
        )
        paths = [self.list_files.item(i).text() for i in range(self.list_files.count())]
        self._split_thread = QThread()
        self._split_worker = SplitWorker(paths, self._plan_int_list(), cfg, self.edit_output.text())
        self._split_worker.moveToThread(self._split_thread)
        self._split_thread.started.connect(self._split_worker.run)
        self._split_worker.progress.connect(self._on_progress)
        self._split_worker.log.connect(self._on_log)
        self._split_worker.finished.connect(self._on_split_finished)
        self._split_worker.failed.connect(self._on_split_failed)
        self._split_thread.start()

    def _on_progress(self, done: int, total: int):
        if total > 0 and self.progress.maximum() != total:
            self.progress.setRange(0, total)
        self.progress.setValue(done)

    def _on_log(self, msg: str):
        self.log_box.append(msg)

    def _on_split_finished(self, written: int, skipped: int, errors: int):
        self.log_box.append(
            self._t("log_done", written=written, skipped=skipped, errors=errors)
        )
        self.statusBar().showMessage(
            self._t("status_done", written=written, skipped=skipped, errors=errors), 10000
        )
        self._teardown_split_thread()

    def _on_split_failed(self, msg: str):
        self.log_box.append(f"{self._t('log_error_prefix')}{msg}")
        QMessageBox.critical(self, self._t("dlg_split_failed_title"), msg)
        self.statusBar().showMessage(self._t("status_failed", msg=msg), 10000)
        self._teardown_split_thread()

    def _on_cancel(self):
        if self._split_worker:
            self._split_worker.cancel()
            self.log_box.append(self._t("log_canceling"))

    def _teardown_split_thread(self):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        if self._split_thread is not None:
            self._split_thread.quit()
            self._split_thread.wait(2000)
        self._split_thread = None
        self._split_worker = None

    # ----------------------- 拖拽支持 ----------------------- #

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p and os.path.isfile(p):
                existing = [self.list_files.item(i).text() for i in range(self.list_files.count())]
                if p not in existing:
                    self.list_files.addItem(QListWidgetItem(p))
