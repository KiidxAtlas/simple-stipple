# Plan: Hoist per-page default settings into top-of-file constants

Goal: every tunable default (blur radius, fill spacing, debounce times, grid
defaults, …) is declared once, in an obvious constants block at the top of the
page that owns it, so changing a default is a one-line edit. No behavior
changes — every constant is introduced with its current value.

Line numbers below are correct as of this commit and may drift; each step
names the exact literal so it can be re-found with grep.

---

## Conventions (apply to every step)

- Constants are module-level `UPPER_SNAKE`, prefixed `DEFAULT_` for values a
  user might want to retune, grouped directly under the imports beneath a
  banner comment:

  ```python
  # ── Page default settings ──────────────────────────────────────────────
  ```

- String defaults that feed `QLineEdit(default)` stay strings (`"0.5"`), so
  `make_resettable_line_edit`'s reset-to-default keeps working unchanged.
  Numeric clamps/bounds are floats/ints.
- A default that is already user-configurable through the Settings dialog
  keeps its single source of truth (`src/infra/settings.py` `DEFAULT_*`,
  `src/ui/pages/trace/form.py` `TRACE_DEFAULTS`). Do **not** duplicate those —
  this plan only hoists literals that currently have no named home.
- One commit per numbered step; each step leaves the app working.

## What already has a home (do not move)

- Per-pattern generator parameters → `src/ui/pages/pattern/_spec.py`
  (`PARAM_SPECS`, declarative, already the model to follow).
- Trace detection defaults → `src/ui/pages/trace/form.py` (`TRACE_DEFAULTS`,
  user-overridable via Settings).
- App-wide behavior defaults → `src/infra/settings.py` (`DEFAULT_SMOOTHING_METHOD`,
  `DEFAULT_SIMPLIFY_TOLERANCE`, sidebar bounds, …).
- Canvas visual constants → `src/ui/canvas/constants.py`.

---

## Step 1 — Pattern page: create `src/ui/pages/pattern/defaults.py`

Why a sibling module instead of literally the top of `tab.py`: these values
are needed by **both** `tab.py` (widget construction) and `params.py`
(`restore_form_state` fallbacks). `tab.py` imports `params.py`, so putting
them in `tab.py` would create a circular import. `defaults.py` is imported at
the top of both files, which keeps the "open one obvious place to change a
default" property.

Create `src/ui/pages/pattern/defaults.py` with exactly the current values:

```python
"""Default settings for the Pattern page — edit here to change defaults."""

# Modifiers
DEFAULT_PATTERN_ROTATION = "0"        # degrees
DEFAULT_BORDER_FADE = "0"             # mm; 0 = off
DEFAULT_DENSITY_STRENGTH = "0.75"     # 0..1
DEFAULT_DENSITY_ANGLE = "0"           # degrees
DEFAULT_DENSITY_MODE = "Uniform"

# Fill
DEFAULT_FILL_MODE = "none"            # none | lines | crosshatch
DEFAULT_FILL_SPACING = "0.5"          # mm
DEFAULT_FILL_ANGLE = "0"              # degrees
DEFAULT_FILL_INSET = "0"              # mm
FILL_SPACING_FLOOR_MM = 0.05          # hard lower clamp when parsing

# Export / fabrication
DEFAULT_MIN_SEGMENT = "0"             # mm; 0 disables
DEFAULT_MIN_ISLAND_AREA = "0"         # mm²; 0 disables
DEFAULT_PREVIEW_QUALITY = "balanced"  # fast | balanced | high

# Scale field bounds
SCALE_MIN_MM = 0.001
SCALE_MAX_MM = 1e9

# Canvas
DEFAULT_GRID_VISIBLE = True
DEFAULT_GRID_SPACING_MM = 1.0

# Preview scheduling
PREVIEW_DEBOUNCE_MS = 400
```

## Step 2 — Pattern page: replace the literals in `tab.py`

Import the constants at the top of `src/ui/pages/pattern/tab.py`, then
replace each literal:

