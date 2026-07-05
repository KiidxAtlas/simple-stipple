"""The User Manual dialog: content-generation correctness (no more broken
CSS variables Qt's rich-text engine can't resolve, a real class-based
<style> block instead of unstyled headings, and coverage for the newer
canvas features) plus basic construction smoke tests.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("PySide6")

from src.ui.pages.help import TOC_ENTRIES, build_help_html  # noqa: E402


def test_no_unresolved_css_variables():
    """Qt's QTextBrowser rich-text engine has no support for CSS custom
    properties (var(--x)) — every occurrence silently fails to apply,
    which is why tables/headings previously had no borders or colors."""
    html = build_help_html()
    assert "var(--" not in html


def test_style_block_defines_every_class_used():
    html = build_help_html()
    assert "<style>" in html
    classes_used = set(re.findall(r'class="([a-zA-Z0-9_ -]+)"', html))
    # class attributes can hold multiple space-separated class names.
    classes_used = {c for group in classes_used for c in group.split()}
    for cls in classes_used:
        assert f".{cls}" in html, f"class {cls!r} is used but never styled"


def test_toc_entries_all_have_a_matching_anchor():
    html = build_help_html()
    for section_id, label in TOC_ENTRIES:
        assert f'id="{section_id}"' in html, f"TOC entry {label!r} has no matching anchor"


@pytest.mark.parametrize(
    "term",
    [
        "Bezier Pen Tool",
        "Dimension Tool",
        "Quick Radial Menu",
        "Fit to Curve",
        "Text &amp; Typography",
        "Display units",
        "New Window",
        "Keybindings dialog",
        "Customize radial menu",
    ],
)
def test_new_feature_documentation_present(term):
    """Content-completeness guard: this session added a bunch of canvas
    features that had no manual coverage at all before."""
    html = build_help_html()
    assert term in html


def test_help_dialog_constructs_without_error(qapp):
    from src.ui.pages.help import HelpDialog

    dlg = HelpDialog(None, None)
    assert dlg._html_content
    assert dlg._toc_list.count() == len(TOC_ENTRIES)


def test_content_font_uses_a_family_fallback_list(qapp):
    """Regression: QFont(str, size) was being passed a comma-separated CSS
    font stack as a single (bogus) family name, which could silently fall
    back to the platform's default font (observed as monospace) instead of
    trying each family in turn."""
    from src.ui.pages.help import HelpDialog

    dlg = HelpDialog(None, None)
    families = dlg._content.font().families()
    assert len(families) > 1


def test_no_single_section_dominates_the_toc():
    """Regression: 'Draft Page' had grown to absorb every new canvas
    feature (Bezier Pen, Dimension, Radial Menu, Path Cleanup, Text tools,
    Layers), making that one section far longer than any other and the
    TOC filter effectively useless for finding any of them individually —
    each now gets its own top-level section, so no one section's content
    should dwarf the rest."""
    from src.ui.pages.help import (
        _build_bezier_pen_tool,
        _build_dimension_tool,
        _build_draft_page,
        _build_layers,
        _build_path_cleanup,
        _build_radial_menu,
        _build_text_tools,
    )

    sizes = {
        "draft-page": len(_build_draft_page()),
        "bezier-pen-tool": len(_build_bezier_pen_tool()),
        "dimension-tool": len(_build_dimension_tool()),
        "radial-menu": len(_build_radial_menu()),
        "path-cleanup": len(_build_path_cleanup()),
        "text-tools": len(_build_text_tools()),
        "layers": len(_build_layers()),
    }
    largest = max(sizes.values())
    total = sum(sizes.values())
    assert largest / total < 0.5


def test_search_box_hints_at_find_in_page(qapp):
    from src.ui.pages.help import HelpDialog

    dlg = HelpDialog(None, None)
    assert "Enter" in dlg._search_box.placeholderText()


@pytest.mark.parametrize(
    "query,expected_label",
    [
        ("polyline", "Draft Page"),
        ("fit", "Draft Page"),
        ("layer", "Layers"),
        ("tangent", "Bezier Pen Tool"),
    ],
)
def test_toc_filter_matches_body_text_not_just_labels(qapp, query, expected_label):
    """Regression: the section-content search sliced between `id="..."`
    and the *same tag's own* `class="section-heading"` attribute (a few
    characters later), instead of the next section's heading — so the
    sliced "body text" was almost empty and body-only terms like
    "polyline" or "fit" never matched anything."""
    from src.ui.pages.help import HelpDialog

    dlg = HelpDialog(None, None)
    dlg._search_box.setText(query)
    visible = [
        dlg._toc_list.item(i).text()
        for i in range(dlg._toc_list.count())
        if not dlg._toc_list.item(i).isHidden()
    ]
    assert expected_label in visible


def test_header_does_not_claim_most_of_the_dialog_height(qapp):
    """Regression: root.addWidget(header) / root.addWidget(splitter) both
    defaulted to stretch 0, so Qt had no basis for keeping the header
    compact — it ballooned to fill most of the dialog instead of the
    content area getting the space."""
    from src.ui.pages.help import HelpDialog

    dlg = HelpDialog(None, None)
    dlg.resize(1100, 750)
    dlg.show()
    layout = dlg.layout()
    assert layout is not None
    item = layout.itemAt(0)
    assert item is not None
    header_widget = item.widget()
    assert header_widget is not None
    assert header_widget.height() < dlg.height() * 0.25


def test_find_in_content_locates_a_term_on_its_own_section(qapp):
    """Enter in the search box should jump to the actual matched text, not
    just filter the TOC list down to a section."""
    from src.ui.pages.help import HelpDialog

    dlg = HelpDialog(None, None)
    dlg._search_box.setText("tangent handles")
    dlg._find_in_content()
    assert dlg._search_box.property("error") is not True
    assert dlg._content.textCursor().hasSelection()
    assert dlg._content.textCursor().selectedText().lower() == "tangent handles"


def test_enter_in_search_box_does_not_close_the_dialog(qapp):
    """Regression: a QPushButton inside a QDialog defaults to autoDefault
    True, so as the dialog's only button (the header's close "X") it
    silently auto-triggered on every Enter press anywhere in the dialog —
    including the search box — closing the manual before a search result
    could ever be seen."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from src.ui.pages.help import HelpDialog

    dlg = HelpDialog(None, None)
    dlg.show()
    dlg._search_box.setText("bezier")
    QTest.keyClick(dlg._search_box, Qt.Key.Key_Return)
    assert dlg.isVisible()


