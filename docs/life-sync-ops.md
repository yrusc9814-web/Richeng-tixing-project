# LifeSync Hub Operations Manual

## 1. Checking System Health

To get a complete, read-only snapshot of the system's status, run the one-click diagnostic command:

```bash
cd /Users/vantawork/Projects/life-sync-hub
python3 -m local_api.scripts.sync_doctor
```

This returns a JSON report indicating Git status, both LaunchAgents (main and alert), preflight readiness, authorization state, and the latest logs.

## 2. Viewing Logs

All logs are stored in `local_api/logs/`.

- **Main Sync Agent Logs**:
  - `background-launchagent.out.log` — Standard output from the main script.
  - `background-launchagent.err.log` — Standard error.
  - `background-sync.log` — Dedicated application logs for synchronization runs.
- **Alert Agent Logs**:
  - `alert-launchagent.out.log` / `.err.log` — Standard streams from the alert wrapper.
  - `sync-alert-check.log` — Historical status entries (e.g., `status=ok | exit=0`).

```bash
tail -n 50 local_api/logs/background-sync.log
tail -n 20 local_api/logs/sync-alert-check.log
```

## 3. LaunchAgent Management

There are two LaunchAgents installed in `~/Library/LaunchAgents/`:
- `com.vanta.lifesync.background-sync` (Interval: 300s)
- `com.vanta.lifesync.alert-check` (Interval: 900s)

Check status:
```bash
launchctl print "gui/$(id -u)/com.vanta.lifesync.background-sync"
```

Reload/Restart (e.g., after modifying the plist):
```bash
launchctl unload ~/Library/LaunchAgents/com.vanta.lifesync.background-sync.plist
launchctl load -w ~/Library/LaunchAgents/com.vanta.lifesync.background-sync.plist
```

## 4. TCC (Calendar Authorization) Issues

The helper application (`LifeSyncCalendarHelper.app`) handles Apple Calendar integration. 

**Important Rules**:
- If `sync_doctor` shows `helper_auth` as anything other than `authorized` (e.g., `notDetermined`), the sync will fail.
- **NEVER rebuild or re-sign the helper app without authorization.** Rebuilding changes the binary signature, which immediately invalidates existing macOS Privacy (TCC) grants.
- If you must rebuild the helper, you **must manually re-authorize it**:
  1. Open System Settings -> Privacy & Security -> Calendars.
  2. If the helper is listed, remove it (minus button).
  3. Run the helper or a preflight script to trigger a new prompt.
  4. Approve the prompt.

Use the preflight script for granular details:
```bash
python3 -m local_api.preflight
```