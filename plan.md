# Simple Stipple UI/UX and Workflow Remediation Plan

Status: implemented and verified (2026-07-21)
Audit basis: `prompts/ui-design.md`, `prompts/workflow-flow.md`, and direct inspection of the current PySide6 application
Primary journey: Load/import → Edit → Apply pattern/engraving → Verify → Export

## Implementation progress

### Slice 1 — Pattern workflow state and unified recovery entry (completed)

- Pattern now represents all five workflow steps from real document readiness and reaches Export after successful generation.
- Changing inputs or invalidating the preview also invalidates the current-export state.
- Startup recovery now opens the same Workspaces & Recovery library used by the menu instead of a separate single-choice dropdown.
- Recovery rows now distinguish snapshots using source workspace, capture timestamp, file size, and a snapshot identifier.
- Invalid recovery snapshots remain visible for management but cannot be opened as valid workspaces.
- Regression coverage verifies all five Pattern states, export invalidation, startup routing, and recovery metadata.

### Slice 2 — Shared accessibility foundations and recovery management (completed)

- The application now supports a 900 × 600 minimum workspace instead of enforcing 1100 × 700.
- The global Arial override was removed so Qt uses the platform-native UI font.
- Buttons, tool buttons, sliders, checkboxes, lists, trees, tabs, and numeric inputs now have explicit keyboard-focus treatment.
- Sliders and checkboxes have larger compact hit areas; checkbox indicators use the planned 18 px size.
- Properties numeric fields can contract to 72 px and no longer enforce a rigid 160 px maximum.
- Properties labels and aspect-lock state use shared semantic styling instead of local hard-coded QSS.
- Workspace Library supports multi-selection deletion and Delete All Snapshots with exact-count confirmation.
- Opening Workspace Library directly on Recovery no longer fires refresh before its list exists.

### Slice 3 — Responsive shell, navigation, and command surfaces (completed)

- The 900 × 600 shell now collapses secondary inspectors and canvas actions into keyboard-operable drawers/overflow controls at the documented breakpoints.
- Settings, Properties, Pattern, Trace, and Convert use responsive inspectors, grouped disclosure, durable widths, and narrow-mode controls.
- Live command metadata, availability reasons, shortcuts, context actions, keybinding import/export, and protected essential commands share the command infrastructure.

### Slice 4 — Draft editing and direct manipulation (completed)

- Escape behavior, selection ownership, dependent annotations, snapping arbitration, constraints, topology operations, adaptive curves, and transform feedback retain undoable document state.
- Cross-page actions remain discoverable when unavailable and explain the geometry/selection requirement.
- Draft/Trace/Convert handoff labels the source and Pattern provides an explicit Undo transfer action.

### Slice 5 — Pattern, engraving, and operator export (completed)

- Pattern has independent complete/current/stale/error step state, automatic preview validation, contextual sections, scoped cutouts/zones, and one remembered Export entry.
- Engraving import preserves physical size, centers/selects the image, provides direct rotation/placement, groups process controls, and validates unsafe values in context.
- LaserStar packaging uses one transactional sheet, documented import order and asset checks, then a nonmodal result card with reveal/copy actions; legacy exports remain available.

### Slice 6 — Trace, Convert, tiles, and recovery continuity (completed)

- Trace auto-previews, collapses its loaded source, keeps a last-valid result, maps failures to recovery guidance, exposes inline smoothing and remembered Next actions, and uses a remembered 300–420 px inspector.
- Convert has a persistent shared input/drop surface, 300 px responsive remembered inspector, wide/narrow task navigation, inline results/handoffs, batch progress, cancellation, and partial-output reporting.
- Custom tile references, embedded fallbacks, validity states, inline rename, configurable storage, and Locate/Repair/Remove/Open Folder actions preserve current Pattern settings.
- Startup, menu, and compatibility recovery entry points now all route exclusively through Workspace Library; durable-write failures are persistent, deduplicated, and cleared only by success.

### Slice 7 — Visual-system polish and final validation (completed)

- Shared spacing, density, semantic state, focus, radius, vector-icon, status, dialog, and 150 ms motion foundations are applied; reduced motion disables transient animation.
- Functional close/disclosure/workflow/status controls use the shared vector icon set and dialog-level stylesheet drift is guarded by tests.
- Verification: 906 tests pass; Ruff passes; Mypy reports no issues across 143 source files; `git diff --check` passes.

## Purpose

This document combines two audits:

1. A visual and interaction-system audit using `prompts/ui-design.md`.
2. An end-to-end workflow audit using `prompts/workflow-flow.md`.

Each finding uses the required format:

`[Failing element] → [Design/workflow problem] → [Actionable specification]`

The specifications preserve existing capabilities. They reorganize, clarify, and expose them rather than removing expert options.

## Product-wide design contract

- The common path must remain visible and take no more than three decisions after valid input is available.
- Advanced controls remain available through progressive disclosure and never block the common path.
- Every interactive control must have default, hover, pressed/active, disabled, and keyboard-focus states.
- Use a 4 px base grid: 4, 8, 12, 16, 24, and 32 px only, except geometry that must match OS metrics.
- Use semantic roles for primary, secondary, destructive, warning, success, selection, and focus states.
- Preserve page state, zoom, selection, and entered values across tab changes and recoverable errors.
- Escape cancels the current transient tool/mode first; a second Escape may clear selection. It must never silently discard document work.
- Long work must show determinate progress when measurable, cancellation, and a stable result/error region.
- All compact layouts must remain keyboard-operable and usable at approximately 900 × 600.

## Existing strengths to preserve

- Draft already uses inline canvas HUDs for several operations instead of forcing modal input.
- Draft and Trace can hand selected geometry directly to Pattern and the main window switches to the destination page.
- Recent-path helpers reduce repeated filesystem navigation.
- Pattern, Trace, and Convert already have asynchronous work/status infrastructure.
- The Workspace Library already combines saved workspaces and recovery snapshots in one browsable surface.
- LaserStar package export already produces an operator-oriented bundle without replacing legacy exports.
- Page state and pattern parameters have explicit session/workspace persistence code.

---

# Part I — UI design findings (`ui-design.md`)

## P0: Product-wide hierarchy and accessibility

### UI-01 — Spacing system

`[Layouts across Pattern, Trace, components, dialogs, and inspectors] → [Dozens of unrelated literal margins and gaps create uneven density and violate the required 4/8 grid] → [Introduce shared SPACE_1=4, SPACE_2=8, SPACE_3=12, SPACE_4=16, SPACE_6=24, SPACE_8=32 tokens; replace layout literals; use 8 px within an atomic control group, 12–16 px between fields, 24 px between sections, and 32 px only for page-level separation.]`

Acceptance:

- No new arbitrary spacing literals in UI construction or QSS.
- Pattern, Trace, Convert, Settings, Properties, and common dialogs use the same section rhythm.

