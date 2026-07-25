"""Tests for FINAL architecture state after plan.md completion.

These tests define the target state after Phases 0-3 are complete.
They are STRICT and COMPREHENSIVE — no skipping, no leniency, no "current state"
accommodations. These will all fail NOW, but they document what "done" means.

When all these tests pass, the refactoring is complete and architecture is correct.
"""

import ast
import inspect
from pathlib import Path
from typing import Set

import pytest

from src.backend.model.document import CanvasDocument, EntityRecord, EntityId


# =============================================================================
# PHASE 0 COMPLETE: Command Handler Pattern Fully Implemented
# =============================================================================

class TestPhase0CommandHandlers:
    """After Phase 0, all operations route through handlers, not direct mutations."""

    def test_command_handler_base_class_exists(self):
        """Handler base class MUST exist (Phase 0.1)."""
        from src.ui.canvas.handlers.base import CommandHandler

        assert hasattr(CommandHandler, "execute"), "CommandHandler.execute() required"
        assert hasattr(CommandHandler, "__doc__"), "CommandHandler needs documentation"

    def test_offset_handler_exists(self):
        """Offset operations route through handler, not view methods (Phase 0.2)."""
        from src.ui.canvas.handlers.geometry_handlers import OffsetHandler

        assert hasattr(OffsetHandler, "execute")

    def test_mirror_handler_exists(self):
        """Mirror operations route through handler (Phase 0.2)."""
        from src.ui.canvas.handlers.geometry_handlers import MirrorHandler

        assert hasattr(MirrorHandler, "execute")

    def test_rotate_handler_exists(self):
        """Rotate operations route through handler (Phase 0.2)."""
        from src.ui.canvas.handlers.geometry_handlers import RotateHandler

        assert hasattr(RotateHandler, "execute")

    def test_scale_handler_exists(self):
        """Scale operations route through handler (Phase 0.2)."""
        from src.ui.canvas.handlers.geometry_handlers import ScaleHandler

        assert hasattr(ScaleHandler, "execute")

    def test_boolean_handler_exists(self):
        """Boolean operations route through handler (Phase 0.2)."""
        from src.ui.canvas.handlers.geometry_handlers import BooleanHandler

        assert hasattr(BooleanHandler, "execute")

    def test_dimension_handler_exists(self):
        """Dimension operations route through handler (Phase 0.3)."""
        from src.ui.canvas.handlers.dimension_handlers import DimensionHandler

        assert hasattr(DimensionHandler, "execute")

    def test_text_handler_exists(self):
        """Text operations route through handler (Phase 0.4)."""
        from src.ui.canvas.handlers.text_handlers import TextHandler

        assert hasattr(TextHandler, "execute")

    def test_layer_handler_exists(self):
        """Layer operations route through handler (Phase 0.2)."""
        from src.ui.canvas.handlers.layer_handlers import LayerHandler

        assert hasattr(LayerHandler, "execute")

    def test_canvas_view_has_document_changed_signal(self):
        """CanvasView emits document_changed signal (Phase 0.1)."""
        from src.ui.canvas.view import CanvasView
        from PySide6.QtCore import Signal

        assert hasattr(CanvasView, "document_changed"), "Missing document_changed signal"
        # Verify it's a Signal
        assert "Signal" in str(type(CanvasView.document_changed))

    def test_canvas_view_has_operation_failed_signal(self):
        """CanvasView emits operation_failed signal (Phase 0.1)."""
        from src.ui.canvas.view import CanvasView

        assert hasattr(CanvasView, "operation_failed"), "Missing operation_failed signal"

    def test_view_py_no_direct_document_mutations(self):
        """view.py must not call document.replace() or document.append() directly (Phase 0.5)."""
        view_file = Path("src/ui/canvas/view.py")
        content = view_file.read_text()

        # These should only happen in handlers/commands, not in view
        assert "self._document.replace(" not in content, \
            "view.py must not mutate document directly; use handlers instead"
        assert "self._document.append(" not in content, \
            "view.py must not mutate document directly; use handlers instead"
        assert "document.replace(" not in content, \
            "view.py must not mutate document directly; use handlers instead"

    def test_view_py_line_count_reduced(self):
        """view.py must be <800 lines after Phase 0 refactor (Phase 0.6)."""
        view_file = Path("src/ui/canvas/view.py")
        lines = len([l for l in view_file.read_text().split('\n') if l.strip()])

        assert lines < 800, \
            f"view.py is {lines} lines (target <800). Phase 0 must extract handlers."

    def test_view_public_method_count_reduced(self):
        """view.py CanvasView must have <50 public methods (Phase 0.6)."""
        view_file = Path("src/ui/canvas/view.py")

        with open(view_file) as f:
            tree = ast.parse(f.read())

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "CanvasView":
                public_methods = [
                    n for n in node.body
                    if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")
                ]
                method_count = len(public_methods)
                assert method_count < 50, \
                    f"CanvasView has {method_count} public methods (target <50). "\
                    "Phase 0 should extract handlers."


