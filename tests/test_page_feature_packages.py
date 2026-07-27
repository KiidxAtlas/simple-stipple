"""Focused structural checks for top-level product feature packages."""

from __future__ import annotations

from pathlib import Path

from simple_stipple.features.convert import ConvertPage
from simple_stipple.features.convert.page import ConvertPage as ConcreteConvertPage
from simple_stipple.features.convert.tasks import FviSubTab
from simple_stipple.features.draft import DraftPage
from simple_stipple.features.draft.page import DraftPage as ConcreteDraftPage
from simple_stipple.features.draft.session import get_draft_workspace_state
from simple_stipple.features.help import HelpDialog
from simple_stipple.features.repository import RepoPage

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "simple_stipple"
FEATURES = PACKAGE / "features"


def test_feature_packages_export_the_concrete_page_types() -> None:
    assert ConvertPage is ConcreteConvertPage
    assert DraftPage is ConcreteDraftPage
    assert FviSubTab.__module__ == "simple_stipple.features.convert.tasks"
    assert get_draft_workspace_state.__module__ == "simple_stipple.features.draft.session"
    assert HelpDialog.__module__ == "simple_stipple.features.help"
    assert RepoPage.__module__ == "simple_stipple.features.repository"


def test_features_have_one_canonical_module_home() -> None:
    assert (FEATURES / "convert" / "page.py").is_file()
    assert (FEATURES / "draft" / "page.py").is_file()
    assert (FEATURES / "pattern" / "page.py").is_file()
    assert (FEATURES / "trace" / "page.py").is_file()
    assert (FEATURES / "help.py").is_file()
    assert (FEATURES / "repository.py").is_file()
    assert not (PACKAGE / "ui" / "pages").exists()
