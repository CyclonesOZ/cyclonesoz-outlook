#!/bin/sh
# Installs the severe-watch map on this Mac: copies it to ~/.cyclonesoz/severe-map and runs it at
# login on http://localhost:8419/ (launchd, like the edit room). Run it again after changing the map.
#
# Since 23 Nov 2026 it also refreshes itself: a second launch agent runs severemap/refresh.py at
# 4:25, 5:15 and 6:45 am (and at login), which runs the morning scan on this Mac in print-only
# mode and keeps the newest areas for the map. For that it keeps its own Python
# (~/.cyclonesoz/severe-map/venv) and its own clone of this repo (~/.cyclonesoz/severe-map/repo).
#
#   sh severemap/install.sh            install or update
#   sh severemap/install.sh remove     stop both and remove the login items (the files stay)
set -e
SRC=$(cd "$(dirname "$0")" && pwd)
DEST="$HOME/.cyclonesoz/severe-map"
LABEL=au.com.cyclonesoz.severe-map
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
RLABEL=au.com.cyclonesoz.severe-map-refresh
RPLIST="$HOME/Library/LaunchAgents/$RLABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/$RLABEL" 2>/dev/null || true
if [ "$1" = remove ]; then
  rm -f "$PLIST" "$RPLIST"
  echo "Severe map stopped; $DEST left in place."
  exit 0
fi

mkdir -p "$DEST/data"
cp "$SRC/server.py" "$SRC/index.html" "$SRC/basemap-style.json" "$SRC/refresh.py" "$DEST/"

# The scan's own Python. Same packages the alert-scan workflow pins, at the newest versions that
# still run on macOS's Python 3.9.
if [ ! -x "$DEST/venv/bin/python" ]; then
  /usr/bin/python3 -m venv "$DEST/venv"
  "$DEST/venv/bin/python" -m pip install --quiet --upgrade pip
fi
"$DEST/venv/bin/python" -m pip install --quiet "numpy==2.0.2" "scipy==1.13.1" "pillow==11.3.0" "contourpy==1.3.0"
[ -d "$DEST/repo/.git" ] || git clone -q --depth=1 https://github.com/CyclonesOZ/cyclonesoz-outlook.git "$DEST/repo"

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

# 4:25 am catches the 4 am run; 5:15 and 6:45 catch a late one. The Mac's clock is Perth time.
cat > "$RPLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$RLABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string><string>-i</string>
    <string>/usr/bin/python3</string>
    <string>$DEST/refresh.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DEST</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>25</integer></dict>
    <dict><key>Hour</key><integer>5</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>45</integer></dict>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$DEST/refresh-launchd.log</string>
  <key>StandardErrorPath</key><string>$DEST/refresh-launchd.log</string>
</dict>
</plist>
EOF
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl bootstrap "gui/$(id -u)" "$RPLIST"
echo "Severe map: http://localhost:8419/ (refreshes itself at 4:25, 5:15 and 6:45 am)"
