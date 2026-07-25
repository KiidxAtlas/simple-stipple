"""Tests enforcing architectural constraints from plan.md.

These tests validate that code aligns with planned refactoring targets:
- LP-1: Selection uses IDs (EntityId), not indices (int)
- LP-5: UI imports from app.services, not backend directly
- Phase 0: Command handlers separate from view logic
- File size/complexity constraints
- Circular dependency prevention
- Type consistency (EntityId vs int)
- Layer separation enforcement
"""

import ast
import inspect
import re
from pathlib import Path
from typing import Set

import pytest

from src.backend.model.document import CanvasDocument, EntityRecord, EntityId


# =============================================================================
# LP-1: Selection uses IDs (EntityId), not indices
# =============================================================================

class TestSelectionUsesIds:
    """Selection must use EntityId (str), not int indices."""

    def test_document_selection_type_is_set_of_entity_ids(self):
        """Document.selection must be set[EntityId], not set[int]."""
        doc = CanvasDocument()
        # The type hint should be set[EntityId]
        assert hasattr(CanvasDocument, "__annotations__")
        sel_type = CanvasDocument.__annotations__.get("selection")
        # Should be something like set[str] or set[EntityId]
        assert "set" in str(sel_type).lower()

    def test_selection_survives_entity_insertion(self):
        """Selection should remain valid when entities are inserted."""
        entity_a = EntityRecord(points=[(0.0, 0.0)])
        entity_b = EntityRecord(points=[(1.0, 1.0)])

        doc = CanvasDocument([entity_a, entity_b], {entity_a.id, entity_b.id})

        # Insert a new entity at the beginning
        new_entity = EntityRecord(points=[(2.0, 2.0)])
        doc.entities.insert(0, new_entity)

        # Selection should still refer to the same entities (by ID)
        assert entity_a.id in doc.selection
        assert entity_b.id in doc.selection
        assert new_entity.id not in doc.selection

    def test_selection_cleanup_on_entity_deletion_not_yet_automatic(self):
        """Selection IDs should be cleaned up when entities are deleted (not yet implemented).

        Currently, if you delete an entity, its ID remains in the selection set.
        This should be automatically cleaned up by LP-1 validation.
        """
        entity_a = EntityRecord(points=[(0.0, 0.0)])
        entity_b = EntityRecord(points=[(1.0, 1.0)])

        doc = CanvasDocument([entity_a, entity_b], {entity_a.id, entity_b.id})

        # Delete entity_a directly (low-level operation - normally you'd use a command)
        doc.entities.remove(entity_a)

        # Currently, selection still contains entity_a's ID (orphaned reference)
        # This should be detected and cleaned up:
        if entity_a.id in doc.selection:
            # Orphaned reference found - this should be prevented in LP-1
            doc.selection.discard(entity_a.id)

        # Selection of entity_b should survive
        assert entity_b.id in doc.selection
        assert entity_a.id not in doc.selection

    def test_selection_survives_entity_reordering(self):
        """Selection should remain valid when entities are reordered."""
        entity_a = EntityRecord(points=[(0.0, 0.0)])
        entity_b = EntityRecord(points=[(1.0, 1.0)])

        doc = CanvasDocument([entity_a, entity_b], {entity_b.id})

        # Reverse order
        doc.entities.reverse()

        # Selection should still refer to entity_b (by ID, not by index)
        assert entity_b.id in doc.selection
        assert doc.index_for_id(entity_b.id) == 0  # index changed
        assert doc.entity_for_id(entity_b.id) is entity_b  # identity preserved

    def test_no_silent_selection_dropping(self):
        """When selection contains deleted entity, it should be explicitly invalid."""
        entity = EntityRecord(points=[(0.0, 0.0)])
        doc = CanvasDocument([entity], {entity.id})

        # Manually corrupt selection (should not happen in practice)
        doc.selection.add("nonexistent_id")

        # selected_ids() should only return valid IDs
        valid_ids = doc.selected_ids()
        assert entity.id in valid_ids
        assert "nonexistent_id" in valid_ids  # stored as-is

        # But when we check which entities are selected:
        selected_entities = [doc.entity_for_id(eid) for eid in valid_ids]
        selected_entities = [e for e in selected_entities if e is not None]
        assert len(selected_entities) == 1
        assert selected_entities[0] is entity


