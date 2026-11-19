#!/bin/sh
# Installs the severe-watch map on this Mac: copies it to ~/.cyclonesoz/severe-map and runs it at
# login on http://localhost:8419/ (launchd, like the edit room). Run it again after changing the map.
#
#   sh severemap/install.sh            install or update
#   sh severemap/install.sh remove     stop it and remove the login item (the files stay)
set -e
SRC=$(cd "$(dirname "$0")" && pwd)
DEST="$HOME/.cyclonesoz/severe-map"
LABEL=au.com.cyclonesoz.severe-map
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "$1" = remove ]; then
  rm -f "$PLIST"
  echo "Severe map stopped; $DEST left in place."
  exit 0
fi

mkdir -p "$DEST"
cp "$SRC/server.py" "$SRC/index.html" "$SRC/basemap-style.json" "$DEST/"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>$DEST/server.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DEST</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$DEST/launchd.log</string>
  <key>StandardErrorPath</key><string>$DEST/launchd.log</string>
</dict>
</plist>
EOF
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Severe map: http://localhost:8419/"
