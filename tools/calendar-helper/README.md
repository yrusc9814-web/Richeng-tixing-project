# LifeSync Calendar Helper

Independent macOS Calendar helper for validating whether a stable visible GUI app bundle can own Apple Calendar TCC permission without changing Hermes Web UI.

## Scope

This helper does not modify Web pages, Hermes Web UI logic, Web UI launch scripts, LaunchAgent, or node/web-ui runtime. It exists only to test a standalone `LifeSync Calendar Helper.app` TCC route.

## Identity

- App name: `LifeSync Calendar Helper`
- Bundle ID: `com.vanta.lifesync.calendar-helper`
- Executable: `Contents/MacOS/LifeSyncCalendarHelper`
- Implementation: Swift `NSApplication` foreground app linked with AppKit and EventKit

## UI

The app opens a small window showing:

- App name
- Bundle ID
- Current Calendar authorization status
- `Request Calendar Access` button
- `Check Access` button
- Callback result and calendar count

## Manual Authorization

Open the app from Finder or with LaunchServices, then click `Request Calendar Access` inside the app window. If macOS shows a Calendar permission prompt, allow it. After that click `Check Access` and verify `calendarAccessGranted: true` and `calendarCount` is greater than 0.

## Build

```bash
./tools/calendar-helper/build-helper.sh
```

The build uses ad-hoc codesign only. It does not touch Hermes Web UI, LaunchAgents, node, TCC reset, Calendar events, or Python sync.
