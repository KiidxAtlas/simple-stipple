#!/usr/bin/env bash
set -euo pipefail

TAG="${1:-}"
DEFAULT_BRANCH="main"

if [[ -z "$TAG" ]]; then
  echo "Usage: ./scripts/release.sh vX.Y.Z"
  echo "Example: ./scripts/release.sh v0.2.0"
  exit 1
fi

if [[ "$TAG" != v* ]]; then
  echo "Tag must start with 'v' (example: v0.2.0)."
  exit 1
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