| Current literal | Location (tab.py) | Replace with |
|---|---|---|
| `QLineEdit("0")` rotation | ~line 964 | `QLineEdit(DEFAULT_PATTERN_ROTATION)` (also the `make_resettable_line_edit(..., "0")` second arg) |
| `QLineEdit("0")` border fade | ~974 | `DEFAULT_BORDER_FADE` |
| `QLineEdit("0.75")` density strength | ~990 | `DEFAULT_DENSITY_STRENGTH` |
| `QLineEdit("0")` density angle | ~997 | `DEFAULT_DENSITY_ANGLE` |
| `QLineEdit("0.5")` fill spacing | ~1113 | `DEFAULT_FILL_SPACING` |
| `QLineEdit("0")` fill angle | ~1120 | `DEFAULT_FILL_ANGLE` |
| `QLineEdit("0")` fill inset | ~1127 | `DEFAULT_FILL_INSET` |
| `QLineEdit("0")` min segment | ~1238 | `DEFAULT_MIN_SEGMENT` |
| `QLineEdit("0")` min island | ~1245 | `DEFAULT_MIN_ISLAND_AREA` |
| `setCurrentIndex(1)` preview quality | ~1216 | `setCurrentIndex(max(0, combo.findData(DEFAULT_PREVIEW_QUALITY)))` |
| `QDoubleValidator(0.001, 1e9, …)` on `_scale_w`/`_scale_h` | ~843, ~859 | `QDoubleValidator(SCALE_MIN_MM, SCALE_MAX_MM, …)` |
| `minimum=0.001` in `_collect_scale` | ~687–698 | `minimum=SCALE_MIN_MM` |
| `max(0.05, float(...))` fill spacing floor | ~1586 | `max(FILL_SPACING_FLOOR_MM, ...)` |
| fallback strings `or "0.5"` / `or "0"` in `_collect_fill_options` / `_collect_fabrication_options` / border-fade parses | ~1586–1614, ~1665, ~1987 | `or DEFAULT_FILL_SPACING` etc. — same constant as the widget |
| `set_grid_visible(True)` / `set_grid_spacing(1.0)` | ~271–273 | `DEFAULT_GRID_VISIBLE` / `DEFAULT_GRID_SPACING_MM` |
| `self._preview_timer.start(400)` | ~1636 | `self._preview_timer.start(PREVIEW_DEBOUNCE_MS)` |

Leave every `start(0)` call alone (those mean "run now", not a setting).

## Step 3 — Pattern page: kill the duplicate fallbacks in `params.py`

`restore_form_state` in `src/ui/pages/pattern/params.py` (~lines 221–247)
re-hardcodes the same defaults (`"0"`, `"0.75"`, `"0.5"`, `"Uniform"`,
`"balanced"`, `"none"`). Import from `defaults.py` and replace each fallback:

- `values.get("rotation", "0")` → `values.get("rotation", DEFAULT_PATTERN_ROTATION)`
- `values.get("border_fade", "0")` → `DEFAULT_BORDER_FADE`
- `values.get("density_mode", "Uniform")` → `DEFAULT_DENSITY_MODE`
- `values.get("density_strength", "0.75")` → `DEFAULT_DENSITY_STRENGTH`
- `values.get("density_angle", "0")` → `DEFAULT_DENSITY_ANGLE`
- `values.get("fill_mode", "none")` → `DEFAULT_FILL_MODE`
- `values.get("fill_spacing", "0.5")` → `DEFAULT_FILL_SPACING`
- `values.get("fill_angle", "0")` / `values.get("fill_inset", "0")` → matching constants
- `values.get("minimum_segment", "0")` / `values.get("minimum_area", "0")` → matching constants
- `values.get("preview_quality", "balanced")` and the `or "balanced"` in
  `collect_form_state` (~line 188) → `DEFAULT_PREVIEW_QUALITY`

This is the step that actually earns the refactor: today a default changed in
`tab.py` silently disagrees with the fallback used when restoring an old
preset/workspace.

## Step 4 — Trace page: constants block at top of `src/ui/pages/trace/tab.py`

`TRACE_BG_COLOR` / `TRACE_BG_BLEND_ALPHA` already sit at the top — extend that
block:

```python
# ── Page default settings ──────────────────────────────────────────────
TRACE_DEBOUNCE_MS = 220            # retrace delay after a control changes
BLUR_SLIDER_MAX = 50               # slider is 0..50 = 0.0..5.0 (×10 fixed-point)
BLUR_SLIDER_SCALE = 10
THRESHOLD_SLIDER_MAX = 255
DEFAULT_GRID_VISIBLE = True
DEFAULT_GRID_SPACING_MM = 1.0
```

Replacements:

- `self._preview_timer.start(220)` (~line 896) → `TRACE_DEBOUNCE_MS`.
- `self._blur_slider.setRange(0, 50)` (~427) → `setRange(0, BLUR_SLIDER_MAX)`;
  the two `* 10` / `/ 10` conversions in `_on_blur_text` / `_on_blur_slider`
  (~441, ~449) → `BLUR_SLIDER_SCALE`.
