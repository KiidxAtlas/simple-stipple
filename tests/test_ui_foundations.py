from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from src.ui.style.theme import load_app_qss


def test_dialogs_do_not_add_local_stylesheets():
    """Dialog visuals belong to the shared theme, except the font preview renderer."""
    from pathlib import Path

    dialog_dir = Path(__file__).parents[1] / "src" / "ui" / "widgets" / "dialogs"
    violations = []
    for path in dialog_dir.glob("*.py"):
        if path.name == "text_dialog.py":
            continue
        if ".setStyleSheet(" in path.read_text(encoding="utf-8"):
            violations.append(path.name)
    assert violations == []


def test_dialog_focus_lifecycle_initializes_escapes_and_restores(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog, QLineEdit, QPushButton, QVBoxLayout, QWidget

    from src.ui.components import install_dialog_focus_lifecycle

    host = QWidget()
    host_layout = QVBoxLayout(host)
    invoker = QPushButton("Open")
    host_layout.addWidget(invoker)
    host.show()
    invoker.setFocus()
    qapp.processEvents()

    dialog = QDialog(host)
    dialog.setModal(True)
    dialog_layout = QVBoxLayout(dialog)
    initial = QLineEdit()
    dialog_layout.addWidget(initial)
    install_dialog_focus_lifecycle(dialog, initial, invoker)
    rejected = []
    dialog.rejected.connect(lambda: rejected.append(True))

    dialog.show()
    qapp.processEvents()
    assert initial.hasFocus()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    qapp.processEvents()

    assert rejected == [True]
    assert invoker.hasFocus()
    host.close()


def test_collapsible_headers_and_status_zoom_use_vector_chevrons(qapp):
    from PySide6.QtWidgets import QLabel

    from src.ui.components import CollapsibleSection
    from src.ui.widgets.canvas.status_strip import CanvasStatusStrip

    section = CollapsibleSection("Placement", QLabel("body"), expanded=False)
    assert section._toggle.text() == "Placement"
    assert not section._toggle.icon().isNull()
    section.set_expanded(True)
    assert section._toggle.text() == "Placement"
    assert not section._toggle.icon().isNull()

    strip = CanvasStatusStrip()
    assert strip._zoom_label.text() == "100%"
    assert not strip._zoom_label.icon().isNull()


def test_status_strip_moves_secondary_details_into_tooltip_below_800(qapp):
    from src.ui.widgets.canvas.status_strip import CanvasStatusStrip

    strip = CanvasStatusStrip()
    strip.set_snapshot(
        mode="draw",
        selected_count=2,
        object_count=12,
        precision_text="Grid 1 mm",
        readiness_text="Drawing line",
        cursor_pos=(3.0, 4.0),
    )
    strip.resize(760, 32)
    strip.show()
    qapp.processEvents()

    assert strip._mode_label.isVisible()
    assert strip._cursor_label.isVisible()
    assert strip._readiness_chip.isVisible()
    assert not strip._objects_label.isVisible()
    assert not strip._precision_label.isVisible()
    assert "12 obj" in strip._mode_label.toolTip()
    assert "Grid 1 mm" in strip._mode_label.toolTip()

    strip.resize(900, 32)
    qapp.processEvents()
    assert strip._objects_label.isVisible()
    assert strip._precision_label.isVisible()
    strip.close()


def test_secondary_inspector_becomes_toggleable_drawer_below_1050(qapp):
    from PySide6.QtWidgets import QLabel

    from src.ui.components import content_splitter

    splitter = content_splitter(QLabel("Canvas"), QLabel("Inspector"), sizes=(800, 280))
    splitter.set_responsive_secondary(1, "Inspector")
    splitter.resize(900, 400)
    splitter.show()
    qapp.processEvents()

    assert not splitter._drawer_toggle.isHidden()
    assert splitter.sizes()[1] == 0
    assert splitter._drawer_toggle.text() == "Show Inspector"

    splitter._drawer_toggle.click()
    qapp.processEvents()
    assert splitter.sizes()[1] > 0
    assert splitter._drawer_toggle.text() == "Hide Inspector"

    splitter.resize(1100, 400)
    qapp.processEvents()
    assert splitter._drawer_toggle.isHidden()
    assert splitter.sizes()[1] > 0
    splitter.close()


def test_theme_uses_native_font_and_complete_focus_states():
    qss = load_app_qss()

    assert 'font-family: "Arial"' not in qss
    for selector in (
        "QPushButton:focus",
        "QToolButton:focus",
        "QSlider:focus",
        "QCheckBox:focus",
    ):
        assert selector in qss


def test_precision_controls_have_usable_compact_hit_sizes():
    qss = load_app_qss()

    assert "QSlider {\n    min-height: 28px;" in qss
    assert "QCheckBox {\n    min-height: 28px;" in qss
    assert "width: 18px;\n    height: 18px;" in qss


def test_comfortable_density_enlarges_interaction_targets():
    qss = load_app_qss(density="comfortable")

    assert 'QPushButton[role="primary"] { min-height: 44px; }' in qss
    assert "QCheckBox, QSlider { min-height: 44px; }" in qss


def test_motion_uses_shared_duration_and_honors_reduced_motion(qapp):
    from PySide6.QtWidgets import QLabel

    from src.ui.components import MOTION_DURATION_MS, CollapsibleSection

    assert MOTION_DURATION_MS == 150
    qapp.setProperty("reducedMotion", True)
    section = CollapsibleSection("Advanced", QLabel("body"), expanded=True)
    section.show()
    section.set_expanded(False)
    assert not section._content.isVisible()
    assert section._motion is None
    qapp.setProperty("reducedMotion", False)
