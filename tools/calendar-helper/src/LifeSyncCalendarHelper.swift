import AppKit
import EventKit
import Foundation

let projectDir = "/Users/vantawork/Projects/life-sync-hub"
let projectPython = "/Users/vantawork/Projects/life-sync-hub/.venv/bin/python"
let smokeReportPath = "/Users/vantawork/Projects/life-sync-hub/tools/calendar-helper/last-smoke-report.json"

struct SmokeReport: Codable {
    let timestamp: String
    let bundleIdentifier: String
    let processName: String
    let authorizationStatus: String
    let calendarAccessGranted: Bool
    let calendarCount: Int
    let taskID: String?
    let taskTitle: String?
    let pythonPath: String?
    let pythonPreflightExitCode: Int?
    let pythonPreflightStdout: String?
    let pythonPreflightStderr: String?
    let syncTriggerExitCode: Int?
    let syncTriggerStdout: String?
    let syncTriggerStderr: String?
    let syncStatus: String?
    let externalID: String?
    let payloadHash: String?
    let lastSyncedAt: String?
    let latestLogResult: String?
    let latestLogErrorCode: String?
    let latestLogErrorMessage: String?
    let eventReadBackTitle: String?
    let error: String?
}

func globalStatusName(_ status: EKAuthorizationStatus) -> String {
    switch status {
    case .notDetermined: return "notDetermined"
    case .restricted: return "restricted"
    case .denied: return "denied"
    case .authorized: return "authorized"
    case .fullAccess: return "fullAccess"
    case .writeOnly: return "writeOnly"
    @unknown default: return "unknown"
    }
}

func globalHasFullCalendarAccess(_ status: EKAuthorizationStatus) -> Bool {
    if #available(macOS 14.0, *) { return status == .fullAccess }
    return status == .authorized
}

