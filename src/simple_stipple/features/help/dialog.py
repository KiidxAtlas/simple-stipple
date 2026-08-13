"""Searchable Qt dialog for the Simple Stipple user manual."""

from __future__ import annotations

import re
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

from .content import TOC_ENTRIES, TOC_SEARCH_TERMS, build_help_html


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
