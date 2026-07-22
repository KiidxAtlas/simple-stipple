"""Settings schema validation and forward-compatibility tests."""

from src.core.paths import custom_tiles_dir, project_root
from src.core.settings import (
    DEFAULT_CONTEXT_MENU_SECTIONS,
    DEFAULT_DRAW_SIDEBAR_WIDTH,
    DEFAULT_RADIAL_MENU_TOOLS,
    SettingsSchema,
    _migrate_settings,
    validate_settings,
)


def test_settings_schema_backfills_defaults():
    settings = validate_settings({})
    assert settings["unit_system"] == "mm"
    assert settings["draw_sidebar_width"] == DEFAULT_DRAW_SIDEBAR_WIDTH
    assert settings["radial_menu_tools"] == list(DEFAULT_RADIAL_MENU_TOOLS)
    assert settings["context_menu_sections"] == list(DEFAULT_CONTEXT_MENU_SECTIONS)
    assert settings["custom_tiles_dir"] == str(project_root() / "tiles")
    assert custom_tiles_dir(settings["custom_tiles_dir"]) == project_root() / "tiles"
    assert settings["snap_vertex"] is True
    assert settings["snap_equal_length"] is True
    assert settings["snap_axis_alignment"] is True
    assert settings["grid_snap"] is False
    assert settings["interface_density"] == "compact"


def test_invalid_known_setting_resets_without_losing_other_values():
    settings = validate_settings({"draw_sidebar_width": "very wide", "repo_dir": "/tmp/example"})
    assert settings["draw_sidebar_width"] == DEFAULT_DRAW_SIDEBAR_WIDTH
    assert settings["repo_dir"] == "/tmp/example"


def test_unknown_future_settings_are_preserved():
    settings = validate_settings({"future_feature": {"enabled": True}})
    assert settings["future_feature"] == {"enabled": True}


def test_schema_rejects_out_of_range_values_at_validation_boundary():
    settings = validate_settings({"ui_scale": 99.0, "auto_fetch_interval_minutes": 0})
    assert settings["ui_scale"] == SettingsSchema().ui_scale
    assert settings["auto_fetch_interval_minutes"] == 10


def test_schema_rejects_unknown_interface_density():
    assert validate_settings({"interface_density": "tiny"})["interface_density"] == "compact"


def test_legacy_default_shape_toolbar_receives_new_tools():
    migrated = _migrate_settings(
        {
            "draw_sidebar_shape_tools": [
                "rectangle",
                "slot",
                "circle",
                "ellipse",
                "polygon",
            ]
        }
    )
    assert "rounded_rectangle" in migrated["draw_sidebar_shape_tools"]
    assert "star" in migrated["draw_sidebar_shape_tools"]


def test_context_menu_sections_are_filtered_and_keep_view_recovery_core():
    settings = validate_settings({"context_menu_sections": ["transform", "unknown", "transform"]})
    assert settings["context_menu_sections"] == ["transform", "view"]
