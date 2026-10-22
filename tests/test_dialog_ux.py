"""Dialog UX: inline validation, safe defaults, deliberate focus and clear labels."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox, QMessageBox, QPushButton, QSizePolicy

from simple_stipple.canvas.dialogs import customize_dialogs, keybindings_dialog
from simple_stipple.canvas.dialogs.keybindings_dialog import KeybindingsDialog, is_valid_shortcut
from simple_stipple.canvas.dialogs.text_dialog import AddTextDialog
from simple_stipple.core.formats.dxf import DxfImportReport
from simple_stipple.platform.settings import (
    DEFAULT_DRAW_SIDEBAR_PATH_TOOLS,
    DEFAULT_DRAW_SIDEBAR_SECTIONS,
    DEFAULT_RADIAL_MENU_TOOLS,
)
from simple_stipple.ui.dialogs import settings_dialog
from simple_stipple.ui.dialogs.files import DxfImportPreviewDialog, VectorImportModeDialog
from simple_stipple.ui.dialogs.settings_dialog import SettingsDialog
from simple_stipple.ui.dialogs.welcome import WELCOME_CHOICES, WelcomeDialog


@pytest.fixture(autouse=True)
def no_blocking_boxes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A modal box would hang the suite; dialogs under test must not need one."""

    def fail(*_args, **_kwargs):
        raise AssertionError("unexpected modal QMessageBox")

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(fail))
    monkeypatch.setattr(QMessageBox, "exec", fail)


# ── Settings ─────────────────────────────────────────────────────────────────


@pytest.fixture
def settings_ui(qtbot, monkeypatch):
    saved: list[dict] = []
    monkeypatch.setattr(settings_dialog, "save_settings", lambda data: saved.append(dict(data)))
    dialog = SettingsDialog(settings={})
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog, saved


@pytest.mark.parametrize("bad", ["abc", "-5", "nan", "inf", "2000000"])
def test_settings_rejects_invalid_machine_numbers_inline(settings_ui, bad) -> None:
    dialog, saved = settings_ui
    entry = dialog._production_entries["machine_kerf_mm"]
    entry.setValidator(None)  # the typing validator is bypassed by paste/programmatic text
    entry.setText(bad)

    dialog._save()

    assert saved == []
    assert dialog.result() != dialog.DialogCode.Accepted
    assert entry.property("error") is True
    assert "Kerf" in dialog._error_label.text()
    assert not dialog._error_label.isHidden()
    assert dialog.focusWidget() is entry
    assert "machine_kerf_mm" not in dialog._settings

    entry.setText("0.2")
    entry.textEdited.emit("0.2")
    assert dialog._error_label.isHidden()
    dialog._save()
    assert saved[-1]["machine_kerf_mm"] == pytest.approx(0.2)


def test_settings_blank_machine_number_means_not_configured(settings_ui) -> None:
    dialog, saved = settings_ui
    dialog._production_entries["machine_bed_width_mm"].setText("")
    dialog._save()
    assert saved[-1]["machine_bed_width_mm"] == 0.0


def test_settings_error_reveals_field_hidden_by_section_filter(settings_ui) -> None:
    dialog, saved = settings_ui
    entry = dialog._production_entries["machine_feed_rate_mm_s"]
    entry.setValidator(None)
    entry.setText("fast")
    dialog._category_combo.setCurrentText("Accessibility")
    assert not entry.isVisibleTo(dialog)

    dialog._save()

    assert saved == []
    assert entry.isVisibleTo(dialog)
    assert dialog.focusWidget() is entry


def test_settings_scalar_error_is_inline_and_keeps_tooltip(settings_ui) -> None:
    dialog, saved = settings_ui
    entry = dialog._scalar_entries["grid_spacing"]
    entry.setValidator(None)
    entry.setText("0")
    dialog._save()
    assert saved == []
    assert "Default grid spacing" in dialog._error_label.text()
    assert dialog.focusWidget() is entry