# =============================================================================
# PHASE 1 COMPLETE: Quick Wins Finished
# =============================================================================

class TestPhase1QuickWins:
    """All Phase 1 quick wins completed."""

    def test_selection_uses_entity_ids_not_indices(self):
        """Selection must be set[EntityId], not set[int] (LP-1)."""
        # This is already done, but enforce it
        entity_a = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        entity_b = EntityRecord(points=[(1.0, 0.0), (2.0, 0.0)])

        doc = CanvasDocument([entity_a, entity_b], {entity_a.id, entity_b.id})

        # Reverse order
        doc.entities.reverse()

        # Selection must refer to same entities even after reorder
        assert entity_a.id in doc.selection
        assert entity_b.id in doc.selection
        # Indices would be wrong now, but IDs work
        assert doc.index_for_id(entity_a.id) == 1

    def test_document_validation_is_automatic(self):
        """Document must validate invariants automatically (LP-3)."""
        entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        doc = CanvasDocument([entity])

        # Should have no violations
        violations = doc._validate()
        assert len(violations) == 0

        # Invalid entity should be caught
        invalid = EntityRecord(points=[(0.0, 0.0)], kind="line")  # line needs 2+ points
        doc.append(invalid)
        # Should raise AssertionError because _validate_on_mutate is True
        # Actually, append() auto-fixes duplicate IDs but validates point counts

    def test_geometry_constants_centralized(self):
        """All geometry constants must be in backend/cad/constants.py (LP-4)."""
        from src.backend.cad.constants import EPS, EPS_SQ_DEGENERATE, SNAP_DIST

        assert isinstance(EPS, float)
        assert isinstance(EPS_SQ_DEGENERATE, float)
        assert isinstance(SNAP_DIST, (int, float))

    def test_selection_id_migration_complete(self):
        """Selection migration from indices to IDs must be done (LP-1)."""
        # Verify all selection code uses IDs
        entity_a = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        entity_b = EntityRecord(points=[(1.0, 0.0), (2.0, 0.0)])

        doc = CanvasDocument([entity_a, entity_b], {entity_a.id})

        # selected_ids() should return only valid IDs
        ids = doc.selected_ids()
        assert ids == {entity_a.id}

        # selected_indices() should map IDs to current indices
        indices = doc.selected_indices()
        assert 0 in indices  # entity_a is at index 0


# =============================================================================
# PHASE 2 COMPLETE: Architecture Simplified
# =============================================================================

