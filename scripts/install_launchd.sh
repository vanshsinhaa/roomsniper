#!/bin/sh
set -eu

release_hour="${1:-0}"
release_minute="${2:-0}"
config_path="${3:-$PWD/config.yaml}"
label="com.local.hayden-room-booker"
agents_dir="$HOME/Library/LaunchAgents"
plist_path="$agents_dir/$label.plist"

mkdir -p "$agents_dir"
umask 077
cat > "$plist_path" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>hayden-booker</string><string>--config</string><string>$config_path</string>
    <string>run</string><string>--due</string><string>--live</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>$release_hour</integer><key>Minute</key><integer>$release_minute</integer></dict>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist_path"
echo "Installed $label. launchd uses system-local time; keep config timezone America/Phoenix."
