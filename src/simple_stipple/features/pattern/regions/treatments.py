"""Region treatments — the Pattern page's single answer to "what does this
shape do".

Replaces the outline-role / zone-membership / fill-target triad (plan
Phase 1). Every closed outline is a *region*; a region carries at most one
treatment; a treated region subtracts itself from its parent automatically,
because the engine's nested-exclusion pass already sees a treated region as
a zone contained in its parent's zone. Nothing about containment is
declared by the user.

``page._zones`` is a read-only projection of ``page._treatments`` in the
shape the ``engine/patterns`` layer already consumes, which is what keeps
this phase contained to the Pattern page.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from simple_stipple.core.patterns.fill import NULL_PATTERN
from simple_stipple.core.patterns.processing import migrate_pattern_name
from simple_stipple.core.patterns.tiling import Region, build_region_tree

TREATMENT_KINDS = ("none", "pattern", "fill", "pattern_fill", "engrave", "cut")

# "Image" is a pattern choice, not a separate panel: a region either carries a
# generated pattern or an image, so both live in the same dropdown.
IMAGE_PATTERN = "Image"

TREATMENT_LABELS = {
    "none": "None",
    "pattern": "Pattern",
    "fill": "Fill",
    "pattern_fill": "Pattern + Fill",
    "engrave": "Engrave image",
    "cut": "Cut only",
}

# Engrave and cut both mean "this area is its own operation; the pattern
# engine emits only its outline". They still become zones, which is what
# makes them subtract from the region that contains them.
_OUTPUT_MODE = {
    "pattern": "pattern",
    "fill": "fill",
    "pattern_fill": "pattern_fill",
    "engrave": "outline",
    "cut": "outline",
}

# Legacy zone output_mode → treatment kind, for workspace migration.
_KIND_FROM_OUTPUT_MODE = {
    "pattern_fill": "pattern_fill",
    "pattern": "pattern",
    "fill": "fill",
    "outline": "cut",
    "none": "none",
}


def region_tree(page: Any) -> dict[str, Region]:
    """Containment tree over the page's current closed outlines."""
    return build_region_tree(page._outline_ids, page._edit_polys)


# ── Undo ──────────────────────────────────────────────────────────────────
#
# Treatments live outside the canvas document, so canvas undo cannot see
# them. Rather than a second stack the user has to reason about, each
# snapshot records the canvas undo depth at the moment it was taken: if the
# canvas has not moved since, the treatment change is the most recent action
# and Cmd+Z undoes it; if it has, the canvas edit came later and goes first.
# ponytail: page-local history; folds into the document stack in Phase 4.


def begin_treatment_change(page: Any) -> dict:
    """Capture the treatments before a change."""
    return deepcopy(page._treatments)


def commit_treatment_change(page: Any, before: dict, region_id: str | None = None) -> None:
    """Record an undo step, but only if the change actually changed anything.

    Applying the treatment already showing in the editor is a no-op; recording
    it would leave a Cmd+Z that visibly does nothing.
    """
    if page._treatments == before:
        return
    depth = page._canvas.undo_depth()
    stack = page._treatment_undo
    # Typing in a parameter field fires per keystroke. Collapse a run of edits
    # to the same region into one undo step, which is what a user means by
    # "undo that change".
    if stack and region_id is not None and stack[-1][0] == depth and stack[-1][1] == region_id:
        return
    stack.append((depth, region_id, before))
    del stack[:-100]
    page._treatment_redo.clear()


def undo_treatments(page: Any) -> bool:
    """Restore the previous treatments if they are the most recent change."""
    stack = page._treatment_undo
    if not stack or stack[-1][0] < page._canvas.undo_depth():
        return False
    depth, region_id, snapshot = stack.pop()
    page._treatment_redo.append((depth, region_id, deepcopy(page._treatments)))
    page._treatments = snapshot
    return True


def redo_treatments(page: Any) -> bool:
    stack = page._treatment_redo
    if not stack or stack[-1][0] < page._canvas.undo_depth():
        return False
    depth, region_id, snapshot = stack.pop()
    page._treatment_undo.append((depth, region_id, deepcopy(page._treatments)))
    page._treatments = snapshot
    return True


