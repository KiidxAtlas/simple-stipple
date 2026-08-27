# Contributing to Simple Stipple

Thanks for helping improve a desktop tool used in real vector and laser workflows. Small, focused pull requests are easiest to review and safest to ship.

## Before opening an issue or pull request

- Search existing issues and discussions.
- Reproduce bugs with the smallest non-private input you can share.
- Never upload private designs, machine credentials, API keys, or customer data.
- For a security concern, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## Local setup

Requires Python 3.10 or newer. The development environment is managed with `uv`:

```bash
uv sync --extra dev
```

Run the same checks used by CI before submitting a change:

```bash
uv run ruff check .
uv run ruff format --check .
uv run python scripts/check_circular_imports.py
uv run python -m compileall -q src tests
QT_QPA_PLATFORM=offscreen uv run pytest -q
uv run mypy src
uv run pyright
```

For an interactive run:

```bash
uv run python -m simple_stipple
```

## Making changes

- Keep the capability-oriented boundaries described in [ARCHITECTURE.md](ARCHITECTURE.md).
- Read surrounding code and tests before changing an exported symbol.
- Prefer a focused change over a broad refactor.
- Add or update tests for new observable behavior and regression fixes.
- Keep UI changes usable with keyboard navigation and clear status feedback.
- Preserve the production safety boundary: the app prepares geometry and never controls laser hardware.
- Update [CHANGELOG.md](CHANGELOG.md) when a user-visible behavior changes.

## Pull requests

Describe:

1. The user problem.
2. The behavior that changed.
3. How you verified it, including platform or `QT_QPA_PLATFORM` details when relevant.
4. Any compatibility, file-format, or release implications.

Keep unrelated formatting or generated files out of the PR. A maintainer will decide release timing and versioning.
