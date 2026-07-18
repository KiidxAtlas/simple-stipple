"""Persistent, user-configurable Trace defaults (Settings dialog ->
settings["trace_defaults"]) — a freshly-opened image or a cleared
workspace should honor an overridden default (e.g. a higher max
resolution), and fall back to the built-in value when nothing is
configured.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from src.ui.pages.trace.form import TRACE_DEFAULTS, trace_default


def test_trace_default_falls_back_to_the_built_in_value_when_unset():
    assert trace_default(None, "max_res") == TRACE_DEFAULTS["max_res"]
    assert trace_default({}, "blur") == TRACE_DEFAULTS["blur"]


def test_trace_default_honors_a_configured_override():
    settings = {"trace_defaults": {"max_res": "3000"}}
    assert trace_default(settings, "max_res") == "3000"
    # Unrelated keys still fall back to their built-ins.
    assert trace_default(settings, "blur") == TRACE_DEFAULTS["blur"]


def test_new_trace_page_starts_with_the_configured_default_resolution(qapp):
    from src.ui.pages.trace.tab import TracePage

    settings = {"trace_defaults": {"max_res": "3000", "simplify": "0.5"}}
    page = TracePage(None, settings)
    assert page._max_res.text() == "3000"
    assert page._simplify.text() == "0.5"


def test_clearing_the_workspace_reapplies_the_configured_default(qapp):
    from src.ui.pages.trace.session import clear_trace_workspace_state
    from src.ui.pages.trace.tab import TracePage

    settings = {"trace_defaults": {"max_res": "3000"}}
    page = TracePage(None, settings)
    page._max_res.setText("1200")  # simulate the user changing it mid-session

    clear_trace_workspace_state(page)

    assert page._max_res.text() == "3000"


def test_settings_dialog_save_persists_a_trace_default_override(qapp, monkeypatch):
    import src.ui.widgets.dialogs.settings_dialog as settings_dialog_mod
    from src.ui.widgets.dialogs.settings_dialog import SettingsDialog

    monkeypatch.setattr(settings_dialog_mod, "save_settings", lambda d: None)

    # A non-empty starting dict -- SettingsDialog does `settings or {}`,
    # which would silently swap in an unrelated fresh dict if `settings`
    # itself were empty/falsy, same as the real app (load_settings() always
    # returns a dict with some defaults already populated).
    #
    # SettingsDialog deep-copies its settings on construction (so Cancel
    # can't leak sub-dialog changes into the caller's live dict — see
    # SettingsDialog.__init__) — the caller adopts the result via
    # `dlg._settings` after Accept, same as the real app's `_open_settings`.
    settings: dict = {"unit_system": "mm"}
    dlg = SettingsDialog(None, settings)
    dlg._trace_default_entries["max_res"].setText("3000")
    dlg._save()

    assert dlg._settings["trace_defaults"]["max_res"] == "3000"
    assert "trace_defaults" not in settings  # caller's own dict is untouched


def test_settings_dialog_save_clears_override_when_field_left_blank(qapp, monkeypatch):
    import src.ui.widgets.dialogs.settings_dialog as settings_dialog_mod
    from src.ui.widgets.dialogs.settings_dialog import SettingsDialog

    monkeypatch.setattr(settings_dialog_mod, "save_settings", lambda d: None)

    settings = {"trace_defaults": {"max_res": "3000"}}
    dlg = SettingsDialog(None, settings)
    dlg._trace_default_entries["max_res"].setText("")
    dlg._save()

    assert "max_res" not in dlg._settings.get("trace_defaults", {})