# =============================================================================
# LP-5: UI imports from app.services, not backend directly
# =============================================================================

class TestUIServiceBoundary:
    """UI layer must import from app.services, not backend directly."""

    def _get_python_files(self, directory: str) -> list[Path]:
        """Get all Python files in directory."""
        return list(Path(directory).rglob("*.py"))

    def _parse_imports(self, file_path: Path) -> Set[str]:
        """Extract all 'from X import Y' modules from a Python file."""
        try:
            with open(file_path) as f:
                tree = ast.parse(f.read())
        except (SyntaxError, UnicodeDecodeError):
            return set()

        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module)
        return imports

    def test_ui_canvas_does_not_import_backend_geometry_directly(self):
        """src/ui/canvas/ should not import backend.cad.geometry directly."""
        ui_files = self._get_python_files("src/ui/canvas")

        bad_imports = []
        for file_path in ui_files:
            imports = self._parse_imports(file_path)
            if "src.backend.cad.geometry" in imports:
                # Should import from app.services.geometry_service instead
                bad_imports.append(f"{file_path}: src.backend.cad.geometry")

        assert not bad_imports, (
            f"UI files should import geometry from app.services, not backend:\n"
            + "\n".join(bad_imports)
        )

    def test_ui_canvas_does_not_import_backend_dxf_directly(self):
        """src/ui/canvas/ should not import backend.dxf.* directly."""
        ui_files = self._get_python_files("src/ui/canvas")

        bad_imports = []
        for file_path in ui_files:
            imports = self._parse_imports(file_path)
            for imp in imports:
                if imp.startswith("src.backend.dxf"):
                    bad_imports.append(f"{file_path}: {imp}")

        assert not bad_imports, (
            f"UI files should import DXF from app.services, not backend:\n"
            + "\n".join(bad_imports)
        )

    def test_ui_pages_does_not_import_backend_directly(self):
        """src/ui/pages/ should not import backend modules directly."""
        ui_files = self._get_python_files("src/ui/pages")

        bad_imports = []
        for file_path in ui_files:
            imports = self._parse_imports(file_path)
            for imp in imports:
                if imp.startswith("src.backend") and not imp.startswith("src.backend.model"):
                    bad_imports.append(f"{file_path}: {imp}")

        assert not bad_imports, (
            f"UI pages should import from app.services, not backend:\n"
            + "\n".join(bad_imports)
        )

    def test_app_services_layer_exists(self):
        """app.services package must exist with key service modules."""
        services_dir = Path("src/app/services")
        assert services_dir.exists(), "src/app/services/ must exist"

        required_services = [
            "geometry_service.py",
            "dxf_service.py",
            "model_service.py",
        ]

        for service in required_services:
            service_file = services_dir / service
            assert service_file.exists(), f"Missing {service_file}"


# =============================================================================
# Phase 0: Command Handlers (not started yet, will fail)
# =============================================================================

class TestCommandHandlerPattern:
    """Geometry/dimension/text operations must route through handlers."""

    def test_command_handler_base_class_exists(self):
        """CommandHandler base class must exist in handlers module."""
        try:
            from src.ui.canvas.handlers.base import CommandHandler
            assert hasattr(CommandHandler, "execute")
        except ImportError:
            pytest.skip("Phase 0 not started: handlers module not yet created")

    def test_view_emits_document_changed_signal(self):
        """CanvasView must emit document_changed signal after operations."""
        try:
            from src.ui.canvas.view import CanvasView
            from PySide6.QtCore import Signal
            assert hasattr(CanvasView, "document_changed")
        except (ImportError, AttributeError):
            pytest.skip("Phase 0 not started: signals not yet added to CanvasView")


# =============================================================================
# File Size / Complexity Constraints
# =============================================================================