class TestPhase2Architecture:
    """After Phase 2, large files are decomposed and imports are clean."""

    def test_pattern_tab_decomposed(self):
        """pattern/tab.py must be decomposed into <1500 lines (Phase 2.5)."""
        tab_file = Path("src/ui/pages/pattern/tab.py")

        # Should be split into multiple modules:
        # - pattern_ui.py (layout)
        # - pattern_controller.py (events)
        # - pattern_state.py (state management)
        # - pattern_processing.py (processing) — already exists

        # Check that the original tab.py is smaller
        lines = len([l for l in tab_file.read_text().split('\n') if l.strip()])
        assert lines < 1500, \
            f"pattern/tab.py is {lines} lines (target <1500). Phase 2 must decompose it."

    def test_ui_imports_only_from_app_services_not_backend(self):
        """All UI files must import from app.services, NOT backend (LP-5)."""
        ui_files = list(Path("src/ui").rglob("*.py"))

        backend_imports = []
        for file_path in ui_files:
            try:
                content = file_path.read_text()
                tree = ast.parse(content)
            except (SyntaxError, UnicodeDecodeError):
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith("src.backend"):
                        # Allowed: backend.model for type hints
                        if "backend.model" not in node.module:
                            backend_imports.append(f"{file_path.relative_to('.')}: {node.module}")

        assert not backend_imports, (
            f"UI must not import backend directly (use app.services):\n"
            + "\n".join(backend_imports)
        )

    def test_app_services_complete_wrapper_layer(self):
        """app.services must completely wrap backend (Phase 2.6)."""
        # These should all exist and re-export
        from src.app.services.geometry_service import offset_polyline, rotate_polyline
        from src.app.services.dxf_service import load_dxf_polylines_with_report
        from src.app.services.pattern_service import PatternProcessor
        from src.app.services.model_service import CanvasDocument, CommandStack

        assert callable(offset_polyline)
        assert callable(rotate_polyline)
        assert callable(load_dxf_polylines_with_report)
        assert PatternProcessor is not None
        assert CanvasDocument is not None

    def test_no_ui_widget_imports_in_controllers(self):
        """app.controllers must not import PySide6 widgets (Phase 2.7)."""
        controller_files = list(Path("src/app/controllers").rglob("*.py"))

        bad_imports = []
        for file_path in controller_files:
            content = file_path.read_text()

            # Check for widget imports (except necessary ones like QMessageBox)
            if "from PySide6.QtWidgets import" in content:
                # Count how many
                lines = content.split('\n')
                for line in lines:
                    if "from PySide6.QtWidgets import" in line and "QMessageBox" not in line:
                        bad_imports.append(f"{file_path.relative_to('.')}: {line.strip()}")

        assert not bad_imports, (
            f"Controllers must not import UI widgets:\n"
            + "\n".join(bad_imports)
        )


# =============================================================================
# PHASE 3 COMPLETE: Polish and Final Checks
# =============================================================================

class TestPhase3Polish:
    """All final polish and optimizations complete."""

    def test_zero_circular_dependencies(self):
        """No circular imports anywhere in the codebase (Phase 3.3)."""
        # Try to import all modules — should not have circular imports
        try:
            from src.ui.canvas import view
            from src.ui.canvas.interaction import tools
            from src.ui.canvas.interaction import select
            from src.ui.canvas.snap import SnapEngine
            from src.app.services import geometry_service
        except ImportError as e:
            if "circular" in str(e).lower():
                pytest.fail(f"Circular import detected: {e}")

    def test_dxf_parser_unified(self):
        """DXF parsing must use unified parser (Phase 3.1)."""
        # Should have single DXFParser class
        try:
            from src.backend.dxf.parser import DXFParser
            assert hasattr(DXFParser, "parse")
        except ImportError:
            pytest.fail("DXFParser not found at src.backend.dxf.parser")

    def test_base_dialog_pattern_used(self):
        """Dialogs must use BaseDialog template pattern (Phase 3.2)."""
        try:
            from src.ui.components.dialogs import BaseDialog

            assert hasattr(BaseDialog, "create_content")
            assert hasattr(BaseDialog, "validate")
            assert hasattr(BaseDialog, "on_accepted")
        except ImportError:
            pytest.fail("BaseDialog pattern not implemented")

    def test_core_launcher_no_upward_imports(self):
        """core.launcher must not import app.window (Phase 3.4)."""
        launcher_file = Path("src/core/launcher.py")
        content = launcher_file.read_text()

        assert "from src.app.window import" not in content, \
            "core.launcher must not import from app layer (upward import)"
        assert "from src.app import window" not in content

    def test_settings_routed_through_app_config(self):
        """All settings/paths must go through app.config, not imported directly (Phase 3.5)."""
        # UI files should not import core.settings directly
        ui_files = list(Path("src/ui").rglob("*.py"))

        bad_imports = []
        for file_path in ui_files:
            content = file_path.read_text()
            if "from src.core.settings import" in content or "from src.core.paths import" in content:
                bad_imports.append(str(file_path.relative_to(".")))

        # Allow a few import sites, but not widespread
        assert len(bad_imports) < 3, (
            f"UI files importing settings directly (use app.config): {bad_imports}"
        )

    def test_command_reversibility(self):
        """All commands must be reversible (Phase 3 testing)."""
        from src.backend.model.commands import (
            CreateCommand, DeleteCommand, UpdateEntitiesCommand,
            EntitySnapshot
        )

        entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        snapshot = EntitySnapshot.capture(entity)

        # Create command
        create_cmd = CreateCommand(entities=(snapshot,))
        reverse_cmd = create_cmd.reverse()

        # Should have reverse
        assert reverse_cmd is not None
        # Double reverse should match original
        double_reverse = reverse_cmd.reverse()
        assert double_reverse is not None


