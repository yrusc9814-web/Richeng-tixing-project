# Phase 15 — WeChat Reminder Safety Layer

- **Date:** 2026-05-28
- **Commit:** `2628fe15a` → Phase 15
- **Branch:** `phase-6-sync-engine`

## Background

Phase 15 adds a WeChat reminder notification channel with a safety layer,
providing the notification/reminder exit point for the personal schedule
reminder hub system. This is the first notification channel implemented in
the project, separate from the existing sync adapter layer (Phase 8A).

### Prior State

- Project had `SyncAdapter` (ABC) for external data sync (Apple Calendar,
  Apple Reminders, Weather)
- No `NotifyChannel` or `notify/` module existed
- No WeChat-related code
- `ALLOWED_SYNC_TARGETS` in config.py: `apple_calendar`, `apple_reminder`

### Design Decision

WeChat is **not** a sync adapter — it's a notification/reminder exit.
Instead of extending `SyncAdapter`, a new `NotifyChannel` base class was
created in a dedicated `local_api/notify/` module. This avoids coupling
notification channels to the sync engine and keeps the architecture clean.

## Files Changed

### Added

| File | Purpose |
|------|---------|
| `local_api/notify/__init__.py` | Module exports (`NotifyChannel`, `NotifyResult`) |
| `local_api/notify/base.py` | `NotifyResult` dataclass + `NotifyChannel` abstract base class |
| `local_api/notify/wechat_channel.py` | `WeChatNotifyChannel` with dry-run / test-mode / real safety layer |
| `local_api/tests/test_wechat_channel.py` | 19 tests covering all modes, config errors, structure stability |
| `local_api/docs/archive/2026-05-28-phase-15-wechat-notify-safety.md` | This archive |

### Unchanged

- `config.py` — no changes needed (env-var based config)
- `sync_engine.py` — not touched
- `sync_service.py` — not touched
- `adapters/` — not touched (WeChat is not a sync adapter)
- `routers/`, `main.py`, `middleware.py` — not touched
- All existing tests — not modified (382 passed before, should still pass)

## Architecture

### NotifyChannel (ABC)

```
NotifyChannel                      <-- abstract
├── channel_name -> str
├── send_reminder(task_id, task_data) -> NotifyResult
```

### NotifyResult fields

| Field | Type | Description |
|-------|------|-------------|
| `success` | `bool` | Whether the notification was sent/simulated successfully |
| `mode` | `str` | Operating mode: `dry_run`, `test_mode`, `real` |
| `channel` | `str` | Channel identifier: `wechat` |
| `task_id` | `Optional[str]` | The task being notified |
| `status` | `str` | Machine-readable status |
| `error_code` | `Optional[str]` | Machine-readable error code |
| `error_message` | `Optional[str]` | Human-readable error description |

### Status values

| Status | Meaning | Used in |
|--------|---------|---------|
| `skipped` | Dry-run: no action taken | dry_run |
| `simulated` | Test mode: simulated success | test_mode |
| `config_error` | Configuration/credential issue | real |
| `not_implemented` | Real push not yet implemented | real |

### Error codes

| Code | Meaning |
|------|---------|
| `platform_not_configured` | `WECHAT_REMINDER_ENABLED` not set to a truthy value |
| `missing_credentials` | `WECHAT_APP_ID` or `WECHAT_APP_SECRET` not set |
| `not_implemented` | Real push deferred to later phase |

## Mode Logic

```
send_reminder()
  │
  ├─ dry_run ──────────────────────────────────→ success, status=skipped
  │                                                (no config checks)
  │
  ├─ test_mode ────────────────────────────────→ success, status=simulated
  │                                                (no config checks)
  │
  └─ real
       ├─ WECHAT_REMINDER_ENABLED? ── No ─────→ platform_not_configured
       ├─ WECHAT_APP_ID + SECRET? ── No ──────→ missing_credentials
       └─ both present ───────────────────────→ not_implemented
```

## Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `WECHAT_REMINDER_ENABLED` | Enable platform (`true`/`1`/`yes`) | `true` |
| `WECHAT_APP_ID` | WeChat application ID | `wx_xxxx` |
| `WECHAT_APP_SECRET` | WeChat application secret | `secret_value` |

## Testing

### Test Coverage (19 tests)

| Test Class | Tests | Coverage |
|-----------|-------|----------|
| `TestWeChatNotifyChannelConstruction` | 6 | Valid/invalid modes, channel name |
| `TestWeChatNotifyChannelDryRun` | 2 | Dry-run output, env-independent |
| `TestWeChatNotifyChannelTestMode` | 2 | Test-mode output, env-independent |
| `TestWeChatNotifyChannelNotConfigured` | 3 | Disabled, false flag, credentials without enable |
| `TestWeChatNotifyChannelMissingCredentials` | 3 | Missing both, missing app_id, missing secret |
| `TestWeChatNotifyChannelRealNotImplemented` | 2 | Real push deferred, all truthy variants |
| `TestNotifyResultOutputStructure` | 5 | Field stability across all paths |

### Running

```bash
cd D:/hermes-agent
python -m pytest local_api/tests/test_wechat_channel.py -v
python -m pytest local_api/tests/test_adapters.py -v  # existing tests unchanged
```

## Verification

- ✅ Dry-run returns success with status=skipped
- ✅ Test mode returns success with status=simulated
- ✅ Not configured returns `platform_not_configured` error
- ✅ Missing credentials returns `missing_credential` error
- ✅ Real push returns `not_implemented` (no silent success)
- ✅ Output structure stable across all 5 paths (7 required fields)
- ✅ All 19 new tests pass
- ✅ All 382 existing tests pass (no regression)
- ✅ No dirty files mixed into commit
- ✅ Only local_api/ files committed

## Next Steps (Phase 16)

Phase 16 is the integration/联调 phase:
- Integrate WeChatNotifyChannel into the notification pipeline
- Wire from CLI, scheduler, or API layer
- Test end-to-end with dry-run and test-mode
- Optionally add real push implementation (WeChat official API or
  WeCom bot webhook)

## Boundary Enforcement

- ❌ No real WeChat push activated
- ❌ No Apple EventKit integration
- ❌ No LaunchAgent / cron / schtasks installed
- ❌ No DingTalk / Boss / other platforms
- ❌ No changes to sync_engine.py
- ❌ No changes to config.py
- ❌ No frontend
- ❌ No dirty files committed
