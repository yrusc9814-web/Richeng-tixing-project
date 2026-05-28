"""Phase 4.4 — Local API configuration."""

import os
from pathlib import Path

# Server
API_HOST = os.environ.get("HERMES_API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("HERMES_API_PORT", "8100"))

# Auth — single Bearer token for local testing
API_TOKEN = os.environ.get("HERMES_API_TOKEN", "test-token-hermes-local-4.3")

# Database — SQLite file inside test_data/
DB_DIR = Path(__file__).resolve().parent / "test_data"
_PYTEST_WORKER = os.environ.get("PYTEST_XDIST_WORKER")
DB_PATH = DB_DIR / (
    f"test_tasks_{_PYTEST_WORKER}.db" if _PYTEST_WORKER else "test_tasks.db"
)

# Logging
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
ACCESS_LOG_PATH = LOG_DIR / "access.log"

# ====== Token LOG RULES (Phase 4.3) ======
# The access log MUST NOT include any fragment of the bearer token.
# Only record: auth_result, token_present, request_id, endpoint, status_code.
#   ❌ logging the token prefix ("test-token…")
#   ❌ logging the token length
#   ❌ logging a hash of the token
#   ✅ "auth_result": "success" | "failed"
#   ✅ "token_present": true | false

# Allowed priority levels
ALLOWED_PRIORITIES = frozenset({"P0", "P1", "P2", "P3"})

# Allowed task statuses
ALLOWED_STATUSES = frozenset({"pending", "completed", "cancelled"})

# Allowed created_channel values
ALLOWED_CHANNELS = frozenset({"local_ui", "wechat", "hermes", "api_test"})

# Allowed timezones (IANA tz identifiers — not exhaustive, but covers the common ones)
ALLOWED_TIMEZONES = frozenset({
    "Asia/Shanghai", "Asia/Tokyo", "Asia/Singapore", "Asia/Hong_Kong",
    "Asia/Seoul", "Asia/Taipei", "Asia/Bangkok", "Asia/Kolkata",
    "Asia/Dubai", "Asia/Jerusalem",
    "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Moscow",
    "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
    "America/Toronto", "America/Vancouver", "America/Mexico_City",
    "America/Sao_Paulo", "America/Argentina/Buenos_Aires",
    "Australia/Sydney", "Australia/Melbourne", "Australia/Perth",
    "Pacific/Auckland", "Pacific/Honolulu",
    "UTC", "Etc/UTC",
})

# Pagination limits
MAX_LIMIT = 100
DEFAULT_LIMIT = 50

# ====== Phase 4.4 — Sync Configuration ======

# Allowed sync targets
ALLOWED_SYNC_TARGETS = frozenset({"apple_calendar", "apple_reminder"})

# Allowed sync_status values (10 states)
ALLOWED_SYNC_STATUSES = frozenset({
    "pending", "in_progress", "synced", "failed", "failed_permanent",
    "skipped", "stale", "orphaned", "disabled", "deleted",
})

# Allowed sync_logs sync_result values
ALLOWED_SYNC_RESULTS = frozenset({"success", "skipped", "failed", "drift_detected"})

# Phase 6 - Sync Engine Configuration
SYNC_SCAN_INTERVAL      = 30
SYNC_TIMEOUT_SECONDS    = 300
SYNC_RETRY_BASE         = 30
SYNC_RETRY_FACTOR       = 3
SYNC_RETRY_MAX_GAP      = 270
SYNC_BATCH_SIZE         = 10
SYNC_JITTER_ENABLED     = True
SYNC_ENGINE_AUTO_START  = False

# ====== Phase 8A — Adapter Configuration ======
ADAPTER_ENABLED: bool = True
ADAPTER_PUSH_TIMEOUT: int = 30