def test_repeated_enter_cycles_through_distinct_matches(qapp):
    """Pressing Enter repeatedly with the same query should advance through
    the document's occurrences rather than reselecting the same spot."""
    from src.ui.pages.help import HelpDialog

    dlg = HelpDialog(None, None)
    dlg._search_box.setText("polyline")
    positions = []
    for _ in range(3):
        dlg._find_in_content()
        positions.append(dlg._content.textCursor().selectionStart())
    assert len(set(positions)) == len(positions)


def test_new_query_after_navigation_finds_from_the_top(qapp):
    """A fresh query should always find the very first match in the
    document, not continue from wherever the cursor was left by a
    previous, unrelated search or TOC click — regardless of how far that
    earlier search had advanced the cursor."""
    from src.ui.pages.help import HelpDialog

    baseline = HelpDialog(None, None)
    baseline._search_box.setText("polyline")
    baseline._find_in_content()
    baseline_pos = baseline._content.textCursor().selectionStart()

    dlg = HelpDialog(None, None)
    dlg._search_box.setText("layers")
    dlg._find_in_content()
    dlg._find_in_content()  # advance the cursor partway through the doc

    dlg._search_box.setText("polyline")  # a brand-new query via _filter_toc
    dlg._find_in_content()
    assert dlg._content.textCursor().selectionStart() == baseline_pos


def test_find_in_content_flags_no_match(qapp):
    from src.ui.pages.help import HelpDialog

    dlg = HelpDialog(None, None)
    dlg._search_box.setText("this text definitely does not appear anywhere")
    dlg._find_in_content()
    assert dlg._search_box.property("error") is True