### UI-02 — Typography

`[Application theme font] → [The theme forces Arial, producing a non-native look and inconsistent metrics across macOS, Windows, and Linux] → [Use the Qt/system UI font by default; define semantic sizes for caption, body, field, section title, and page title; reserve monospace for coordinates, dimensions, and machine values.]`

### UI-03 — Keyboard focus

`[Buttons, tool buttons, tabs, sliders, checkboxes, lists, trees, canvas actions] → [Focus treatment is mainly visible on text inputs, so keyboard users lose location] → [Add a universal 2 px focus ring using the focus semantic color with 2 px visual separation; never encode focus solely as a fill change; audit every QWidget with StrongFocus or TabFocus.]`

### UI-04 — State completeness

`[Shared controls and custom widgets] → [Hover/active/disabled/focus states are incomplete or visually inconsistent] → [Define all five states in the shared theme for QPushButton, QToolButton, QComboBox, QSpinBox, QSlider, QCheckBox, QTabBar, QListView, QTreeView, collapsible headers, workflow steps, and canvas HUD actions.]`

### UI-05 — Competing Pattern actions

`[Pattern export/action area] → [DXF export, engraving export, and LaserStar export compete as separate high-emphasis actions] → [Provide one sticky primary “Export” split button/menu whose remembered default runs on click and whose menu lists DXF, engraving assets, LaserStar package, and existing formats; rename intermediate engraving generation to “Review engraving output”.]`

### UI-06 — Pattern inspector hierarchy

`[Pattern left inspector: Shape, Zones, Pattern, Fill, Image] → [A long, equally weighted form makes users repeatedly scan irrelevant controls] → [Use contextual accordion sections; expand the current required section, collapse completed sections to one-line summaries, keep user-opened sections open, and show validation on the section header.]`

### UI-07 — Settings information architecture

`[Settings tab] → [One long scrolling form makes options hard to discover and relationships hard to understand] → [Add category navigation: General, Files & Folders, Canvas & Snapping, Drawing, Pattern, Trace, Export & Machines, Interface, Shortcuts & Menus, Updates; provide search; keep Save/Apply/Reset in a sticky footer.]`

### UI-08 — Properties information architecture

`[Draft/Trace Properties panel] → [Geometry, transform, parameters, constraints, and destructive actions form one cluttered column] → [Split into collapsible Geometry, Transform, Shape Parameters, Constraints/Dimensions, and Actions groups; show only properties valid for the current selection; preserve group expansion per page.]`

### UI-09 — Responsive properties

`[Properties field rows and action grids] → [Fixed 88–160 px field assumptions clip at narrow widths and the panel lacks a deliberate overflow model] → [Place the inspector in a scroll area, set a 72 px minimum editor width, allow labels to wrap or move above values, and collapse action grids to one column below 260 px.]`

### UI-10 — Main-window minimum size

`[Main window minimum 1100 × 700] → [The application cannot fit smaller laptops, split-screen work, or remote operator displays] → [Target a tested minimum near 900 × 600; below 1050 px collapse secondary inspectors into toggleable drawers and move low-priority toolbar actions into overflow.]`

### UI-11 — Canvas toolbar density

`[Draft/Trace canvas toolbars] → [Persistent primary and secondary tools create visual noise and label truncation] → [Keep Select, Draw, and Edit mode controls permanently visible; group tool-specific actions contextually; move secondary actions into a labeled overflow below 1000 px; never truncate the currently active tool name.]`

## P1: Interaction quality

### UI-12 — Button and tool-button focus semantics

`[Primary, secondary, toolbar, and menu-launch buttons] → [Mouse affordances exist but keyboard affordances do not consistently match] → [Centralize roles and focus styling; expose accessible names and shortcuts; include shortcut text in tooltips sourced from the live keybinding registry.]`

### UI-13 — Slider usability

`[Pattern/engraving/trace sliders] → [Small handles and no obvious focus state make precision adjustment difficult] → [Use at least a 28 px effective hit height, 16–18 px handle, keyboard arrows/PageUp/PageDown, inline numeric editor, max two displayed decimal places, and focused-track/handle treatment.]`

### UI-14 — Checkbox usability

`[Checkboxes throughout settings and inspectors] → [15 px indicators, weak disabled states, and limited focus visibility reduce scanability] → [Use an 18 px indicator, 8 px label gap, full-row click target where safe, visible focus ring, and clear checked/unchecked/disabled contrast.]`

### UI-15 — Workflow-strip consistency

`[Pattern and Trace workflow steppers] → [Mixed text labels, arrows, and tool-button behavior makes progress look partly decorative and partly navigational] → [Use one step component with completed/current/pending/error states; completed steps are clickable, pending steps are disabled with a reason tooltip, and the current step has a strong label plus concise next action.]`

### UI-16 — Collapsible headers

`[Inspector accordion headers] → [Small padding, hover-only affordance, and missing focus treatment make disclosure hard to discover] → [Give headers a minimum 32 px compact height or 8 px vertical padding, consistent chevron icon, full-row hit target, keyboard Enter/Space behavior, and visible focus.]`

### UI-17 — Image-versus-geometry selection

`[Pattern canvas with engraving image enabled] → [Image manipulation can intercept geometry work, so the active target is ambiguous] → [Introduce explicit object selection: image gets a bounding box and handles only when selected; geometry remains selectable otherwise; clicking empty canvas or Escape deselects; status/HUD always names the active target.]`

## P1: Page-level visual hierarchy

### UI-18 — Trace top-heavy layout

`[Trace source-image section above the canvas] → [A large source section pushes the primary tracing workspace down] → [After load, collapse it to a thumbnail, filename, Replace button, and Trace action; move background/reload utilities to overflow; collapse Recipes and Advanced by default.]`

### UI-19 — Trace sidebar sizing

`[Trace sidebar fixed around 320–360 px] → [It consumes too much width on small windows and cannot adapt to verbose labels] → [Use a resizable 300–420 px inspector with remembered width; below 340 px stack field labels above editors and wrap help text.]`

### UI-20 — Convert layout sizing

`[Convert sidebar fixed around 360–440 px] → [Task names and controls either consume excess canvas space or clip] → [Allow a 300 px minimum resizable sidebar; use a segmented task selector when wide and a task combo when narrow; remember width and selected task.]`

### UI-21 — Convert navigation versus action

`[Convert task choices] → [Task selectors resemble primary action buttons, weakening hierarchy] → [Render tasks as navigation rows/segments with icons and descriptions; reserve filled primary styling for the single action that runs the selected conversion.]`

### UI-22 — Engraving control grouping

`[Pattern image-engraving controls] → [Placement, visual processing, laser parameters, and export are mixed together] → [Create Placement, Appearance, Laser Process, and Output subsections; keep Placement open while the image is selected and summarize material/process choices when collapsed.]`

