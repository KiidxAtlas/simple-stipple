#!/usr/bin/env bash
set -euo pipefail

APP_NAME="simple-stipple"
REPO_URL="${1:-https://github.com/KiidxAtlas/simple-stipple.git}"

if command -v pipx >/dev/null 2>&1; then
  if pipx list | grep -q "package ${APP_NAME}"; then
    pipx upgrade "${APP_NAME}"
  else
    pipx install "git+${REPO_URL}" --force
  fi
  echo "Updated with pipx."
  exit 0
fi

python3 -m pip install --user --upgrade "git+${REPO_URL}"
echo "Updated with pip --user."
