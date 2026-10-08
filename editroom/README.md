# Outlook edit room

Josh's morning editor for the daily category outlook (manual outlook, Oct 2026).

**Open it:** http://localhost:8417/ on the Mac (bookmark it). A LaunchAgent keeps the server running:
`~/Library/LaunchAgents/au.com.cyclonesoz.edit-room.plist`, logs in `~/.cyclonesoz/edit-room/`.

**Each morning:** the editor starts from your last published edit carried forward (yesterday's
days 2-8 are today's days 1-7; the new day 8 comes from the model). Squares outlined magenta are
where the new model run changed its mind and now disagrees sharply with you. Paint, box or outline
squares with a category or Upgrade/Downgrade; days 5-8 use the public map's coarse scale. Open
Preview to see the public page itself drawing your draft: anything you changed that its smoothing
would remove is outlined red. Then Publish.

**Sandbox (now):** Publish writes to `~/.cyclonesoz/edit-room/sandbox/` only. View the result at
http://localhost:8417/sandbox/, the public page fed the sandbox outlook. Nothing goes live.

**How it fits together:** `server.py` (standard-library Python, runs from its own clone in
`~/.cyclonesoz/edit-room/repo`, refreshed from GitHub when the editor loads) uses
`pipeline/manual_compose.py`, the same code the nightly pipeline uses to carry edits forward, so
the preview, the publish and the overnight carry-forward always agree.

**Restart / stop:**
`launchctl kickstart -k gui/$(id -u)/au.com.cyclonesoz.edit-room` /
`launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/au.com.cyclonesoz.edit-room.plist`