### UI-23 — Engraving safety messaging

`[Laser/material guidance] → [Long safety text competes with controls and is easy to ignore] → [Use a compact amber callout containing the machine/material caveat and “Review settings”; place detailed guidance in an expandable panel; show blocking warnings next to the exact unsafe value.]`

### UI-24 — Cutout controls

`[Pattern cutout marking in multiple surfaces] → [Duplicate-looking entry points obscure whether cutouts belong to source geometry or a repeated pattern cell] → [Use one Cutout property with explicit scope “Outline zone” or “Pattern cell”; context menus are shortcuts to the same command; Fill shows a count/summary and one Clear action.]`

### UI-25 — Status-strip overflow

`[Bottom status strip] → [Object count, precision, coordinates, snap state, mode, and messages compete at narrow widths] → [Keep mode, coordinates, and current operation visible; below 800 px move object count and precision into a details tooltip/popover; never elide errors or progress.]`

### UI-26 — Density modes

`[30–38 px desktop controls] → [Compact sizes are efficient for mouse experts but not comfortable for touch or accessibility needs] → [Add global Compact and Comfortable density modes; Comfortable uses 44 px primary hit targets and increased inspector row spacing without changing canvas scale.]`

### UI-27 — Preset-dialog sidebar

`[Pattern preset dialog fixed near 170 px] → [Names truncate and the content relationship is rigid] → [Use a splitter with 160 px minimum and 240 px preferred sidebar width; remember the split; show full names in tooltips.]`

### UI-28 — Inline dialog styling

`[Dialogs with local setStyleSheet calls] → [At least dozens of local rules bypass theme semantics and drift across light/dark modes] → [Replace local QSS with shared semantic properties/roles; keep geometry in widget code and visual styling in the theme; add a theme audit test that rejects new dialog-level style sheets except documented rendering widgets.]`

### UI-29 — Dialog action order

`[Dialog footers] → [Primary, Cancel, Reset, Help, and Close actions change order and prominence] → [Standardize footer order: Help/Reset on the left; stretch; Cancel then primary on the right; 8 px gaps; minimum 88 px buttons; destructive actions separated and red only when imminent.]`

## P2: Visual-system refinement

### UI-30 — Excess borders

`[Panels, cards, groups, and nested sections] → [Repeated borders flatten hierarchy and create a boxed-in appearance] → [Reserve borders for inputs, selected objects, warnings, and genuinely nested cards; separate top-level sections with spacing and subtle surface contrast.]`

### UI-31 — Radius consistency

`[Controls and panels using 3/4/5/6/8/10 px radii] → [Near-identical radii create accidental inconsistency] → [Use 4 px for compact controls, 8 px for cards/popovers, and 12 px for dialogs/large overlays.]`

### UI-32 — Blue semantic overload

`[Selection, focus, workflow, primary actions, and informational states] → [The same blue treatment weakens meaning] → [Use solid accent only for the primary action, translucent accent for selection, outline accent for keyboard focus, neutral styling for completed workflow steps, and semantic colors for info/warning/error/success.]`

### UI-33 — Unicode icon inconsistency

`[Ellipsis, close, angle, disclosure, lock, and status glyphs] → [Font glyphs differ by OS and do not match toolbar line weight] → [Replace functional Unicode glyphs with a single SVG/vector icon set at 16/20/24 px; provide accessible names; keep text symbols only where they are actual mathematical content.]`

### UI-34 — Motion consistency

`[Panels, status changes, selection, and progress feedback] → [Transitions are inconsistent or absent, reducing continuity] → [Use a shared 150 ms ease-out for hover, selection, accordion, and lightweight panel transitions; keep the allowed range 100–250 ms; honor the OS reduced-motion setting and never animate geometry unexpectedly.]`

---

# Part II — Workflow findings (`workflow-flow.md`)

## Target end-to-end flow

The default journey should be:

1. Import or draw geometry.
2. Clean/edit only if validation detects a problem.
3. Send the active selection to Pattern in one action.
4. Choose a treatment with a useful default and see an automatic preview.
5. Export through one remembered export action, with machine-ready packaging available beside legacy formats.

The interface should not force users through every advanced zone, fill, material, or repair decision when defaults are valid.

## P0: Cross-application flow

### WF-01 — Workspace entry point

`[File/Workspace menus and workspace header] → [Saved workspaces and recovery are discoverable only through menu knowledge, despite being central to state restoration] → [Add a visible workspace-name button in the header that opens Workspaces & Recovery; retain File menu entries and shortcuts; show unsaved state and last autosave time in its popover.]`

### WF-02 — Startup recovery

`[Startup recovery QInputDialog] → [A single dropdown hides snapshot count, timestamps, sizes, validity, and delete controls; users repeatedly see the same apparent save] → [Open the existing Workspace Library directly on its Recovery category; show name, source workspace, modified time, age, size, and validity; support multi-select delete, Delete All, reveal folder, and explicit Recover as Copy.]`

### WF-03 — Duplicate recovery implementations

`[Autosave controller’s legacy recovery picker versus Workspace Library] → [Two recovery experiences can drift and make deletion/restoration behavior inconsistent] → [Route startup, menu, and error recovery through WorkspaceLibraryDialog; remove or retire the nested QInputDialog manager after parity tests; keep one service for listing, validating, restoring, and deleting snapshots.]`

### WF-04 — Recovery persistence feedback

`[Autosave/recovery background writes] → [Failures are primarily logged, so operators may assume protection exists when it does not] → [Show a nonmodal warning in the workspace header after the first failure with reason and “Manage storage”; suppress duplicate toasts; clear the warning only after a successful durable write.]`

### WF-05 — Cross-page handoff

`[Draft/Trace “Use as outline” handoff to Pattern] → [The tab switches correctly, but the user receives little confirmation of what transferred or what to do next] → [Preserve source selection, switch to Pattern, fit/highlight imported outline once, show “N shapes from Draft/Trace”, focus the first incomplete Pattern step, and provide an Undo transfer action that restores the prior Pattern outline.]`

### WF-06 — No-selection handoff

`[Send to Pattern with no valid selection] → [The common action can become unavailable or fail without explaining the required selection] → [Keep the command discoverable but disabled with reason text; Command Palette and context menu must say “Select one or more closed shapes”; offer “Use all visible closed shapes” when safe.]`

### WF-07 — Common-path action count

`[Load → Edit → Pattern → Export] → [Users can be exposed to zones, fill internals, image processing, and output details before a useful result exists] → [After a valid outline arrives, auto-select the last-used/default treatment, generate a draft-quality preview, and expose Export; require at most treatment selection, preview confirmation, and export destination—three decisions.]`

### WF-08 — State continuity

