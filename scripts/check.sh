#!/usr/bin/env bash
# Run the full test suite. Exits non-zero on any failure, so nothing can be committed past a failing test.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
"$PY" -m pytest -q
find . -name __pycache__ -prune -exec rm -rf {} +
rm -rf .pytest_cache
