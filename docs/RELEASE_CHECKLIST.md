# Simple Stipple release checklist

This checklist separates work that can be automated from decisions that require the maintainer. Run it for every public desktop release.

## 1. Product and repository gate

- [ ] Confirm the release has a clear user-facing goal and a dated entry in [CHANGELOG.md](../CHANGELOG.md).
- [ ] Confirm the version in `pyproject.toml` is the intended public version.
- [ ] Review the README's download links, supported platforms, safety notes, and known limitations.
- [ ] Add or refresh at least one real screenshot or short demo recording before announcing the release. Do not use mock UI or placeholder artwork.
- [ ] Review the repository description, website URL, topics, social preview, and About text in GitHub repository settings.
- [ ] Choose and publish an explicit software license before accepting outside contributions or redistributing binaries. The repository currently does not select one for you.

## 2. Local quality gate

Run from a clean checkout or an isolated release branch:

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run python scripts/check_circular_imports.py
uv run python -m compileall -q src tests
uv run python -c "import simple_stipple.app; import simple_stipple.app.launcher"
QT_QPA_PLATFORM=offscreen uv run pytest -q
uv run mypy src
uv run pyright
```

Launch the application normally as well. Exercise at least one complete flow:

1. Import or draw a closed outline.
2. Generate a pattern and inspect the output.
3. Save and reopen a workspace.
4. Export a file and inspect it in the target machine software or a safe preview.
5. Open **Help → Check for Updates** and **Help → User Manual**.

## 3. Release metadata gate

The release script checks the package version and dated changelog heading without mutating the repository:

```bash
./scripts/release.sh --check vX.Y.Z
```

Before creating a tag:

- [ ] Commit the intended changes and ensure the working tree is clean.
- [ ] Confirm the tag does not already exist locally or on `origin`.
- [ ] Confirm the public branch contains the release commit.
- [ ] Confirm `MACOS_CERTIFICATE_BASE64`, `MACOS_CERTIFICATE_PASSWORD`, and `MACOS_SIGNING_IDENTITY` are configured in GitHub Actions secrets.
- [ ] Confirm `APPLE_ID`, `APPLE_APP_PASSWORD`, and `APPLE_TEAM_ID` are configured for notarization.
- [ ] Confirm the repository's Actions policy permits the release workflow and write access to releases.

Create and push the tag from the public branch:

```bash
./scripts/release.sh vX.Y.Z
```

## 4. Artifact gate

Do not announce until the release workflow is green and the GitHub release contains both artifacts and both checksums:

- [ ] `SimpleStipple.exe` launches on a supported Windows machine.
- [ ] `SimpleStipple-macOS.dmg` opens and the app launches on a supported macOS machine.
- [ ] The `.sha256` sidecars match the downloaded files.
- [ ] The release notes explain the user-visible changes, upgrade notes, and known limitations.
- [ ] Update checking finds the release and verifies its checksum.
- [ ] A fresh install and an upgrade from the previous release both work.

For a macOS artifact, verify the signature and notarization status before publication. Never silently publish an unsigned or unnotarized downloadable build.

## 5. Announcement gate

Use a short, concrete announcement with one call to action:

- What changed in user terms.
- A real screenshot or short clip.
- Who benefits from the release.
- Direct link to the [latest release](https://github.com/KiidxAtlas/simple-stipple/releases/latest).
- A safety note that exported geometry must be verified in the machine software.
- A request for one useful action: try a workflow, report a reproducible bug, or share a project result.

Post where the intended users already gather. Do not send unsolicited bulk messages or add tracking that is not clearly disclosed and opt-in.

## 6. After publication

- [ ] Watch the first installation and update reports.
- [ ] Triage reproducible bugs using the issue templates.
- [ ] Label feedback by workflow (`draft`, `pattern`, `trace`, `convert`, `release`) and platform.
- [ ] Record the release announcement and notable feedback in the next changelog draft.
- [ ] Keep the support and security paths visible; never request private designs or credentials in public issues.