`[Tab changes, preview toggles, dialogs, and recoverable errors] → [Selection, zoom, expanded panels, or input focus can change unexpectedly] → [Persist per-page view transform, selection, scroll position, expanded sections, and active tool; after a modal closes, restore focus to its invoker; after an error, preserve all valid inputs.]`

## P0: Pattern workflow

### WF-09 — Incorrect Pattern step state

`[Pattern five-step workflow indicator] → [Current logic advances only to indices 0–3; after generation it marks Preview current and never reaches Export] → [Define a single state reducer: 0 no outline, 1 outline/no treatment, 2 treatment configured/no current preview, 3 preview current, 4 export-ready/exported; update state on every relevant mutation and add tests for all transitions.]`

### WF-10 — Step validity versus chronology

`[Pattern workflow stepper] → [A purely chronological current index cannot represent a stale preview after upstream settings change] → [Track complete/current/stale/error independently; when upstream input changes, mark Preview stale and Export unavailable while keeping completed configuration accessible.]`

### WF-11 — Preview dependency

`[Pattern export, especially LaserStar] → [Export can require a preview cache, but the dependency is not consistently framed as validation] → [Make Preview an automatic validation stage; if stale at export, regenerate automatically with progress and continue export, or show a single actionable blocking error if regeneration fails.]`

### WF-12 — Preview-mode interruption

`[Marking outline cutouts while preview mode is active] → [The app tells the user to exit preview, breaking the edit-feedback loop] → [Allow source geometry selection through a Source/Result canvas toggle or temporarily reveal source on hover; marking a cutout must update preview automatically without a manual mode exit.]`

### WF-13 — Cutout scope ambiguity

`[Outline-zone cutout and repeated-cell cutout workflows] → [Users cannot predict whether a void applies once or repeats in every tile] → [At command time show scope choices “Cut out this outline area” and “Repeat this cutout in every tile”; display distinct overlays/legend entries; persist repeated cutouts in the custom tile asset.]`

### WF-14 — Zone assignment overhead

`[Create zone → select geometry → choose pattern → assign → preview] → [The workflow requires several disconnected decisions for the common case] → [When geometry is selected and Add Zone is invoked, create and select the zone immediately, inherit the current pattern/fill, and update preview; keep a compact inline editor for overrides.]`

### WF-15 — Engraving placement workflow

`[Add image engraving to a patterned shape] → [Import, target, placement, sliders, material, preview, and export are not presented as one continuous task] → [On image import preserve native physical size, place at canvas center, select it, show drag/resize/rotate handles, open Placement, and render live clipped preview; material presets initialize settings but never overwrite later user edits without confirmation.]`

### WF-16 — Engraving output readiness

`[Pattern plus image engraving export] → [Users must infer which files belong together and how they reach the laser] → [The unified Export menu must label outputs by purpose: vector-only, engraving-only, combined job assets, and LaserStar operator package; validate registration/origin and show a concise contents summary before writing.]`

### WF-17 — LaserStar export steps

`[Job name dialog → destination dialog → completion modal] → [Three serial dialogs interrupt an otherwise prepared job] → [Use one export sheet with remembered job name pattern, remembered output folder, machine profile, and package contents; primary “Create Package”; finish with a nonmodal result card offering Reveal Folder and Copy Operator Notes.]`

### WF-18 — Machine handoff

`[LaserStar package consumed in StarFX] → [The operator still has to interpret setup text and manually assemble/import assets] → [Keep old exports, but add a guided LaserStar checklist with numbered import order, expected units/origin, material preset values, asset presence checks, and a printable setup sheet; never claim automatic machine compatibility beyond verified docs.]`

### WF-19 — Export naming

`[Repeated export filename/job-name prompts] → [Operators repeatedly make low-value naming decisions] → [Derive defaults from workspace + outline + material + date, sanitize automatically, remember the last destination per export type, and ask only on collision or explicit Save As.]`

## P1: Draft and editing workflow

### WF-20 — Mode exit and cancellation

`[Draw/Edit/Dimension/Scale/Spline/Bezier transient modes] → [Cancellation behavior is difficult to predict and may trap selection/editing] → [Escape cancels the in-progress gesture and returns to Select; if no gesture is active it exits the mode; show the Escape hint in the mode HUD; restore the pre-gesture geometry on cancel.]`

### WF-21 — Tool parameter focus

`[Tab cycling while drawing parametric shapes] → [Focus cycles among only some numeric inputs and labels do not identify the active value] → [Define explicit tab order for every tool parameter; show a persistent labeled HUD such as “Star — Outer radius”, “Inner radius”, “Points”; highlight the active field and allow Shift+Tab reversal.]`

### WF-22 — Contextual edit operations

`[Right-click in Edit/Draw and “More actions”] → [Common operations such as round, chamfer, merge, split, close, and constraints are buried or absent] → [Build menus from the central command registry, rank valid selection-specific actions first, expose configurable Primary and More groups per mode, and hide or explain invalid commands consistently.]`

### WF-23 — Command Palette trust

`[Command Palette contents and shortcut labels] → [Missing commands and stale shortcut text make the palette unreliable] → [Populate exclusively from the same live command/action registry used by menus and toolbars; include every user-invokable command, current shortcut, mode/selection requirements, aliases, and disabled reason; add registry-parity tests.]`

### WF-24 — Selection ownership

`[Geometry, dimensions, layer tree, and canvas selection] → [Associated dimensions or layer-selected objects can become inaccessible after dimensioning] → [Use one document selection model; selecting geometry selects it regardless of dimensions; dimensions remain independently selectable; Select All obeys configurable geometry/dimension filters; Delete geometry removes dependent dimensions in the same undoable command.]`

### WF-25 — Constraint/dimension editing

`[Editing multiple dimensions/angles] → [Changing one value can move the whole shape or produce non-predictive competing results] → [Implement constraint-solver rules with anchored geometry, explicit driven/reference state, conflict detection, and a preview before commit; choose the least-displacement solution; show which constraint blocks a requested change and offer reference/disable choices.]`

### WF-26 — Snapping feedback

`[Endpoint, on-edge, same-axis, equal-length, parallel, and perpendicular snaps] → [Snap hierarchy and invisible competition make placement difficult] → [Use configurable snap toggles and priorities; score by screen-space distance plus priority; show one dominant snap glyph/label and faint alternatives; Tab cycles candidates; same-axis endpoints and equal-length remain separate toggles.]`

### WF-27 — Topology-changing drawings

`[Region split/carve with lines, circles, curves, and containing shapes] → [Users cannot predict when a new shape will split, carve, remain, or disappear] → [Separate commands: Draw adds geometry only; Split divides a target when a cutter crosses its boundary; Carve subtracts a closed cutter; Preserve cutter defaults on; a containing cutter must never erase contained targets; show a pre-commit overlay and result count.]`

### WF-28 — Curved split fidelity

