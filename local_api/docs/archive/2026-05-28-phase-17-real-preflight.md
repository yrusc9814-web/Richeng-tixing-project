# Phase 17 — Real Environment Preflight Checks

- **Date:** 2026-05-28
- **Commit:** `798175bc9` → Phase 17
- **Branch:** `phase-6-sync-engine`

## Background

Phase 17 adds a preflight check module that verifies all conditions
required for real Apple Calendar/Reminders and WeChat notification
acceptance, WITHOUT making any real external calls. This is the
prerequisite gate before Phase 18 (real Apple minimum write).

## Files Changed

### Added

| File | Purpose |
|------|---------|
| `local_api/preflight.py` | Preflight check module (standalone CLI + API) |
| `local_api/tests/test_preflight.py` | 27 tests covering all checks |
| `local_api/docs/archive/2026-05-28-phase-17-real-preflight.md` | This archive |

### Unchanged

- All existing source files — no changes to business logic
- `config.py` — not touched
- `sync_engine.py`, `sync_service.py`, `adapters/`, `notify/` — not touched

## Preflight Check Items (11 checks)

| # | Check ID | Status on non-macOS | Status on macOS | What it checks |
|---|----------|---------------------|-----------------|----------------|
| 1 | `apple_platform` | ❌ fail | ✅ pass | `sys.platform == 'darwin'` |
| 2 | `apple_eventkit_deps` | ⏭️ skip | ✅/❌ pass/fail | Can import EventKit / PyObjC |
| 3 | `apple_calendar_permission` | ⏭️ skip | ✅/❌ pass/fail | Calendar TCC permission (deferred) |
| 4 | `apple_reminder_permission` | ⏭️ skip | ✅/❌ pass/fail | Reminders TCC permission (deferred) |
| 5 | `apple_dry_run_available` | ✅ pass | ✅ pass | AppleSyncAdapter supports `dry_run=True` |
| 6 | `apple_test_mode_available` | ✅ pass | ✅ pass | AppleSyncAdapter supports `test_mode=True` |
| 7 | `wechat_config` | ❌/✅ fail/pass | ❌/✅ fail/pass | WECHAT_REMINDER_ENABLED + credentials |
| 8 | `wechat_dry_run_available` | ✅ pass | ✅ pass | WeChatNotifyChannel supports `mode='dry_run'` |
| 9 | `wechat_test_mode_available` | ✅ pass | ✅ pass | WeChatNotifyChannel supports `mode='test_mode'` |
| 10 | `test_data_marking_rules` | ✅ pass | ✅ pass | `_SYNC_TEST_PREFIX = "[SYNC-TEST] "` exists |
| 11 | `rollback_cleanup_rules` | ✅ pass | ✅ pass | `reset_db()` + external_id cleanup known |

## Readiness Levels

| Level | Criteria | Meaning |
|-------|----------|---------|
| `ready` | All checks pass or skip | Ready for real environment acceptance |
| `partial` | Only warnings, no failures | Review warnings before real writes |
| `not_ready` | Any check fails | Resolve failures before real writes |

## Usage

```bash
# CLI: print report as JSON
python -m local_api.preflight

# Python API
from local_api.preflight import run_preflight
report = run_preflight()
print(report.to_dict())
```

## Design Principles

1. **No real external calls** — no EventKit API, no WeChat API, no TCC probes.
   Calendar/Reminders permission checks are deferred (return False on all platforms).
2. **Each check is self-contained and idempotent** — running preflight multiple
   times produces identical output (no side effects).
3. **Structured JSON output** — programmatically consumable by CI/CD pipelines
   and monitoring tools.
4. **Graceful non-macOS degradation** — Apple-specific checks are skipped on
   non-macOS with clear messaging.

## Testing

### Coverage (27 tests)

| Test Class | Tests | Coverage |
|-----------|-------|----------|
| `TestPreflightDataTypes` | 5 | Check item, report, dict, constants |
| `TestPreflightOnNonMacOS` | 6 | Platform fail, skips, dry-run pass, readiness |
| `TestPreflightWeChatConfig` | 6 | Not set, enabled+creds, partial creds, disabled |
| `TestPreflightReadiness` | 3 | Not ready, partial, ready levels |
| `TestPreflightInternalCheckFunctions` | 2 | EventKit deps, rollback cleanup |
| `TestPreflightOutputStructure` | 5 | Field stability, uniqueness, count, statuses |

### Running

```bash
cd D:/hermes-agent
python -m pytest local_api/tests/test_preflight.py -v
python -m local_api.preflight
```

## Verification Results

- ✅ 27 preflight tests pass
- ✅ Preflight report on non-macOS correctly shows `not_ready` (2 failures)
- ✅ WeChat config checks correctly distinguish all partial states
- ✅ Dry-run/test-mode checks always pass (built-in feature, not platform-dependent)
- ✅ All 11 check names are unique and documented
- ✅ Output structure stable (4 fields per check, 3 fields per report)
- ✅ No dirty files committed

## Next Steps

Phase 18 — Real Apple Calendar/Reminders minimum write:
- Run preflight on macOS (must show `ready`)
- Wire real AppleSyncAdapter with EventKit
- Write one test event to Calendar
- Write one test reminder to Reminders
- Verify [SYNC-TEST] prefix on created items
- Clean up test items
- Verify WeChat config on real environment
