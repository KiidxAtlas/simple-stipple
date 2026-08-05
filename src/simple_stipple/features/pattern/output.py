"""What this document actually produces, in the order the machine runs it.

Export used to be three kinds behind one button — vector, engraving,
LaserStar — chosen by a remembered default, each writing its own file for the
user to reconcile at the machine. A document does not have a "kind": it has
operations. This module derives them from the region treatments, so the panel
is a readout of the document rather than a mode the user has to pick.

Run order is engrave → mark → cut, which is the order a laser wants: raster
first while the stock is still fully supported, cut last because cutting frees
the part.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from simple_stipple.engine.cad.preflight import GeometryIssue
from simple_stipple.features.pattern.treatments import (
    IMAGE_PATTERN,
    region_engraving,
    treatment_kind,
)

# Lower runs first.
_ORDER = {"engrave": 0, "mark": 1, "cut": 2}

_KIND_LABEL = {"engrave": "Engrave", "mark": "Mark", "cut": "Cut"}


@dataclass(frozen=True)
class Operation:
    """One row of the Output panel: one thing the machine will do."""

    key: str
    kind: str  # engrave | mark | cut
    subject: str  # what is produced
    target: str  # where it lands
    detail: str = ""

    @property
    def label(self) -> str:
        parts = [f"{_KIND_LABEL[self.kind]}  {self.subject}  →  {self.target}"]
        if self.detail:
            parts.append(self.detail)
        return "      ".join(parts)


def _region_name(page, region_id: str) -> str:
    ids = [rid for rid in page._outline_ids if rid in page._region_tree()]
    return f"Region {ids.index(region_id) + 1}" if region_id in ids else "Region"


def document_operations(page) -> list[Operation]:
    """Every operation this document produces, in run order."""
    operations: list[Operation] = []
    for region_id in page._outline_ids:
        kind = treatment_kind(page, region_id)
        name = _region_name(page, region_id)
        if kind == "engrave":
            engraving = region_engraving(page, region_id)
            image = Path(engraving["path"]).name if engraving else "no image"
            operations.append(
                Operation(
                    key=f"engrave:{region_id}",
                    kind="engrave",
                    subject=image,
                    target=f"inside {name}",
                    detail=_engraving_detail(page),
                )
            )
        elif kind in {"pattern", "fill", "pattern_fill"}:
            treatment = page._treatments.get(region_id) or {}
            subject = str(treatment.get("pattern_label") or treatment.get("pattern") or "Fill")
            if subject in {"— None —", IMAGE_PATTERN}:
                subject = "Fill"
            operations.append(
                Operation(
                    key=f"mark:{region_id}",
                    kind="mark",
                    subject=subject,
                    target=name,
                    detail="",
                )
            )
        elif kind == "cut":
            operations.append(
                Operation(key=f"cut:{region_id}", kind="cut", subject=name, target="outline")
            )
    # The part still has to come off the sheet. An outermost region is cut
    # whatever is done inside it, which is why the reference scenario's ring
    # is both marked and cut — the boundary is the same shape either way.
    tree = page._region_tree()
    for region_id in page._outline_ids:
        region = tree.get(region_id)
        if region is None or region.depth != 0:
            continue
        if treatment_kind(page, region_id) == "cut":
            continue  # already listed as its own Cut
        operations.append(
            Operation(
                key=f"cut:boundary:{region_id}",
                kind="cut",
                subject=f"{_region_name(page, region_id)} boundary",
                target="outline",
            )
        )
    untreated = sum(
        1
        for rid in page._outline_ids
        if treatment_kind(page, rid) == "none" and (tree.get(rid) is None or tree[rid].depth > 0)
    )
    if untreated:
        operations.append(
            Operation(
                key="cut:remaining",
                kind="cut",
                subject=f"{untreated} untreated outline{'s' if untreated != 1 else ''}",
                target="outline",
            )
        )
    operations.sort(key=lambda op: _ORDER[op.kind])
    return operations


def _engraving_detail(page) -> str:
    try:
        return (
            f"{page._engrave_max_power.value():g}% · "
            f"{page._engrave_speed.value():g} mm/s · "
            f"{page._engrave_passes.value():g} pass"
        )
    except AttributeError:
        return ""


# ── Density validation ────────────────────────────────────────────────────


def density_issues(
    jobs: list[dict],
    minimum_spacing_mm: float,
) -> tuple[GeometryIssue, ...]:
    """Flag regions whose solved fill spacing is below the machine minimum.

    This is the salvageable part of the "digital twin" idea: no simulation,
    just a threshold check on a number the solver already produced, surfaced
    while the design is being made instead of at export.
    """
    if minimum_spacing_mm <= 0:
        return ()
    issues: list[GeometryIssue] = []
    for index, job in enumerate(jobs):
        fill = job.get("fill")
        if not isinstance(fill, dict):
            continue
        spacing = float(fill.get("spacing") or 0.0)
        if spacing <= 0 or spacing >= minimum_spacing_mm:
            continue
        polys = job.get("polys") or []
        point = next((poly[0] for poly in polys if poly), (0.0, 0.0))
        issues.append(
            GeometryIssue(
                "density",
                index,
                tuple(point),
                f"Fill spacing {spacing:g} mm is below the {minimum_spacing_mm:g} mm "
                "machine minimum",
                "warning",
            )
        )
    return tuple(issues)