def region_ids(page: Any) -> list[str]:
    """Region ids in document order — the Regions list's row order."""
    tree = region_tree(page)
    return [outline_id for outline_id in page._outline_ids if outline_id in tree]


def treatment_kind(page: Any, region_id: str) -> str:
    treatment = page._treatments.get(region_id)
    kind = str(treatment.get("kind", "none")) if isinstance(treatment, dict) else "none"
    return kind if kind in TREATMENT_KINDS else "none"


def set_treatment(page: Any, region_id: str, treatment: dict) -> None:
    kind = str(treatment.get("kind", "none"))
    if kind not in TREATMENT_KINDS:
        kind = "none"
    if kind == "none":
        page._treatments.pop(region_id, None)
        return
    page._treatments[region_id] = {**deepcopy(treatment), "kind": kind}


def prune_treatments(page: Any, valid_ids: set[str]) -> int:
    """Drop treatments whose region no longer exists. Returns how many went."""
    dropped = [rid for rid in page._treatments if rid not in valid_ids]
    for rid in dropped:
        del page._treatments[rid]
    return len(dropped)


def zone_region_ids(page: Any) -> list[str]:
    """Region ids that produce a zone, in the engine's zone order."""
    return [
        outline_id for outline_id in page._outline_ids if treatment_kind(page, outline_id) != "none"
    ]


def zones(page: Any) -> list[dict]:
    """Project treatments into the zone dicts ``engine/patterns`` consumes."""
    result: list[dict] = []
    for outline_id in zone_region_ids(page):
        treatment = page._treatments[outline_id]
        kind = str(treatment["kind"])
        raw_pattern = treatment.get("pattern") or NULL_PATTERN
        # Image is a UI choice, not a generator — the engine emits the
        # region's outline and the raster is exported separately.
        pattern = (
            NULL_PATTERN if raw_pattern == IMAGE_PATTERN else migrate_pattern_name(raw_pattern)
        )
        zone = {
            "outline_ids": [outline_id],
            "region_id": outline_id,
            "kind": kind,
            "pattern": pattern,
            "pattern_label": str(treatment.get("pattern_label") or pattern),
            "params": dict(treatment.get("params") or {}),
            "scale": tuple(treatment.get("scale") or (page._orig_w, page._orig_h)),
            "fill": treatment.get("fill"),
            "output_mode": _OUTPUT_MODE[kind],
            "form_state": treatment.get("form_state") or {},
        }
        zone["label"] = zone_label(page, outline_id, len(result))
        result.append(zone)
    return result


def zone_label(page: Any, region_id: str, index: int) -> str:
    kind = treatment_kind(page, region_id)
    detail = TREATMENT_LABELS[kind]
    if kind in {"pattern", "pattern_fill"}:
        treatment = page._treatments.get(region_id) or {}
        detail = f"{detail}: {treatment.get('pattern_label') or treatment.get('pattern') or NULL_PATTERN}"
    return f"Region {index + 1} · {detail}"


def region_row_label(page: Any, region_id: str, index: int, tree: dict[str, Region]) -> str:
    """Row text for the Regions list, indented by containment depth."""
    region = tree.get(region_id)
    indent = "    " * (region.depth if region is not None else 0)
    return f"{indent}{zone_label(page, region_id, index)}"


# ── Engraving lives on the region ─────────────────────────────────────────
#
# The image used to be page-global: one path, one placement, for the whole
# document. That made "which region is this image for?" a separate combo and
# meant two engraved regions were impossible. Storing it on the treatment
# makes the region that owns the image the same region that masks it.

ENGRAVING_DEFAULTS = {
    "path": "",
    "x": 0.0,
    "y": 0.0,
    "width": 0.0,
    "height": 0.0,
    "rotation": 0.0,
}


def region_engraving(page: Any, region_id: str) -> dict | None:
    """The engraving carried by a region, if it carries one."""
    treatment = page._treatments.get(region_id)
    if not isinstance(treatment, dict):
        return None
    engraving = treatment.get("engraving")
    return engraving if isinstance(engraving, dict) and engraving.get("path") else None


