#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python3"
fi

echo "Running black --check ..."
"$PYTHON" -m black --check .

echo "Running ruff check ..."
"$PYTHON" -m ruff check .

echo "Lint passed."
