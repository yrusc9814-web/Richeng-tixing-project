#!/bin/bash
set -euo pipefail

# Phase 40 — Wrapper script for background sync alert checks

# Ensure we run from the project root
cd "$(dirname "$0")/.."
PROJECT_ROOT="$PWD"

# Output log file
LOG_DIR="${PROJECT_ROOT}/local_api/logs"
LOG_FILE="${LOG_DIR}/sync-alert-check.log"

mkdir -p "$LOG_DIR"

# Timestamp for the log entry
TS=$(date '+%Y-%m-%d %H:%M:%S')

# Determine Python executable
if [ -x "${PROJECT_ROOT}/.venv/bin/python" ]; then
    PYTHON="${PROJECT_ROOT}/.venv/bin/python"
elif [ -x "${PROJECT_ROOT}/venv/bin/python" ]; then
    PYTHON="${PROJECT_ROOT}/venv/bin/python"
else
    PYTHON="python3"
fi

# Run the alert check
# Adding --notify is optional, we just want to run the check and log it
OUTPUT=$("$PYTHON" -m local_api.scripts.sync_alert_check)
EXIT_CODE=$?

# We extract the status directly using jq or grep. If jq isn't available, we fallback to simple grep.
if command -v jq >/dev/null 2>&1; then
    STATUS=$(echo "$OUTPUT" | jq -r '.status')
else
    STATUS=$(echo "$OUTPUT" | grep -o '"status": *"[^"]*"' | cut -d'"' -f4)
fi

echo "[$TS] status=$STATUS | exit=$EXIT_CODE" >> "$LOG_FILE"

exit $EXIT_CODE