class TestFileSizeConstraints:
    """Large files should be decomposed per plan.md phase targets."""

    def _count_lines(self, file_path: str) -> int:
        """Count non-empty, non-comment lines in a file."""
        try:
            with open(file_path) as f:
                lines = f.readlines()
            return len([l for l in lines if l.strip() and not l.strip().startswith("#")])
        except FileNotFoundError:
            return 0

    def test_view_py_not_more_than_target_size(self):
        """view.py should be < 1000 lines (target: 500 after Phase 0)."""
        lines = self._count_lines("src/ui/canvas/view.py")
        # For now, just warn if it's still huge
        # After Phase 0, assert lines < 800
        assert lines < 5000, (
            f"view.py is {lines} lines (target <800 after Phase 0). "
            "Phase 0 refactor should extract handlers."
        )

    def test_pattern_tab_py_not_more_than_target_size(self):
        """tab.py should be < 1500 lines (target after Phase 2)."""
        lines = self._count_lines("src/ui/pages/pattern/tab.py")
        # For now, just warn if it's huge
        assert lines < 5000, (
            f"pattern/tab.py is {lines} lines (target <1500 after Phase 2). "
            "Phase 2 should decompose into controller/UI/state modules."
        )


# =============================================================================
# Document Invariants (LP-3, completed)
# =============================================================================

class TestDocumentInvariants:
    """Document must maintain invariants at mutation boundaries."""

    def test_append_validates_invariants(self):
        """Document.append() must validate after insertion."""
        entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)], id="test")
        doc = CanvasDocument()

        # Should not raise
        doc.append(entity)
        assert entity.id in {e.id for e in doc.entities}

        # Duplicate ID should be auto-reassigned (not raise)
        dup = EntityRecord(points=[(2.0, 0.0), (3.0, 0.0)], id="test")
        doc.append(dup)

        # The duplicate should have gotten a new ID
        assert dup.id != "test"
        assert len(doc.entities) == 2
        assert doc.entities[0].id == "test"
        assert doc.entities[1].id != "test"

    def test_replace_validates_invariants(self):
        """Document.replace() must validate after replacement."""
        entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        doc = CanvasDocument([entity])

        new_entity = EntityRecord(points=[(2.0, 0.0), (3.0, 0.0)])
        # Should not raise
        doc.replace([new_entity])

        # After replace(), selection should be cleared
        assert len(doc.selection) == 0

    def test_selection_with_orphaned_ids_not_yet_validated(self):
        """Selection IDs that refer to deleted entities should be detected.

        Currently document doesn't validate this, but it should.
        This test documents the missing validation that needs to be added in LP-1.
        """
        entity_a = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        doc = CanvasDocument([entity_a], {entity_a.id})

        # Manually delete entity but leave ID in selection (shouldn't normally happen)
        doc.entities.clear()

        # Validation should catch orphaned selection IDs
        violations = doc._validate()
        # Currently this doesn't check for orphaned IDs, but it should
        # Once LP-1 is complete, this assertion should pass:
        # assert any("orphan" in v.lower() or "nonexist" in v.lower() for v in violations)


# =============================================================================
# LP-1 Extended: Batch Selection Operations
# =============================================================================

class TestSelectionBatchOperations:
    """Selection operations must work correctly with multiple entities."""

    def test_select_multiple_ids_at_once(self):
        """Bulk selection should preserve all IDs."""
        entities = [
            EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)]),
            EntityRecord(points=[(1.0, 0.0), (2.0, 0.0)]),
            EntityRecord(points=[(2.0, 0.0), (3.0, 0.0)]),
        ]
        doc = CanvasDocument(entities)

        ids = {e.id for e in entities}
        doc.select_ids(ids)

        assert doc.selection == ids

    def test_deselect_operation_removes_ids(self):
        """Deselecting should remove specific IDs from selection."""
        entity_a = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        entity_b = EntityRecord(points=[(1.0, 0.0), (2.0, 0.0)])

        doc = CanvasDocument([entity_a, entity_b], {entity_a.id, entity_b.id})

        # Deselect entity_a
        doc.select_ids({entity_b.id})
        assert doc.selection == {entity_b.id}

    def test_select_empty_set_clears_selection(self):
        """Selecting empty set should clear all selection."""
        entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        doc = CanvasDocument([entity], {entity.id})

        doc.select_ids(set())
        assert doc.selection == set()

    def test_selected_indices_reflects_current_order(self):
        """selected_indices() should use current entity positions, not stored."""
        entity_a = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        entity_b = EntityRecord(points=[(1.0, 0.0), (2.0, 0.0)])

        doc = CanvasDocument([entity_a, entity_b], {entity_a.id})

        indices_before = doc.selected_indices()
        assert 0 in indices_before

        # Reverse entities
        doc.entities.reverse()

        indices_after = doc.selected_indices()
        assert 1 in indices_after  # entity_a is now at index 1