# ── Import dialogs ───────────────────────────────────────────────────────────


def test_import_dialogs_name_their_action(qtbot) -> None:
    vector = VectorImportModeDialog("/tmp/a.svg", format_name="SVG", has_existing_geometry=False)
    qtbot.addWidget(vector)
    dxf = DxfImportPreviewDialog(
        "/tmp/a.dxf",
        {"0": [[(0.0, 0.0), (1.0, 1.0)]]},
        DxfImportReport(1, {}, {}, 0, {"0": 1}),
        has_existing_geometry=False,
    )
    qtbot.addWidget(dxf)
    for dialog in (vector, dxf):
        box = dialog.findChild(QDialogButtonBox)
        assert box.button(QDialogButtonBox.StandardButton.Ok).text() == "Import"


def test_dxf_import_without_layers_shows_inline_error_and_focuses_list(qtbot) -> None:
    dialog = DxfImportPreviewDialog(
        "/tmp/a.dxf",
        {"0": [[(0.0, 0.0), (1.0, 1.0)]]},
        DxfImportReport(1, {}, {}, 0, {"0": 1}),
        has_existing_geometry=False,
    )
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._layers.item(0).setCheckState(Qt.CheckState.Unchecked)

    dialog._button_box.button(QDialogButtonBox.StandardButton.Ok).click()

    assert dialog.result() != dialog.DialogCode.Accepted
    assert "at least one layer" in dialog._error_label.text()
    assert not dialog._error_label.isHidden()
    assert dialog.focusWidget() is dialog._layers

    dialog._layers.item(0).setCheckState(Qt.CheckState.Checked)
    assert dialog._error_label.isHidden()
    dialog._button_box.button(QDialogButtonBox.StandardButton.Ok).click()
    assert dialog.result() == dialog.DialogCode.Accepted


# ── Customize dialogs ────────────────────────────────────────────────────────


def test_draw_sidebar_apply_never_substitutes_defaults(qtbot) -> None:
    dialog = customize_dialogs.DrawSidebarCustomizeDialog(
        sections=list(DEFAULT_DRAW_SIDEBAR_SECTIONS),
        path_tools=list(DEFAULT_DRAW_SIDEBAR_PATH_TOOLS),
    )
    qtbot.addWidget(dialog)
    for i in range(dialog._path_list.count()):
        dialog._path_list.item(i).setCheckState(Qt.CheckState.Unchecked)
    assert not dialog._apply_btn.isEnabled()

    dialog._apply()  # reachable via Enter/programmatic callers

    assert dialog.result() != dialog.DialogCode.Accepted
    assert "path tool" in dialog._hint.text()
    assert dialog.get_path_tools() == list(DEFAULT_DRAW_SIDEBAR_PATH_TOOLS)  # unchanged


def test_radial_menu_apply_blocks_too_few_tools(qtbot) -> None:
    dialog = customize_dialogs.RadialMenuDialog(tools=list(DEFAULT_RADIAL_MENU_TOOLS))
    qtbot.addWidget(dialog)
    for i in range(dialog._list.count()):
        dialog._list.item(i).setCheckState(Qt.CheckState.Unchecked)
    dialog._apply()
    assert dialog.result() != dialog.DialogCode.Accepted
    assert "required" in dialog._hint.text()


# ── Keyboard shortcuts ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,valid",
    [
        ("", True),
        ("Ctrl+K", True),
        ("ctrl+shift+d", True),
        ("Ctrl+K, Ctrl+S", True),
        ("Ctrl+,", True),
        ("Ctrl+,, Ctrl+K", True),
        ("Ctrl+Shft+K, Ctrl+S", False),
        ("garbage", False),
        ("Ctrl+Shft+K", False),
        ("Ctrl+K, foo", False),
        ("Ctrl+", False),
    ],
)
def test_shortcut_validity(text: str, valid: bool) -> None:
    assert is_valid_shortcut(text) is valid


