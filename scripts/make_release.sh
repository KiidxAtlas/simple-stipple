#!/usr/bin/env bash
set -euo pipefail

PY=${PY:-python3}

if [ -n "${1:-}" ]; then
  VERSION="$1"
else
  VERSION=$($PY - <<'PY'
import tomllib
with open('pyproject.toml','rb') as f:
    data = tomllib.load(f)
print(data['project']['version'])
PY
)
fi

# Ensure tag starts with 'v'
TAG="$VERSION"
case "$TAG" in v*) ;; *) TAG="v$TAG";; esac

echo "Releasing $TAG"

git fetch origin --tags >/dev/null 2>&1 || true

# If tag exists (locally or on origin) recreate it at the same commit and push
if git ls-remote --tags origin "refs/tags/$TAG" | grep -q "$TAG" || git rev-parse -q --verify "refs/tags/$TAG" >/dev/null 2>&1; then
  # If tag exists only on origin, fetch it locally so we can read its commit
  if ! git rev-parse -q --verify "refs/tags/$TAG" >/dev/null 2>&1; then
    git fetch origin "refs/tags/$TAG:refs/tags/$TAG" >/dev/null 2>&1 || true
  fi
  commit=$(git rev-parse "$TAG")
  echo "Tag $TAG exists (commit $commit) — recreating and pushing to origin"
  git push --delete origin "$TAG" 2>/dev/null || true
  git tag -d "$TAG" 2>/dev/null || true
  git tag -a "$TAG" "$commit" -m "Release $TAG (recreated by make release)"
  git push origin "$TAG"
else
  # Otherwise call the existing release script which will create and push the tag
  ./scripts/release.sh "$TAG"
fi