def engraving_regions(page: Any) -> list[tuple[str, dict]]:
    """Every region carrying an image, in document order."""
    found: list[tuple[str, dict]] = []
    for region_id in page._outline_ids:
        engraving = region_engraving(page, region_id)
        if engraving is not None and treatment_kind(page, region_id) == "engrave":
            found.append((region_id, engraving))
    return found


def set_region_engraving(page: Any, region_id: str, engraving: dict | None) -> None:
    """Attach or clear a region's image, recorded as one undo step.

    Choosing an image also makes the region an Engrave region — that is what
    the user meant by dropping an image into it, and it keeps the mask and the
    image owned by the same thing.
    """
    before = begin_treatment_change(page)
    treatment = dict(page._treatments.get(region_id) or {})
    if engraving is None:
        treatment.pop("engraving", None)
        if treatment.get("kind") == "engrave":
            treatment["kind"] = "cut"
    else:
        treatment["engraving"] = {**ENGRAVING_DEFAULTS, **engraving}
        treatment["kind"] = "engrave"
        treatment.setdefault("pattern", NULL_PATTERN)
        treatment.setdefault("params", {})
    if treatment.get("kind"):
        page._treatments[region_id] = treatment
    commit_treatment_change(page, before)


def update_region_engraving(page: Any, region_id: str, **fields: Any) -> None:
    """Change placement on an existing image, coalesced into one undo step."""
    current = region_engraving(page, region_id)
    if current is None:
        return
    before = begin_treatment_change(page)
    treatment = dict(page._treatments[region_id])
    treatment["engraving"] = {**current, **fields}
    page._treatments[region_id] = treatment
    commit_treatment_change(page, before, region_id)


def generation_polys(page: Any) -> list[list[tuple[float, float]]]:
    """Outlines the non-zone (whole-document) pattern path consumes.

    With the role triad gone there is nothing to filter: an open path is
    classified as linework by the engine, and a region only stops
    contributing once it carries its own treatment, which routes the whole
    document through the zone path instead.
    """
    return [list(poly) for poly in page._edit_polys]


def engraving_mask_polys(page: Any) -> list[list[tuple[float, float]]]:
    """The regions an engraving is clipped to.

    This is the line the old ``Cutout`` role made impossible to draw: the
    shape that defines the engraving area is simply the region carrying the
    Engrave treatment. Falls back to the whole document when no region
    claims the image.
    """
    masks = [
        list(poly)
        for outline_id, poly in zip(page._outline_ids, page._edit_polys)
        if treatment_kind(page, outline_id) == "engrave"
    ]
    return masks or generation_polys(page)


def migrate_workspace_zones(
    outline_ids: list[str],
    raw_zones: list[dict],
    exclusion_ids: list[str],
) -> dict[str, dict]:
    """Map a pre-Phase-1 workspace onto region treatments.

    Each legacy zone's settings land on every region it owned; each
    cutout-role outline becomes a ``cut`` region, which is what a cutout
    always meant geometrically.
    """
    valid = set(str(v) for v in outline_ids)
    treatments: dict[str, dict] = {}
    for raw in raw_zones:
        if not isinstance(raw, dict):
            continue
        kind = _KIND_FROM_OUTPUT_MODE.get(str(raw.get("output_mode", "pattern_fill")))
        if kind is None or kind == "none":
            continue
        pattern = migrate_pattern_name(raw.get("pattern") or NULL_PATTERN)
        treatment = {
            "kind": kind,
            "pattern": pattern,
            "pattern_label": str(raw.get("pattern_label") or pattern),
            "params": dict(raw.get("params") or {}),
            "scale": tuple(raw.get("scale") or ()) or None,
            "fill": raw.get("fill"),
            "form_state": raw.get("form_state") or {},
        }
        if treatment["scale"] is None:
            del treatment["scale"]
        for region_id in (str(v) for v in raw.get("outline_ids", [])):
            if region_id in valid:
                treatments[region_id] = deepcopy(treatment)
    for region_id in (str(v) for v in exclusion_ids):
        # A cutout owned no zone by construction, so this never clobbers one.
        if region_id in valid:
            treatments[region_id] = {"kind": "cut", "pattern": NULL_PATTERN, "params": {}}
    return treatments