- **Fix a real inconsistency while here:** `self._blur_slider.setValue(15)`
  (~428) hardcodes 1.5 while the field default `trace_default(settings, "blur")`
  is `"1.0"` (`TRACE_DEFAULTS` in `form.py`). Derive the initial slider
  position from the field instead:
  `setValue(int(float(trace_default(self._settings, "blur")) * BLUR_SLIDER_SCALE))`.
  Same for `self._thresh_slider.setValue(128)` (~434) → derive from
  `trace_default(..., "threshold")`.
- `set_grid_visible(True)` / `set_grid_spacing(1.0)` (~578–580) → constants.
- Do **not** move the numeric detection defaults themselves — they live in
  `form.py:TRACE_DEFAULTS` and are user-editable in Settings. Add a one-line
  comment in the new block pointing there:
  `# Detection defaults (blur, threshold, …) live in trace/form.py TRACE_DEFAULTS.`

## Step 5 — Draft page: constants block at top of `src/ui/pages/draft.py`

`DraftPage.DEFAULT_LAYER` already exists as a class constant; keep it. Add at
module top:

```python
# ── Page default settings ──────────────────────────────────────────────
DEFAULT_QUICK_SHAPE_MODE = "rectangle"
COMMAND_GUIDANCE_POLL_MS = 120
VECTOR_IMPORT_EXTENSIONS = (".dxf", ".fvi", ".svg")
```

Replacements:

- `set_quick_shape_mode("rectangle", flash=False)` in
  `clear_draft_workspace_state` (~line 986) → `DEFAULT_QUICK_SHAPE_MODE`.
- `self._command_status_timer.setInterval(120)` (~93) → `COMMAND_GUIDANCE_POLL_MS`.
- The duplicated `(".dxf", ".fvi", ".svg")` tuples in `dragEnterEvent` /
  `dropEvent` (~606, ~614) → `VECTOR_IMPORT_EXTENSIONS`.

## Step 6 — Convert page: constants block at top of `src/ui/pages/convert.py`

```python
# ── Page default settings ──────────────────────────────────────────────
DEFAULT_GRID_VISIBLE = True
DEFAULT_GRID_SPACING_MM = 1.0
LOG_PANEL_MAX_HEIGHT = 140
```

Replacements: preview-canvas grid setup (~1256–1258) and
`self._log.setMaximumHeight(140)` (~1281).

Deliberately **not** hoisted: `_scroll.setMaximumHeight(560)` (~1162) — that
cap is the audit's F8 layout bug and is slated for deletion, not for a named
constant that legitimizes it.

## Step 7 — Shared grid-spacing clamp: one constant, two consumers

The grid-spacing clamp `0.1 … 100.0` mm is currently duplicated in three
places and can drift:

- `src/ui/widgets/precision_bar.py` ~203 and ~221 (`max(0.1, min(100.0, …))`)
- `src/ui/canvas/interaction/commands.py` ~93 (`min(100.0, …)`) and ~98 (`max(0.1, …)`)

Add to `src/ui/canvas/constants.py` (the existing "single source of truth"
module, imported by both files' packages already):

```python
# ── Grid spacing bounds (mm) ─────────────────────────────────────────────
GRID_SPACING_MIN_MM = 0.1
GRID_SPACING_MAX_MM = 100.0
```

Replace all four literals with the constants.

## Step 8 — Verification

1. `grep` for the retired literals to confirm none survive outside the
   constants blocks:
   `grep -rn 'QLineEdit("0' src/ui/pages/pattern/tab.py`,
   `grep -rn 'start(400)\|start(220)\|setValue(15)\|max(0.1, min(100' src/ui`.
2. Run the test suite: `uv run pytest` (workspace round-trip tests in
   `tests/test_workspace_roundtrip.py` cover the params.py restore path
   touched in Step 3).
3. Smoke-check each page renders and behaves:
   - Pattern: type a fill spacing, clear it with the field's ✕ — it must
     reset to `0.5`; save + reload a workspace — restored values unchanged.
   - Trace: open the page — blur field and blur slider must now agree (both
     `1.0`); drag the slider — field updates.
   - Draft: New workspace — quick shape resets to Rectangle.
   - Convert: run any conversion — log panel and preview grid unchanged.
4. Optional: rerun the offscreen render script from the audit
   (`scratchpad/render_screens.py`) and diff screenshots — they should be
   pixel-identical except the Trace blur slider position (the Step 4 fix).

## Explicitly out of scope

- Changing any default's *value* (except the Trace blur-slider/field
  mismatch, which is a bug fix — the field value `1.0` wins).
- Layout dimensions (splitter sizes, panel widths, button heights) — those
  are geometry, not settings, and several are already flagged for redesign
  in the audit.
- Moving Settings-dialog-backed defaults out of `infra/settings.py` or
  `trace/form.py` — they already have a single home.
