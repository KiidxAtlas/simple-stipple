"""Qt-free state for the drafting workflow."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DraftModel:
    """Persistable workflow state that is not owned by the canvas widget."""

    last_output_path: str | None = None
    last_input_path: str | None = None
    import_note: str = ""

    def record_import(self, path: str, note: str = "") -> None:
        self.last_input_path = path
        self.import_note = note.strip()

    def record_export(self, path: str) -> None:
        self.last_output_path = path