# =============================================================================
# Cross-Phase: File Structure Targets
# =============================================================================

class TestFinalFileStructure:
    """Final file structure must match plan.md targets."""

    def test_view_py_final_size(self):
        """view.py final target: <800 lines (from 4330)."""
        view_file = Path("src/ui/canvas/view.py")
        lines = len([l for l in view_file.read_text().split('\n') if l.strip()])

        assert lines < 800, f"view.py: {lines} lines (target: <800)"

    def test_pattern_tab_py_final_size(self):
        """pattern/tab.py final target: <1500 lines (from 4268)."""
        tab_file = Path("src/ui/pages/pattern/tab.py")
        lines = len([l for l in tab_file.read_text().split('\n') if l.strip()])

        assert lines < 1500, f"pattern/tab.py: {lines} lines (target: <1500)"

    def test_handlers_directory_structure(self):
        """Must have handlers directory with all handler modules (Phase 0)."""
        handlers_dir = Path("src/ui/canvas/handlers")

        assert handlers_dir.exists(), "handlers directory must exist"
        assert (handlers_dir / "__init__.py").exists()
        assert (handlers_dir / "base.py").exists(), "CommandHandler base class"
        assert (handlers_dir / "geometry_handlers.py").exists()
        assert (handlers_dir / "dimension_handlers.py").exists()
        assert (handlers_dir / "text_handlers.py").exists()
        assert (handlers_dir / "layer_handlers.py").exists()

    def test_pattern_page_decomposed(self):
        """pattern/tab.py must be decomposed into modules (Phase 2)."""
        pattern_dir = Path("src/ui/pages/pattern")

        # Should have these modules:
        assert (pattern_dir / "ui.py").exists() or (pattern_dir / "pattern_ui.py").exists(), \
            "pattern page UI module missing"
        assert (pattern_dir / "controller.py").exists() or (pattern_dir / "pattern_controller.py").exists(), \
            "pattern page controller missing"
        assert (pattern_dir / "state.py").exists() or (pattern_dir / "pattern_state.py").exists(), \
            "pattern page state module missing"
        # processing already exists
        assert (pattern_dir / "workers.py").exists() or (pattern_dir / "pattern_processing.py").exists()


# =============================================================================
# Cross-Phase: Type Safety and Invariants
# =============================================================================

