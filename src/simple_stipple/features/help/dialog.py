"""Searchable Qt dialog for the Simple Stipple user manual."""

from __future__ import annotations

import importlib.resources as _resources
import re
from html import escape as _html_escape
from html import unescape as _html_unescape

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.ui.components.feedback import refresh_style
from simple_stipple.ui.components.icons import tool_icon


class HelpDialog(QDialog):
    """Fully-fledged help dialog with searchable table of contents.

    Features:
    - Searchable TOC filter box (filters entries as you type)
    - Splitter between TOC and content (drag to resize)
    - Clickable TOC entries scroll to the corresponding section
    - In-content anchor links update the TOC highlight
    - Content is generated dynamically from the command registry
    """

    def __init__(self, parent: QWidget | None = None, main_window: QMainWindow | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self._main_window = main_window
        self.setWindowTitle("Simple Stipple — User Manual")
        self.setMinimumSize(950, 700)

        # Build content dynamically
        self._html_content = build_help_html()
        self._toc_entries = list(TOC_ENTRIES)
        self._last_find_query: str | None = None

        # Apply theme-aware stylesheet
        self._apply_stylesheet()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header bar ───────────────────────────────────────────────
        header = QFrame()
        header.setObjectName("helpHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 12)

        title_label = QLabel("User Manual")
        title_label.setObjectName("helpTitle")
        header_layout.addWidget(title_label)

        header_layout.addStretch()
        home_btn = QPushButton("Browse topics")
        home_btn.setObjectName("helpBrowseButton")
        home_btn.setToolTip("Clear search and return to the start of the manual")
        home_btn.clicked.connect(self._show_manual_home)
        header_layout.addWidget(home_btn)

        close_btn = QPushButton()
        close_btn.setIcon(tool_icon("cancel", size=16))
        close_btn.setAccessibleName("Close user manual")
        close_btn.setObjectName("helpCloseButton")
        close_btn.setMinimumSize(32, 32)
        close_btn.setToolTip("Close user manual")
        # A QPushButton inside a QDialog defaults to autoDefault=True, so as
        # the dialog's only button it silently auto-triggers on every Enter
        # press anywhere in the dialog (including the search box) and closes
        # it — even though the search box's own returnPressed handler also
        # fires correctly. Without this, "search then press Enter" always
        # closed the manual before you could see any result.
        close_btn.setAutoDefault(False)
        close_btn.setDefault(False)
        close_btn.clicked.connect(self.close)
        header_layout.addWidget(close_btn)

        root.addWidget(header)

        # ── Splitter: TOC | Content ──────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left panel — TOC with search ─────────────────────────────
        toc_widget = QFrame()
        toc_widget.setObjectName("tocPanel")
        toc_layout = QVBoxLayout(toc_widget)
        toc_layout.setContentsMargins(0, 8, 0, 8)
        toc_layout.setSpacing(4)

        # Search/filter box
        search_label = QLabel("FILTER")
        search_label.setObjectName("tocLabel")
        toc_layout.addWidget(search_label)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search help…")
        self._search_box.setToolTip(
            "Type to filter topics. Press Enter repeatedly to find each occurrence."
        )
        self._search_box.setObjectName("tocSearch")
        self._search_box.setClearButtonEnabled(True)
        self._search_box.textChanged.connect(self._filter_toc)
        self._search_box.returnPressed.connect(self._find_in_content)
        toc_layout.addWidget(self._search_box)

        self._search_status = QLabel(f"{len(self._toc_entries)} topics")
        self._search_status.setObjectName("tocSearchStatus")
        self._search_status.setAccessibleName("Help search results")
        toc_layout.addWidget(self._search_status)

        self._toc_list = QListWidget()
        self._toc_list.setObjectName("tocList")

        for section_id, label in self._toc_entries:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, section_id)
            self._toc_list.addItem(item)

        self._toc_list.currentItemChanged.connect(self._on_toc_changed)
        toc_layout.addWidget(self._toc_list)

        splitter.addWidget(toc_widget)

        # ── Right panel — Help content ───────────────────────────────
        self._content = QTextBrowser()
        self._content.setObjectName("helpContent")
        self._content.setHtml(self._html_content)
        self._content.anchorClicked.connect(self._on_anchor_clicked)

        # QFont(str, ...) treats a comma-separated CSS font stack as one
        # (bogus) family name — Qt falls back to its default font, which on
        # some platforms is monospace. Use the families-list constructor,
        # matching the family fallback chain the rest of the app uses.
        font = QFont(["Arial", "Helvetica Neue"], 13)
        self._content.setFont(font)

        splitter.addWidget(self._content)

        # Set initial splitter sizes (TOC : Content ≈ 1 : 3)
        splitter.setSizes([310, 640])
        # Without an explicit stretch factor here, Qt had no basis to keep
        # the header compact — it and the splitter both defaulted to
        # stretch 0, and the header ballooned to fill most of the dialog
        # instead of the content area. Matches the app shell's own header
        # pattern (src/simple_stipple/app.py's central_layout.addWidget(self._tabs, stretch=1)).
        root.addWidget(splitter, stretch=1)

    # ── Styling ────────────────────────────────────────────────────────

    def _apply_stylesheet(self) -> None:
        """Refresh centralized Help-dialog selectors after construction."""
        self.style().unpolish(self)
        self.style().polish(self)

    # ── Search / Filter ────────────────────────────────────────────────

    def _section_text(self, section_id: str) -> str:
        if not self._html_content:
            return ""
        try:
            section_marker = f'id="{section_id}"'
            section_start = self._html_content.find(section_marker)
            if section_start < 0:
                return ""
            tag_end = self._html_content.find(">", section_start)
            search_from = tag_end + 1 if tag_end >= 0 else section_start + len(section_marker)
            next_section = self._html_content.find('class="section-heading"', search_from)
            section_end = next_section if next_section >= 0 else len(self._html_content)
            section_html = self._html_content[section_start:section_end]
            plain_text = _html_unescape(re.sub(r"<[^>]+>", " ", section_html))
            return " ".join(plain_text.casefold().split())
        except Exception:  # noqa: BLE001 - malformed help content should not break filtering
            return ""

    def _section_score(self, section_id: str, query: str) -> int:
        """Rank a topic using labels, user-language aliases, then body text."""
        normalized = " ".join(query.casefold().split())
        if not normalized:
            return 1
        label = next(
            (text.casefold() for entry_id, text in self._toc_entries if entry_id == section_id),
            "",
        )
        aliases = TOC_SEARCH_TERMS.get(section_id, ())
        if normalized in label:
            return 300
        if any(normalized == alias or normalized in alias for alias in aliases):
            return 240

        text = self._section_text(section_id)
        if " " in normalized or len(normalized) > 5:
            return 80 if normalized in text else 0
        # Short CAD terms such as "round" must not match "background" or
        # "around"; those false positives previously buried the useful topic.
        return 80 if re.search(rf"\b{re.escape(normalized)}\b", text) else 0

    def _select_first_visible_toc_item(self) -> None:
        for index in range(self._toc_list.count()):
            item = self._toc_list.item(index)
            if item.isHidden():
                continue
            self._toc_list.setCurrentItem(item)
            return

    def _filter_toc(self, text: str) -> None:
        """Filter TOC entries based on search text."""
        # Any edit invalidates the in-page find cursor position, so the
        # next Enter press starts a fresh search from the top of the
        # document instead of continuing from wherever a previous, now-
        # stale search left the cursor.
        self._last_find_query = None
        query = text.strip().lower()
        visible_count = 0

        best_item: QListWidgetItem | None = None
        best_score = 0
        for index in range(self._toc_list.count()):
            item = self._toc_list.item(index)
            if not query:
                item.setHidden(False)
                continue
            section_id = str(item.data(Qt.ItemDataRole.UserRole)).casefold()
            score = self._section_score(section_id, query)
            found = score > 0
            item.setHidden(not found)
            visible_count += int(found)
            if score > best_score:
                best_score = score
                best_item = item

        if not query:
            visible_count = self._toc_list.count()
        if best_item is not None:
            self._toc_list.setCurrentItem(best_item)
            self._toc_list.scrollToItem(best_item)
        elif visible_count:
            self._select_first_visible_toc_item()
        else:
            self._toc_list.clearSelection()
            self._toc_list.setCurrentRow(-1)
        noun = "topic" if visible_count == 1 else "topics"
        best_text = f" · best: {best_item.text()}" if best_item is not None else ""
        self._search_status.setText(f"{visible_count} {noun}{best_text}")
        self._search_box.setProperty("error", bool(query) and visible_count == 0)
        refresh_style(self._search_box)

    def _show_manual_home(self) -> None:
        """Restore the complete navigation list and its introductory page."""
        self._search_box.clear()
        self._content.scrollToAnchor("manual-home")
        if self._toc_list.count():
            self._toc_list.setCurrentRow(0)

    def _find_in_content(self) -> None:
        """Enter in the search box jumps to, selects (highlighted via the
        app's selection color), and scrolls to the actual matched text on
        the page — the TOC filter above only narrows down which *section*
        to open, it doesn't locate a specific word within one.

        The first Enter after typing a new query always finds the first
        match from the top of the document (not from wherever the cursor
        last happened to be, e.g. after TOC navigation); pressing Enter
        again with the same query advances to the next match and wraps
        around once the end is reached.
        """
        query = self._search_box.text().strip()
        if not query:
            return
        if query != self._last_find_query:
            current = self._toc_list.currentItem()
            section_id = str(current.data(Qt.ItemDataRole.UserRole)) if current is not None else ""
            cursor = self._cursor_for_anchor(section_id)
            self._content.setTextCursor(cursor)
            self._last_find_query = query
        found = self._content.find(query)
        if not found:
            # Wrap around: reset to the document start and retry once so
            # repeated Enter presses cycle instead of dead-ending.
            cursor = self._content.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self._content.setTextCursor(cursor)
            found = self._content.find(query)
        self._search_box.setProperty("error", not found)
        refresh_style(self._search_box)

    def _cursor_for_anchor(self, anchor: str) -> QTextCursor:
        """Return a cursor at an HTML anchor, falling back to document start."""
        document = self._content.document()
        block = document.begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and anchor in fragment.charFormat().anchorNames():
                    cursor = QTextCursor(document)
                    cursor.setPosition(fragment.position())
                    return cursor
                iterator += 1
            block = block.next()
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        return cursor

    # ── TOC interaction ────────────────────────────────────────────────

    def _on_toc_changed(self, current: QListWidgetItem | None) -> None:
        if current is None:
            return
        section_id = current.data(Qt.ItemDataRole.UserRole)
        self._scroll_to_section(str(section_id))

    def _on_anchor_clicked(self, url: QUrl) -> None:
        anchor = url.fragment()
        if anchor:
            self._scroll_to_section(anchor)

    def _scroll_to_section(self, section_id: str) -> None:
        self._content.scrollToAnchor(section_id)

        # Highlight the corresponding TOC entry (considering filtered items)
        for i in range(self._toc_list.count()):
            item = self._toc_list.item(i)
            if item.isHidden():
                continue
            if item.data(Qt.ItemDataRole.UserRole) == section_id:
                self._toc_list.setCurrentItem(item)
                # Scroll TOC to show the item
                self._toc_list.scrollToItem(item)
                break

    # ── Public API ─────────────────────────────────────────────────────

    @classmethod
    def show_help(
        cls, parent: QWidget | None = None, main_window: QMainWindow | None = None
    ) -> HelpDialog:
        """Show the help dialog. Returns the dialog instance."""
        dialog = cls(parent, main_window)
        dialog.exec()
        return dialog


def _toc_entry(section_id: str, label: str) -> tuple[str, str]:
    """Create a TOC entry."""
    return section_id, label


def build_help_html() -> str:
    """Build the complete help HTML from all sections."""
    sections = [
        _build_getting_started(),
        _build_common_tasks(),
        _build_production_workflows(),
        _build_files_and_recovery(),
        _build_precision_editing(),
        _build_draft_page(),
        _build_bezier_pen_tool(),
        _build_dimension_tool(),
        _build_radial_menu(),
        _build_path_cleanup(),
        _build_text_tools(),
        _build_layers(),
        _build_pattern_page(),
        _build_pattern_types(),
        _build_trace_page(),
        _build_convert_page(),
        _build_repo_page(),
        _build_canvas_commands(),
        _build_shortcuts(),
        _build_troubleshooting(),
        _build_settings_updates(),
        _build_support(),
    ]

    return f"""
<style>
    body {{ color: #c9d1d9; }}
    p {{ color: #c9d1d9; line-height: 1.6; }}
    li {{ color: #c9d1d9; margin-bottom: 4px; }}
    strong {{ color: #f0f6fc; }}
    em {{ color: #8b949e; }}
    a {{ color: #58a6ff; }}
    .section-heading {{
        color: #f0f6fc;
        font-size: 22px;
        font-weight: 700;
        border-bottom: 2px solid #2f81f7;
        padding-bottom: 6px;
        margin-top: 28px;
        margin-bottom: 14px;
    }}
    .subheading {{
        color: #79c0ff;
        font-size: 16px;
        font-weight: 600;
        margin-top: 20px;
        margin-bottom: 8px;
    }}
    .panel-title {{
        color: #e6edf3;
        font-size: 13px;
        font-weight: 700;
        margin-top: 14px;
        margin-bottom: 4px;
    }}
    kbd {{
        background-color: #21262d;
        border: 1px solid #30363d;
        border-radius: 4px;
        padding: 1px 6px;
        font-family: Menlo, Consolas, Courier;
        font-size: 12px;
        color: #e6edf3;
    }}
    code {{
        background-color: #161b22;
        border: 1px solid #30363d;
        border-radius: 3px;
        padding: 1px 4px;
        font-family: Menlo, Consolas, Courier;
        color: #79c0ff;
    }}
</style>

<h1 id="manual-home" style="text-align:center;color:#f0f6fc;">Simple Stipple Manual</h1>
<p style="text-align:center;color:#8b949e;">A task-first guide to drawing, patterning, image engraving, and production-ready export.</p>

<hr style="border:none;border-top:2px solid #2f81f7;margin:20px 0;">

{"".join(sections)}
"""


# ── TOC entries (section id → display label) ───────────────────────────────

TOC_ENTRIES: list[tuple[str, str]] = [
    _toc_entry("getting-started", "Start here · Getting Started"),
    _toc_entry("common-tasks", "Start here · I Want To…"),
    _toc_entry("production-workflows", "Start here · Production Workflows"),
    _toc_entry("files-recovery", "Files · Save, Import & Recover"),
    _toc_entry("precision-editing", "Edit · Select, Round & Align"),
    _toc_entry("draft-page", "Create · Draft Page"),
    _toc_entry("bezier-pen-tool", "Create · Bezier Pen"),
    _toc_entry("dimension-tool", "Create · Dimensions"),
    _toc_entry("text-tools", "Create · Text & Typography"),
    _toc_entry("pattern-page", "Create · Pattern Page"),
    _toc_entry("pattern-types", "Create · Pattern Types"),
    _toc_entry("trace-page", "Create · Trace Images"),
    _toc_entry("layers", "Organize · Layers"),
    _toc_entry("radial-menu", "Navigate · Quick Radial Menu"),
    _toc_entry("canvas-commands", "Navigate · Canvas Commands"),
    _toc_entry("keyboard-shortcuts", "Navigate · Keyboard Shortcuts"),
    _toc_entry("path-cleanup", "Edit · Path Cleanup"),
    _toc_entry("convert-page", "Files · Convert & Repair"),
    _toc_entry("repo-page", "Files · Repository Sync"),
    _toc_entry("troubleshooting", "Solve · Troubleshooting"),
    _toc_entry("settings-updates", "Configure · Settings & Updates"),
    _toc_entry("support", "Solve · Support & Diagnostics"),
]

# User vocabulary does not always match the exact control label. These aliases
# intentionally map common goals and CAD terms to the section that explains
# how to complete them.
TOC_SEARCH_TERMS: dict[str, tuple[str, ...]] = {
    "getting-started": ("begin", "first project", "new user", "overview", "workflow"),
    "common-tasks": ("how do i", "what can i do", "workflow", "quick start", "goal"),
    "production-workflows": (
        "laser",
        "production",
        "engraving workflow",
        "first export",
        "image engraving",
    ),
    "files-recovery": (
        "save",
        "open",
        "workspace",
        "autosave",
        "recover",
        "recovery",
        "import",
        "export",
        "lost work",
        "recover lost work",
        "movedist",
    ),
    "precision-editing": (
        "select",
        "round",
        "rounded corner",
        "rounded corners",
        "fillet",
        "chamfer",
        "bevel",
        "offset",
        "boolean",
        "union",
        "subtract",
        "align",
        "snap",
        "constraint",
        "construction",
    ),
    "draft-page": ("draw", "shape", "edit", "resize"),
    "bezier-pen-tool": ("curve", "handles", "pen", "smooth curve"),
    "dimension-tool": ("measure", "measurement", "length", "angle", "radius"),
    "radial-menu": ("quick menu", "tool wheel", "q menu"),
    "path-cleanup": ("smooth", "simplify", "cleanup", "repair path", "fit curve"),
    "text-tools": ("font", "lettering", "type", "text on path"),
    "layers": ("hide", "lock", "organize", "color", "group"),
    "pattern-page": (
        "engrave",
        "engraving",
        "fill",
        "generate",
        "region",
        "treatment",
        "slider",
        "decimal",
        "precision",
        "parameter value",
    ),
    "pattern-types": ("honeycomb", "voronoi", "hatch", "tile", "motif"),
    "trace-page": ("image", "photo", "bitmap", "outline", "vectorize", "threshold"),
    "convert-page": ("convert", "repair", "fix dxf", "svg", "fvi"),
    "repo-page": ("git", "pull", "push", "commit", "repository"),
    "canvas-commands": ("pan", "zoom", "select", "undo", "grid", "snap"),
    "keyboard-shortcuts": ("hotkey", "key binding", "shortcut"),
    "troubleshooting": ("problem", "error", "failed", "not working", "missing"),
    "settings-updates": (
        "settings",
        "preferences",
        "units",
        "keybindings",
        "shortcut",
        "update",
        "update app",
        "install",
        "change units",
    ),
    "support": ("settings", "preferences", "update", "logs", "help"),
}


# ── Help Dialog ───────────────────────────────────────────────────────────


def _load_manual_section(filename: str) -> str:
    return (
        _resources.files("simple_stipple.resources")
        .joinpath("manual")
        .joinpath(filename)
        .read_text(encoding="utf-8")
    )


def _build_canvas_commands() -> str:
    """Canvas command reference from the actual registry."""
    try:
        from simple_stipple.canvas import commands as cmd_mod

        rows = cmd_mod.shortcut_reference_rows()
    except Exception:  # noqa: BLE001 — graceful fallback if commands module unavailable
        rows = []

    sections_html = ""
    for label, keys in rows:
        if not label:
            continue
        if not keys:
            sections_html += f"<h4 class='panel-title'>{_esc(label)}</h4>"
        else:
            sections_html += (
                f"<tr><td style='padding:4px;border-bottom:1px solid #30363d;'>"
                f"<strong>{_esc(label)}</strong></td>"
                f"<td style='padding:4px;border-bottom:1px solid #30363d;'>"
                f"{_esc(keys)}</td></tr>"
            )

    if sections_html:
        return f"""
<h2 id="canvas-commands" class="section-heading">
    Canvas Commands — Complete Reference
</h2>

<p>This reference is generated directly from the application's command registry, so it always stays in sync with your installed version.</p>

<table style="width:100%;border-collapse:collapse;margin:8px 0;">
    <tr style="background-color:#1c2e4a;">
        <th style="padding:8px;text-align:left;border-bottom:2px solid #2f81f7;">Command</th>
        <th style="padding:8px;text-align:left;border-bottom:2px solid #2f81f7;">Shortcut</th>
    </tr>
    {sections_html}
</table>

<h3 class="subheading">Canvas Interaction</h3>
<ul>
    <li><strong>Pan:</strong> Space-drag or middle mouse button</li>
    <li><strong>Zoom:</strong> Mouse wheel, ⌘+ / ⌘−</li>
    <li><strong>Nudge selection:</strong> Arrow keys (⇧ = 1 mm step)</li>
    <li><strong>Delete selected:</strong> Backspace / Del</li>
    <li><strong>Quick radial menu:</strong> Press Q to open — customizable in Settings, see Draft Page → Quick Radial Menu</li>
    <li><strong>Grid:</strong> Toggle with G, snap to grid with Shift+G</li>
    <li><strong>Rulers:</strong> Toggle with Ctrl+R. Drag from rulers to create guides.</li>
    <li><strong>Scale by reference:</strong> Press M, pick the fixed base point and a second reference point, then enter the real target distance. The explicit selection is scaled; with no selection, all visible unlocked geometry is used. Expressions and the active unit are supported. Shift constrains the reference angle, Alt bypasses snapping, right-click steps back, and Undo reverses the result.</li>
    <li><strong>Smart dimensions:</strong> Press Shift+M and hover an edge to highlight its exact segment. Selecting the same segment twice measures its length; two intersecting segments create an angle; two parallel segments create perpendicular spacing; and two separate segments create their shortest distance. Precise vertex-to-vertex dimensions and circle diameters remain available. Free point dimensions use two snapped points plus a third click for label offset. Drag a placed linear dimension to change its offset, double-click it to edit precision, or right-click for precision and delete actions. Dimensions persist with the workspace.</li>
    <li><strong>Fit view:</strong> Double-click empty space or press F</li>
    <li><strong>Snap engine:</strong> Automatically snaps to vertices, edges, and guides</li>
    <li><strong>Rubber band selection:</strong> Plain drag = window select (fully enclosed), Shift+drag = crossing select (touched)</li>
</ul>

<h3 class="subheading">Entity Properties</h3>
<p>Each entity on the canvas can have properties set via the properties panel:</p>
<ul>
    <li><strong>Hidden:</strong> Entity is not displayed or exported</li>
    <li><strong>Locked:</strong> Entity cannot be selected or moved</li>
    <li><strong>Construction:</strong> Drawn as dashed lines, not exported to DXF</li>
    <li><strong>Group:</strong> Entities grouped together move as one object</li>
    <li><strong>Layer:</strong> Organize entities into named layers (visible in layer tree)</li>
</ul>
"""
    return ""


def _build_shortcuts() -> str:
    """Keyboard shortcuts reference."""
    return _load_manual_section("shortcuts.html")


def _build_troubleshooting() -> str:
    """Troubleshooting section."""
    return _load_manual_section("troubleshooting.html")


def _build_support() -> str:
    """Support section."""
    return _load_manual_section("support.html")


# ── Full HTML content builder ─────────────────────────────────────────────


def _build_production_workflows() -> str:
    """Task-first routes for the capabilities added across the application."""
    return _load_manual_section("production_workflows.html")


def _build_pattern_page() -> str:
    """Pattern page section."""
    return _load_manual_section("pattern_page.html")


def _build_pattern_types() -> str:
    """Available pattern types and parameters."""
    return _load_manual_section("pattern_types.html")


def _build_trace_page() -> str:
    """Trace page section."""
    return _load_manual_section("trace_page.html")


def _build_convert_page() -> str:
    """Convert page section."""
    return _load_manual_section("convert_page.html")


def _build_repo_page() -> str:
    """Repo page section."""
    return _load_manual_section("repo_page.html")


def _esc(text: str) -> str:
    """HTML-escape a string."""
    return _html_escape(str(text))


# ── Content generators ────────────────────────────────────────────────────


def _build_getting_started() -> str:
    """Getting Started section."""
    return _load_manual_section("getting_started.html")


def _build_common_tasks() -> str:
    """Goal-first routes for users who do not know the feature names."""
    return _load_manual_section("common_tasks.html")


def _build_files_and_recovery() -> str:
    """Explain the complete file lifecycle and recovery choices."""
    return _load_manual_section("files_and_recovery.html")


def _build_precision_editing() -> str:
    """Task-focused drafting and editing reference."""
    return _load_manual_section("precision_editing.html")


def _build_settings_updates() -> str:
    """Explain navigation and operational settings in user language."""
    return _load_manual_section("settings_updates.html")


def _build_draft_page() -> str:
    """Draft page section."""
    return _load_manual_section("draft_page.html")


def _build_bezier_pen_tool() -> str:
    """Bezier Pen tool section."""
    return _load_manual_section("bezier_pen_tool.html")


def _build_dimension_tool() -> str:
    """Dimension / annotation tool section."""
    return _load_manual_section("dimension_tool.html")


def _build_radial_menu() -> str:
    """Quick radial menu section."""
    return _load_manual_section("radial_menu.html")


def _build_path_cleanup() -> str:
    """Simplify / Smooth / Fit to Curve section."""
    return _load_manual_section("path_cleanup.html")


def _build_text_tools() -> str:
    """Multi-line text + text-on-path section."""
    return _load_manual_section("text_tools.html")


def _build_layers() -> str:
    """Layer management section."""
    return _load_manual_section("layers.html")
