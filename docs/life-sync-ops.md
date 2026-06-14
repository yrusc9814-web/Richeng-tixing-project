# LifeSync Hub Operations Manual

## 1. Checking System Health

To get a complete, read-only snapshot of the system's status, run the one-click diagnostic command:

```bash
cd /Users/vantawork/Projects/life-sync-hub
python3 -m local_api.scripts.sync_doctor
```

This returns a JSON report indicating Git status, both LaunchAgents (main and alert), preflight readiness, authorization state, and the latest logs.

## 2. 日常日程任务创建

日常可以通过双击脚本来创建日程任务：
1. 在 Finder 中打开 `scripts/` 目录。
2. 双击运行 `创建日程任务.command`。
3. 终端会弹出提示，根据提示输入：
   - **任务标题**：必填，不能为空（例如 `整理明天日程`）。
   - **开始时间 / 结束时间**：默认提供当前时间及一小时后的 ISO 格式。直接回车即使用默认时间，或自行输入 ISO 格式，例如：`2026-06-13T10:00:00Z`。
   - **备注**：选填。
   - **是否立即同步**：输入 `Y` (默认) 或 `N`。

**常见问题处理：**
- **创建后 Calendar 没出现**：如果选了 `Y`，检查终端最后输出的 `final_sync_status` 和 `external_id` 是否成功。如果选了 `N`，系统会在 5 分钟内通过后台自动同步。
- **时间格式报错**：请务必确保时间字符串包含 `T` 和末尾的 `Z`，例如 `2026-06-13T10:00:00Z`。

## 3. Viewing Logs

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