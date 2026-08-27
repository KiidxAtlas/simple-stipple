"""Stable public facade for shared UI components.

Production code imports directly from concern-specific submodules
(e.g. `from simple_stipple.ui.components.layout import CollapsibleSection`).
This facade remains for external consumers and tests.
"""

# NOTE: All re-exports below are intentionally removed as dead code.
# Production imports go directly to submodules (e.g. ui.components.layout,
# ui.components.inputs, etc.). This __init__.py now only exposes submodule
# aliases for test compatibility.

from simple_stipple.ui.components import (  # noqa: F401  # noqa: F401
    cycle_button,
    feedback,
    focus,
    icons,
    inputs,
    layout,
    recent,
    units,
    workflow,
)

__all__: list[str] = []
