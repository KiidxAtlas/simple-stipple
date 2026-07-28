#!/usr/bin/env bash
set -euo pipefail

CHECK_ONLY=false
if [[ "${1:-}" == "--check" ]]; then
  CHECK_ONLY=true
  shift
fi

TAG="${1:-}"
DEFAULT_BRANCH="main"
PYTHON_BIN="${PYTHON:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

if [[ -z "$TAG" ]]; then
  echo "Usage: ./scripts/release.sh [--check] vX.Y.Z"
  echo "Example: ./scripts/release.sh v0.2.0"
  exit 1
fi

if [[ "$TAG" != v* ]]; then
  echo "Tag must start with 'v' (example: v0.2.0)."
  exit 1
fi

"$PYTHON_BIN" - "$TAG" <<'PY'
import re
import sys
import tomllib
from pathlib import Path

tag = sys.argv[1]
tag_version = tag.removeprefix("v")
with Path("pyproject.toml").open("rb") as handle:
    package_version = tomllib.load(handle)["project"]["version"]

if tag_version != package_version:
    raise SystemExit(
        f"Tag {tag!r} does not match pyproject.toml version {package_version!r}."
    )

changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
release_heading = rf"^## {re.escape(package_version)} — \d{{4}}-\d{{2}}-\d{{2}}$"
if re.search(release_heading, changelog, flags=re.MULTILINE) is None:
    raise SystemExit(
        f"CHANGELOG.md has no dated release heading for version {package_version}."
    )
PY

if [[ "$CHECK_ONLY" == true ]]; then
  echo "Release metadata is consistent for $TAG."
  exit 0
fi

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "Not inside a git repository."
  exit 1
fi

current_branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$current_branch" != "$DEFAULT_BRANCH" ]]; then
  echo "Warning: current branch is '$current_branch' (expected '$DEFAULT_BRANCH')."
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Working tree is not clean. Commit or stash changes before releasing."
  exit 1
fi

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "Tag '$TAG' already exists locally."
  exit 1
fi

git fetch origin --tags >/dev/null 2>&1 || true
if git ls-remote --tags origin "refs/tags/$TAG" | grep -q "$TAG"; then
  echo "Tag '$TAG' already exists on origin."
  exit 1
fi

git tag -a "$TAG" -m "Release $TAG"
git push origin "$TAG"

echo "Pushed tag $TAG. GitHub Actions will build both:"
echo "- Windows: dist/SimpleStipple.exe"
echo "- macOS:   dist/SimpleStipple-macOS.dmg"
echo "Check Actions/Release page for artifacts."
