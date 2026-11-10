#!/usr/bin/env bash
# Builds "Form Studio.app" in ~/Applications.
#
# The app is a small native Mac shell (mac/main.swift) that starts the Python
# server from this folder's .venv in the background and shows the interface.
# Re-run this after `git pull` or after moving this folder.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
APP_DIR="${FORMSTUDIO_APP_DIR:-$HOME/Applications}"
APP="$APP_DIR/Form Studio.app"
LOG="${FORMSTUDIO_HOME:-$HOME/FormStudio}/formstudio.log"

if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "Run scripts/setup-mac.sh first."; exit 1
fi

# quit a running copy so it can be replaced
osascript -e 'tell application id "in.alwi.formstudio" to quit' >/dev/null 2>&1 || true
pkill -f "formstudio.desktop --serve" >/dev/null 2>&1 || true

mkdir -p "$APP_DIR"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Form Studio</string>
  <key>CFBundleDisplayName</key><string>Form Studio</string>
  <key>CFBundleIdentifier</key><string>in.alwi.formstudio</string>
  <key>CFBundleVersion</key><string>0.2.0</string>
  <key>CFBundleShortVersionString</key><string>0.2.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>FormStudio</string>
  <key>CFBundleIconFile</key><string>FormStudio</string>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.graphics-design</string>
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>FSProjectRoot</key><string>$ROOT</string>
</dict>
</plist>
PLIST

# 1. the native shell
NATIVE=0
if command -v swiftc >/dev/null 2>&1; then
  ARCH="$(uname -m)"
  echo "Compiling the app ($ARCH)…"
  if swiftc -O -swift-version 5 -target "$ARCH-apple-macos12.0" \
       -framework AppKit -framework WebKit \
       "$ROOT/mac/main.swift" -o "$APP/Contents/MacOS/FormStudio"; then
    NATIVE=1
  else
    echo "!! Compiling the native app failed (errors above). Using the browser launcher instead."
  fi
else
  echo "!! swiftc not found. Install Apple's Command Line Tools with: xcode-select --install"
  echo "   Using the browser launcher for now."
fi

# 2. fallback: start the server, open the browser, report errors in a dialog
if [ "$NATIVE" = 0 ]; then
  cat > "$APP/Contents/MacOS/FormStudio" <<LAUNCH
#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"
export PYTORCH_ENABLE_MPS_FALLBACK=1
mkdir -p "\$(dirname "$LOG")"
cd "$ROOT"
"$ROOT/.venv/bin/python" -m formstudio.desktop --browser >> "$LOG" 2>&1 &
PID=\$!
sleep 4
if ! kill -0 \$PID 2>/dev/null; then
  osascript -e 'display alert "Form Studio couldn'"'"'t start" message "See the log: $LOG" as critical'
fi
LAUNCH
  chmod +x "$APP/Contents/MacOS/FormStudio"
fi

# 3. icon: assets/icon.png → .icns
ICONSET="$(mktemp -d)/FormStudio.iconset"
mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$ROOT/assets/icon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$ROOT/assets/icon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/FormStudio.icns"

# 4. sign locally (no Apple account needed) so macOS treats it as one app
codesign --force --deep -s - "$APP" >/dev/null 2>&1 || true
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" >/dev/null 2>&1 || true

echo "Built: $APP"
if [ "$NATIVE" = 1 ]; then
  echo "Open it from Launchpad or Spotlight. If anything goes wrong, the log is $LOG"
fi