`[Spline/Bezier/circle cutters] → [Tessellation can create jagged results and visual mismatch] → [Use adaptive curve flattening based on view/export tolerance, preserve analytic metadata where possible, preview the actual result path, and expose precision only under Advanced.]`

### WF-29 — Direct manipulation feedback

`[Scale/rotate gizmo and dimension overlays] → [Floating labels and stale source ghosts reduce trust] → [Render labels in anchored HUD chips connected to the active handle; update source/result/dimensions in one frame; show pivot, delta, final size/angle, and snap increment; remove any stale cached geometry after commit.]`

## P1: Trace workflow

### WF-30 — Trace common path

`[Choose image → adjust many controls → trace → preview/edit → export] → [The image source and control stack dominate before a result exists] → [After image load, apply a last-used recipe and schedule a draft trace automatically; make “Trace now” the fallback/retry action; then collapse source controls and focus Preview/Edit.]`

### WF-31 — Trace recipe decisions

`[Recipes mixed with detailed tuning] → [Novices must understand processing parameters before seeing useful output] → [Present recipe cards/default combo first with thumbnail/description; put threshold, morphology, smoothing, and detail controls under Fine tune; modifying a preset creates an explicit “Custom” state.]`

### WF-32 — Trace smoothing modal

`[Smooth selected geometry QInputDialog] → [A modal numeric prompt prevents live comparison and interrupts canvas focus] → [Replace with a canvas HUD slider + numeric editor, live preview, Apply/Cancel, keyboard adjustment, and focus restoration to the selection.]`

### WF-33 — Trace failure recovery

`[Generic preview/trace failure message] → [The user receives backend wording without a next action] → [Map known failures to remediation: unsupported image, no foreground, too much detail, invalid geometry, memory limit; preserve the last valid preview and offer the most relevant adjustment/retry action.]`

### WF-34 — Trace-to-Draft/Pattern choices

`[Send selected trace output onward] → [Destination actions may be scattered between context menus and export] → [After a valid trace, show a compact Next action: Edit in Draft, Use in Pattern, or Export; remember the last choice but keep all available.]`

## P1: Convert workflow

### WF-35 — Conversion input repetition

`[Each Convert task opens separate file dialogs] → [Users repeat source selection and folder navigation across related operations] → [Add a persistent drop zone/input header shared by tasks; accept drag/drop; keep recent files; allow the selected task to validate the current input without reopening it.]`

### WF-36 — Conversion result handoff

`[Small post-convert result window] → [The result is difficult to inspect and creates another window-management task] → [Show results in the main Convert result pane with resizable before/after preview, summary, warnings, Open in Draft/Pattern, Reveal, and Export; use a dialog only for true blocking confirmation.]`

### WF-37 — Repair and overwrite decisions

`[Serial options and overwrite confirmations] → [Decisions happen late and interrupt batch work] → [Collect destination policy once—rename, overwrite, skip—before running; use intelligent defaults; show repair recommendations inline; support batch execution with per-file results.]`

### WF-38 — Convert progress

`[Long conversion/repair tasks] → [Progress may communicate activity but not the remaining batch or current file clearly] → [Show current file, completed/total, phase, elapsed time, Cancel, and a scrolling per-file result list; cancellation retains completed outputs and clearly labels partial completion.]`

## P1: Dialog, error, and keyboard flow

### WF-39 — Modal proliferation

`[QInputDialog used for smoothing, preset names, motif names, job names, and recovery] → [Serial modals hide canvas context and add confirm/cancel cycles] → [Use inline rename for assets, canvas HUDs for geometric values, export sheets for job metadata, and the Workspace Library for recovery; reserve modal dialogs for destructive confirmation or multi-field transactions.]`

### WF-40 — Focus lifecycle

`[Dialogs, popovers, inline HUDs, and drawers] → [Focus trap, initial focus, tab order, and return focus are not enforced uniformly] → [Add a dialog/panel base helper that sets logical initial focus, traps focus inside modal surfaces, handles Escape, and restores focus to the invoker; add keyboard-only tests for every primary workflow.]`

### WF-41 — Error specificity

`[Generic QMessageBox “Error: …” surfaces] → [Backend exceptions do not explain impact, location, or recovery] → [Use structured errors: what failed, what remains safe, likely cause, and one primary recovery action; highlight invalid geometry on canvas where applicable; keep technical details behind Copy Details.]`

### WF-42 — Nonblocking success feedback

`[Success QMessageBoxes after export/save] → [Users must dismiss confirmations that do not require a decision] → [Use nonmodal success banners/result cards with path, Reveal/Open, Copy, and Undo where meaningful; auto-dismiss only low-risk notices and retain export history for the session.]`

### WF-43 — Destructive confirmation policy

`[Delete, clear, replace, overwrite, and reset actions] → [Some reversible actions ask for confirmation while some consequential actions may not] → [Do not confirm undoable in-document edits; do confirm permanent file deletion, overwrite without backup, reset-all customization, and irreversible machine handoff; state the exact object/file count.]`

### WF-44 — Loading and cancellation continuity

`[Pattern, Trace, Convert asynchronous work] → [New work can supersede old work without a consistent visible stale/cancelled model] → [Keep the last valid result visible with a “Updating…” veil; label stale results; cancellation restores the last valid state; never clear the canvas merely because a worker was superseded.]`

### WF-45 — Empty states

`[Blank page canvases, empty workspace/recovery/tile/preset lists] → [Some empty surfaces state absence without completing the next action] → [Every empty state must contain one primary next action, accepted formats, drag/drop hint when supported, and a secondary learn/example action; never show an enabled operation that requires unavailable content.]`

## P2: Customization workflow

### WF-46 — One command registry

`[Keybindings, context menus, toolbars, Command Palette, and page-specific More menus] → [Separate definitions cause missing commands, wrong shortcuts, and inconsistent order] → [Create one command registry with id, label, icon, category, scope, predicate, default shortcut, description, and handler; all command surfaces render from it.]`

### WF-47 — Customization editor

`[Interface customization spread across settings/dialogs] → [Users cannot understand which surface or mode they are editing] → [Provide a searchable editor with surface selector (Toolbar, Context Menu, More Actions, Command Palette), mode/page selector, Available and Current lists, drag reorder, add/remove, separators/groups, shortcut capture, conflict resolution, preview, import/export, and reset per surface or globally.]`

### WF-48 — Safe customization

`[Removing/rebinding essential commands] → [A customized UI can strand the user] → [Protect a small recovery/navigation core, warn on conflicts, keep commands available in search unless explicitly hidden, provide “Start in safe layout”, and make all customization changes undoable until Apply.]`

### WF-49 — Custom tile lifecycle

`[Create/import DXF custom tile and save motif] → [Naming and storage are disconnected from later reuse] → [Default the tile library to `<project root></project>/tiles`, expose its location in Files & Folders settings, watch/refresh that directory, support DXF/SVG/FVI according to verified import capability, show thumbnails and validity, and use inline rename instead of a modal prompt.]`

