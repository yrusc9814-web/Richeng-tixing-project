# Phase 16 — Local Integration Test (联调)

- **Date:** 2026-05-28
- **Commit:** `8383a9873` → Phase 16
- **Branch:** `phase-6-sync-engine`

## Background

Phase 16 adds a local integration test that exercises the full pipeline
end-to-end WITHOUT real external services:

```
sync trigger → scheduler → SyncService → Apple safety adapter
└─ then separately → WeChat notify safety layer
```

This phase does NOT wire real Apple EventKit or WeChat push. It verifies
that the safety layers introduced in Phases 14 (Apple) and 15 (WeChat)
interoperate correctly with the existing SyncService/SyncScheduler layer,
and that all error paths produce explicit error_code output.

## Discrepancy Fix

Phase 15 report stated "19 tests" but actual count was 24. Root cause:
the `test_wechat_channel.py` file has 7 test classes totaling 24 test
methods (7 + 2 + 2 + 3 + 3 + 2 + 5 = 24). The number 19 was a reporting
error in the Phase 15 conversation — the file itself was correct.
No file changes needed.

## Files Changed

### Added

| File | Purpose |
|------|---------|
| `local_api/tests/test_local_integration.py` | 24 integration tests |

### Unchanged

- All existing source files — no changes to business logic
- `config.py`, `sync_engine.py`, `sync_service.py`, `adapters/`,
  `notify/`, `routers/`, `main.py` — not touched

## Test Architecture

24 tests organized into 4 sections:

### Section 1: AppleSyncAdapter Direct Safety (5 tests)

Tests the adapter's internal mode logic directly, bypassing SyncService
validation. On non-macOS, `validate_config()` returns `(False, ...)` so
through SyncService this would block at config validation.

| Test | Verifies |
|------|----------|
| `test_dry_run_push_returns_skipped` | Dry-run returns skipped |
| `test_test_mode_push_returns_success_with_test_prefix` | Test-mode returns success + test_ prefix |
| `test_real_mode_on_non_macos_returns_platform_unsupported` | Real mode = platform_unsupported |
| `test_validate_config_on_non_macos_returns_false` | validate_config fails on non-macOS |
| `test_apple_reminder_routes_to_calendar_adapter` | Routing: apple_reminder → apple_calendar adapter |

### Section 2: SyncService + SyncScheduler Integration (9 tests)

Tests the full chain through SyncService and SyncScheduler.

| Test | Verifies |
|------|----------|
| `test_apple_calendar_through_service_returns_config_error` | Apple sync through SyncService = adapter_config_invalid on non-macOS |
| `test_apple_reminder_through_service_also_config_error` | Same for apple_reminder routing |
| `test_mock_adapter_through_service_succeeds` | MockAppleAdapter (no platform check) succeeds |
| `test_sync_scheduler_invalid_target_has_reason_field` | Invalid target → `reason` field in output |
| `test_sync_scheduler_nonexistent_task` | Missing task → error in output |
| `test_sync_scheduler_all_targets_with_mock_adapter` | sync_task_all_targets with all targets succeeds |
| `test_sync_pending_with_mock_adapter` | sync_pending processes pending records |
| `test_skip_detection_with_mock_adapter` | Second sync_all_targets skips terminal states |
| `test_skip_count_in_output` | Output always has skip_count field |

### Section 3: WeChat Notify Integration (5 tests)

Tests WeChatNotifyChannel in integration context (identical to Phase 15
unit tests but runs in the integration test suite for completeness).

| Test | Verifies |
|------|----------|
| `test_dry_run_returns_skipped` | Dry-run returns skipped |
| `test_test_mode_returns_simulated` | Test-mode returns simulated |
| `test_real_not_configured_returns_explicit_error` | No config → platform_not_configured |
| `test_real_missing_credentials_returns_explicit_error` | No creds → missing_credentials |
| `test_real_not_implemented_with_full_config` | Full config → not_implemented |

### Section 4: Cross-Layer Output Structure (5 tests)

Verifies output field stability across all layers.

| Test | Verifies |
|------|----------|
| `test_sync_service_result_has_required_fields` | SyncService result has 6 fields |
| `test_scheduler_result_has_required_fields` | Scheduler result has 4 fields |
| `test_scheduler_all_targets_has_summary_fields` | All-targets has 5 summary fields |
| `test_notify_result_has_all_required_fields` | NotifyResult has 7 fields |
| `test_wechat_error_has_code_and_message` | Every error→code+message |

## Key Architectural Insight: non-macOS Safety

On non-macOS platforms, `AppleSyncAdapter.validate_config()` returns
`(False, "...")` because EventKit requires Darwin. This check runs BEFORE
`push()` in `SyncService.run_task_sync()`, so even with `dry_run=True`,
the Apple adapter cannot succeed through SyncService on non-macOS.

This is correct behavior — the safety layer prevents accidental use on
unsupported platforms. For local integration testing on Windows/Linux:

- Test AppleSyncAdapter's internal mode logic via **direct calls**
- Test SyncService pipeline with **MockAppleAdapter** (no platform check)
- Test error paths with **AppleSyncAdapter** through SyncService (correctly
  returns `adapter_config_invalid`)

On macOS with EventKit available, the adapter's `dry_run` and `test_mode`
paths would work correctly through SyncService.

## Testing

```bash
# Full suite
cd D:/hermes-agent
python -m pytest local_api/tests/

# Specific files
python -m pytest local_api/tests/test_local_integration.py -v
python -m pytest local_api/tests/test_adapters.py -v
python -m pytest local_api/tests/test_wechat_channel.py -v
python -m pytest local_api/tests/test_sync_trigger.py -v
```

## Verification Results

- ✅ Phase 15 test count discrepancy resolved (reporting error, not code)
- ✅ AppleSyncAdapter dry-run/test-mode produce correct results (direct)
- ✅ SyncService routes correctly with AppleSyncAdapter on all platforms
- ✅ SyncScheduler + SyncService integration verified with MockAppleAdapter
- ✅ WeChatNotifyChannel dry-run/test-mode/error paths all verified
- ✅ Cross-layer output structure stable (all fields present)
- ✅ Skip/duplicate detection works (skip_count, reason field)
- ✅ 430 total tests pass (406 + 24 new, 0 regression)
- ✅ No dirty files committed

## Next Steps (Phase 17 — Real Environment Acceptance)

- Wire real adapters on macOS (EventKit backend)
- Configure WeChat credentials for real push
- Test Apple Calendar/Reminders sync via SyncScheduler
- Test WeChat reminder delivery via WeChatNotifyChannel
- End-to-end dry-run → live-mode migration
