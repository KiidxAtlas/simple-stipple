# Simple Stipple — Interaction Model Overhaul

> **Naming note:** several module docstrings reference an older `plan.md`
> ("see plan.md Section 9.1") describing a completed refactor. That file no
> longer exists. This document is unrelated to those references; do not try
> to reconcile them.

## Purpose

This plan replaces the app's tool-centric structure (four pages that each own
a private copy of the geometry) with a document-centric one (regions of a
single document carry treatments). It is written to be executed by an agent
with no prior context on this codebase.

**Read this whole document before starting any phase.** The phases are
dependency-ordered; several later phases delete code that earlier phases
touch, and doing them out of order means writing code twice.

---

## The reference scenario

Every phase is judged against one job the app cannot currently do:

> A part with an outer boundary. A circle in the middle. A logo image
> engraved inside the circle. Honeycomb pattern filling the area between the
> circle and the outer boundary.

**Today this dead-ends.** The reason is worth understanding before changing
anything:

- To make honeycomb flow around the circle, the circle must be marked
  **Cutout** — [`on_canvas_outline_role_change`](src/simple_stipple/features/pattern/outlines.py:36).
- That function *strips the circle from every zone* ("a cutout cannot also
  own a zone"), and [`assign_zone`](src/simple_stipple/features/pattern/zones.py:521)
  refuses to add a cutout to a zone.
- To clip the engraving to the circle, the circle must be the engraving's
  mask. [`_engraving_mask_polys`](src/simple_stipple/features/pattern/page.py:1209)
  offers two sources: `target="outline"` uses
  [`_generation_polys()`](src/simple_stipple/features/pattern/outlines.py:216),
  which **excludes cutouts**; `target="zone"` needs the circle in a zone,
  which is forbidden.

So the one shape that defines the engraving region is the one shape that
cannot be an engraving region. The only workaround is duplicating the circle
(copy A = cutout, copy B = a zone with output `none` used purely as a mask),
which no user will find.

**Phase 1 exists to delete this contradiction.** When Phase 1 is done, the
scenario is: click the ring → Honeycomb; click inside the circle → Engrave →
drop the logo. Two clicks, two picks.

---

## Design principles for this work

1. **Derive, don't declare.** If the engine can compute a fact from geometry,
   the UI must not ask the user to assert it. The cutout role is the canonical
   violation — see Phase 1.
2. **One place per decision.** Any setting reachable from two different panels
   whose meaning depends on invisible state is a bug, not a convenience.
3. **No modes that block editing.** A state the user must exit before they can
   act is a design failure. `_showing_preview` is the canonical violation —
   see Phase 2.
4. **Delete before adding.** Most items below remove more lines than they add.
   If an item's diff is net-positive by a wide margin, re-read it — it has
   probably drifted into feature work.
5. **Ship each phase independently.** Every phase below leaves the app in a
   working, releasable state. Do not begin a phase until the prior one is
   merged and its acceptance checks pass.

---

## Audit of the source proposal

The 14-item "Four Pillars" list this plan was built from is directionally
right on spatial control and contextual UI, but it proposes three things the
codebase already has, one thing that solves a problem this app does not have,
and it misses the structural cause of the reference scenario's dead end.
Verdict on each item:

| Source item | Verdict | Where it went |
|---|---|---|
| Zone-Based Paradigm | **Keep, reframed** | Phase 1 — but as *derived regions*, not user-declared zones |
| Spatial Masking (Stencil) | **Keep, reframed** | Phase 1 — masking *is* boolean math; the app already runs Shapely/Clipper. The work is deriving containment, not adding a mask system |
| Nested Layering (Stack) | **Keep** | Phase 1 — containment tree |
| Boundary Interpolation / Edge Morphing | **Cut** | Solves a problem this app does not have. Adjacent regions get *clipped*, which is correct for a cut path — a laser needs a definite edge, not a blended one. The useful 5% of this idea already exists as the per-region `inset` parameter |
| Contextual Inspector | **Keep** | Phase 3 |
| Workflow Guidance / Narrative Strip | **Cut** | Already built and already rejected. `workflow_strip` exists; Draft and Trace both construct one and immediately `setVisible(False)` ([draft/page.py:105](src/simple_stipple/features/draft/page.py:105), [trace/page.py:152](src/simple_stipple/features/trace/page.py:152)). Two of four pages voted against it in code. Phase 3 removes the remaining two |
| Responsive Drawer | **Already built** | `content_splitter` + `set_responsive_secondary` ([ui/components/layout.py](src/simple_stipple/ui/components/layout.py)), used by every page |
| "Flow" UI (proactive tools) | **Merged** | Restatement of Contextual Inspector — Phase 3 |
| Density Field Mapping | **Already built, worth extending** | `_apply_density_field` ([processing.py:591](src/simple_stipple/engine/patterns/processing.py:591)) with UI fields for field/strength/angle. Painting weight instead of setting an angle is a real upgrade — Phase 7, lowest priority |
| Scale-Invariant Grid Mapping | **Keep, upgraded to a defect** | Phase 5. This is not a nice-to-have: every generator anchors to `outline_poly.bounds` ([tiling.py:29](src/simple_stipple/engine/patterns/tiling.py:29)), so two adjacent regions with identical settings produce *misaligned lattices*. Directly visible in the reference scenario |
| Generative Seed Algorithms | **Already built** | `gen_voronoi`, `gen_flow_lines`, `gen_topographic`, `gen_stipple_*` ([organic.py](src/simple_stipple/engine/patterns/organic.py)) |
| Scene Graph | **Demoted** | Phase 7. Layers + groups + the layer tree already cover this; a hierarchy is only worth it once regions nest, which Phase 1 delivers |
| Hardware-Aware Simulation / Digital Twin | **Cut as stated, salvaged** | "Digital twin" is out of scope for this app. The useful part is real-time density validation — merged into Predictive Error Handling |
| Predictive Error Handling | **Keep** | Phase 6. Half-built already: [`engine/cad/preflight.py`](src/simple_stipple/engine/cad/preflight.py) computes `GeometryIssue` records with canvas-ready coordinates and severity. It runs at export instead of continuously |

**What the source list was missing** — and what dominates this plan:

- **The document model.** Draft/Pattern/Trace hold *copies* of the geometry
  connected by one-way signals. `load_outline_polys` wipes the destination.
  No amount of zone/mask work fixes an edit loop that requires re-sending
  geometry and redoing every assignment. Phase 4.
- **Preview as a blocking mode.** Not mentioned in the source list, but it is
  the cheapest large win and a prerequisite for the contextual inspector
  feeling right. Phase 2.
- **Export fragmentation.** Three export kinds behind one button producing
  separate files. Phase 6.
- **Images as second-class.** The Pattern page owns image engraving but
  *rejects image drops*. Phase 3.

---

## Phase 1 — Regions and treatments

**Goal:** delete the cutout/zone/role triad; replace it with derived regions
that each carry one treatment. Unblocks the reference scenario.

**Ship independently:** yes. This phase is contained to the Pattern page and
requires no document-model work.

### Current state

Three overlapping concepts answer "what does this shape do":

1. **Outline role** — `boundary | cutout | open_path | ignore`, stored in
   `page._outline_roles`, assigned by right-click or the layer tree
   ([layers/widget.py:960](src/simple_stipple/canvas/layers/widget.py:960)).
   Logic lives in [features/pattern/outlines.py](src/simple_stipple/features/pattern/outlines.py).
2. **Zone membership** — `page._zones`, a list of dicts each holding
   `outline_ids`, `pattern`, `params`, `scale`, `fill`, `output_mode`.
   Logic in [features/pattern/zones.py](src/simple_stipple/features/pattern/zones.py).
3. **Fill targets** — per-zone checkboxes (`target_outline`, `target_pattern`).

They interact through rules the user must learn and the UI never states up
front: a cutout cannot own a zone; the only closed outline cannot be a
cutout; an open path cannot be a boundary; a cutout is excluded from
`_generation_polys()` and therefore from the engraving mask.

**The engine already derives what the UI asks the user to declare:**

- [`_zone_nested_exclusions`](src/simple_stipple/engine/patterns/processing.py:974)
  — any zone geometrically contained in another zone is *automatically*
  subtracted from the outer one. Written, working, tested.
- [`_floating_open_cutouts`](src/simple_stipple/engine/patterns/processing.py:947)
  — unassigned open shapes act as automatic cutouts for every zone.
- [`nested_polygon_region`](src/simple_stipple/engine/patterns/_shared.py:242)
  — builds a nested even-odd region from a polyline set.

The manual `cutout` role exists only because zone membership is opt-in. Make
region membership total and the role becomes dead weight.

### Target state

Every closed outline defines a **region**. Regions form a containment tree
computed from geometry. Each region carries exactly one **treatment**:

```
None  ·  Pattern  ·  Fill  ·  Engrave image  ·  Cut only
```

A region with a treatment automatically subtracts itself from its parent's
treatment. There is no cutout concept. There is no zone assignment step. An
open path is always `Cut only` and cannot be given a region treatment
(the current `open_path` role, renamed and derived rather than assigned).

Reference scenario after this phase:

1. Click inside the ring → inspector shows the ring's treatment → Pattern →
   Honeycomb.
2. Click inside the circle → treatment → Engrave image → choose/drop the
   logo. It is clipped to the circle *by construction*, because the region
   owns the mask.

### Tasks

**1.1 — Build the containment tree.**
New module `src/simple_stipple/engine/patterns/regions.py`.

```python
@dataclass(frozen=True)
class Region:
    id: str                    # stable, from the owning outline's entity id
    outline_id: str
    depth: int                 # 0 = outermost
    parent_id: str | None
    children: tuple[str, ...]
```

One entry point: `build_region_tree(outline_ids, polys) -> dict[str, Region]`.
Use `shapely.prepared.prep` + `contains` for the containment test — the same
approach `_zone_nested_exclusions` already uses, so lift that logic rather
than writing new geometry code. Open polylines get no region.

Ambiguity to resolve explicitly, not implicitly: overlapping-but-not-nested
shapes. Treat them as siblings at the same depth and let the fill boolean
sort out the overlap; do not attempt to split them into new regions.

**1.2 — Replace `_zones` with region treatments.**
A treatment is the existing zone dict minus `outline_ids` (region identity
replaces it) and minus `output_mode`'s cutout-adjacent values:

```python
{"kind": "pattern" | "fill" | "pattern_fill" | "engrave" | "cut" | "none",
 "pattern": str, "params": dict, "scale": tuple, "fill": dict,
 "engraving": dict | None}   # engraving options move here from the page
```

Store as `page._treatments: dict[str, dict]` keyed by region id. Keep
`page._zones` as a computed property that assembles the old shape for
[`snapshot_zone_jobs`](src/simple_stipple/features/pattern/zones.py:107) —
this keeps the whole `engine/patterns` layer untouched in this phase, which
is what makes Phase 1 shippable on its own. Do not refactor the engine here.

**1.3 — Feed exclusions from the tree.**
In `snapshot_zone_jobs`, replace the `exclusion_polys` derived from
`page._exclusion_ids` with: *the polys of every direct child region that has
a non-`none` treatment*. `_zone_nested_exclusions` becomes redundant for
this path — leave it in place for now (it is defensive and cheap), remove it
in Phase 5 once nothing else feeds it.

**1.4 — Delete the cutout UI surface.**
- Remove the "Outline role" submenu from the layer tree
  ([layers/widget.py:958-972](src/simple_stipple/canvas/layers/widget.py:958)).
- Remove the cutout callout, `_cutout_icon`, `_cutout_status_label`,
  `_cutout_clear_btn` and their styling from
  [layout.py:755-785](src/simple_stipple/features/pattern/layout.py:755).
- Delete from [outlines.py](src/simple_stipple/features/pattern/outlines.py):
  `on_canvas_cutout_toggle`, `on_canvas_outline_role_change`,
  `explain_outline_role`, `mark_selection_as_cutout`, `clear_exclusions`,
  `sync_canvas_cutout_highlight`, `apply_cutout_callout_style`,
  `refresh_cutout_status`. Keep `ensure_outline_roles` only if something
  still needs open/closed classification; prefer calling
  `is_open_polyline` directly.
- Remove `page._exclusion_ids`, `page._outline_roles`, and the canvas
  callbacks `on_cutout_toggle` / `on_outline_role_change` /
  `on_outline_role_explain` ([pattern/layout.py:127-131](src/simple_stipple/features/pattern/layout.py:127)).
- Remove the corresponding entries from the canvas context menu
  ([canvas/widget.py](src/simple_stipple/canvas/widget.py)).

**1.5 — Region picking on canvas.**
Clicking *inside* a closed region (not on its edge) selects the region.
Clicking an edge continues to select the entity. Implement in the hit-test
path ([canvas/operations/hit_test.py](src/simple_stipple/canvas/operations/hit_test.py)):
point-in-polygon against the innermost containing region wins. Render the
active region as a translucent tint, not an outline highlight — the user is
selecting an *area*, and the feedback must say so.

**1.6 — Pattern-cell cutouts.**
`_pattern_cell_cutouts` / `_pattern_cell_instance_cutouts` (clicking an
individual honeycomb cell to knock it out) are a *different* feature and
genuinely useful. Leave them intact. Rename the UI affordance to "Remove
cell" so it stops sharing vocabulary with the deleted cutout role.

### Acceptance checks

- The reference scenario completes with no duplicated geometry: honeycomb in
  the ring, logo clipped to the circle, one preview showing both.
- Grep confirms zero remaining references to `_exclusion_ids`,
  `_outline_roles`, `"cutout"` as a role string (pattern-cell cutouts
  excepted), and `refresh_cutout_status`.
- Moving the circle re-solves the honeycomb around its new position with no
  re-assignment step.
- A region nested three deep (outer → circle → inner detail) subtracts
  correctly at every level.
- Existing workspace files that contain zones and cutout roles load without
  raising. Migration: map each zone to treatments on its member regions; map
  each cutout-role outline to a region with treatment `none`. Write this in
  [features/pattern/session.py](src/simple_stipple/features/pattern/session.py)
  at the `apply_pattern_workspace_state` boundary.
- `tests/` — add coverage for `build_region_tree` (nesting, siblings,
  overlap, open paths) and for the workspace migration.

---

## Phase 2 — Kill preview mode

**Goal:** the solved pattern is always visible; there is no state to exit.

**Ship independently:** yes. Do this after Phase 1 — Phase 1 removes several
of the `_showing_preview` branches for free, and doing this first means
writing those branches twice.

### Current state

`Show Preview` is a checkable button that swaps the canvas between two
worlds. `page._showing_preview` gates behavior across
[page.py](src/simple_stipple/features/pattern/page.py),
[zones.py](src/simple_stipple/features/pattern/zones.py), and
[outlines.py](src/simple_stipple/features/pattern/outlines.py). The app tells
the user to leave: *"Exit preview mode to assign cutouts"*, *"Exit preview
mode to assign outline roles"*.

Preview maintains a parallel index space — `_preview_categories`,
`_preview_zone_owners`, `_preview_polys_cache` — and several functions
(`assign_zone`, `mark_selection_as_cutout`, `select_zone_for_canvas_selection`)
carry two full implementations, one per world, with entity-index remapping
between them.

### Target state

Generated pattern geometry renders continuously as a de-emphasized layer
beneath the editable outlines. Editing geometry re-solves it (debounced,
cancellable — the existing `_preview_timer` + `CancellableTaskState` already
provide this). No toggle, no mode, no "exit to do X".

The `Show Preview` button becomes a **result opacity/visibility** control on
the layer tree, alongside the other layers. Auto-preview checkbox is deleted;
it exists only because preview was expensive and modal.

### Tasks

**2.1** Render generated geometry as a real, non-selectable canvas layer
(`pattern_result`) rather than by replacing the entity set. The layer tree
already supports per-layer visibility — reuse it.

**2.2** Delete `page._showing_preview` and every branch on it. Phase 1
removed the cutout-related ones; the rest are in `assign_zone`
(the `_resolve_preview_zone_selection` path), `_on_preview_toggled`,
`preview_outline_indices_for_zone`, `highlight_zone_on_canvas`, and
`select_zone_for_canvas_selection`.

**2.3** Delete `_preview_user_opt_out`, `_auto_preview_cb`, `_preview_btn`,
`_on_preview_clicked`, `_on_preview_toggled`. Keep `_cancel_preview_btn` —
it moves to the status strip as a transient "solving…" affordance with a
cancel action.

**2.4** Keep `_preview_is_stale` as an internal freshness flag for export
gating; remove it from the user-facing vocabulary.

**2.5** Promoting a generated cell to a real outline (currently a side effect
of assigning a zone while previewing) becomes an explicit context-menu
action: **Convert to outline**.

### Acceptance checks

- Grep: zero occurrences of `_showing_preview`.
- Every operation available with the pattern hidden is available with it
  shown. No flash message anywhere instructs the user to leave a mode.
- Dragging a vertex re-solves within the existing debounce; a solve in flight
  is cancelled by the next edit, not queued.
- Net line count for this phase is negative.

---

## Phase 3 — One contextual inspector

**Goal:** one panel edits whatever is selected. Images become first-class.

**Ship independently:** yes.

### Current state

The Pattern sidebar contains a **Pattern** section, a **Fill** section, *and*
a Zone Manager holding a second pattern combo, second fill mode, second
spacing/angle/inset. Which set applies depends on invisible state:
[`_pattern_props_scope`](src/simple_stipple/features/pattern/layout.py:600)
flips between *"New zone defaults"* and *"Select a zone to edit its
settings"*.

Image engraving is a collapsed section at the bottom of that sidebar. Adding
an image auto-fits it to the *whole outline's* bounds
([`_choose_engraving_image`](src/simple_stipple/features/pattern/page.py:1092)),
then centers it, then requires a canvas-edit toggle or Tab-cycling five
numeric fields to place it. Dropping an image file on the page is **rejected**
with *"Pattern accepts DXF, FVI, or SVG outline files"*
([page.py:350](src/simple_stipple/features/pattern/page.py:350)).

### Target state

One inspector, one position, on every page. It edits the current selection:
a region, an entity, an image, or — when nothing is selected — document
defaults, stated plainly in its header.

### Tasks

**3.1** Collapse the Pattern / Fill / Zone Manager triplicate into a single
selection-driven inspector. Delete the duplicate widget set; keep one.
`_pattern_props_scope` becomes a header that names the actual target
("Ring · Pattern") instead of describing a mode.

**3.2** Accept image drops. Extend `dragEnterEvent`/`dropEvent` on the
Pattern page ([page.py:341](src/simple_stipple/features/pattern/page.py:341))
to accept the image extensions already listed in `_choose_engraving_image`.
A dropped image lands **at the drop point**, at its natural size (DPI-derived,
logic already present), selected, with handles — no auto-fit, no auto-center.
If dropped inside a region, that region becomes its clip mask.

**3.3** Make the placement fields (`_engrave_x/_y/_w/_h/_rotation`) part of
the inspector's image view rather than a bespoke section, and delete
`_on_engraving_canvas_key`'s Tab-cycling hack — the inspector gives real
focus order for free.

**3.4** Delete the `_engrave_target` combo (`outline` vs `zone`). The clip
mask is the region the image sits in. Deleting this combo is what closes the
reference scenario's dead end at the UI level; Phase 1 closed it at the model
level.

**3.5** Remove the `workflow_strip` from the two pages still showing it
(Pattern, Convert) and delete `ui/components/workflow.py` if nothing else
imports it. Invest that space in the canvas empty states, which are already
well-written but are numbered prose where they should be buttons:

```
Start a pattern
[ Import outline… ]  [ Draw one ]  [ Trace an image ]
```

### Acceptance checks

- No control appears twice in the app with the same label and different
  scope.
- Dropping a PNG on the Pattern canvas places it; dropping a DXF imports
  geometry. Neither shows a rejection message for a file type the page
  supports.
- Selecting a region, an entity, and an image in turn changes the inspector's
  contents and header; nothing else moves on screen.
- `grep -rn workflow_strip src/` returns nothing.

---

## Phase 4 — One document

**Goal:** stop copying geometry between pages.

**Ship independently:** yes, but it is the largest phase. Do not begin until
Phases 1–3 are merged — they shrink the surface this phase has to move.

### Current state

Draft, Pattern, and Trace each construct their own `DxfCanvas` and hold
private geometry. They exchange point lists through one-way signals:
`sendSelectedToDraftRequested`, `sendSelectedToPatternRequested`,
`customTileRequested`. The receiving end
([`load_outline_polys`](src/simple_stipple/features/pattern/page.py:1814))
**replaces everything** — `_zones.clear()`, fresh outline IDs, cleared
exclusions, reset preview.

Consequences:

- The edit loop is one-way. Fix the circle in Draft, re-send, redo every
  assignment.
- [`SETTINGS_SYNC_TABLE`](src/simple_stipple/app/pages.py:86) is 36 settings
  pushed to every canvas on every page, plus a dozen `_apply_to_canvases` /
  `_connect_signals_to` wrappers — machinery that exists only because there
  are three canvases pretending to be one.
- Trace and Pattern cannot know that the image you traced and the image
  you're engraving are the same picture at the same place.

### Target state

One `Document` ([document/model.py](src/simple_stipple/document/model.py)
already defines it: entities, selection, layers, groups, constraints,
guides, dimensions). One canvas. Pages become **modes** over it:

- **Draw** — the current Draft toolset.
- **Treat** — the current Pattern inspector.
- **Trace** — an import filter, not a destination.
- **Convert** — file open/save handling, not a page.

### Tasks

**4.1** Extend `Document` to hold placed images and region treatments. Both
already have serialized forms (`engraving_image_path` / `engraving_options`
at [model.py:321](src/simple_stipple/document/model.py:321), and the treatment
dict from Phase 1) — this is mostly relocation, not new schema.

**4.2** Hoist a single `DxfCanvas` into the window; pages render mode-specific
chrome around it. `PageRuntime.content_canvas_attrs` and the
per-page canvas construction in `draft/page.py`, `pattern/layout.py`, and
`trace/page.py` all collapse.

**4.3** Delete `SETTINGS_SYNC_TABLE` and the `_apply_to_canvases` /
`_connect_signals_to` / per-setting wrapper methods in
[app/pages.py](src/simple_stipple/app/pages.py). With one canvas, settings
apply directly.

**4.4** Delete the `sendSelectedTo*` signal family and their handlers. Mode
switching replaces geometry transfer.

**4.5** Trace becomes a treatment on a placed image: **Trace to outlines**
alongside **Engrave**. Same image object, same position, same scale, one
decision that is cheap to reverse. This is the item that makes "should the
mountain range be engraved or cut?" a toggle rather than a tab choice made
before you have seen it on the part.

**4.6** Unify undo. `document/history.py` and `document/commands.py` exist;
the Pattern page currently installs its own shortcuts
([`_install_pattern_shortcuts`](src/simple_stipple/features/pattern/page.py:1578)).
One document, one stack.

### Acceptance checks

- Editing geometry in Draw mode and switching to Treat mode shows the updated
  geometry with all treatments intact. No transfer step, no reset.
- `grep -rn "sendSelectedTo\|SETTINGS_SYNC_TABLE\|_apply_to_canvases" src/`
  returns nothing.
- One undo stack: an edit in Draw mode, a treatment change in Treat mode, and
  an image move all undo in reverse order from either mode.
- Workspace files from before this phase still load (migration at the
  `apply_workspace_document` boundary).

---

## Phase 5 — Pattern grids are global, not per-region

**Goal:** adjacent regions with identical settings produce a continuous
lattice.

**Ship independently:** yes. Small phase; can be done any time after Phase 1.

### Current state

Every generator anchors to the region's own bounding box:

```python
minx, miny, maxx, maxy = outline_poly.bounds     # tiling.py:29
cx = minx - pad + col * col_step + off
```

In the reference scenario, the ring's bbox differs from the circle's, so two
regions sharing "Honeycomb, 4 mm" produce visibly misaligned lattices with a
seam at the boundary. Users read this as a bug, and they are right.

`gen_custom_tile` ([_shared.py:470](src/simple_stipple/engine/patterns/_shared.py:470))
already solves it — it accepts `origin_x` / `origin_y` and computes
`phase_x = origin_x % col_step`. That approach needs generalizing, not
inventing.

### Tasks

**5.1** Add a document-level pattern origin (default `(0, 0)`; user-settable,
snappable to a selected point).

**5.2** Thread `origin_x`/`origin_y` through every generator in
[tiling.py](src/simple_stipple/engine/patterns/tiling.py) and
[organic.py](src/simple_stipple/engine/patterns/organic.py), applying the
same phase-offset arithmetic `gen_custom_tile` uses. For the stochastic
generators (`gen_voronoi`, `gen_stipple_*`), the equivalent is a document-level
**seed** so the point set is stable and continuous across regions.

**5.3** Per-region override: **Align to region** for the case where a user
deliberately wants a motif centered in one shape.

**5.4** Once nothing feeds it, remove
[`_zone_nested_exclusions`](src/simple_stipple/engine/patterns/processing.py:974)
— Phase 1 replaced its input.

### Acceptance checks

- Two adjacent regions, same pattern, same size: the lattice is continuous
  across the shared edge with no seam.
- Resizing a region reveals more or less of the lattice; it does not stretch
  the cells.
- Re-solving twice with a fixed seed produces byte-identical Voronoi output.

---

## Phase 6 — One output, continuous validation

**Goal:** one export, one preflight, one job. Errors surface while designing.

**Ship independently:** yes.

### Current state

`Export` runs one of three kinds — `vector | engraving | laserstar` — chosen
by a remembered default (`_export_default`) and a `⋯` menu
([page.py:1276](src/simple_stipple/features/pattern/page.py:1276)). Engraving
exports a PNG package; vector exports a DXF. Two files, reconciled by hand at
the machine.

Preflight is a label (`_summary_chip`) inside a collapsed "Export options"
section, computed at export time — even though
[`engine/cad/preflight.py`](src/simple_stipple/engine/cad/preflight.py)
already produces `GeometryIssue` records carrying a point, a message, and a
severity: exactly the shape a canvas overlay needs.

### Tasks

**6.1** Replace the export-kind fork with an **Output** panel listing the
operations the document produces, in run order:

```
1  Engrave   logo.png  →  inside Circle      80% · 100 mm/s · 1 pass
2  Mark      Honeycomb →  Ring
3  Cut       Outer boundary
```

Reorder, toggle, assign layer/tool per row. One Export writes them all.
Destination format (DXF / FVI / LaserStar package) becomes a field on the
export dialog, not a fork in the flow.

**6.2** Run preflight continuously on the document, debounced with the same
timer the solver uses. Render `GeometryIssue` records as canvas markers at
their `point`, colored by `severity`. Clicking a marker selects the offending
entity.

**6.3** Add density validation to the same channel: a region whose solved
spacing falls below a configurable machine minimum flags as a warning at
design time, not at export. This is the salvageable part of the
"digital twin" idea — no simulation, just a threshold check on data the
solver already computed.

**6.4** The Output panel is the only preflight surface. Delete
`_summary_chip`.

### Acceptance checks

- The reference scenario exports one job containing all three operations.
- An open path that should be closed shows a marker on canvas while drawing,
  not a message at export.
- No path through the UI produces two files that the user must combine
  manually.

---

## Phase 7 — Deferred

Do not start these until Phases 1–6 are merged and in users' hands. They are
listed so they are not lost, not so they are scheduled.

- **Weight painting for density fields.** Brush-driven density instead of
  angle + strength. `_apply_density_field` is the integration point; needs a
  raster weight buffer per region.
- **Region hierarchy in the layer tree.** Once regions nest (Phase 1), show
  them as a tree rather than a flat list. Marginal until a real document has
  20+ nested regions.
- **Start screen.** Replace tab-picking as the first decision with
  "Import a file / Draw it / Trace an image / Recent". Cheap, but it only
  makes sense after Phase 4 makes those four things land in one document.

---

## Execution rules for the implementing agent

1. **One phase per branch.** Do not start the next phase until the current
   one is merged and its acceptance checks pass.
2. **Run the existing suite before and after every phase.** `tests/` has real
   coverage of the pattern engine; treat a regression there as a blocker, not
   a signal to update the test.
3. **Workspace compatibility is not optional.** Every phase that changes
   persisted state adds migration at the `apply_workspace_state` /
   `apply_workspace_document` boundary and a test that loads a pre-phase file.
4. **Report deletions.** Each phase should report lines added vs removed.
   Phases 1, 2, and 4 must be net-negative. If one is not, it has drifted
   into feature work — stop and re-read the phase.
5. **When a phase's design is ambiguous, ask.** Do not resolve an interaction
   question by picking the option that is easier to implement.