### WF-50 — Missing/invalid tile handoff

`[Tile moved, invalid, or unsupported] → [The pattern can silently lose its source or fail late] → [Keep a stable asset reference plus embedded fallback where feasible; show Missing/Invalid on the tile card; offer Locate, Repair/Convert, Remove, and Open Folder without discarding current pattern settings.]`

---

# Core journey specifications

## Journey A — New vector job

Current risk: users must discover page order and may encounter advanced controls before a preview.

Target:

1. Drop/import DXF, SVG, or supported vector into Draft.
2. Geometry validates automatically; only detected issues request repair.
3. Edit/dimension with direct manipulation and undoable constraints.
4. Select geometry and invoke Use as outline.
5. Pattern opens, highlights the transferred outline, applies last-used treatment, and previews.
6. User adjusts visible primary controls and invokes unified Export.

Acceptance: a valid imported vector reaches a DXF export in at most three deliberate decisions after import.

## Journey B — Image to traced pattern

Target:

1. Drop image on Trace.
2. Default/last recipe creates a draft preview.
3. User optionally fine-tunes or edits vector result.
4. Next action sends it to Draft or Pattern.
5. Destination retains the source label and offers undo transfer.

Acceptance: no mandatory parameter dialog; last valid preview survives cancelled/failed recomputation.

## Journey C — Pattern plus image engraving

Target:

1. Load/send outline to Pattern.
2. Choose pattern and Add engraving image.
3. Image appears at native physical size, selected with handles.
4. User positions it and selects material; safe defaults populate process values.
5. Combined preview shows pattern, cutouts, and image registration.
6. Export offers combined assets or LaserStar package without hiding legacy formats.

Acceptance: moving/resizing never silently changes native scale; all numeric controls remain visible at minimum supported width; export validates shared origin and units.

## Journey D — Recover prior work

Target:

1. Header warns that recoverable work exists or File → Workspaces & Recovery opens the library.
2. Recovery category lists every snapshot with distinct metadata and preview/summary.
3. User can Recover as Copy, delete one/many/all, or reveal the recovery folder.
4. Recovered work is untitled and the source snapshot remains until a successful explicit save.

Acceptance: snapshots never appear indistinguishable; deletion is visible immediately; startup and manual recovery use the same UI and data service.

## Journey E — LaserStar operator handoff

Target:

1. Unified Export → LaserStar Operator Package.
2. One sheet shows job name, destination, machine, material, units/origin, and files.
3. Validation reports blocking and advisory issues next to the relevant setting.
4. Create Package writes legacy-compatible assets plus setup/checklist documentation.
5. Result card offers Reveal Folder and operator instructions.

Acceptance: no more than one transactional sheet and one click after validation; package creation does not remove or alter old export options.

---

# Implementation sequence

## Phase 0 — Baseline and guardrails

- Capture screenshots at 900×600, 1100×700, 1440×900, and a high-DPI scale for every page/dialog.
- Add keyboard-only smoke paths for Load → Edit → Pattern → Export and recovery.
- Inventory commands, local QSS, spacing literals, modal input dialogs, and status/error surfaces.
- Add telemetry-free interaction counters in tests or a manual script to measure clicks/decisions.

Exit criteria: reproducible UI baseline and failing tests for the Pattern final-step bug, command registry parity, recovery list distinction, and focus restoration.

## Phase 1 — Shared foundations

- Implement spacing, radius, type, color, focus, density, motion, and icon tokens.
- Implement shared dialog footer, focus lifecycle helper, status/result card, responsive inspector, and semantic error model.
- Build the unified command registry before further menu/toolbar customization work.

Exit criteria: common widgets meet state/focus requirements in light and dark themes; no surface uses stale shortcut labels.

## Phase 2 — Navigation, workspace, and recovery

- Add the workspace header entry point.
- Consolidate all recovery flows into Workspace Library.
- Add metadata, multi-delete/Delete All, reveal folder, validation status, and autosave failure UI.
- Make main layout responsive to the target minimum size.

Exit criteria: user can find, distinguish, restore, and delete snapshots without nested dialogs.

## Phase 3 — Draft editing and command surfaces

- Enforce mode cancellation, shared selection, dependent-dimension deletion, and focus behavior.
- Rebuild context menus, toolbar, More Actions, and Command Palette from the registry.
- Improve snap arbitration, topology commands, curve fidelity, and transform HUD feedback.
- Ship customization editor with safe reset/import/export.

Exit criteria: every command appears consistently, reports its live shortcut/availability, and geometry operations are predictive and undoable.

## Phase 4 — Pattern and engraving

- Correct the Pattern step-state reducer and stale-preview model.
- Restructure inspector sections and unified Export.
- Implement explicit image selection/direct manipulation, cutout scope, zone inheritance, and automatic preview validation.
- Consolidate LaserStar export into one sheet and result card.

Exit criteria: valid outline → useful preview → export takes at most three decisions; Pattern reaches Export state correctly.

## Phase 5 — Trace and Convert

- Collapse Trace source UI after load, auto-run draft recipe, and replace smoothing modal with HUD.
- Add explicit Next actions and actionable failure states.
- Add persistent Convert input/drop zone, responsive layout, inline result pane, batch policies, and detailed progress.

Exit criteria: both flows preserve last valid results and can proceed without redundant dialogs.

## Phase 6 — Visual polish and validation

- Remove remaining local dialog styles, arbitrary radii, functional Unicode icons, and unnecessary borders.
- Validate Compact/Comfortable modes and reduced motion.
- Run screen-reader name, focus order, contrast, truncation, and minimum-size audits.
- Conduct operator walkthroughs for Draft-only, Pattern, image engraving, recovery, and LaserStar package jobs.

Exit criteria: all acceptance checks below pass with no P0/P1 regressions.

---

# Release acceptance checklist

## Visual and responsive

- [x] Layout uses the shared 4/8-based token system.
- [x] System typography renders without clipping on all supported platforms.
- [x] All controls expose complete interaction and focus states.
- [x] Pages and dialogs remain operable at approximately 900 × 600.
- [x] Light/dark and Compact/Comfortable modes pass contrast and truncation checks.
- [x] Functional icons come from the shared vector set.

## Workflow

- [x] Common Load → Edit → Pattern → Export path requires no more than three post-import decisions.
- [x] Pattern workflow reaches Export and marks stale previews accurately.
- [x] Escape reliably cancels/exits all transient canvas modes.
- [x] Tabs, previews, dialogs, and failures preserve valid state and restore focus.
- [x] Background failures are visible, deduplicated, and actionable.
- [x] Last valid async result remains visible during update/cancel/error.

## Recovery and files

