"""Focused structural checks for top-level product feature packages."""

from __future__ import annotations

from pathlib import Path

from simple_stipple.features.convert import ConvertPage
from simple_stipple.features.convert.page import ConvertPage as ConcreteConvertPage
from simple_stipple.features.convert.tasks import (
    FixerSubTab,
    FviSubTab,
    SvgSubTab,
    SvgToDxfSubTab,
    _ConversionSubTab,
)
from simple_stipple.features.convert.tasks import FviSubTab as CanonicalFviSubTab
from simple_stipple.features.draft import DraftPage
from simple_stipple.features.draft.page import DraftPage as ConcreteDraftPage
from simple_stipple.features.draft.session import get_draft_workspace_state
from simple_stipple.features.help import HelpDialog, build_help_html
from simple_stipple.features.pattern.layout import (
    build_export_section,
    build_fill_section,
    build_image_engraving_section,
    build_pattern_section,
    build_shape_section,
)
from simple_stipple.features.repository import RepoPage

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "simple_stipple"
FEATURES = PACKAGE / "features"


def test_feature_packages_export_the_concrete_page_types() -> None:
    assert ConvertPage is ConcreteConvertPage
    assert DraftPage is ConcreteDraftPage
    assert FviSubTab is CanonicalFviSubTab
    for task_form in (FviSubTab, FixerSubTab, SvgSubTab, SvgToDxfSubTab, _ConversionSubTab):
        assert task_form.__module__ == "simple_stipple.features.convert.tasks"
    assert get_draft_workspace_state.__module__ == "simple_stipple.features.draft.session"
    assert HelpDialog.__module__ == "simple_stipple.features.help"
    assert 'id="manual-home"' in build_help_html()
    assert RepoPage.__module__ == "simple_stipple.features.repository.page"


def test_features_have_one_canonical_module_home() -> None:
    assert (FEATURES / "convert" / "page.py").is_file()
    assert (FEATURES / "draft" / "page.py").is_file()
    assert (FEATURES / "pattern" / "page.py").is_file()
    assert (FEATURES / "trace" / "page.py").is_file()
    assert (FEATURES / "help" / "__init__.py").is_file()
    assert (FEATURES / "repository" / "page.py").is_file()
    assert not (FEATURES / "repository.py").exists()
    assert not (PACKAGE / "ui" / "pages").exists()


def test_convert_task_forms_are_bounded_by_stable_conversion_responsibility() -> None:
    convert_package = FEATURES / "convert"
    assert (convert_package / "tasks.py").is_file()
    assert not (convert_package / "form_base.py").exists()
    assert not (convert_package / "svg_tasks.py").exists()


def test_pattern_layout_sections_have_one_canonical_layout_home() -> None:
    pattern_package = FEATURES / "pattern"
    assert (pattern_package / "layout.py").is_file()
    assert not (pattern_package / "layout_sections.py").exists()
    for builder in (
        build_shape_section,
        build_pattern_section,
        build_fill_section,
        build_image_engraving_section,
        build_export_section,
    ):
        assert builder.__module__ == "simple_stipple.features.pattern.layout"


def test_help_package_keeps_manual_and_dialog_in_one_module() -> None:
    help_package = FEATURES / "help"
    assert {path.name for path in help_package.iterdir()} >= {"__init__.py", "dialog.py"}
    # The content builders exist only to feed HelpDialog, so the manual and its
    # dialog share one module instead of a two-file split.
    dialog_source = (help_package / "dialog.py").read_text(encoding="utf-8")
    assert "def build_help_html" in dialog_source
    assert "class HelpDialog" in dialog_source
