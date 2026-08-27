## What changed?

<!-- State the user problem and the resulting behavior. -->

## Verification

<!-- Include the exact commands and manual flows you ran. -->

- [ ] `uv run ruff check .`
- [ ] `uv run ruff format --check .`
- [ ] `QT_QPA_PLATFORM=offscreen uv run pytest -q`
- [ ] `uv run mypy src`
- [ ] `uv run pyright`
- [ ] Manual application smoke test (describe below)

## Release impact

- [ ] User-visible behavior is documented in `CHANGELOG.md`.
- [ ] File-format or workspace compatibility was considered.
- [ ] UI changes preserve keyboard access and clear feedback.
- [ ] No private designs, credentials, or generated build artifacts are included.

## Notes for reviewers

<!-- Mention risks, migration details, screenshots, or follow-up decisions. -->
