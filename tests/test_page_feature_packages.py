"""Focused structural checks for top-level product feature packages."""

from __future__ import annotations

from pathlib import Path

from simple_stipple.features.convert import ConvertPage
from simple_stipple.features.convert.page import ConvertPage as ConcreteConvertPage
from simple_stipple.features.convert.tasks import FviSubTab as CanonicalFviSubTab
from simple_stipple.features.convert.tasks import FviSubTab
from simple_stipple.features.draft import DraftPage
from simple_stipple.features.draft.page import DraftPage as ConcreteDraftPage
from simple_stipple.features.draft.session import get_draft_workspace_state
from simple_stipple.features.help import HelpDialog, build_help_html
from simple_stipple.features.repository import RepoPage

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "simple_stipple"
FEATURES = PACKAGE / "features"


def test_feature_packages_export_the_concrete_page_types() -> None:
    assert ConvertPage is ConcreteConvertPage
    assert DraftPage is ConcreteDraftPage
    assert FviSubTab is CanonicalFviSubTab
    assert FviSubTab.__module__ == "simple_stipple.features.convert.tasks"
    assert get_draft_workspace_state.__module__ == "simple_stipple.features.draft.session"
    assert HelpDialog.__module__ == "simple_stipple.features.help"
    assert 'id="manual-home"' in build_help_html()
    assert RepoPage.__module__ == "simple_stipple.features.repository"


def test_features_have_one_canonical_module_home() -> None:
    assert (FEATURES / "convert" / "page.py").is_file()
    assert (FEATURES / "draft" / "page.py").is_file()
    assert (FEATURES / "pattern" / "page.py").is_file()
    assert (FEATURES / "trace" / "page.py").is_file()
    assert (FEATURES / "help" / "__init__.py").is_file()
    assert (FEATURES / "repository.py").is_file()
    assert not (PACKAGE / "ui" / "pages").exists()


def test_convert_task_forms_are_bounded_by_stable_conversion_responsibility() -> None:
    forms = FEATURES / "convert" / "task_forms"
    assert {path.stem for path in forms.glob("*.py")} >= {"base", "fvi", "repair", "svg"}
    assert all(
        len(path.read_text(encoding="utf-8").splitlines()) <= 700 for path in forms.glob("*.py")
    )


def test_help_package_separates_content_assembly_and_dialog_without_god_modules() -> None:
    help_package = FEATURES / "help"
    assert {path.name for path in help_package.iterdir()} >= {"__init__.py", "dialog.py", "content"}
    assert all(
        len(path.read_text(encoding="utf-8").splitlines()) <= 700
        for path in help_package.rglob("*.py")
    )
