c

# Architecture

Simple Stipple is a capability-first modular monolith and installed `src`-layout
application. Runtime code has one import root, `simple_stipple`; repository tooling remains
outside that package.

## Package map

- `app` — process entry, main window, page registry, menus, tasks, settings, and other
  application-wide Qt coordination.
- `document` — canonical editable state, commands, undo history, mutation service, and
  workspace persistence.
- `engine` — UI-independent CAD, geometry, editing, pattern, imaging, and file-format
  capabilities.
- `features` — product workflows named as users see them: Draft, Pattern, Trace, Convert,
  Repository, and Help.
- `canvas` — the reusable interactive vector editor: runtime, rendering, tools, operations,
  view behavior, canvas widgets, and layer tree.
- `ui` — genuinely shared Qt components, dialogs, file pickers, notifications, units, and
  packaged styling.
- `platform` — paths, settings, storage, updates, error reporting, and generic process
  bootstrap.
- `resources` — packaged non-code runtime data.

## Dependency direction

```text
platform       engine
    \           /
      document
       /    \
    canvas    features
       \      /
          app
```

The diagram is a navigation model rather than permission for every possible edge. The
executable rules in `tests/test_dependency_boundaries.py` are authoritative:

1. Platform imports no product, UI, document, or engine subsystem.
2. Engine imports no app, canvas, feature, or UI subsystem.
3. Document imports no app, canvas, feature, or UI subsystem.
4. Engine and document contain no Qt dependencies.
5. Canvas imports no app or product feature.
6. A feature never imports another feature's internals; shared page behavior belongs in
   `features.base`.
7. App is the composition root and may import every subsystem.

## Where changes go

| Change                                                                 | Home                                                  |
| ---------------------------------------------------------------------- | ----------------------------------------------------- |
| startup, window, navigation, global menu/task coordination             | `app/`                                              |
| entities, selection, commands, undo, workspace serialization           | `document/`                                         |
| pure geometry, CAD, image/pattern processing, import/export formats    | `engine/`                                           |
| behavior exclusive to one user workflow                                | `features/<workflow>/`                              |
| shared vector editing gestures, rendering, tools, canvas chrome        | `canvas/`                                           |
| shared Qt control, dialog, visual token, notification, or unit display | `ui/`                                               |
| operating-system path, settings, storage, update, or reporting concern | `platform/`                                         |
| packaged runtime data                                                  | `resources/` or the owning packaged style directory |

Do not add `common`, `misc`, `helpers`, or `utils`. Promote code out of a feature only after
two independent consumers exist or when it represents an explicit shared aggregate.

## Public surfaces

- `simple_stipple.app.launcher:main` is the console-script entry point.
- `python -m simple_stipple` delegates to the same launcher.
- `app.pages.default_page_specs` is the top-level workflow registry.
- `document.service.DocumentService` is the command-oriented mutation boundary.
- `canvas.widget.DxfCanvas` is the reusable editor widget.
- Feature packages expose their primary page where they are packages; Help and Repository
  are intentionally single modules.

Cross-subsystem code imports canonical modules directly. Do not create compatibility
wrappers at old `core`, `backend`, `ui.pages`, or `ui.canvas` paths.

## Verification

The focused replacement suite checks module homes, package exports, feature isolation,
dependency direction, UI remediation behavior, and representative runtime flows. The
legacy test suite was intentionally removed and must not be restored.

Release verification additionally builds an sdist and wheel, inventories wheel resources,
installs the wheel in isolation, and performs an offscreen startup smoke test. Windows and
Linux artifact behavior requires external CI.
