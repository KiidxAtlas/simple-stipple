"""Qt-free workflow state for pattern generation and export."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from simple_stipple.core.patterns.processing import PatternProcessor
from simple_stipple.features.pattern.workers import CancellableTaskState

Point = tuple[float, float]
Polyline = list[Point]


@dataclass
class PatternModel:
    """State shared by Pattern's canvas, preview jobs, and export jobs.

    Widgets render and edit this model; it deliberately contains no Qt
    objects, timers, or signals.
    """

    original_polys: list[Polyline] = field(default_factory=list)
    editable_polys: list[Polyline] = field(default_factory=list)
    original_width: float = 0.0
    original_height: float = 0.0
    updating_dimensions: bool = False
    preview_task: CancellableTaskState = field(default_factory=CancellableTaskState)
    generate_task: CancellableTaskState = field(default_factory=CancellableTaskState)
    preview_thread: threading.Thread | None = None
    generate_thread: threading.Thread | None = None
    shutting_down: bool = False
    last_output_path: str | None = None
    export_is_current: bool = False
    preview_is_stale: bool = False
    output_order: list[str] = field(default_factory=list)
    output_disabled: set[str] = field(default_factory=set)
    force_export_quality: bool = False
    pending_export_after_preview: Any | None = None
    presets: dict[str, dict] = field(default_factory=dict)
    base_patterns: list[str] = field(default_factory=list)
    preview_polys_cache: list[Polyline] = field(default_factory=list)
    preview_categories: dict[str, list[Polyline]] = field(
        default_factory=lambda: {"outline": [], "pattern": [], "fill": []}
    )
    preview_zone_owners: list[int | None] = field(default_factory=list)
    outline_ids: list[str] = field(default_factory=list)
    outline_layers: dict[str, str] = field(default_factory=dict)
    pattern_cell_cutouts: list[Polyline] = field(default_factory=list)
    pattern_cell_instance_cutouts: list[Polyline] = field(default_factory=list)
    preview_revision: int = 0
    generation_revision: int = 0
    pattern_service: PatternProcessor = field(default_factory=PatternProcessor)
    treatments: dict[str, dict] = field(default_factory=dict)
    treatment_undo: list[tuple[int, str | None, dict]] = field(default_factory=list)
    treatment_redo: list[tuple[int, str | None, dict]] = field(default_factory=list)
    loading_zone: bool = False
    engraving_image_path: str = ""

    def invalidate_preview(self) -> None:
        """Invalidate cached result geometry after a document edit."""
        self.export_is_current = False
        self.preview_is_stale = bool(self.preview_polys_cache)
        self.preview_polys_cache.clear()
        self.preview_categories = {"outline": [], "pattern": [], "fill": []}
        self.preview_zone_owners.clear()