func runProcess(_ executable: String, _ arguments: [String], cwd: String? = nil) -> (Int32, String, String) {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = arguments
    if let cwd { process.currentDirectoryURL = URL(fileURLWithPath: cwd) }
    let stdoutPipe = Pipe()
    let stderrPipe = Pipe()
    process.standardOutput = stdoutPipe
    process.standardError = stderrPipe
    do {
        try process.run()
        process.waitUntilExit()
    } catch {
        return (127, "", "process_run_failed: \(error.localizedDescription)")
    }
    let stdout = String(data: stdoutPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    let stderr = String(data: stderrPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    return (process.terminationStatus, stdout, stderr)
}

func sqliteValue(_ sql: String) -> String? {
    let result = runProcess("/usr/bin/sqlite3", ["-noheader", "-batch", "local_api/test_data/test_tasks.db", sql], cwd: projectDir)
    guard result.0 == 0 else { return nil }
    let value = result.1.trimmingCharacters(in: .whitespacesAndNewlines)
    return value.isEmpty ? nil : value
}

func sqliteExec(_ sql: String) -> (Bool, String) {
    let result = runProcess("/usr/bin/sqlite3", ["local_api/test_data/test_tasks.db", sql], cwd: projectDir)
    return (result.0 == 0, result.1 + result.2)
}

func sqliteEscape(_ value: String) -> String {
    return value.replacingOccurrences(of: "'", with: "''")
}

func smokeTaskIDFromArguments() -> String? {
    let arguments = CommandLine.arguments
    guard let index = arguments.firstIndex(of: "--smoke-task-id") else { return nil }
    let valueIndex = arguments.index(after: index)
    guard valueIndex < arguments.endIndex else { return nil }
    let value = arguments[valueIndex].trimmingCharacters(in: .whitespacesAndNewlines)
    return value.isEmpty ? nil : value
}

func writeSmokeReport(_ report: SmokeReport) {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    if let data = try? encoder.encode(report) {
        try? data.write(to: URL(fileURLWithPath: smokeReportPath))
    }
}

func findEventTitle(externalID: String) -> String? {
    let store = EKEventStore()
    let now = Date()
    guard let start = Calendar.current.date(byAdding: .day, value: -3, to: now),
          let end = Calendar.current.date(byAdding: .day, value: 4, to: now) else { return nil }
    let predicate = store.predicateForEvents(withStart: start, end: end, calendars: nil)
    return store.events(matching: predicate).first(where: { $0.eventIdentifier == externalID })?.title
}

func performSmokeAndQuit() {
    let bundleID = Bundle.main.bundleIdentifier ?? "unknown"
    let processName = ProcessInfo.processInfo.processName
    let status = EKEventStore.authorizationStatus(for: .event)
    let granted = globalHasFullCalendarAccess(status)
    let count = granted ? EKEventStore().calendars(for: .event).count : 0
    let now = ISO8601DateFormatter().string(from: Date())

    func finish(taskID: String? = nil, title: String? = nil, pythonPreflight: (Int32, String, String)? = nil, sync: (Int32, String, String)? = nil, error: String? = nil) {
        var syncStatus: String? = nil
        var externalID: String? = nil
        var payloadHash: String? = nil
        var lastSyncedAt: String? = nil
        var logResult: String? = nil
        var logErrorCode: String? = nil
        var logErrorMessage: String? = nil
        var eventTitle: String? = nil
        if let taskID {
            syncStatus = sqliteValue("SELECT sync_status FROM sync_state WHERE task_id='\(taskID)' AND sync_target='apple_calendar' ORDER BY updated_at DESC LIMIT 1;")
            externalID = sqliteValue("SELECT external_id FROM sync_state WHERE task_id='\(taskID)' AND sync_target='apple_calendar' ORDER BY updated_at DESC LIMIT 1;")
            payloadHash = sqliteValue("SELECT payload_hash FROM sync_state WHERE task_id='\(taskID)' AND sync_target='apple_calendar' ORDER BY updated_at DESC LIMIT 1;")
            lastSyncedAt = sqliteValue("SELECT last_synced_at FROM sync_state WHERE task_id='\(taskID)' AND sync_target='apple_calendar' ORDER BY updated_at DESC LIMIT 1;")
            if let latestLog = sqliteValue("SELECT sync_result || '|' || IFNULL(error_code,'') || '|' || IFNULL(error_message,'') FROM sync_logs WHERE local_task_id='\(taskID)' ORDER BY created_at DESC LIMIT 1;") {
                let parts = latestLog.split(separator: "|", omittingEmptySubsequences: false).map(String.init)
                if parts.indices.contains(0) { logResult = parts[0] }
                if parts.indices.contains(1) { logErrorCode = parts[1] }
                if parts.indices.contains(2) { logErrorMessage = parts[2] }
            }
            if let externalID { eventTitle = findEventTitle(externalID: externalID) }
        }
        let inferredError = error ?? ((syncStatus == "failed_permanent" || (sync?.1.contains("auth_failed") ?? false) || (sync?.2.contains("auth_failed") ?? false)) ? "helper TCC was not inherited by Python sync_trigger subprocess" : nil)
        writeSmokeReport(SmokeReport(timestamp: now, bundleIdentifier: bundleID, processName: processName, authorizationStatus: globalStatusName(status), calendarAccessGranted: granted, calendarCount: count, taskID: taskID, taskTitle: title, pythonPath: projectPython, pythonPreflightExitCode: pythonPreflight.map { Int($0.0) }, pythonPreflightStdout: pythonPreflight?.1, pythonPreflightStderr: pythonPreflight?.2, syncTriggerExitCode: sync.map { Int($0.0) }, syncTriggerStdout: sync?.1, syncTriggerStderr: sync?.2, syncStatus: syncStatus, externalID: externalID, payloadHash: payloadHash, lastSyncedAt: lastSyncedAt, latestLogResult: logResult, latestLogErrorCode: logErrorCode, latestLogErrorMessage: logErrorMessage, eventReadBackTitle: eventTitle, error: inferredError))
        NSApp.terminate(nil)
    }

    guard bundleID == "com.vanta.lifesync.calendar-helper", granted, count > 0 else {
        finish(error: "helper_preflight_failed")
        return
    }

    let suffix = String(Int(Date().timeIntervalSince1970))
    let requestedTaskID = smokeTaskIDFromArguments()
    let taskID = requestedTaskID ?? "task_helper_smoke_\(suffix)"
    let existingTitle = sqliteValue("SELECT title FROM tasks WHERE task_id='\(sqliteEscape(taskID))' LIMIT 1;")
    let title = existingTitle ?? "[SYNC-TEST] LifeSync Calendar Helper smoke \(now)"
    let syncID = "sync_helper_smoke_\(suffix)"
    if existingTitle == nil {
        let escapedTaskID = sqliteEscape(taskID)
        let escapedTitle = sqliteEscape(title)
        let start = ISO8601DateFormatter().string(from: Date().addingTimeInterval(1800))
        let due = ISO8601DateFormatter().string(from: Date().addingTimeInterval(3600))
        let sql = """
        INSERT INTO tasks (task_id,title,description,priority,status,start_time,due_time,timezone,location,need_weather_check,reminder_channels,created_channel,sync_targets,created_at,updated_at,sync_enabled,last_sync_status)
        VALUES ('\(escapedTaskID)','\(escapedTitle)','Helper smoke test task','P3','pending','\(start)','\(due)','Asia/Shanghai',NULL,0,'["local_ui"]','api_test','["apple_calendar"]','\(now)','\(now)',1,NULL);
        INSERT INTO sync_state (sync_id,task_id,sync_target,sync_key,payload_hash,sync_status,sync_version,external_id,last_synced_at,last_sync_trigger,started_at,locked_at,created_at,updated_at)
        VALUES ('\(syncID)','\(escapedTaskID)','apple_calendar','\(escapedTaskID):apple_calendar',NULL,'pending',1,NULL,NULL,NULL,NULL,NULL,'\(now)','\(now)');
        """
        let inserted = sqliteExec(sql)
        guard inserted.0 else {
            finish(taskID: taskID, title: title, error: "insert_test_task_failed: \(inserted.1)")
            return
        }
    } else if sqliteValue("SELECT sync_id FROM sync_state WHERE task_id='\(sqliteEscape(taskID))' AND sync_target='apple_calendar' LIMIT 1;") == nil {
        let escapedTaskID = sqliteEscape(taskID)
        let sql = """
        INSERT INTO sync_state (sync_id,task_id,sync_target,sync_key,payload_hash,sync_status,sync_version,external_id,last_synced_at,last_sync_trigger,started_at,locked_at,created_at,updated_at)
        VALUES ('\(syncID)','\(escapedTaskID)','apple_calendar','\(escapedTaskID):apple_calendar',NULL,'pending',1,NULL,NULL,NULL,NULL,NULL,'\(now)','\(now)');
        """
        let inserted = sqliteExec(sql)
        guard inserted.0 else {
            finish(taskID: taskID, title: title, error: "insert_sync_state_failed: \(inserted.1)")
            return
        }
    }
    let pythonPreflight = runProcess(projectPython, ["-c", "import sys, EventKit, objc; print(sys.executable); print('EventKit PASS'); print('objc PASS')"], cwd: projectDir)
    guard pythonPreflight.0 == 0 else {
        finish(taskID: taskID, title: title, pythonPreflight: pythonPreflight, error: "python_preflight_failed")
        return
    }
    let sync = runProcess(projectPython, ["-m", "local_api.scripts.sync_trigger", "sync-task", taskID, "--target", "apple_calendar"], cwd: projectDir)
    finish(taskID: taskID, title: title, pythonPreflight: pythonPreflight, sync: sync)
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let eventStore = EKEventStore()
    private var window: NSWindow!
    private var outputView: NSTextView!
    private var requestButton: NSButton!
    private var checkButton: NSButton!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        if CommandLine.arguments.contains("--smoke") || CommandLine.arguments.contains("--smoke-task-id") {
            performSmokeAndQuit()
            return
        }
        buildMenu()
        buildWindow()
        checkAccess()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }

    private func buildMenu() {
        let mainMenu = NSMenu()
        let appMenuItem = NSMenuItem()
        let appMenu = NSMenu(title: "LifeSync Calendar Helper")
        appMenu.addItem(NSMenuItem(title: "Check Access", action: #selector(checkAccess), keyEquivalent: "r"))
        appMenu.addItem(NSMenuItem(title: "Request Calendar Access", action: #selector(requestCalendarAccess), keyEquivalent: ""))
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(NSMenuItem(title: "Quit LifeSync Calendar Helper", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        appMenuItem.submenu = appMenu
        mainMenu.addItem(appMenuItem)
        NSApp.mainMenu = mainMenu
    }

    private func buildWindow() {
        let appName = Bundle.main.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String
            ?? Bundle.main.object(forInfoDictionaryKey: "CFBundleName") as? String
            ?? "LifeSync Calendar Helper"

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 680, height: 420),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = appName
        window.center()
        window.isReleasedWhenClosed = false

        let content = NSView(frame: NSRect(x: 0, y: 0, width: 680, height: 420))
        content.autoresizingMask = [.width, .height]

        let title = NSTextField(labelWithString: appName)
        title.frame = NSRect(x: 24, y: 372, width: 500, height: 28)
        title.font = NSFont.boldSystemFont(ofSize: 20)
        title.autoresizingMask = [.width, .minYMargin]
        content.addSubview(title)

        let bundleID = Bundle.main.bundleIdentifier ?? "unknown"
        let bundle = NSTextField(labelWithString: "Bundle ID: \(bundleID)")
        bundle.frame = NSRect(x: 24, y: 344, width: 620, height: 20)
        bundle.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        bundle.autoresizingMask = [.width, .minYMargin]
        content.addSubview(bundle)

        let scrollView = NSScrollView(frame: NSRect(x: 24, y: 86, width: 632, height: 238))
        scrollView.borderType = .bezelBorder
        scrollView.hasVerticalScroller = true
        scrollView.autoresizingMask = [.width, .height]

        outputView = NSTextView(frame: scrollView.bounds)
        outputView.isEditable = false
        outputView.isSelectable = true
        outputView.font = NSFont.monospacedSystemFont(ofSize: 13, weight: .regular)
        outputView.autoresizingMask = [.width, .height]
        outputView.string = "Checking Calendar access..."
        scrollView.documentView = outputView
        content.addSubview(scrollView)

        requestButton = NSButton(frame: NSRect(x: 24, y: 30, width: 210, height: 34))
        requestButton.title = "Request Calendar Access"
        requestButton.bezelStyle = .rounded
        requestButton.target = self
        requestButton.action = #selector(requestCalendarAccess)
        requestButton.isEnabled = true
        requestButton.autoresizingMask = [.maxXMargin, .maxYMargin]
        content.addSubview(requestButton)

        checkButton = NSButton(frame: NSRect(x: 248, y: 30, width: 140, height: 34))
        checkButton.title = "Check Access"
        checkButton.bezelStyle = .rounded
        checkButton.target = self
        checkButton.action = #selector(checkAccess)
        checkButton.keyEquivalent = "\r"
        checkButton.isEnabled = true
        checkButton.autoresizingMask = [.maxXMargin, .maxYMargin]
        content.addSubview(checkButton)

        let shortcut = NSTextField(labelWithString: "Shortcut: Cmd+R checks access")
        shortcut.frame = NSRect(x: 408, y: 37, width: 250, height: 20)
        shortcut.font = NSFont.systemFont(ofSize: 12)
        shortcut.autoresizingMask = [.minXMargin, .maxYMargin]
        content.addSubview(shortcut)

        window.contentView = content
        window.initialFirstResponder = checkButton
        window.defaultButtonCell = checkButton.cell as? NSButtonCell
        window.makeKeyAndOrderFront(nil)
        window.orderFrontRegardless()
        window.makeMain()
    }

    @objc private func requestCalendarAccess() {
        let statusBefore = EKEventStore.authorizationStatus(for: .event)
        appendOutput("""
        requestButtonClicked=true
        mainThreadAtRequest=\(Thread.isMainThread)
        statusBeforeRequest=\(statusName(statusBefore))
        callbackCompleted=false
        """)

        let performRequest = { [weak self] in
            guard let self else { return }
            if #available(macOS 14.0, *) {
                self.eventStore.requestFullAccessToEvents { [weak self] granted, error in
                    DispatchQueue.main.async {
                        guard let self else { return }
                        let statusAfter = EKEventStore.authorizationStatus(for: .event)
                        self.appendOutput("""
                        callbackCompleted=true
                        callbackGranted=\(granted)
                        callbackError=\(error?.localizedDescription ?? "nil")
                        statusAfterRequest=\(self.statusName(statusAfter))
                        calendarAccessGranted=\(self.hasFullCalendarAccess(statusAfter))
                        calendarCount=\(self.hasFullCalendarAccess(statusAfter) ? String(self.eventStore.calendars(for: .event).count) : "n/a")
                        """)
                    }
                }
            } else {
                self.eventStore.requestAccess(to: .event) { [weak self] granted, error in
                    DispatchQueue.main.async {
                        guard let self else { return }
                        let statusAfter = EKEventStore.authorizationStatus(for: .event)
                        self.appendOutput("""
                        callbackCompleted=true
                        callbackGranted=\(granted)
                        callbackError=\(error?.localizedDescription ?? "nil")
                        statusAfterRequest=\(self.statusName(statusAfter))
                        calendarAccessGranted=\(self.hasFullCalendarAccess(statusAfter))
                        calendarCount=\(self.hasFullCalendarAccess(statusAfter) ? String(self.eventStore.calendars(for: .event).count) : "n/a")
                        """)
                    }
                }
            }
        }

        if Thread.isMainThread {
            performRequest()
        } else {
            DispatchQueue.main.async(execute: performRequest)
        }
    }

    @objc private func checkAccess() {
        DispatchQueue.main.async { [weak self] in
            self?.writeOutput(extra: "checkAccess: refreshed")
        }
    }

    private func writeOutput(extra: String) {
        let status = EKEventStore.authorizationStatus(for: .event)
        let granted = hasFullCalendarAccess(status)
        let countText = granted ? String(eventStore.calendars(for: .event).count) : "n/a"
        let bundleID = Bundle.main.bundleIdentifier ?? "unknown"
        let processName = ProcessInfo.processInfo.processName
        let timestamp = ISO8601DateFormatter().string(from: Date())
        outputView.string = """
        timestamp: \(timestamp)
        appName: LifeSync Calendar Helper
        bundleIdentifier: \(bundleID)
        processName: \(processName)
        authorizationStatus: \(statusName(status))
        calendarAccessGranted: \(granted)
        calendarCount: \(countText)
        \(extra)
        """
    }

    private func statusName(_ status: EKAuthorizationStatus) -> String {
        switch status {
        case .notDetermined:
            return "notDetermined"
        case .restricted:
            return "restricted"
        case .denied:
            return "denied"
        case .authorized:
            return "authorized"
        case .fullAccess:
            return "fullAccess"
        case .writeOnly:
            return "writeOnly"
        @unknown default:
            return "unknown"
        }
    }

    private func hasFullCalendarAccess(_ status: EKAuthorizationStatus) -> Bool {
        if #available(macOS 14.0, *) {
            return status == .fullAccess
        }
        return status == .authorized
    }

    private func appendOutput(_ line: String) {
        let existing = outputView.string
        let timestamp = ISO8601DateFormatter().string(from: Date())
        outputView.string = existing + "\n\n[\(timestamp)] \(line)"
        outputView.scrollToEndOfDocument(nil)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
