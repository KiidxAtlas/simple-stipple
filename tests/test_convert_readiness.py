"""The Convert page's primary action must never sit enabled with no input.

Every conversion tool previously left its Convert/Fix button fully enabled on
an empty form — clicking it did nothing but flash a status message, a classic
dead-end click. These pin that each tool's button (and the page-level footer
CTA that mirrors it) starts disabled and enables the instant a source path is
entered, matching how the Repository page already gates Pull/Commit/Push.
"""

import pytest

pytest.importorskip("PySide6")

from src.ui.pages.convert import ConvertPage, FixerSubTab, FviSubTab, SvgSubTab, SvgToDxfSubTab


@pytest.mark.parametrize("subtab_cls", [FviSubTab, FixerSubTab, SvgSubTab, SvgToDxfSubTab])
def test_convert_button_disabled_until_source_is_entered(qapp, subtab_cls):
    tab = subtab_cls(None, {})
    assert not tab._btn.isEnabled()

    tab._src_edit.setText("/some/path")
    assert tab._btn.isEnabled()

    tab._src_edit.setText("")
    assert not tab._btn.isEnabled()


def test_footer_cta_reflects_tab_readiness_not_just_running_state(qapp):
    page = ConvertPage(None, {})
    # Freshly switched-to tab with no input: the footer must not be a dead end.
    assert not page._footer_btn.isEnabled()

    page._fvi_subtab._src_edit.setText("/some/path")
    assert page._footer_btn.isEnabled()

    # Switching away and back preserves the (now-ready) state.
    page._on_tool_changed(1)
    page._on_tool_changed(0)
    assert page._footer_btn.isEnabled()
