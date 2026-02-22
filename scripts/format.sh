#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python3"
fi

echo "Formatting with black ..."
"$PYTHON" -m black .

echo "Applying safe ruff fixes ..."
"$PYTHON" -m ruff check . --fix

echo "Format complete."