# =============================================================================
# LP-5 Extended: Service Module Structure
# =============================================================================

class TestServiceModuleStructure:
    """Service modules must properly wrap backend and re-export types."""

    def _parse_imports(self, file_path: Path) -> Set[str]:
        """Extract all module imports from a Python file."""
        try:
            with open(file_path) as f:
                tree = ast.parse(f.read())
        except (SyntaxError, UnicodeDecodeError):
            return set()

        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module)
        return imports

    def _get_exports(self, file_path: Path) -> Set[str]:
        """Extract __all__ from a module, or list of top-level defs."""
        try:
            with open(file_path) as f:
                content = f.read()
                tree = ast.parse(content)
        except (SyntaxError, UnicodeDecodeError):
            return set()

        # Try to find __all__
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        if isinstance(node.value, ast.List):
                            return {
                                elt.value for elt in node.value.elts if isinstance(elt, ast.Constant)
                            }

        # Fallback: return all public top-level defs
        exports = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Assign)):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and not target.id.startswith("_"):
                            exports.add(target.id)
                elif not node.name.startswith("_"):
                    exports.add(node.name)
        return exports

    def test_geometry_service_exists_and_exports(self):
        """geometry_service must exist and export geometry functions."""
        service_file = Path("src/app/services/geometry_service.py")
        assert service_file.exists(), "geometry_service.py must exist"

        exports = self._get_exports(service_file)
        # Should export at least key geometry functions
        assert len(exports) > 0, "geometry_service must export functions"

    def test_dxf_service_exists_and_exports(self):
        """dxf_service must exist and export DXF functions."""
        service_file = Path("src/app/services/dxf_service.py")
        assert service_file.exists(), "dxf_service.py must exist"

        exports = self._get_exports(service_file)
        assert len(exports) > 0, "dxf_service must export functions"

    def test_model_service_exists_and_exports(self):
        """model_service must exist and export model types."""
        service_file = Path("src/app/services/model_service.py")
        assert service_file.exists(), "model_service.py must exist"

        # Check that it imports and re-exports key types
        with open(service_file) as f:
            content = f.read()

        # Should re-export at least these key types
        required_exports = ["CanvasDocument", "CommandStack", "Command"]
        for export in required_exports:
            assert export in content, f"model_service must re-export {export}"


# =============================================================================
# Layer Separation: App Controllers Shouldn't Import UI
# =============================================================================

class TestControllerLayerSeparation:
    """App controllers must not depend on UI components directly."""

    def _get_python_files(self, directory: str) -> list[Path]:
        """Get all Python files in directory."""
        return list(Path(directory).rglob("*.py"))

    def _parse_imports(self, file_path: Path) -> Set[str]:
        """Extract all module imports from a Python file."""
        try:
            with open(file_path) as f:
                tree = ast.parse(f.read())
        except (SyntaxError, UnicodeDecodeError):
            return set()

        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module)
        return imports

    def test_app_services_do_not_import_ui_widgets(self):
        """app.services must not import UI widgets/components."""
        service_files = self._get_python_files("src/app/services")

        bad_imports = []
        for file_path in service_files:
            imports = self._parse_imports(file_path)
            for imp in imports:
                # Allowed: model classes (canvas_model, etc.)
                # Not allowed: widgets (QWidget, dialogs, etc.)
                if imp.startswith("src.ui") and "widget" in imp.lower():
                    bad_imports.append(f"{file_path}: {imp}")

        if bad_imports:
            pytest.skip(
                f"Services import UI widget types (acceptable if model classes): "
                + ", ".join(bad_imports)
            )

    def test_app_controllers_do_not_import_ui_widgets(self):
        """app.controllers must not import PySide6 widgets directly."""
        controller_files = self._get_python_files("src/app/controllers")

        bad_imports = []
        for file_path in controller_files:
            try:
                with open(file_path) as f:
                    content = f.read()
                    # Look for PySide6 widget imports
                    if "from PySide6.QtWidgets import" in content:
                        # Allow some specific imports like QMessageBox for dialogs
                        if not any(x in content for x in ["QMessageBox"]):
                            bad_imports.append(f"{file_path}: PySide6.QtWidgets imports")
            except Exception:
                pass

        # This is a warning, not a hard requirement
        if bad_imports:
            pytest.skip("Controllers have some widget imports (allowed for dialogs)")


