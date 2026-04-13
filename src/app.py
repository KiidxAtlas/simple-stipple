"""Main application window."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QCloseEvent, QColor, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.core.persistence import read_json_file, write_json_file_atomic
from src.core.workspace_state import (
    WORKSPACE_FILE_SUFFIX,
    build_workspace_document,
    normalize_workspace_path,
    validate_workspace_document,
)
from src.settings import load_settings, save_settings
from src.ui.helpers import _info_chip, _surface_frame
from src.ui.settings_dialog import SettingsDialog
from src.ui.tabs.fvi_tab import UtilitiesTab
from src.ui.tabs.image_tab import ImageTab
from src.ui.tabs.pattern_tab import PatternTab
from src.ui.tabs.shape_tab import ShapeTab
from src.ui.tabs.sketch_tab import SketchTab


def _apply_dark_palette(app: QApplication) -> None:
    """Apply a modern dark palette with stronger surfaces and spacing."""
    app.setStyle("Fusion")
    p = QPalette()
    # 4-level background hierarchy: deepest → sidebar → panel → elevated
    p.setColor(QPalette.ColorRole.Window, QColor("#161b22"))
    p.setColor(QPalette.ColorRole.WindowText, QColor("#e6edf3"))
    p.setColor(QPalette.ColorRole.Base, QColor("#0d1117"))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor("#1c2128"))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor("#1c2128"))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor("#e6edf3"))
    p.setColor(QPalette.ColorRole.Text, QColor("#e6edf3"))
    p.setColor(QPalette.ColorRole.Button, QColor("#21262d"))
    p.setColor(QPalette.ColorRole.ButtonText, QColor("#e6edf3"))
    p.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.Highlight, QColor("#2f81f7"))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor("#484f58"))
    p.setColor(QPalette.ColorRole.Mid, QColor("#30363d"))
    p.setColor(QPalette.ColorRole.Dark, QColor("#0d1117"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#484f58"))
    p.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#484f58")
    )
    app.setPalette(p)
    app.setStyleSheet(
        """
        /* ── Global font ─────────────────────────────────────── */
        * {
            font-family: "Helvetica Neue", sans-serif;
            font-size: 13px;
        }

        QMainWindow {
            background: qlineargradient(
                x1: 0, y1: 0, x2: 1, y2: 1,
                stop: 0 #0d1117,
                stop: 0.45 #121922,
                stop: 1 #161b22
            );
        }

        /* ── Tab bar ─────────────────────────────────────────── */
        QTabWidget::pane {
            border: none;
            background: transparent;
        }
        QTabBar {
            background: transparent;
        }
        QTabBar::tab {
            background: transparent;
            color: #6e7681;
            padding: 6px 16px;
            margin-right: 2px;
            border: none;
            border-bottom: 2px solid transparent;
            border-radius: 0;
            font-size: 13px;
            font-weight: 600;
        }
        QTabBar::tab:selected {
            color: #e6edf3;
            background: transparent;
            border-bottom: 2px solid #2f81f7;
        }
        QTabBar::tab:hover:!selected {
            color: #c9d1d9;
            border-bottom: 2px solid #30363d;
        }

        QLabel[role="shell-eyebrow"], QLabel[role="eyebrow"] {
            color: #6e7681;
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 1px;
        }
        QLabel[role="shell-title"] {
            color: #f0f6fc;
            font-size: 14px;
            font-weight: 700;
        }
        QLabel[role="shell-subtitle"] {
            color: #8b949e;
            font-size: 12px;
        }
        QLabel[role="shell-meta"] {
            color: #c9d1d9;
            font-size: 12px;
            font-weight: 600;
        }
        QLabel[role="page-title"] {
            color: #f0f6fc;
            font-size: 20px;
            font-weight: 700;
        }
        QLabel[role="page-subtitle"] {
            color: #8b949e;
            font-size: 12px;
        }
        QLabel[role="callout-title"] {
            color: #f0f6fc;
            font-size: 13px;
            font-weight: 700;
        }
        QLabel[role="callout-body"] {
            color: #8b949e;
            font-size: 12px;
        }
        QLabel[role="chip"] {
            padding: 2px 8px;
            border-radius: 4px;
            border: 1px solid #2b3440;
            background: rgba(31, 42, 56, 0.9);
            color: #c9d1d9;
            font-size: 10px;
            font-weight: 600;
        }
        QLabel[role="chip"][tone="accent"] {
            background: rgba(47, 129, 247, 0.14);
            border-color: rgba(88, 166, 255, 0.5);
            color: #79c0ff;
        }
        QLabel[role="chip"][tone="success"] {
            background: rgba(63, 185, 80, 0.14);
            border-color: rgba(63, 185, 80, 0.45);
            color: #7ee787;
        }
        QLabel[role="chip"][tone="warn"] {
            background: rgba(210, 153, 34, 0.16);
            border-color: rgba(210, 153, 34, 0.45);
            color: #e3b341;
        }
        QLabel[role="chip"][tone="danger"] {
            background: rgba(248, 81, 73, 0.12);
            border-color: rgba(248, 81, 73, 0.45);
            color: #ffb4ab;
        }

        /* ── Tooltip ─────────────────────────────────────────── */
        QToolTip {
            background: #1c2128;
            color: #e6edf3;
            border: 1px solid #30363d;
            padding: 5px 8px;
            border-radius: 4px;
        }

        /* ── Buttons ─────────────────────────────────────────── */
        QPushButton {
            padding: 5px 12px;
            border-radius: 6px;
            background: #1a222d;
            border: 1px solid #303a47;
            color: #e6edf3;
            font-size: 12px;
        }
        QPushButton:hover {
            background: #212b37;
            border-color: #58a6ff;
        }
        QPushButton:pressed {
            background: #121922;
        }
        QPushButton:disabled {
            background: #161b22;
            border-color: #21262d;
            color: #484f58;
        }
        QPushButton:checked {
            background: #1f3a6e;
            border-color: #2f81f7;
            color: #79c0ff;
        }

        /* ── Active mode button (toolbar toggles) ────────────── */
        QPushButton[active="true"] {
            background: #1f3a6e;
            border-color: #2f81f7;
            color: #79c0ff;
        }
        QPushButton[active="true"]:hover {
            background: #25437e;
            border-color: #58a6ff;
        }

        /* ── Primary action button ───────────────────────────── */
        QPushButton[role="primary"] {
            background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #2f81f7, stop:1 #1f6feb);
            border-color: #2f81f7;
            color: #ffffff;
            font-weight: 600;
        }
        QPushButton[role="primary"]:hover {
            background: #388bfd;
            border-color: #58a6ff;
        }
        QPushButton[role="primary"]:pressed {
            background: #1a70e0;
        }
        QPushButton[role="primary"]:disabled {
            background: #161b22;
            border-color: #21262d;
            color: #484f58;
            font-weight: normal;
        }
        QPushButton[role="danger"] {
            background: rgba(248, 81, 73, 0.08);
            border-color: rgba(248, 81, 73, 0.45);
            color: #ffb4ab;
        }
        QPushButton[role="danger"]:hover {
            background: rgba(248, 81, 73, 0.16);
            border-color: #f85149;
        }

        /* ── Input fields ────────────────────────────────────── */
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
            padding: 5px 8px;
            border-radius: 6px;
            border: 1px solid #2b3440;
            background: #0f141b;
            color: #e6edf3;
            selection-background-color: #1f3a6e;
        }
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
            border-color: #2f81f7;
        }
        QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {
            border-color: #8b949e;
        }
        QComboBox::drop-down {
            border: none;
            width: 20px;
        }
        QComboBox::down-arrow {
            width: 10px;
            height: 10px;
        }
        QComboBox QAbstractItemView {
            background: #1c2128;
            border: 1px solid #30363d;
            selection-background-color: #1f3a6e;
            outline: none;
        }
        QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
            width: 16px;
            border: none;
            background: transparent;
        }

        /* ── Scroll areas ────────────────────────────────────── */
        QScrollArea {
            border: none;
            background: transparent;
        }

        QFrame[surface="sidebar"], QFrame[surface="panel"], QFrame[surface="canvas"] {
            background: rgba(17, 23, 32, 0.92);
            border: 1px solid #26303b;
            border-radius: 10px;
        }

        QFrame[role="hero"] {
            background: rgba(17, 23, 32, 0.95);
            border: 1px solid #2b3440;
            border-radius: 10px;
        }
        QFrame[role="page-header"] {
            border-radius: 10px;
        }
        QFrame[role="callout"] {
            background: rgba(23, 30, 40, 0.94);
            border-color: #2b3440;
        }
        QFrame[role="collapsible"], QFrame[role="status-strip"], QFrame[role="object-browser"] {
            border-radius: 8px;
            border-color: #2b3440;
        }

        QToolButton {
            padding: 4px 2px;
            color: #e6edf3;
            font-size: 12px;
            font-weight: 700;
            border: none;
            background: transparent;
            text-align: left;
        }
        QToolButton:hover {
            color: #79c0ff;
        }

        QListWidget {
            border: 1px solid #2b3440;
            border-radius: 6px;
            background: #0f141b;
            padding: 2px;
            outline: none;
        }
        QListWidget::item {
            padding: 5px 8px;
            margin: 1px 0;
            border-radius: 4px;
            color: #c9d1d9;
        }
        QListWidget::item:selected {
            background: rgba(47, 129, 247, 0.18);
            color: #f0f6fc;
            border: 1px solid rgba(88, 166, 255, 0.35);
        }
        QListWidget::item:hover:!selected {
            background: rgba(33, 43, 55, 0.85);
        }

        QFrame[surface="canvas"] {
            background: rgba(15, 20, 27, 0.96);
        }

        QSplitter::handle {
            background: transparent;
            width: 10px;
        }
        QSplitter::handle:hover {
            background: rgba(88, 166, 255, 0.18);
            border-radius: 5px;
        }

        /* ── Sliders ─────────────────────────────────────────── */
        QSlider::groove:horizontal {
            height: 3px;
            background: #21262d;
            border-radius: 2px;
            margin: 0 3px;
        }
        QSlider::sub-page:horizontal {
            background: #2f81f7;
            border-radius: 2px;
        }
        QSlider::handle:horizontal {
            background: #e6edf3;
            border: 2px solid #2f81f7;
            width: 14px;
            height: 14px;
            margin: -6px -1px;
            border-radius: 8px;
        }
        QSlider::handle:horizontal:hover {
            background: #2f81f7;
            border-color: #58a6ff;
        }

        /* ── Progress bar ────────────────────────────────────── */
        QProgressBar {
            border: none;
            border-radius: 3px;
            background: #21262d;
            text-align: center;
            color: transparent;
            max-height: 4px;
        }
        QProgressBar::chunk {
            background: #2f81f7;
            border-radius: 3px;
        }

        /* ── Check boxes ─────────────────────────────────────── */
        QCheckBox {
            spacing: 6px;
        }
        QCheckBox::indicator {
            width: 15px;
            height: 15px;
            border: 1px solid #30363d;
            border-radius: 4px;
            background: #0d1117;
        }
        QCheckBox::indicator:checked {
            background: #2f81f7;
            border-color: #2f81f7;
        }
        QCheckBox::indicator:hover {
            border-color: #58a6ff;
        }

        /* ── Plain text / log ────────────────────────────────── */
        QPlainTextEdit {
            border: 1px solid #2b3440;
            border-radius: 6px;
            background: #0f141b;
        }

        /* ── Scroll bars ─────────────────────────────────────── */
        QScrollBar:vertical {
            width: 5px;
            background: transparent;
            margin: 0;
        }
        QScrollBar::handle:vertical {
            background: #30363d;
            border-radius: 3px;
            min-height: 24px;
        }
        QScrollBar::handle:vertical:hover {
            background: #484f58;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
        }
        QScrollBar:horizontal {
            height: 5px;
            background: transparent;
            margin: 0;
        }
        QScrollBar::handle:horizontal {
            background: #30363d;
            border-radius: 3px;
            min-width: 24px;
        }
        QScrollBar::handle:horizontal:hover {
            background: #484f58;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0;
        }

        /* ── Menu ────────────────────────────────────────────── */
        QMenu {
            background: #1c2128;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 4px;
        }
        QMenu::item {
            padding: 5px 16px;
            border-radius: 4px;
            color: #e6edf3;
        }
        QMenu::item:selected {
            background: #1f3a6e;
            color: #79c0ff;
        }
        QMenu::separator {
            height: 1px;
            background: #30363d;
            margin: 4px 8px;
        }

        /* ── Message boxes ───────────────────────────────────── */
        QMessageBox {
            background: #161b22;
        }

        /* ── Dialog ──────────────────────────────────────────── */
        QDialog {
            background: #161b22;
        }
        """
    )


class App(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AA Laser Studio")
        self.resize(1100, 740)
        self.setMinimumSize(860, 580)

        self._settings = load_settings()
        self._workspace_path: Path | None = None
        self._workspace_dirty: bool = False
        self._last_saved_document: dict | None = None
        self._workspace_timer = QTimer(self)
        self._workspace_timer.setSingleShot(True)
        self._workspace_timer.timeout.connect(self._update_workspace_dirty)

        # Auto-save every 60 seconds if workspace has a path and is dirty
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(60_000)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(8, 8, 8, 8)
        central_layout.setSpacing(6)
        self.setCentralWidget(central)

        self._shell_header = self._build_shell_header()
        central_layout.addWidget(self._shell_header)

        # ── Tabs ──────────────────────────────────────────────────────────────
        self._tabs = QTabWidget()
        central_layout.addWidget(self._tabs, stretch=1)

        self._utilities_tab = UtilitiesTab(settings=self._settings)
        self._pattern_tab = PatternTab(settings=self._settings)
        self._shape_tab = ShapeTab(settings=self._settings)
        self._image_tab = ImageTab(settings=self._settings)
        self._sketch_tab = SketchTab(settings=self._settings)

        self._tabs.addTab(self._shape_tab, "Draft")
        self._tabs.addTab(self._sketch_tab, "Sketch")
        self._tabs.addTab(self._pattern_tab, "Pattern Fill")
        self._tabs.addTab(self._image_tab, "Trace")
        self._tabs.addTab(self._utilities_tab, "Convert")

        for tab in (
            self._pattern_tab,
            self._shape_tab,
            self._image_tab,
            self._sketch_tab,
        ):
            tab.stateChanged.connect(self._schedule_workspace_dirty_check)
        self._shape_tab.sendSelectedToPatternRequested.connect(
            self._send_shape_selection_to_pattern
        )
        self._tabs.currentChanged.connect(self._schedule_workspace_dirty_check)
        self._tabs.currentChanged.connect(lambda _: self._refresh_workspace_header())

        self._workspace_menu = self.menuBar().addMenu("File")
        self._recent_workspaces_menu = self._workspace_menu.addMenu("Open Recent")
        self._build_workspace_actions()
        self._rebuild_recent_workspaces_menu()

        # ── Keyboard shortcuts ────────────────────────────────────────────────
        settings_action = QAction("Settings…", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self._open_settings)
        self.addAction(settings_action)

        self._clear_workspace_state()
        self._last_saved_document = self._collect_workspace_document()
        self._update_title()

    def _build_shell_header(self) -> QWidget:
        shell = _surface_frame("panel")
        shell.setProperty("role", "hero")
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)

        # App identity — compact single line
        title = QLabel("AA Laser Studio")
        title.setProperty("role", "shell-title")
        layout.addWidget(title)

        # Separator
        sep = QLabel("·")
        sep.setStyleSheet("color: #30363d; font-size: 18px;")
        layout.addWidget(sep)

        # Workspace name
        self._workspace_title_label = QLabel()
        self._workspace_title_label.setProperty("role", "shell-meta")
        layout.addWidget(self._workspace_title_label)

        # Status chip
        self._workspace_state_chip = _info_chip("Saved", "success")
        layout.addWidget(self._workspace_state_chip)

        layout.addStretch()

        # File actions — grouped tightly
        for text, slot, role in [
            ("New", self._new_workspace, None),
            ("Open", self._open_workspace, None),
            ("Save", self._save_workspace, "primary"),
            ("Save As", self._save_workspace_as, None),
        ]:
            btn = QPushButton(text)
            btn.setMinimumHeight(30)
            if role:
                btn.setProperty("role", role)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

        # Settings — visually separated
        settings_btn = QPushButton("⚙")
        settings_btn.setFixedSize(30, 30)
        settings_btn.setToolTip("Settings (Ctrl+,)")
        settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(settings_btn)

        return shell

    def _refresh_workspace_header(self) -> None:
        title = self._workspace_path.stem if self._workspace_path else "Untitled"
        self._workspace_title_label.setText(title)
        chip_text = "Unsaved" if self._workspace_dirty else "Saved"
        chip_tone = "warn" if self._workspace_dirty else "success"
        self._workspace_state_chip.setText(chip_text)
        self._workspace_state_chip.setProperty("tone", chip_tone)
        self._workspace_state_chip.style().unpolish(self._workspace_state_chip)
        self._workspace_state_chip.style().polish(self._workspace_state_chip)

    def _build_workspace_actions(self) -> None:
        self._new_workspace_action = QAction("New Workspace", self)
        self._new_workspace_action.setShortcut(QKeySequence("Ctrl+N"))
        self._new_workspace_action.triggered.connect(self._new_workspace)
        self._workspace_menu.addAction(self._new_workspace_action)

        self._open_workspace_action = QAction("Open Workspace…", self)
        self._open_workspace_action.setShortcut(QKeySequence("Ctrl+O"))
        self._open_workspace_action.triggered.connect(self._open_workspace)
        self._workspace_menu.addAction(self._open_workspace_action)

        self._save_workspace_action = QAction("Save Workspace", self)
        self._save_workspace_action.setShortcut(QKeySequence("Ctrl+S"))
        self._save_workspace_action.triggered.connect(self._save_workspace)
        self._workspace_menu.addAction(self._save_workspace_action)

        self._save_workspace_as_action = QAction("Save Workspace As…", self)
        self._save_workspace_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self._save_workspace_as_action.triggered.connect(self._save_workspace_as)
        self._workspace_menu.addAction(self._save_workspace_as_action)

        self._workspace_menu.addSeparator()

    def _workspace_default_dir(self) -> str:
        return self._settings.get("workspace_dir", str(Path.home()))

    def _collect_workspace_document(self) -> dict:
        workspace_name = (
            self._workspace_path.stem.replace(WORKSPACE_FILE_SUFFIX.replace(".json", ""), "")
            if self._workspace_path
            else "Untitled Workspace"
        )
        return build_workspace_document(
            workspace_name=workspace_name,
            app_state={"current_tab": self._tabs.currentIndex()},
            tab_states={
                "shape": self._shape_tab.get_workspace_state(),
                "sketch": self._sketch_tab.get_workspace_state(),
                "pattern": self._pattern_tab.get_workspace_state(),
                "image": self._image_tab.get_workspace_state(),
                "utilities": self._utilities_tab.get_workspace_state(),
            },
            preset_state={
                "shape": self._shape_tab.get_preset_state(),
                "pattern": self._pattern_tab.get_preset_state(),
            },
            meta={
                "workspace_path": str(self._workspace_path)
                if self._workspace_path
                else ""
            },
        )

    def _apply_workspace_document(self, document: dict) -> None:
        data = validate_workspace_document(document)
        self._shape_tab.apply_preset_state(data.get("presets", {}).get("shape", {}))
        self._pattern_tab.apply_preset_state(data.get("presets", {}).get("pattern", {}))
        tabs = data.get("tabs", {})
        self._shape_tab.apply_workspace_state(tabs.get("shape", {}))
        self._sketch_tab.apply_workspace_state(tabs.get("sketch", {}))
        self._pattern_tab.apply_workspace_state(tabs.get("pattern", {}))
        self._image_tab.apply_workspace_state(tabs.get("image", {}))
        self._utilities_tab.apply_workspace_state(tabs.get("utilities", {}))
        self._tabs.setCurrentIndex(int(data.get("app", {}).get("current_tab", 0)))

    def _clear_workspace_state(self) -> None:
        self._shape_tab.clear_workspace_state()
        self._sketch_tab.clear_workspace_state()
        self._pattern_tab.clear_workspace_state()
        self._image_tab.clear_workspace_state()
        self._utilities_tab.clear_workspace_state()
        self._tabs.setCurrentIndex(0)

    def _schedule_workspace_dirty_check(self) -> None:
        self._workspace_timer.start(150)

    def _send_shape_selection_to_pattern(
        self,
        polys: list[list[tuple[float, float]]],
    ) -> None:
        if not polys:
            return
        self._pattern_tab.load_outline_polys(polys, source_label="Draft selection")
        self._tabs.setCurrentWidget(self._pattern_tab)
        self._schedule_workspace_dirty_check()

    def _update_workspace_dirty(self) -> None:
        if self._last_saved_document is None:
            self._workspace_dirty = False
        else:
            self._workspace_dirty = self._collect_workspace_document() != self._last_saved_document
        self._update_title()

    def _update_title(self) -> None:
        if self._workspace_path:
            name = self._workspace_path.name
        else:
            name = "Untitled Workspace"
        dirty = " *" if self._workspace_dirty else ""
        self.setWindowTitle(f"AA Laser Studio — {name}{dirty}")
        self._refresh_workspace_header()

    def _remember_workspace_path(self, path: Path) -> None:
        self._settings["workspace_dir"] = str(path.parent)
        self._settings["current_workspace"] = str(path)
        recent = [p for p in self._settings.get("recent_workspaces", []) if p != str(path)]
        recent.insert(0, str(path))
        self._settings["recent_workspaces"] = recent[:8]
        save_settings(self._settings)
        self._rebuild_recent_workspaces_menu()

    def _rebuild_recent_workspaces_menu(self) -> None:
        self._recent_workspaces_menu.clear()
        recent = [
            Path(path)
            for path in self._settings.get("recent_workspaces", [])
            if Path(path).exists()
        ]
        if not recent:
            action = QAction("No recent workspaces", self)
            action.setEnabled(False)
            self._recent_workspaces_menu.addAction(action)
            return
        for path in recent:
            self._recent_workspaces_menu.addAction(
                path.name,
                lambda checked=False, p=path: self._load_workspace_file(p),
            )

    def _confirm_discard_if_dirty(self) -> bool:
        self._update_workspace_dirty()
        if not self._workspace_dirty:
            return True
        choice = QMessageBox.question(
            self,
            "Unsaved Workspace",
            "The current workspace has unsaved changes. Save before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            return False
        if choice == QMessageBox.StandardButton.Save:
            return self._save_workspace()
        return True

    def _new_workspace(self) -> None:
        if not self._confirm_discard_if_dirty():
            return
        self._workspace_path = None
        self._clear_workspace_state()
        self._last_saved_document = self._collect_workspace_document()
        self._workspace_dirty = False
        self._update_title()

    def _open_workspace(self) -> None:
        if not self._confirm_discard_if_dirty():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Workspace",
            self._workspace_default_dir(),
            f"Workspace files (*{WORKSPACE_FILE_SUFFIX});;JSON files (*.json);;All files (*)",
        )
        if path:
            self._load_workspace_file(Path(path), check_dirty=False)

    def _load_workspace_file(self, path: Path, check_dirty: bool = True) -> None:
        if check_dirty and not self._confirm_discard_if_dirty():
            return
        try:
            data = read_json_file(path, default={})
            if not isinstance(data, dict):
                raise TypeError("Workspace file is not a JSON object.")
            self._apply_workspace_document(data)
            self._workspace_path = path
            self._last_saved_document = self._collect_workspace_document()
            self._workspace_dirty = False
            self._remember_workspace_path(path)
            self._update_title()
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(self, "Workspace Error", str(exc))

    def _save_workspace(self) -> bool:
        if self._workspace_path is None:
            return self._save_workspace_as()
        try:
            document = self._collect_workspace_document()
            write_json_file_atomic(self._workspace_path, document)
            self._last_saved_document = document
            self._workspace_dirty = False
            self._remember_workspace_path(self._workspace_path)
            self._update_title()
            return True
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(self, "Workspace Error", str(exc))
            return False

    def _save_workspace_as(self) -> bool:
        default_name = (
            self._workspace_path.name
            if self._workspace_path
            else f"workspace{WORKSPACE_FILE_SUFFIX}"
        )
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Workspace As",
            str(Path(self._workspace_default_dir()) / default_name),
            f"Workspace files (*{WORKSPACE_FILE_SUFFIX});;JSON files (*.json)",
        )
        if not path:
            return False
        self._workspace_path = normalize_workspace_path(path)
        return self._save_workspace()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._confirm_discard_if_dirty():
            event.accept()
            return
        event.ignore()

    def _autosave(self) -> None:
        """Auto-save workspace if it has a path and is dirty."""
        if self._workspace_path and self._workspace_dirty:
            try:
                document = self._collect_workspace_document()
                write_json_file_atomic(self._workspace_path, document)
                self._last_saved_document = document
                self._workspace_dirty = False
                self._update_title()
            except (OSError, TypeError, ValueError) as exc:
                logging.warning("Auto-save failed: %s", exc)
                self._workspace_state_chip.setText("Auto-save failed")
                self._workspace_state_chip.setProperty("tone", "danger")
                self._workspace_state_chip.style().unpolish(self._workspace_state_chip)
                self._workspace_state_chip.style().polish(self._workspace_state_chip)

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self, self._settings)
        if dlg.exec() == SettingsDialog.DialogCode.Accepted:
            # Propagate changed settings to tabs that cache paths at init time.
            self._settings = dlg._settings
            for tab in (
                self._utilities_tab,
                self._pattern_tab,
                self._shape_tab,
                self._image_tab,
            ):
                tab._settings = self._settings