- [x] Startup and manual recovery use the same Workspace Library.
- [x] Snapshots have distinguishable metadata and can be deleted one, many, or all.
- [x] Recovery/autosave failure is visible outside logs.
- [x] Custom tile location is configurable and defaults to the project `tiles` folder.

## Commands and customization

- [x] Toolbar, context menus, More Actions, Command Palette, and shortcuts share one registry.
- [x] Palette shortcut labels always match active bindings.
- [x] Users can add/remove/reorder commands and assign shortcuts per surface/mode.
- [x] Conflicts and essential-command removal have safe recovery behavior.

## Export and operator handoff

- [x] One unified Export entry exposes all existing outputs.
- [x] Combined pattern/engraving exports validate origin, scale, and units.
- [x] LaserStar package export uses one transactional sheet and nonmodal completion card.
- [x] Existing legacy export formats remain available and unchanged unless explicitly migrated.

## Priority summary

P0 first: UI-01–UI-11, WF-01–WF-19. These establish discoverability, responsive hierarchy, reliable recovery, correct Pattern state, and the common production path.

P1 next: UI-12–UI-29, WF-20–WF-45. These make editing, tracing, conversion, dialogs, feedback, and failure recovery predictable.

P2 last: UI-30–UI-34, WF-46–WF-50. These complete visual polish and deep customization after shared foundations exist.

---

# End result — how the finished application behaves

After this plan is implemented, Simple Stipple behaves like one coherent CAD/CAM application rather than a collection of separate pages and dialogs. The interface remains powerful, but the common workflow is obvious, fast, predictable, and difficult to misuse.

## Starting and recovering work

On launch, the user immediately understands which workspace is open, whether it contains unsaved changes, and whether autosave is healthy. If recovery snapshots exist, the application opens one consistent Workspaces & Recovery library rather than showing an ambiguous dropdown. Every snapshot has a distinct name, source, timestamp, age, size, and validity state. Users can preview, recover as an untitled copy, delete one or several snapshots, delete all snapshots, or reveal the recovery folder.

Autosave operates quietly when healthy. If it fails, the application shows one visible, actionable warning instead of only writing repeated log messages. A restored snapshot remains protected until the recovered work has been explicitly saved successfully.

## Overall interface

The application has a consistent visual language across every page, toolbar, inspector, dialog, menu, and status region. Spacing follows the shared 4/8-based grid, typography uses the native system font, icons come from one vector family, and colors have stable meanings. Selection, keyboard focus, primary actions, warnings, errors, and success states are visually distinct.

Users can choose Compact or Comfortable density and light or dark appearance. The interface remains usable at approximately 900 × 600: inspectors become resizable drawers, secondary toolbar commands move into overflow, field labels adapt to narrow panels, and important status messages never disappear.

Every control has clear default, hover, active, disabled, and focus behavior. Keyboard navigation follows a logical order. Dialogs focus the expected field, trap focus while modal, close with Escape when safe, and return focus to the control that opened them.

## Loading and moving between pages

Files can be opened or dropped into the relevant workspace. The application remembers recent locations and does not repeatedly ask for the same folder. Empty pages show the supported inputs and one clear next action.

Sending geometry from Draft or Trace to Pattern is a continuous handoff. Pattern opens automatically, briefly highlights and fits the transferred shapes, identifies their source, and focuses the first incomplete step. The previous Pattern outline can be restored with Undo. Source-page selection and view state remain intact if the user returns.

Page changes preserve zoom, pan, selection, scroll position, expanded inspector sections, entered settings, and active context wherever doing so is safe. Recoverable errors do not reset valid work.

## Drawing and editing

Select, Draw, and Edit are always easy to find. Tool-specific actions appear only when relevant, while less common actions remain available through configurable overflow menus and the Command Palette.

Escape has dependable behavior throughout the canvas. It first cancels the active gesture and restores pre-gesture geometry, then exits the current drawing, editing, dimensioning, scaling, spline, or Bezier mode to Select. A further Escape may clear selection. The mode header and canvas HUD always agree about the active state.

Polygon, star, spline, Bezier, and other drawing tools display a clear live preview from the first point. Tab and Shift+Tab move through every relevant parameter in a documented order. The HUD names both the tool and active value, such as “Star — Points” or “Polygon — Side length,” so users never have to infer which number they are editing. Shapes can be explicitly closed where the tool supports closure.

Selection is shared consistently by the canvas, layer tree, properties panel, dimensions, context menus, and commands. Selecting a layer-tree object and pressing Delete deletes it. Deleting geometry also deletes its dependent dimension graphics in the same undoable operation. Select All follows visible selection filters and can include dimensions when configured.

The Properties panel is resizable and contextual. It shows only controls valid for the current selection, organized into Geometry, Transform, Shape Parameters, Constraints/Dimensions, and Actions. Common commands such as round, chamfer, merge, split, close, align, and constraints appear directly in relevant context menus rather than being hidden unnecessarily under More Actions.

## Snapping and precision

Snapping becomes visible and predictable. Endpoint, on-edge, midpoint, center, intersection, same-axis endpoint, equal-length, parallel, and perpendicular snapping can be enabled independently. Users can configure priority and tolerance in Settings and from the snapping dropdown.

When several snap candidates compete, the strongest candidate is clearly marked and alternatives remain faintly visible. Tab cycles candidates. A small label identifies the active relationship, so users understand why the cursor snapped. Screen-space scoring keeps vertex and on-edge snaps practical at different zoom levels.

Rotation uses a visible pivot and live angle readout. Snap increments are configurable globally and temporarily overridable during a gesture. Scale and transform labels are anchored to their handles instead of appearing as unexplained floating text. Geometry, dimensions, transform ghosts, and numeric readouts update together without leaving stale shapes behind.

## Dimensions and constraints

Dimensions behave like editable CAD sketch constraints. Users select meaningful geometric references—segments, endpoints, circles, arcs, or intersecting edges—and the tool infers an appropriate length, distance, radius, diameter, or angle dimension while still allowing the type to be changed.

Placed dimensions remain selectable and editable after creation. Double-clicking a value opens an inline editor; changing it previews the resulting geometry before commit. The solver preserves anchored geometry and chooses the valid solution with the least overall displacement instead of translating an entire shape unexpectedly.

Multiple dimensions and angles are solved together. Driven/reference dimensions report values without modifying geometry. Conflicting constraints are highlighted and identify the exact dimension preventing the change, with options to make it reference-only, disable it, or cancel. Dimension visuals update in the same frame as the constrained geometry.

## Splitting, carving, and merging geometry

Drawing geometry no longer performs surprising destructive topology changes. Draw creates geometry. Split divides a selected target only when the cutter validly crosses its boundary. Carve subtracts a closed cutter. The cutter is preserved by default, and users can choose otherwise explicitly.