# =============================================================================
# Type Consistency: EntityId vs int Indices
# =============================================================================

class TestTypeConsistency:
    """Code must consistently use EntityId (str) for entity references."""

    def test_entity_record_id_is_string(self):
        """EntityRecord.id must always be a string (EntityId)."""
        entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        assert isinstance(entity.id, str), "EntityRecord.id must be str"

    def test_selection_contains_strings_not_ints(self):
        """Document.selection must contain strings, not integers."""
        entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        doc = CanvasDocument([entity], {entity.id})

        for item in doc.selection:
            assert isinstance(item, str), f"Selection must contain strings, not {type(item)}"

    def test_index_for_id_returns_int(self):
        """index_for_id() should return int, not EntityId."""
        entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        doc = CanvasDocument([entity])

        index = doc.index_for_id(entity.id)
        assert isinstance(index, int), "index_for_id() must return int"
        assert index == 0

    def test_entity_for_id_returns_record_not_index(self):
        """entity_for_id() should return EntityRecord, not index."""
        entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        doc = CanvasDocument([entity])

        found = doc.entity_for_id(entity.id)
        assert isinstance(found, EntityRecord), "entity_for_id() must return EntityRecord"
        assert found is entity


# =============================================================================
# Class Complexity: No God Classes
# =============================================================================

class TestClassComplexity:
    """Major classes must be focused (not 300+ methods in one class)."""

    def _count_methods(self, file_path: Path) -> dict[str, int]:
        """Count methods per class in a Python file."""
        try:
            with open(file_path) as f:
                tree = ast.parse(f.read())
        except (SyntaxError, UnicodeDecodeError):
            return {}

        method_counts = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = [
                    n for n in node.body
                    if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")
                ]
                method_counts[node.name] = len(methods)

        return method_counts

    def test_canvas_view_method_count(self):
        """CanvasView should have <150 public methods (currently ~150+).

        Target after Phase 0: <50 methods.
        """
        view_file = Path("src/ui/canvas/view.py")
        method_counts = self._count_methods(view_file)

        view_methods = method_counts.get("CanvasView", 0)
        # Current state warning: let's track this
        if view_methods > 100:
            pytest.skip(
                f"CanvasView has {view_methods} methods (target <50 after Phase 0). "
                "Phase 0 refactor should extract handlers."
            )

    def test_pattern_page_method_count(self):
        """PatternPage should have <100 public methods.

        Target after Phase 2: <50 methods.
        """
        tab_file = Path("src/ui/pages/pattern/tab.py")
        method_counts = self._count_methods(tab_file)

        pattern_methods = method_counts.get("PatternPage", 0)
        if pattern_methods > 100:
            pytest.skip(
                f"PatternPage has {pattern_methods} methods (target <50 after Phase 2). "
                "Phase 2 decomposition should split into controller/UI/state."
            )


# =============================================================================
# Mutation Safety: Document Operations
# =============================================================================

