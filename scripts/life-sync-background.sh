#!/bin/bash
# =============================================================================
# Phase 37 — LifeSync background sync entry
# Non-interactive LaunchAgent-safe wrapper for Apple Calendar sync.
#
# Uses the project .venv Python and helper-owned Calendar TCC preflight.
# Does not require .venv/bin/python to have Calendar TCC.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_DIR/local_api/logs"
LOG_FILE="$LOG_DIR/background-sync.log"
LOCK_DIR="$PROJECT_DIR/local_api/logs/background-sync.lock"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"

mkdir -p "$LOG_DIR"

log() {
    local timestamp
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
    local line="[$timestamp] $1"
    echo "$line"
    echo "$line" >> "$LOG_FILE"
}

cleanup() {
    rmdir "$LOCK_DIR" 2>/dev/null || true
}

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    log "⚠️ background sync already running — skip this trigger"
    exit 0
fi
trap cleanup EXIT

if [ ! -d "$PROJECT_DIR" ]; then
    log "❌ project directory missing: $PROJECT_DIR"
    exit 1
fi
log "✅ project directory: $PROJECT_DIR"

if [ ! -x "$PYTHON_BIN" ]; then
    log "❌ project .venv python missing or not executable: $PYTHON_BIN"
    exit 1
fi
log "✅ project python: $PYTHON_BIN"

log "======================================================================"
log "🔍 running helper-owned preflight"
log "======================================================================"
PREFLIGHT_OUTPUT=$(cd "$PROJECT_DIR" && "$PYTHON_BIN" -m local_api.preflight 2>&1 || true)
READINESS=$(echo "$PREFLIGHT_OUTPUT" | /usr/bin/python3 -c "import sys,json; print(json.load(sys.stdin)['readiness'])" 2>/dev/null || echo "parse_error")

if [ "$READINESS" != "ready" ]; then
    log "❌ preflight: $READINESS — blocking background sync"
    echo "$PREFLIGHT_OUTPUT" >> "$LOG_FILE"
    exit 1
fi
log "✅ preflight: ready — running sync-pending"

log "======================================================================"
log "🚀 running sync-pending (limit=10)"
log "======================================================================"
cd "$PROJECT_DIR"
"$PYTHON_BIN" -m local_api.scripts.sync_trigger sync-pending --limit 10 2>&1 | tee -a "$LOG_FILE"
SYNC_EXIT=${PIPESTATUS[0]}

if [ "$SYNC_EXIT" -eq 0 ]; then
    log "✅ background sync completed"
else
    log "⚠️ background sync returned exit code $SYNC_EXIT"
fi
exit "$SYNC_EXIT"
