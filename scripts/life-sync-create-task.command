#!/bin/bash
set -euo pipefail

# Phase 46 — Wrapper command script for task creation

# Ensure we run from the project root
# If this is run via double-click in Finder, $0 is an absolute path.
cd "$(dirname "$0")/.."
PROJECT_ROOT="$PWD"

# Determine Python executable
if [ -x "${PROJECT_ROOT}/.venv/bin/python" ]; then
    PYTHON="${PROJECT_ROOT}/.venv/bin/python"
elif [ -x "${PROJECT_ROOT}/venv/bin/python" ]; then
    PYTHON="${PROJECT_ROOT}/venv/bin/python"
else
    PYTHON="python3"
fi

# Execute the task creation CLI with all forwarded arguments
exec "$PYTHON" -m local_api.scripts.create_task "$@"
