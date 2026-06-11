#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR/LifeSyncCalendarHelper.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"
SRC="$SCRIPT_DIR/src/LifeSyncCalendarHelper.swift"
BIN="$MACOS_DIR/LifeSyncCalendarHelper"

mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"
cat > "$CONTENTS_DIR/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>zh_CN</string>
    <key>CFBundleDisplayName</key>
    <string>LifeSync Calendar Helper</string>
    <key>CFBundleExecutable</key>
    <string>LifeSyncCalendarHelper</string>
    <key>CFBundleIdentifier</key>
    <string>com.vanta.lifesync.calendar-helper</string>
    <key>CFBundleName</key>
    <string>LifeSync Calendar Helper</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>14.0</string>
    <key>NSCalendarsFullAccessUsageDescription</key>
    <string>LifeSync Calendar Helper needs Calendar access to sync explicitly marked tasks to Apple Calendar.</string>
    <key>NSCalendarsUsageDescription</key>
    <string>LifeSync Calendar Helper needs Calendar access to sync explicitly marked tasks to Apple Calendar.</string>
    <key>NSCalendarsWriteOnlyAccessUsageDescription</key>
    <string>LifeSync Calendar Helper needs Calendar write access to sync explicitly marked tasks to Apple Calendar.</string>
</dict>
</plist>
PLIST

xcrun swiftc "$SRC" -framework AppKit -framework Foundation -framework EventKit -o "$BIN"
chmod +x "$BIN"
codesign --force --sign - "$APP_DIR"
codesign --verify --deep --strict --verbose=2 "$APP_DIR"
codesign -dv "$APP_DIR" 2>&1