def test_keybindings_reject_malformed_shortcut_inline(qtbot) -> None:
    dialog = KeybindingsDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    key = "app.settings"
    dialog._entries[key].setText("Ctrl+Shft+K")

    dialog._apply()

    assert dialog.result() != dialog.DialogCode.Accepted
    assert not dialog._status.isHidden()
    assert dialog._entries[key].property("error") is True
    assert dialog.focusWidget() is dialog._entries[key]

    dialog._entries[key].setText("Ctrl+Alt+Shift+9")
    dialog._entries[key].textEdited.emit("Ctrl+Alt+Shift+9")
    assert dialog._status.isHidden()
    dialog._apply()
    assert dialog.result() == dialog.DialogCode.Accepted
    assert dialog.get_keybindings()[key] == "Ctrl+Alt+Shift+9"


def test_keybindings_duplicate_is_inline_and_focuses_conflict(qtbot) -> None:
    dialog = KeybindingsDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._entries["app.settings"].setText(dialog._entries["workspace.open"].text())
    dialog._filter.setText("zzz-no-match")  # hidden rows must still be revealed
    dialog._apply()
    assert dialog.result() != dialog.DialogCode.Accepted
    assert "only one action" in dialog._status.text()
    assert dialog._filter.text() == ""
    assert dialog.focusWidget() in (
        dialog._entries["app.settings"],
        dialog._entries["workspace.open"],
    )


def test_keybindings_reset_all_confirms_and_defaults_to_cancel(qtbot, monkeypatch) -> None:
    dialog = KeybindingsDialog()
    qtbot.addWidget(dialog)
    key = "app.settings"
    dialog._entries[key].setText("Ctrl+Alt+9")
    asked: list[dict] = []

    def decline(parent, title, message, **kwargs):
        asked.append(kwargs)
        return False

    monkeypatch.setattr(keybindings_dialog, "confirm", decline)
    dialog._reset_all()
    assert dialog._entries[key].text() == "Ctrl+Alt+9"
    assert len(asked) == 1
    assert "default" not in asked[0]  # helper's safe default (Cancel) is kept

    monkeypatch.setattr(keybindings_dialog, "confirm", lambda *a, **k: True)
    dialog._reset_all()
    assert dialog._entries[key].text() == dialog._defaults[key]


def test_keybindings_reset_all_is_silent_when_nothing_differs(qtbot, monkeypatch) -> None:
    dialog = KeybindingsDialog()
    qtbot.addWidget(dialog)
    monkeypatch.setattr(
        keybindings_dialog,
        "confirm",
        lambda *a, **k: pytest.fail("nothing to reset; no prompt expected"),
    )
    dialog._reset_all()


# ── Text, welcome ────────────────────────────────────────────────────────────


def test_text_editor_grows_with_dialog_and_actions_are_named(qtbot) -> None:
    dialog = AddTextDialog()
    qtbot.addWidget(dialog)
    editor = dialog._text_edit
    assert editor.minimumHeight() >= 70
    assert editor.maximumHeight() > 70
    assert editor.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Expanding
    assert dialog._ok_btn.text() == "Add Text"
    dialog.set_values({"text": "hi"})
    assert dialog._ok_btn.text() == "Update Text"


def test_welcome_focuses_first_choice_and_enter_uses_focused_choice(qtbot) -> None:
    dialog = WelcomeDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    choices = [
        b
        for b in dialog.findChildren(QPushButton)
        if b.text() in {text for _id, text, _desc in WELCOME_CHOICES}
    ]
    assert len(choices) == len(WELCOME_CHOICES)
    assert all(b.autoDefault() for b in choices)
    qtbot.waitUntil(lambda: choices[0].hasFocus(), timeout=2000)

    choices[2].setFocus()
    qtbot.keyClick(choices[2], Qt.Key.Key_Return)
    assert dialog.choice == WELCOME_CHOICES[2][0]