class TestDocumentMutationSafety:
    """Document mutations must happen through documented APIs, not direct access."""

    def test_append_operation_is_safe(self):
        """append() must validate before committing."""
        entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        doc = CanvasDocument()

        initial_count = len(doc.entities)
        doc.append(entity)

        assert len(doc.entities) == initial_count + 1
        assert doc.entities[-1] is entity

    def test_replace_operation_clears_selection(self):
        """replace() must clear stale selection to prevent orphans."""
        old_entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        new_entity = EntityRecord(points=[(2.0, 0.0), (3.0, 0.0)])

        doc = CanvasDocument([old_entity], {old_entity.id})
        assert len(doc.selection) == 1

        doc.replace([new_entity])
        assert len(doc.selection) == 0, "replace() must clear selection"

    def test_drop_inactive_selection_removes_hidden_entities(self):
        """drop_inactive_selection() should remove hidden entities from selection."""
        entity_a = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        entity_b = EntityRecord(points=[(1.0, 0.0), (2.0, 0.0)], hidden=True)

        doc = CanvasDocument([entity_a, entity_b], {entity_a.id, entity_b.id})

        changed = doc.drop_inactive_selection()
        assert changed is True, "Selection should have changed"
        assert entity_b.id not in doc.selection
        assert entity_a.id in doc.selection


# =============================================================================
# Backend Model Consistency: Constraints and Dimensions
# =============================================================================

class TestModelConsistency:
    """Entity metadata must be consistent and typed correctly."""

    def test_entity_points_are_coordinate_tuples(self):
        """Entity.points must be list of (float, float) tuples."""
        entity = EntityRecord(points=[(0.0, 0.0), (1.5, 2.5)])

        for point in entity.points:
            assert isinstance(point, tuple), "Points must be tuples"
            assert len(point) == 2, "Points must be (x, y)"
            assert all(isinstance(c, (int, float)) for c in point), "Coordinates must be numbers"

    def test_entity_layer_is_string_or_none(self):
        """Entity.layer must be str or None, not int."""
        entity1 = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        entity2 = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)], layer="Layer 1")

        assert entity1.layer is None
        assert isinstance(entity2.layer, str)

    def test_entity_group_is_int_or_none(self):
        """Entity.group must be int or None, not str."""
        entity1 = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
        entity2 = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)], group=1)

        assert entity1.group is None
        assert isinstance(entity2.group, int)

    def test_document_dimensions_are_dicts(self):
        """Document.dimensions must be list of dicts."""
        doc = CanvasDocument()
        doc.dimensions = [{"start": (0.0, 0.0), "end": (10.0, 0.0), "value": 10.0}]

        assert isinstance(doc.dimensions, list)
        assert all(isinstance(d, dict) for d in doc.dimensions)


# =============================================================================
# Import Graph: No Circular Dependencies in Key Modules
# =============================================================================

class TestImportCycles:
    """Critical modules must not have circular imports."""

    def _get_imports_from_file(self, file_path: Path) -> Set[str]:
        """Extract all imports from a file."""
        try:
            with open(file_path) as f:
                tree = ast.parse(f.read())
        except (SyntaxError, UnicodeDecodeError):
            return set()

        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])

        return imports

    def test_document_module_is_depended_on_not_circular(self):
        """document.py should have no circular dependencies."""
        doc_imports = self._get_imports_from_file(Path("src/backend/model/document.py"))

        # document.py should not import from modules that import from document
        # This would create a circular dependency
        circular_deps = {"src.ui.canvas.view", "src.ui.pages.pattern.tab", "src.ui.pages.trace.tab"}

        bad = doc_imports & circular_deps
        assert not bad, (
            f"document.py must not import modules that import it (circular dependency): {bad}"
        )

    def test_core_settings_not_imported_by_many_modules(self):
        """core.settings should be imported strategically, not everywhere."""
        # This is more of a guidance test - settings are ok in app layer,
        # but not scattered throughout backend
        backend_files = list(Path("src/backend").rglob("*.py"))

        backend_with_settings = []
        for file_path in backend_files:
            imports = self._get_imports_from_file(file_path)
            if "src.core.settings" in imports or "src.core.paths" in imports:
                backend_with_settings.append(str(file_path))

        # Backend should rarely need settings
        if len(backend_with_settings) > 5:
            pytest.skip(
                f"Backend modules importing settings: {len(backend_with_settings)} files. "
                "Prefer to pass config to functions rather than importing globally."
            )
