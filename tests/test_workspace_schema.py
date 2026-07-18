from __future__ import annotations

from copy import deepcopy

import pytest

from src.backend.model.document import (
    WORKSPACE_SCHEMA_VERSION,
    empty_workspace_document,
    validate_workspace_document,
)


def test_unknown_future_page_state_is_preserved():
    document = empty_workspace_document()
    document["tabs"]["future-page"] = {"new_field": [1, 2, 3]}
    assert validate_workspace_document(document)["tabs"]["future-page"] == {"new_field": [1, 2, 3]}


def test_validation_does_not_mutate_input():
    document = empty_workspace_document()
    original = deepcopy(document)
    validate_workspace_document(document)
    assert document == original


def test_wrong_schema_version_is_rejected():
    document = empty_workspace_document()
    document["schema_version"] = WORKSPACE_SCHEMA_VERSION + 1
    with pytest.raises(ValueError, match="Unsupported workspace schema version"):
        validate_workspace_document(document)


def test_legacy_workspace_is_migrated_to_current_schema():
    document = empty_workspace_document()
    document["schema_version"] = 2
    document.pop("app")
    document["current_tab"] = 2
    migrated = validate_workspace_document(document)
    assert migrated["schema_version"] == WORKSPACE_SCHEMA_VERSION
    assert migrated["app"]["current_tab"] == 2


def test_malformed_top_level_workspace_state_is_rejected():
    document = empty_workspace_document()
    document["app"] = "bad"
    with pytest.raises(ValueError, match="app state"):
        validate_workspace_document(document)