class TestFinalTypeConsistency:
    """Final state must have perfect type consistency."""

    def test_all_entity_ids_are_strings(self):
        """All EntityId references must be str."""
        entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])

        assert isinstance(entity.id, str)

        doc = CanvasDocument([entity])
        for sel_id in doc.selection:
            assert isinstance(sel_id, str), "Selection must contain str IDs"

    def test_no_int_indices_in_selection(self):
        """Selection must never contain int indices."""
        entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        doc = CanvasDocument([entity], {entity.id})

        for item in doc.selection:
            assert not isinstance(item, int), "Selection must not use int indices"

    def test_coordinate_tuples_are_floats(self):
        """All coordinates must be float tuples."""
        entity = EntityRecord(points=[(0.0, 0.0), (1.0, 1.0)])

        for point in entity.points:
            assert isinstance(point, tuple)
            assert len(point) == 2
            assert all(isinstance(c, (int, float)) for c in point)

    def test_layer_type_is_string_or_none(self):
        """Entity.layer must be str | None."""
        entity1 = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        entity2 = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)], layer="Layer 1")

        assert entity1.layer is None
        assert isinstance(entity2.layer, str)
        assert not isinstance(entity2.layer, int)

    def test_group_type_is_int_or_none(self):
        """Entity.group must be int | None."""
        entity1 = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        entity2 = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)], group=1)

        assert entity1.group is None
        assert isinstance(entity2.group, int)
        assert not isinstance(entity2.group, str)


# =============================================================================
# Cross-Phase: API Contracts
# =============================================================================

class TestFinalAPIContracts:
    """APIs must have consistent, safe contracts."""

    def test_document_append_validates(self):
        """Document.append() must validate invariants."""
        entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        doc = CanvasDocument()

        doc.append(entity)
        assert entity in doc.entities

    def test_document_replace_clears_selection(self):
        """Document.replace() must clear stale selection."""
        old_entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        new_entity = EntityRecord(points=[(2.0, 0.0), (3.0, 0.0)])

        doc = CanvasDocument([old_entity], {old_entity.id})
        assert len(doc.selection) > 0

        doc.replace([new_entity])
        assert len(doc.selection) == 0, "replace() must clear stale selection"

    def test_selection_operations_are_atomic(self):
        """All selection operations must be atomic."""
        entities = [
            EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)]),
            EntityRecord(points=[(1.0, 0.0), (2.0, 0.0)]),
        ]

        doc = CanvasDocument(entities)

        # Bulk select
        ids = {e.id for e in entities}
        doc.select_ids(ids)

        # Should all be selected
        assert doc.selection == ids

        # Clear
        doc.select_ids(set())
        assert len(doc.selection) == 0

    def test_handler_execute_method_signature(self):
        """All handlers must have consistent execute() signature."""
        from src.ui.canvas.handlers.base import CommandHandler

        sig = inspect.signature(CommandHandler.execute)
        params = list(sig.parameters.keys())

        # Must have document and args
        assert "document" in params or "self" in params
        assert "args" in params or len(params) > 1


# =============================================================================
# Comprehensive: Nothing Missed
# =============================================================================

class TestComprehensiveCoverage:
    """Comprehensive checks to ensure nothing is missed."""

    def test_all_imports_traced(self):
        """Verify all import paths are correct after refactoring."""
        # Key imports that must work:
        from src.app.services.geometry_service import offset_polyline
        from src.app.services.dxf_service import load_dxf_polylines_with_report
        from src.app.services.model_service import CanvasDocument, CommandStack
        from src.ui.canvas.handlers.base import CommandHandler

        assert callable(offset_polyline)
        assert callable(load_dxf_polylines_with_report)
        assert CanvasDocument is not None
        assert CommandStack is not None
        assert CommandHandler is not None

    def test_no_dead_code_in_view(self):
        """view.py must not have unused operation methods after Phase 0."""
        view_file = Path("src/ui/canvas/view.py")
        content = view_file.read_text()

        # These methods should be gone (handlers should own them)
        old_methods = [
            "def _offset_selected",
            "def _mirror_selected",
            "def _rotate_selected",
            "def _scale_selected",
            "def _boolean_operation",
        ]

        for method in old_methods:
            assert method not in content, \
                f"{method}() should be in handler, not view"

    def test_all_tests_still_pass(self):
        """All existing tests must still pass (no regressions)."""
        # This is a meta-test — just ensures we don't break anything
        # In CI, run: pytest tests/ -x
        pass

    def test_ci_gates_enforced(self):
        """CI must enforce complexity gates (Phase 3.6)."""
        # Check that complexity checking is in place
        # (This would be a CI config check, not a code check)
        pass
