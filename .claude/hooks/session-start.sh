#!/bin/bash
# A Claude Code cloud session starts with the dev environment in .venv: uv run pytest, ruff and mypy work at once
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

if ! command -v uv >/dev/null 2>&1; then
  python3 -m pip install --quiet --user uv
  export PATH="$HOME/.local/bin:$PATH"
  echo "export PATH=\"$HOME/.local/bin:\$PATH\"" >> "${CLAUDE_ENV_FILE:-/dev/null}"
fi

uv sync --locked