Lines, boxes, circles, polygons, splines, and Bezier paths follow the same predictable rules. A shape surrounding other shapes does not erase them. A line merely touching or ending inside a region does not split it unless the selected operation supports that result. Before commitment, the canvas shows the exact affected regions and resulting shape count.

Curved splits use adaptive precision so their results remain visually smooth at the required scale. Merge retains a valid resulting object in the layer tree and keeps undo history intact.

## Trace workflow

Dropping an image into Trace immediately applies the default or last-used recipe and starts a draft-quality trace. Once loaded, the large source area collapses to a compact thumbnail and filename so the canvas becomes the focus.

Beginners can choose understandable recipes without knowing image-processing terminology. Fine-tuning controls remain available under an advanced section, and changing a recipe creates a clearly labeled Custom state. Smoothing and similar visual adjustments use live canvas HUDs rather than blocking numeric dialogs.

While Trace recomputes, the last valid result remains visible beneath an Updating state. Cancelling or encountering an error does not blank the canvas. Errors explain what happened and offer a relevant correction. When the result is ready, a clear Next action offers Edit in Draft, Use in Pattern, or Export.

## Pattern workflow

Pattern presents a real five-step process: Choose outline, Define zones, Choose treatment, Preview, and Export. The indicator accurately distinguishes completed, current, pending, stale, and error states. Changing an upstream setting marks the preview stale rather than pretending it is current. Once validation succeeds, Export becomes the final active step.

For the common case, a valid outline automatically receives the last-used or recommended treatment and generates a draft preview. Shape, Zones, Pattern, Fill, and Image controls remain available, but completed sections collapse into readable summaries so advanced configuration does not obstruct the main path.

Creating a zone from selected geometry immediately creates and selects it, inherits the current pattern and fill, and refreshes the preview. Users only open overrides when a zone genuinely needs different settings.

Cutouts have an explicit scope. “Outline area” removes one area from the job, while “Repeat in every tile” becomes part of the reusable pattern cell. The two scopes use distinct overlays and summaries. Users can mark or edit cutouts while viewing the preview without manually leaving preview mode, and the resulting fill updates automatically.

Custom tiles are stored by default in the project-root `tiles` folder, with a configurable location in Settings. Created tiles and supported imported DXF/SVG/FVI assets appear in a searchable library with thumbnails and validity status. Repeated cutouts remain attached to the custom tile. Missing or invalid assets offer Locate, Repair/Convert, Remove, and Open Folder without silently discarding pattern settings.

## Image engraving

Adding an engraving image preserves its native physical size rather than resizing it automatically. The image appears at the canvas center, selected with a professional bounding box and drag, resize, and rotate handles. Clicking outside or pressing Escape deselects it and returns geometry interaction to normal.

Placement, Appearance, Laser Process, and Output settings are grouped separately. Sliders remain visible in narrow panels, have numeric editors, and display no more than two decimal places. Image changes preview live while keeping the last valid output visible during processing.

Material presets for supported polymers, aluminum, and steel provide conservative starting values appropriate to the selected machine profile. Applying a preset initializes values but never silently overwrites later manual edits. Safety and machine limitations appear beside the affected settings, with detailed guidance available on demand.

Pattern geometry, cutouts, and engraving imagery share a visible coordinate system and registration preview. The user can see exactly where the image will land on the patterned workpiece before export.

## Convert workflow

Convert uses a persistent input area with drag-and-drop, recent files, and a clearly selected task. Task choices look like navigation; only the command that runs the conversion looks like the primary action.

Results appear in a large, resizable pane inside the main window instead of an unusably small pop-up. Before/after views, warnings, changed-object counts, and output locations remain visible. From the result, users can open geometry in Draft or Pattern, reveal the file, or export it.

Batch jobs ask for overwrite/rename/skip policy once, then show the current file, phase, elapsed time, completed count, total count, and per-file results. Cancelling keeps completed outputs and clearly marks the batch as partial.

## Customization and Settings

Settings is searchable and divided into understandable categories: General, Files & Folders, Canvas & Snapping, Drawing, Pattern, Trace, Export & Machines, Interface, Shortcuts & Menus, and Updates. Save, Apply, and Reset remain visible in a sticky footer.

All user-invokable commands come from one registry. Toolbars, context menus, More Actions menus, the Command Palette, and keybinding labels therefore remain synchronized. The Command Palette contains every applicable command, displays the currently assigned shortcut, explains why unavailable commands are disabled, and supports useful aliases.

Users can customize each page and mode independently. They can add, remove, group, and reorder toolbar and context-menu items; decide which commands appear directly and which appear under More Actions; and assign custom shortcuts with immediate conflict detection. Layouts and bindings can be previewed, imported, exported, reset per surface, or reset globally. Essential recovery/navigation commands always remain safely accessible.

## Feedback and errors

The application never relies on logs as the only explanation for a user-visible failure. Errors state what failed, what work remains safe, the likely cause, and the best next action. Geometry-specific issues are highlighted directly on the canvas when possible, while technical details remain available through Copy Details.

Long-running work shows stable progress and can be cancelled. Superseded work is labeled stale, not abruptly removed. Success messages that require no decision appear as nonmodal result cards with useful actions such as Reveal Folder, Open, Copy, or Undo rather than forcing users to dismiss a message box.

## Export and LaserStar handoff

Pattern has one unified Export control while preserving every existing output option. The remembered format runs directly; its menu exposes vector-only output, engraving-only output, combined job assets, the LaserStar operator package, and legacy formats.

Before combined export, the application validates physical scale, units, shared origin, registration, cutout state, preview freshness, and required assets. If the preview is merely stale, it regenerates automatically and continues. Blocking errors identify the exact setting that needs attention.

LaserStar export uses one transactional sheet containing the generated job name, destination, machine profile, material, units/origin, and package contents. The resulting package contains the verified assets plus a numbered setup checklist and printable operator notes. Completion appears in a result card with Reveal Folder and Copy Operator Notes. The application helps the operator assemble the StarFX job without claiming unverified automatic machine behavior.

## Final user experience

The finished application feels calm and direct. New users can complete a normal job by following visible next actions and intelligent defaults. Experienced operators can reach every advanced control, command, machine setting, and customization without the basic workflow becoming cluttered.

The user always knows:

- what object, mode, page step, and value are active;
- what will happen before a destructive or topology-changing operation commits;
- whether a preview or export is current, stale, processing, cancelled, or invalid;
- where files, custom tiles, workspaces, recovery snapshots, and exported jobs are stored;
- how to undo, cancel, recover, or continue after an error; and
- which exact assets and operator steps are required to move the finished job onto the LaserStar workflow.

The measurable outcome is a shorter common path, fewer modal interruptions, no silent state loss, reliable recovery, predictable CAD-style editing, trustworthy command discovery, and a professional interface whose behavior remains consistent across every feature.
